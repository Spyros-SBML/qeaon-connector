"""Tier-2: grounded key-characteristics extraction agent.

For a chemical NOT in the Tier-1 curated table, this agent drafts a KC profile
from authoritative sources (IARC / EPA IRIS / NTP), CITING each assignment, and
flags it for expert review. The reviewed row is then appended to the Tier-1
table (data/iarc_kc.json) so it becomes a deterministic, citable entry.

Design rules (non-negotiable, for regulatory defensibility):
  * Every KC weight must be backed by a quoted/cited source statement.
  * Output "insufficient evidence" (weight 0) rather than guessing.
  * Result is a DRAFT requiring human confirmation before it joins Tier 1.
  * The LLM never overrides an existing Tier-1 row.

The SAME grounded/cited/review-gated pattern also extracts CANCER POTENCY
(oral slope factor / inhalation unit risk / RfD) and writes it to a chemical's
`iris` block, which the connector's tier1.iris_pod() turns into the POD (the 1e-4
risk-specific dose) that anchors the app's dose -> intensity conversion. This is
how PODs are populated at scale for chemicals whose potency is not in EPA IRIS
(e.g. aflatoxin, whose value comes from OEHHA/JECFA): the connector already
auto-derives PODs for IRIS/CTX-covered chemicals; this agent fills the gaps, cited.

Two modes (both KC and potency):
  1. Manual (no key): prints the grounded prompt; run it in any grounded LLM
     (e.g. Claude with web access), then commit the reviewed JSON.
  2. Automated (ANTHROPIC_API_KEY set): calls the API with web search to draft it.

CLI:
  # key characteristics -> g/p
  python kc_agent.py prompt  "vinyl bromide"          # print the grounded KC prompt
  python kc_agent.py draft   "vinyl bromide"          # automated KC draft (needs key)
  python kc_agent.py append  reviewed_kc.json         # validate + add KC row to Tier 1
  # cancer potency -> POD
  python kc_agent.py potency-prompt "aflatoxin b1"    # print the grounded potency prompt
  python kc_agent.py potency-draft  "aflatoxin b1"    # automated potency draft (needs key)
  python kc_agent.py set-potency reviewed_potency.json # validate + write iris block (=> POD)
"""
from __future__ import annotations
import json, os, sys

KC_DEFS = [
    "1 Is electrophilic or can be metabolically activated to electrophiles",
    "2 Is genotoxic",
    "3 Alters DNA repair or causes genomic instability",
    "4 Induces epigenetic alterations",
    "5 Induces oxidative stress",
    "6 Induces chronic inflammation",
    "7 Is immunosuppressive",
    "8 Modulates receptor-mediated effects",
    "9 Causes immortalisation",
    "10 Alters cell proliferation, cell death or nutrient supply",
]

PROMPT_TEMPLATE = """You are a carcinogenicity hazard analyst. Assess the chemical below against the
IARC ten key characteristics of carcinogens (Smith et al. 2016). Use ONLY
authoritative sources you can cite: IARC Monographs (mechanistic / key-characteristics
sections), EPA IRIS, US NTP Report on Carcinogens, ECHA. Do NOT use your own
recollection as evidence; if you cannot find a citable source for a characteristic,
score it 0.

Chemical: {chemical}

The ten key characteristics:
{kc_list}

For EACH characteristic assign a weight:
  1.0 = STRONG evidence explicitly stated by an authoritative source
  0.5 = SOME / limited evidence
  0   = none, or no citable source found
For every NON-ZERO weight, give a one-line citation (source + which finding).

Return ONLY a JSON object with this exact schema (no prose outside it):
{{
  "name": "<preferred name>",
  "casrn": "<CAS number or null>",
  "iarc_group": "<1 | 2A | 2B | 3 | null>",
  "monograph": "<IARC volume / source + year>",
  "aliases": ["<lowercase synonyms>"],
  "kc": [w1,w2,w3,w4,w5,w6,w7,w8,w9,w10],
  "kc_citations": {{"2":"<source for KC2>", "8":"<source for KC8>"}},
  "mechanism": "<one-line dominant mechanism>",
  "review_status": "DRAFT - expert confirmation required"
}}
Be conservative: only 1.0 where a source says the evidence is strong."""


def build_prompt(chemical: str) -> str:
    return PROMPT_TEMPLATE.format(chemical=chemical, kc_list="\n".join(KC_DEFS))


def validate_row(row: dict):
    """Raise ValueError if the drafted row is not safe to append to Tier 1."""
    if not row.get("name"):
        raise ValueError("missing 'name'")
    kc = row.get("kc")
    if not isinstance(kc, list) or len(kc) != 10:
        raise ValueError("'kc' must be a list of 10 weights")
    for w in kc:
        if not isinstance(w, (int, float)) or not (0 <= w <= 1):
            raise ValueError(f"kc weight out of range: {w}")
    if not row.get("casrn") and not row.get("dtxsid"):
        raise ValueError("need at least a CAS or DTXSID to match the chemical")
    # every non-zero KC should carry a citation
    cites = {str(k): v for k, v in (row.get("kc_citations") or {}).items()}
    for i, w in enumerate(kc, start=1):
        if w > 0 and not cites.get(str(i)):
            raise ValueError(f"KC{i} has weight {w} but no citation")
    return True


