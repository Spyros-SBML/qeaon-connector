"""Tier-1 lookup: curated IARC key-characteristics profiles -> authoritative g/p.

This is the top tier of the hybrid. If a chemical is in the curated IARC table,
its g/p come straight from the Working Group's mechanistic key-characteristics
evaluation (deterministic, citable) and override the ToxCast/GeneTox tiers.
g/p are derived from the stored KC weights using the SAME KC partition as
kc_mapping, so every tier sits on one scale.
"""
from __future__ import annotations
import json, os
from kc_mapping import KC, KC_DIV

_DATA = os.path.join(os.path.dirname(__file__), "data", "iarc_kc.json")
_INDEX = None   # built once: lookup key -> enriched entry

# Phase-A target-tissue assignment (primary site with sufficient human evidence,
# per the IARC Monograph cited in each entry). Used to partition stressors into
# per-tissue fields so cross-organ mixtures combine independently instead of
# sharing one field. Multi-site agents are tagged "multi-site" (handled as their
# own field for now). CAS-keyed so it is independent of naming.
TISSUE_BY_CAS = {
    "71-43-2":     "haematopoietic",   # benzene -> AML/leukaemia (IARC 120)
    "50-00-0":     "nasopharynx",      # formaldehyde -> nasopharynx (+leukaemia) (IARC 100F)
    "50-32-8":     "lung",             # benzo[a]pyrene -> lung/skin (IARC 100F)
    "75-01-4":     "liver",            # vinyl chloride -> liver angiosarcoma (IARC 100F)
    "106-99-0":    "haematopoietic",   # 1,3-butadiene -> leukaemia/lymphoma (IARC 100F)
    "75-21-8":     "haematopoietic",   # ethylene oxide -> lymphoid (IARC 100F)
    "79-01-6":     "kidney",           # trichloroethylene -> kidney (IARC 106)
    "1746-01-6":   "multi-site",       # 2,3,7,8-TCDD -> all-cancers promoter (IARC 100F)
    "1162-65-8":   "liver",            # aflatoxin B1 -> liver (IARC 100F)
    "7440-38-2":   "lung",             # inorganic arsenic -> lung (inhal.; also skin/bladder) (IARC 100C)
    "7440-43-9":   "lung",             # cadmium -> lung (IARC 100C)
    "18540-29-9":  "lung",             # Cr(VI) -> lung (IARC 100C)
    "7440-02-0":   "lung",             # nickel compounds -> lung/nasal (IARC 100C)
    "57465-28-8":  "liver",            # PCB-126 -> liver (dioxin-like) (IARC 107)
}


def _norm_cas(s):
    return (s or "").strip()


def _gp_from_kc(kc):
    g = sum((kc[i] if i < len(kc) else 0) * KC[i][1] for i in range(len(KC)))
    p = sum((kc[i] if i < len(kc) else 0) * KC[i][2] for i in range(len(KC)))
    return round(min(1.0, g / KC_DIV), 3), round(min(1.0, p / KC_DIV), 3)


SECONDARY_SCALE = 0.5   # a channel that is NOT the agent's primary MOA contributes at half weight


def _dominance(kc, g, p):
    """Mechanism-dominance scaling. KC2 (genotoxicity) is the primacy signal:
      strong genotoxin (KC2>=1) -> initiation-dominant: down-weight promoter p
      non-genotoxic (KC2==0)    -> promotion-dominant:  down-weight initiator g
      partial (KC2==0.5)        -> balanced: no scaling
    This keeps the KC breadth visible but makes g/p reflect the primary MOA,
    so a multi-mechanism genotoxin (benzene) no longer reads as a full promoter."""
    genotox = kc[1] if len(kc) > 1 else 0
    if genotox >= 1.0:
        return g, round(p * SECONDARY_SCALE, 3), "initiation-dominant (strong genotoxicity)"
    if genotox == 0:
        return round(g * SECONDARY_SCALE, 3), p, "promotion-dominant (non-genotoxic)"
    return g, p, "balanced (mixed MOA)"


