"""INTEGRA exposure-model client (AUTH source-to-dose platform).

Wraps the four INTEGRA models behind one interface. The connector holds each
model's complete default payload (data/integra_*.json); the app supplies a small
scenario override, and this module POSTs the merged payload to INTEGRA and
aggregates the per-pathway intake into a mean daily dose (mg/kg/day) that drives
the q-eAON `intensity` (= dose / POD).

Config (server-side, never shipped to the browser):
  INTEGRA_BASE_URL  (default https://integra-dev.cheng.auth.gr)
  INTEGRA_API_KEY   (X-API-Key)

NOTE: the intake aggregators below follow the documented EXPECTED_OUTPUTS units;
they are validated/finalised against a real INTEGRA response (see probe step).
Every parse is echoed in the returned `diagnostics` so the mapping is auditable.
"""
from __future__ import annotations
import os, json, httpx

_HERE = os.path.dirname(__file__)
INTEGRA_BASE = os.environ.get("INTEGRA_BASE_URL", "https://integra-dev.cheng.auth.gr")

# INTEGRA's inhalation/dermal exposure series is reported ~1000x too high (an
# internal m3/L or ug/mg unit mismatch: e.g. it returns 30864 ug/h from a
# 100 ug/h source, which is mass-balance-impossible). Correct the inhalation
# intake by this factor. Remove/adjust if INTEGRA fixes the exposure units.
INHALATION_UGH_CORRECTION = 1e-3

ENDPOINTS = {
    "dietary":    "/models/dietary/run",
    "inhalation": "/models/inhalation/run",
    "multimedia": "/models/multimedia/run",
    "nondietary": "/models/non-dietary/run",
}
_DEFAULT_FILE = {
    "dietary":    "integra_dietary.json",
    "inhalation": "integra_inhalation.json",
    "multimedia": "integra_multimedia.json",
    "nondietary": "integra_nondietary.json",
}
_CACHE = {}


def default_payload(model):
    if model not in _CACHE:
        p = os.path.join(_HERE, "data", _DEFAULT_FILE[model])
        try:
            _CACHE[model] = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            _CACHE[model] = {}
    return dict(_CACHE[model])


def _expand(model, overrides):
    """Translate the app's simplified scenario keys into full INTEGRA fields,
    so the UI can send one concentration per route instead of dozens of fields:
      dietary:    _food_conc_ug_g   -> every MASS_CONCENTRATION_* food field
      inhalation: _air_conc_ug_m3   -> C_chem_gas_outdoor (+ indoor baselines)
      nondietary: _soil_conc_ug_g, _dust_conc_ug_g
    Any remaining keys are treated as explicit INTEGRA fields and passed through."""
    ov = dict(overrides or {})
    out = {}
    if model == "dietary":
        dp = default_payload("dietary")
        # (legacy, deprecated) one concentration applied to every food group.
        fc = ov.pop("_food_conc_ug_g", None)
        if fc is not None:
            for k in dp:
                if k.startswith("MASS_CONCENTRATION_"):
                    out[k] = fc
        # PER-FOOD concentration map {FOOD: ug/g}. When supplied we ZERO every food
        # first (an unlisted food is uncontaminated), then set the ones given -- so
        # the chemical is no longer assumed present in all foods at one concentration.
        conc = ov.pop("_food_conc", None)
        if conc is not None:
            for k in dp:
                if k.startswith("MASS_CONCENTRATION_"):
                    out[k] = 0.0
            for food, c in (conc or {}).items():
                key = "MASS_CONCENTRATION_" + food
                if key in dp and c is not None:
                    out[key] = float(c)
        # PER-FOOD daily consumption {FOOD: g/day}. TIME_EATEN=[1,25] over period=48h
        # means two eating events per 48h, so AMOUNT_EATEN=[D,D] -> D g/day.
        amt = ov.pop("_food_amount", None) or {}
        for food, d in amt.items():
            key = food + "_AMOUNT_EATEN"
            if key in dp and d is not None:
                out[key] = [float(d), float(d)]
    elif model == "inhalation":
        ac = ov.pop("_air_conc_ug_m3", None)
        if ac is not None:
            out["C_chem_gas_outdoor"] = ac
            out["C0_chem_gas1"] = ac
            out["C0_chem_gas2"] = ac
            # Drive inhalation from the supplied ambient/indoor air concentration,
            # not the demo indoor emission bursts: zero the emission incidents
            # (keep the [time, rate, duration] structure with rate = 0).
            out["E_chem_gas1"] = [[0, 0, 1]]
            out["E_chem_gas2"] = [[0, 0, 1]]
    elif model == "nondietary":
        sc = ov.pop("_soil_conc_ug_g", None)
        dc = ov.pop("_dust_conc_ug_g", None)
        if sc is not None:
            out["concentration_of_chemical_in_soil"] = sc
        if dc is not None:
            out["concentration_of_chemical_in_dust"] = dc
    out.update(ov)          # explicit real-field overrides win
    if model == "inhalation":
        # INTEGRA's R wrapper validates inhalation_rates with `&&`, which errors
        # under R>=4.3 on a length>1 vector. Send a scalar (constant breathing
        # rate) as a safe workaround until the server uses any(inhalation_rates<0).
        ir = out.get("inhalation_rates", default_payload("inhalation").get("inhalation_rates"))
        if isinstance(ir, list) and ir:
            out["inhalation_rates"] = ir[0]
    return out