def append_to_tier1(row: dict, data_path=None):
    """Validate a reviewed row and append it to the Tier-1 table (dedupe by CAS)."""
    data_path = data_path or os.path.join(os.path.dirname(__file__), "data", "iarc_kc.json")
    validate_row(row)
    db = json.load(open(data_path, encoding="utf-8"))
    entry = {
        "name": row["name"], "casrn": row.get("casrn"), "dtxsid": row.get("dtxsid"),
        "iarc_group": row.get("iarc_group"), "monograph": row.get("monograph"),
        "aliases": [a.lower() for a in row.get("aliases", [])],
        "kc": [float(w) for w in row["kc"]],
        "mechanism": row.get("mechanism", ""),
        "kc_citations": row.get("kc_citations", {}),
        "review_status": row.get("review_status", "appended via kc_agent"),
    }
    cas = (entry["casrn"] or "").strip()
    db["entries"] = [e for e in db["entries"] if (e.get("casrn") or "").strip() != cas or not cas]
    db["entries"].append(entry)
    json.dump(db, open(data_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    return entry["name"]


# ======================================================================
# Tier-2 CANCER-POTENCY extractor (feeds the POD table)
# ----------------------------------------------------------------------
# Same grounded, cited, review-gated pattern as the KC extractor, but it
# fills the `iris` block {osf, iur, rfd, rfc} of a chemical's Tier-1 entry.
# The connector's tier1.iris_pod() then turns that into the POD (the 1e-4
# risk-specific dose) that anchors the app's dose -> intensity conversion.
# ======================================================================

POTENCY_PROMPT_TEMPLATE = """You are a cancer dose-response analyst. Extract the CANCER POTENCY parameters
for the chemical below, to anchor its point-of-departure (the 1e-4 risk-specific dose).
Use ONLY authoritative sources you can cite, in this order of preference:
  1. US EPA IRIS            (oral slope factor OSF, inhalation unit risk IUR, RfD, RfC)
  2. CalEPA / OEHHA         (cancer potency factors)
  3. US ATSDR               (cancer potency / MRLs)
  4. WHO JECFA / IARC        (quantitative potency)
  5. Peer-reviewed derivation (ONLY if none of the above give a value)
Do NOT use your own recollection as a value. Every number MUST carry a citation:
source name + the exact value with its units + a URL or DOI. If no citable value
exists for a parameter, return null for it.

Report in these units (convert if the source differs, and state the conversion in the citation):
  osf = oral slope factor,    (mg/kg-day)^-1
  iur = inhalation unit risk, (ug/m3)^-1
  rfd = reference dose,        mg/kg-day     (threshold surrogate; last resort)
  rfc = reference concentration, mg/m3
Flag any route- or host-status dependence (e.g. HBV+ vs HBV- for aflatoxin) in "notes".

Chemical: {chemical}

Return ONLY a JSON object with this exact schema (no prose outside it):
{{
  "name": "<preferred name>",
  "casrn": "<CAS number or null>",
  "iris": {{ "osf": <num|null>, "iur": <num|null>, "rfd": <num|null>, "rfc": <num|null>,
             "source": "<primary source + year>", "moa": "<linear|mutagenic|threshold>" }},
  "iris_citations": {{ "osf": "<source, value, URL/DOI>", "iur": "...", "rfd": "...", "rfc": "..." }},
  "notes": "<route/host-status dependence or caveats, or ''>",
  "review_status": "DRAFT - expert confirmation required"
}}
Prefer OSF, then IUR, then RfD. Be conservative and cite every non-null value."""

# loose sanity bounds so a mis-typed exponent is caught (spans TCDD OSF 1.3e5 .. benzene IUR 7.8e-6)
_POT_RANGE = {"osf": (1e-4, 1e7), "iur": (1e-9, 1e3), "rfd": (1e-9, 10.0), "rfc": (1e-6, 100.0)}
_INH_FACTOR = 20.0 / 70.0 / 1000.0     # ug/m3 -> mg/kg/day (matches tier1.iris_pod)


def build_potency_prompt(chemical: str) -> str:
    return POTENCY_PROMPT_TEMPLATE.format(chemical=chemical)


def compute_pod(iris: dict, risk: float = 1e-4):
    """Mirror tier1.iris_pod: 1e-4 risk-specific dose (mg/kg/day). OSF > IUR > RfD."""
    if not iris:
        return None, None
    osf, iur, rfd = iris.get("osf"), iris.get("iur"), iris.get("rfd")
    if osf:
        return risk / osf, "oral slope factor"
    if iur:
        return (risk / iur) * _INH_FACTOR, "inhalation unit risk"
    if rfd:
        return rfd, "RfD (threshold surrogate)"
    return None, None


def validate_potency(rec: dict):
    """Raise ValueError unless the drafted potency record is safe to commit."""
    if not rec.get("name"):
        raise ValueError("missing 'name'")
    if not rec.get("casrn") and not rec.get("dtxsid"):
        raise ValueError("need a CAS or DTXSID to match the chemical")
    iris = rec.get("iris") or {}
    cites = rec.get("iris_citations") or {}
    if not any(iris.get(k) for k in ("osf", "iur", "rfd")):
        raise ValueError("no potency value (need at least one of osf / iur / rfd)")
    for k, (lo, hi) in _POT_RANGE.items():
        v = iris.get(k)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or v <= 0:
            raise ValueError(f"{k}={v!r} must be a positive number or null")
        if not (lo <= v <= hi):
            raise ValueError(f"{k}={v} outside plausible range {lo}..{hi} (check units/exponent)")
        if not cites.get(k):
            raise ValueError(f"{k}={v} has no citation in iris_citations")
    return True


def set_potency_in_tier1(rec: dict, data_path=None):
    """Validate a reviewed potency record and write its `iris` block onto the matching
    Tier-1 entry (by CAS, else name/alias). Returns (name, POD, basis).
    The entry must already exist (its KC row supplies g/p); add the KC row first if not."""
    data_path = data_path or os.path.join(os.path.dirname(__file__), "data", "iarc_kc.json")
    validate_potency(rec)
    db = json.load(open(data_path, encoding="utf-8"))
    cas = (rec.get("casrn") or "").strip()
    nm = (rec.get("name") or "").strip().lower()
    target = None
    for e in db["entries"]:
        if cas and (e.get("casrn") or "").strip() == cas:
            target = e; break
        names = [e.get("name", "")] + e.get("aliases", [])
        if nm and nm in [n.strip().lower() for n in names]:
            target = e; break
    if target is None:
        raise ValueError(
            f"'{rec.get('name')}' is not in Tier-1 yet. Add its KC row first "
            f"(`python kc_agent.py append <kc_row.json>`), then set its potency.")
    iris = {k: rec["iris"].get(k) for k in ("osf", "iur", "rfd", "rfc")}
    iris["source"] = rec["iris"].get("source", "")
    iris["moa"] = rec["iris"].get("moa", "")
    target["iris"] = iris
    target["iris_citations"] = rec.get("iris_citations", {})
    if rec.get("notes"):
        target["iris_notes"] = rec["notes"]
    target["iris_review"] = rec.get("review_status", "set via kc_agent potency extractor")
    json.dump(db, open(data_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    pod, basis = compute_pod(iris)
    return target["name"], pod, basis


def draft_potency_with_anthropic(chemical: str, api_key=None):
    """Automated potency draft via the Anthropic API with web search (best effort)."""
    return _draft_with_prompt(build_potency_prompt(chemical), chemical, api_key)


def _draft_with_prompt(prompt: str, chemical: str, api_key=None):
    """Shared Anthropic-API call with web search; extracts the JSON object from the reply.
    Requires ANTHROPIC_API_KEY and network access."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY set - use manual mode "
                           "(`prompt` / `potency-prompt`) for %r" % chemical)
    import httpx
    body = {
        "model": os.environ.get("KC_AGENT_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 1500,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    r = httpx.post("https://api.anthropic.com/v1/messages", json=body,
                   headers=headers, timeout=120.0)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError("no JSON object found in model output:\n" + text[:500])
    return json.loads(text[s:e + 1])


def draft_with_anthropic(chemical: str, api_key=None):
    """Automated KC draft via the Anthropic API with web search (best effort)."""
    return _draft_with_prompt(build_prompt(chemical), chemical, api_key)


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__); sys.exit(0)
    cmd, arg = args[0], args[1]
    if cmd == "prompt":
        print(build_prompt(arg))
    elif cmd == "draft":
        print(json.dumps(draft_with_anthropic(arg), indent=2, ensure_ascii=False))
    elif cmd == "append":
        print("Appended KC row to Tier 1:", append_to_tier1(json.load(open(arg, encoding="utf-8"))))
    elif cmd == "potency-prompt":
        print(build_potency_prompt(arg))
    elif cmd == "potency-draft":
        print(json.dumps(draft_potency_with_anthropic(arg), indent=2, ensure_ascii=False))
    elif cmd == "set-potency":
        nm, pod, basis = set_potency_in_tier1(json.load(open(arg, encoding="utf-8")))
        print(f"Set potency for {nm}: POD = {pod:.4g} mg/kg/day ({basis})" if pod
              else f"Set potency for {nm} (no numeric POD - only RfC?)")
    else:
        print("commands:\n"
              "  KC:      prompt <chem> | draft <chem> | append <kc_row.json>\n"
              "  potency: potency-prompt <chem> | potency-draft <chem> | set-potency <potency_row.json>")