def _enrich(e):
    kc = e.get("kc", [0] * len(KC))
    g, p = _gp_from_kc(kc)
    g, p, dominance = _dominance(kc, g, p)
    strong = [i + 1 for i, w in enumerate(kc) if w >= 1.0]
    some = [i + 1 for i, w in enumerate(kc) if 0 < w < 1.0]
    return {
        "name": e["name"], "casrn": e.get("casrn"), "dtxsid": e.get("dtxsid"),
        "iarc_group": e.get("iarc_group"), "monograph": e.get("monograph"),
        "mechanism": e.get("mechanism"), "kc": kc,
        "kc_strong": strong, "kc_some": some,
        "g": g, "p": p, "dominance": dominance,
        "tissue": TISSUE_BY_CAS.get(_norm_cas(e.get("casrn")), "unspecified"),
        "source": f"IARC key characteristics (curated; {e.get('monograph','')})",
        "iris": e.get("iris"),
    }


def _build():
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    idx = {}
    try:
        data = json.load(open(_DATA, encoding="utf-8"))
    except (OSError, ValueError):
        _INDEX = {}
        return _INDEX
    for e in data.get("entries", []):
        rec = _enrich(e)
        if e.get("casrn"):
            idx["cas:" + _norm_cas(e["casrn"])] = rec
        if e.get("dtxsid"):
            idx["dtx:" + e["dtxsid"].strip().upper()] = rec
        for a in [e["name"]] + e.get("aliases", []):
            idx["name:" + a.strip().lower()] = rec
    _INDEX = idx
    return _INDEX


def lookup(casrn=None, dtxsid=None, name=None):
    """Return the curated record (with derived g/p) or None."""
    idx = _build()
    if casrn and ("cas:" + _norm_cas(casrn)) in idx:
        return idx["cas:" + _norm_cas(casrn)]
    if dtxsid and ("dtx:" + dtxsid.strip().upper()) in idx:
        return idx["dtx:" + dtxsid.strip().upper()]
    if name and ("name:" + name.strip().lower()) in idx:
        return idx["name:" + name.strip().lower()]
    return None


def all_entries():
    """Every curated record (for bundling the offline library)."""
    seen, out = set(), []
    for rec in _build().values():
        key = rec["casrn"] or rec["name"]
        if key not in seen:
            seen.add(key); out.append(rec)
    return out


IRIS_RISK = 1e-4                       # excess-cancer-risk benchmark for the risk-specific dose
INH_FACTOR = 20.0 / 70.0 / 1000.0      # ug/m3 -> mg/kg/day (20 m3/day, 70 kg, ug->mg)


def _sig(x, n=4):
    from math import log10, floor
    if not x:
        return x
    return round(x, -int(floor(log10(abs(x)))) + (n - 1))


def iris_pod(iris, risk=IRIS_RISK):
    """Authoritative POD (mg/kg/day) from IRIS/OEHHA cancer potency, as the
    risk-specific dose at `risk`: OSF (oral) preferred; else IUR (inhalation,
    converted with EPA defaults); else RfD (threshold). Returns None if absent."""
    if not iris:
        return None
    osf, iur, rfd = iris.get("osf"), iris.get("iur"), iris.get("rfd")
    if osf:
        return {"aed_pod_mgkgday": _sig(risk / osf), "basis": "oral slope factor",
                "risk_benchmark": risk, "source": iris.get("source")}
    if iur:
        return {"aed_pod_mgkgday": _sig((risk / iur) * INH_FACTOR),
                "basis": "inhalation unit risk", "risk_benchmark": risk,
                "source": iris.get("source")}
    if rfd:
        return {"aed_pod_mgkgday": _sig(rfd), "basis": "RfD (threshold)",
                "risk_benchmark": None, "source": iris.get("source")}
    return None
