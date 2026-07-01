"""Map EPA CTX Hazard data (authoritative) onto the q-eAON g channel and a
cancer-classification prior. Pairs with kc_mapping.py (ToxCast -> p).

Tiered logic: authoritative genetic-toxicity (Ames/micronucleus, EPA GeneTox)
LEADS the initiator weight g, because in-vitro HTS misses metabolic activation
(the benzene problem). ToxCast leads p. Cancer classifications (IARC/NTP/EPA)
frame both as a prior + confidence label.
"""
from __future__ import annotations
import re

# IARC group severity ranking (higher = stronger carcinogen evidence)
IARC_RANK = {"1": 5, "2a": 4, "2b": 3, "3": 1, "4": 0}


def genetox_to_g(summary: dict | None):
    """Authoritative initiator weight g in [0,1] from the GeneTox summary record.
    Returns None when the call is equivocal/absent so the caller can fall back
    to ToxCast genotox assays."""
    if not summary:
        return None
    call = (summary.get("genetoxCall") or "").strip().lower()
    ames = (summary.get("ames") or "").strip().lower()
    micro = (summary.get("micronucleus") or "").strip().lower()
    pos = int(summary.get("reportsPositive") or 0)
    neg = int(summary.get("reportsNegative") or 0)
    tot = pos + neg
    posfrac = pos / tot if tot else 0.0
    if "positiv" in call:
        base = 1.0 if ("positiv" in ames and "positiv" in micro) else 0.85
        g = min(1.0, base + 0.15 * posfrac)
        verdict = "positive"
    elif "negativ" in call:
        g = 0.20 * posfrac          # authoritatively non-genotoxic -> near 0
        verdict = "negative"
    else:
        return None                 # equivocal / not reported -> fall back
    return {
        "g": round(g, 3),
        "verdict": verdict,
        "ames": ames or None,
        "micronucleus": micro or None,
        "reportsPositive": pos,
        "reportsNegative": neg,
        "source": "EPA GeneTox authoritative summary",
    }


def classify_cancer(records: list | None):
    """Summarise cancer classifications across authoritative sources."""
    records = records or []
    calls = []
    iarc_group = None
    iarc_rank = -1
    prior = False
    for r in records:
        src = (r.get("source") or "").strip()
        call = (r.get("cancerCall") or "").strip()
        if not call:
            continue
        calls.append({"source": src, "call": call,
                      "route": r.get("exposureRoute"), "url": r.get("url")})
        cl = call.lower()
        m = re.search(r"group\s*(1|2a|2b|3|4)\b", cl)
        if m and src.upper() == "IARC":
            rank = IARC_RANK.get(m.group(1), -1)
            if rank > iarc_rank:
                iarc_rank, iarc_group = rank, call
        neg_guard = ("not classifiable", "not carcinogenic", "no evidence",
                     "non-carcinogen", "not likely", "evidence of non",
                     "inadequate", "group 3", "group 4")
        pos_terms = ("group 1", "group 2a", "group 2b", "known",
                     "likely to be carcinogenic", "reasonably anticipated",
                     "carcinogenic to humans", "occupational carcinogen",
                     "probable", "possible")
        if not any(n in cl for n in neg_guard) and any(t in cl for t in pos_terms):
            prior = True
    return {"classifications": calls, "iarc": iarc_group, "carcinogen_prior": prior}


def confidence(g_auth_present: bool, toxcast_active: int, carcinogen_prior: bool):
    if g_auth_present and toxcast_active >= 5:
        return "high"
    if g_auth_present or toxcast_active >= 5:
        return "medium"
    return "low"