def merge_payload(model, overrides):
    p = default_payload(model)
    p.update(_expand(model, overrides))
    return p


# ---------- intake aggregators (ug/day) ----------
def _series_daily(v, period_h=24):
    """Coerce a value that may be a scalar rate (ug/h), a time series (list of
    ug/h), or None into a daily total (ug/day) = mean rate * period."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v) * period_h
    if isinstance(v, list):
        nums = [x for x in v if isinstance(x, (int, float))]
        return (sum(nums) / len(nums)) * period_h if nums else 0.0
    return 0.0


def _results(resp):
    """INTEGRA wraps outputs under a 'results' object; fall back to the root."""
    if isinstance(resp, dict) and isinstance(resp.get("results"), dict):
        return resp["results"]
    return resp if isinstance(resp, dict) else {}


def _find(container, *names):
    """Return the first present key value from a dict (case-tolerant)."""
    if not isinstance(container, dict):
        return None
    low = {k.lower(): v for k, v in container.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def dietary_intake_ugday(resp):
    """Mean daily dietary intake (ug/day) from results.daily_intake.values[].intake."""
    r = _results(resp)
    di = _find(r, "daily_intake", "Daily_Intake")
    if isinstance(di, dict) and isinstance(di.get("values"), list):
        vals = [float((x.get("intake", x.get("Intake")) or 0.0))
                for x in di["values"] if isinstance(x, dict)]
        return sum(vals) / len(vals) if vals else 0.0
    if isinstance(di, dict) and di.get("Intake") is not None:
        return float(di.get("Intake") or 0.0)
    if isinstance(di, list):
        vals = [float((x.get("intake", x.get("Intake")) or 0.0))
                for x in di if isinstance(x, dict)]
        return sum(vals) / len(vals) if vals else 0.0
    return 0.0


def inhalation_intake_ugday(resp, period_h=24, zone="1"):
    """Near-field (zone 1) inhalation + dermal uptake -> ug/day.
    Real INTEGRA shape: results.time_series.{inhalation,dermal}.zone_1 (ug/h series)."""
    r = _results(resp)
    ts = r.get("time_series") if isinstance(r, dict) else None
    tot = 0.0
    if isinstance(ts, dict):
        for group in ("inhalation", "dermal"):
            g = ts.get(group)
            if isinstance(g, dict):
                tot += _series_daily(_find(g, f"zone_{zone}", f"Zone_{zone}"), period_h)
        if tot:
            return tot * INHALATION_UGH_CORRECTION
    for k in (f"inhalation_exposure_Zone_{zone}", f"dermal_exposure_Zone_{zone}"):
        v = _find(r, k)          # fallback: flat documented shape
        if v is not None:
            tot += _series_daily(v, period_h)
    return tot * INHALATION_UGH_CORRECTION


def nondietary_intake_ugday(resp, period_h=24):
    """Soil + dust + mouthing + ingested-PCP pathways (ug/h series) -> ug/day."""
    r = _results(resp)
    tot = 0.0
    for k in ("m_soil", "m_dust", "m_mouthing", "m_ingestion_PCPs"):
        v = _find(r, k)
        if v is not None:
            tot += _series_daily(v, period_h)
    return tot


def sig(x, n=6):
    """Round to n significant figures (NOT fixed decimals). Fixed-decimal rounding
    floors ultra-low values to 0 -- e.g. round(8.6e-11, 10) and round(6e-6, 4) == 0 --
    which wiped out dioxin-class intakes (food conc in pg/g -> dose ~1e-9..1e-12
    mg/kg/day). Significant-figure rounding preserves value across all magnitudes."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if x == 0.0 or x != x or x in (float("inf"), float("-inf")):
        return 0.0 if x == 0.0 else x
    import math
    return round(x, -int(math.floor(math.log10(abs(x)))) + (n - 1))


def dose_mgkgday(intake_ugday, bodyweight_kg):
    if not bodyweight_kg:
        return None
    return sig(intake_ugday / 1000.0 / float(bodyweight_kg), 6)


class IntegraClient:
    def __init__(self, api_key=None, base=None, timeout=180.0):
        self.key = api_key or os.environ.get("INTEGRA_API_KEY", "")
        self.base = (base or INTEGRA_BASE).rstrip("/")
        self.timeout = timeout

    def _h(self):
        return {"X-API-Key": self.key, "Accept": "application/json",
                "Content-Type": "application/json"}

    async def run(self, model, overrides=None):
        if model not in ENDPOINTS:
            raise ValueError(f"unknown INTEGRA model: {model}")
        url = self.base + ENDPOINTS[model]
        payload = merge_payload(model, overrides)
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(url, json=payload, headers=self._h())
            r.raise_for_status()
            return r.json()
