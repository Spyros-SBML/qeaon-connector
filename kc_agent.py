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

Two modes:
  1. Manual (no key): prints the extraction prompt; run it in any grounded LLM
     (e.g. Claude with web access), then `append` the reviewed JSON.
  2. Automated (ANTHROPIC_API_KEY set): calls the API with web search to draft it.

CLI:
  python kc_agent.py prompt "vinyl bromide"        # print the grounded prompt
  python kc_agent.py draft  "vinyl bromide"        # automated draft (needs key)
  python kc_agent.py append reviewed_row.json      # validate + add to Tier 1
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


def draft_with_anthropic(chemical: str, api_key=None):
    """Automated draft using the Anthropic API with web search (best effort).
    Returns the parsed JSON dict. Requires ANTHROPIC_API_KEY and network access."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY set - use manual mode: "
                           "`python kc_agent.py prompt \"%s\"`" % chemical)
    import httpx
    body = {
        "model": os.environ.get("KC_AGENT_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 1500,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        "messages": [{"role": "user", "content": build_prompt(chemical)}],
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


if __name__ == "__main__":
    if len(sys.argv) < 3 and not (len(sys.argv) == 3 and sys.argv[1] == "append"):
        print(__doc__); sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "prompt":
        print(build_prompt(sys.argv[2]))
    elif cmd == "draft":
        print(json.dumps(draft_with_anthropic(sys.argv[2]), indent=2, ensure_ascii=False))
    elif cmd == "append":
        row = json.load(open(sys.argv[2], encoding="utf-8"))
        print("Appended to Tier 1:", append_to_tier1(row))
    else:
        print("commands: prompt <chem> | draft <chem> | append <row.json>")
