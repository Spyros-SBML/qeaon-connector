"""Potency / dose anchoring for the intensity redefinition (see INTENSITY_DOSE_SPEC.md).

From active ToxCast assays (each carrying an AC50 in uM and its key-characteristics)
we derive channel-specific points of departure:
  POD_g  = 5th-percentile AC50 over genotoxic-side assays (KC 1-3, +half 4-5)
  POD_p  = 5th-percentile AC50 over promoter-side assays  (KC 6-10, +half 4-5)
Then httk Css (mg/L per 1 mg/kg/day) + molecular weight convert the internal POD (uM)
to an administered equivalent dose AED_POD (mg/kg/day). intensity = dose / anchor.
"""
from __future__ import annotations
from kc_mapping import assay_to_kcs

G_KCS = {0, 1, 2}          # electrophilic, genotoxic, DNA-repair (0-indexed)
P_KCS = {5, 6, 7, 8, 9}    # inflammation..proliferation
BOTH = {3, 4}              # epigenetic, oxidative stress -> both channels


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(values, q):
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return round(xs[0], 4)
    k = (len(xs) - 1) * q / 100.0
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 4)


def _gather(hits, cytotox_uM):
    g, p, allv = [], [], []
    for a in hits:
        if a.hitc is None or a.hitc < 0.5 or a.ac50 is None:
            continue
        if cytotox_uM and a.ac50 >= cytotox_uM:   # drop nonspecific (cytotoxic burst)
            continue
        kcs = set(assay_to_kcs(a))
        allv.append(a.ac50)
        if kcs & (G_KCS | BOTH):
            g.append(a.ac50)
        if kcs & (P_KCS | BOTH):
            p.append(a.ac50)
    return g, p, allv


def channel_pods(hits, cytotox_uM=None):
    """5th-percentile AC50 (uM) per channel. Nonspecific (>cytotox) hits dropped;
    falls back to unfiltered if that leaves nothing."""
    g, p, allv = _gather(hits, cytotox_uM)
    filtered = bool(cytotox_uM)
    if cytotox_uM and not allv:
        g, p, allv = _gather(hits, None); filtered = False
    return {
        "pod_g_uM": _pct(g, 5), "n_g": len(g),
        "pod_p_uM": _pct(p, 5), "n_p": len(p),
        "pod_overall_uM": _pct(allv, 5), "n_pod": len(allv),
        "cytotox_uM": round(cytotox_uM, 3) if cytotox_uM else None,
        "cytotox_filtered": filtered,
    }


def pick_css(records):
    """Choose the best steady-state plasma concentration (mg/L per 1 mg/kg/day)
    from CTX httk records: prefer human, a mechanistic model, conservative pct."""
    css = [r for r in (records or [])
           if (r.get("parameter") or "").lower() == "css" and r.get("predicted") is not None]
    if not css:
        return None

    def score(r):
        model = (r.get("model") or "").lower()
        sp = (r.get("dataSourceSpecies") or r.get("species") or "").lower()
        pct = str(r.get("percentile") or "")
        s = 100 if "human" in sp else 0
        s += {"3compartmentss": 30, "pbtk": 20, "1compartment": 10}.get(model, 5)
        s += 5 if "95" in pct else 0
        return s

    b = max(css, key=score)
    return {"css_mgL_per_mgkgday": _to_float(b.get("predicted")),
            "model": b.get("model"), "percentile": b.get("percentile"),
            "species": b.get("dataSourceSpecies") or b.get("species"),
            "units": b.get("units")}


def aed_from_pod(pod_uM, css_mgL_per_mgkgday, mw):
    """Administered equivalent dose (mg/kg/day) whose internal Css equals the POD.
    AED = (POD_uM * MW / 1000  [mg/L]) / Css[mg/L per mg/kg/day]."""
    if not (pod_uM and css_mgL_per_mgkgday and mw):
        return None
    pod_mgL = pod_uM * mw / 1000.0
    return round(pod_mgL / css_mgL_per_mgkgday, 4)


def pick_seem(records):
    """Best SEEM3 predicted human exposure (mg/kg/day): prefer Total population,
    Consensus predictor, inside applicability domain."""
    recs = [r for r in (records or []) if r.get("median") is not None]
    if not recs:
        return None

    def score(r):
        s = 0
        if (r.get("demographic") or "").lower() == "total":
            s += 100
        if "consensus" in (r.get("predictor") or "").lower():
            s += 50
        if r.get("ad") in (1, "1", True):
            s += 10
        return s

    b = max(recs, key=score)
    return {"exposure_mgkgday": _to_float(b.get("median")),
            "l95": _to_float(b.get("l95")), "u95": _to_float(b.get("u95")),
            "predictor": b.get("predictor"), "demographic": b.get("demographic"),
            "reference": b.get("reference"), "units": b.get("units")}


def ber(aed_mgkgday, exposure_mgkgday):
    """Bioactivity-Exposure Ratio = AED_POD / predicted exposure. High = ample margin."""
    if not (aed_mgkgday and exposure_mgkgday):
        return None
    return round(aed_mgkgday / exposure_mgkgday, 2)
