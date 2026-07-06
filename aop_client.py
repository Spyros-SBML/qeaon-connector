"""Tier-0 AOP resolver: auto-pull adverse-outcome-pathway chains from AOP-Wiki by
chemical, and map their events onto the q-eAON canonical KE ontology.

Design
------
* The chemical -> AOP-ID pointer is a small, EXTENSIBLE seed map (CAS_TO_AOPS).
  AOP-Wiki stressor fields are frequently chemical *classes* or empty, so a reliable
  chemical->AOP index is not available live; seeding the pointer keeps this deterministic.
  Growing coverage = adding one CAS -> [aop_id] line (or later, an AOP-DB import).
* The CHAINS themselves are fetched LIVE from AOP-Wiki (aopwiki.org/aops/<id>.json),
  ordered by the key-event relationships, and mapped onto our shared KE vocabulary, so
  the mechanistic content, event IDs and citations are authoritative and current --
  never hand-typed. Events that don't match the canonical vocabulary are returned as
  novel nodes so the pathway is still shown in full.

This is Tier-0. Curated expert chains (Tier-1, AOP_CHAINS in the app) take precedence
where they exist (they can encode mixture modifiers / MIEs AOP-Wiki lacks); Tier-0 fills
the long tail of chemicals that have no curated chain.
"""
from __future__ import annotations
import re
import httpx

AOPWIKI = "https://aopwiki.org"

# --- chemical (CAS) -> AOP-Wiki AOP ids (extensible seed pointer) ---
CAS_TO_AOPS = {
    # AOP 220 - CYP2E1 activation -> liver cancer
    "67-66-3":   [220],   # chloroform            (CYP2E1 substrate class)
    "123-91-1":  [220],   # 1,4-dioxane           (CYP2E1 substrate class)
    "56-23-5":   [220],   # carbon tetrachloride   - canonical CYP2E1 hepatocarcinogen
    "62-75-9":   [220],   # N-nitrosodimethylamine - nitrosamine; CYP2E1-activated hepatocarcinogen
    "55-18-5":   [220],   # N-nitrosodiethylamine  - nitrosamine; CYP2E1-activated hepatocarcinogen
    # AOP 41 - sustained AhR activation -> rodent liver tumours
    "1746-01-6": [41],    # 2,3,7,8-TCDD
    # AOP 397 - bulky DNA adducts -> mutations
    "50-32-8":   [397],   # benzo[a]pyrene         (PAH class stressor)
    "1162-65-8": [397],   # aflatoxin B1           - listed stressor
    "313-67-7":  [397],   # aristolochic acid      - listed stressor (urothelial carcinogen)
    "53-96-3":   [397],   # 2-acetylaminofluorene  - classic bulky-adduct hepatocarcinogen
    "92-87-5":   [397],   # benzidine              - aromatic amine; arylamine-DNA adducts (bladder)
    "92-67-1":   [397],   # 4-aminobiphenyl        - aromatic amine (bladder carcinogen)
    "91-59-8":   [397],   # 2-naphthylamine        - aromatic amine (bladder carcinogen)
    "95-53-4":   [397],   # o-toluidine            - aromatic amine (bladder carcinogen)
    # AOP 296 - oxidative DNA damage -> mutations / chromosomal aberrations
    "7440-38-2": [296],   # inorganic arsenic      (oxidative-genotoxic proxy)
    "71-43-2":   [296],   # benzene                (hydroquinone-metabolite proxy)
    "123-31-9":  [296],   # hydroquinone           - listed stressor
    "7758-01-2": [296],   # potassium bromate      - listed stressor (renal carcinogen)
    "10108-64-2":[296],   # cadmium chloride       - listed stressor
    "7440-43-9": [296],   # cadmium                - IARC Grp 1; oxidative-stress genotoxicity
    "18540-29-9":[296],   # chromium(VI)           - IARC Grp 1; oxidative/genotoxic (lung)
    "56-57-5":   [296],   # 4-nitroquinoline 1-oxide - listed stressor
    "75-91-2":   [296],   # tert-butyl hydroperoxide - listed stressor
    "7440-02-0": [296],   # nickel                 - IARC Grp 1 (lung/nasal); oxidative/genotoxic
    "7440-48-4": [296],   # cobalt                 - IARC Grp 2B; oxidative/genotoxic
    # AOP 200 - estrogen-receptor activation -> breast cancer
    "80-05-7":   [200],   # bisphenol A
    "50-28-2":   [200],   # 17beta-estradiol       - prototypical ER agonist
    "56-53-1":   [200],   # diethylstilbestrol     - ER; transplacental carcinogen
    "57-63-6":   [200],   # ethinylestradiol       - potent ER agonist
    # AOP 167 - early-life ER agonism -> endometrial adenosquamous carcinoma
    "446-72-0":  [167],   # genistein              - listed stressor
    # AOP 165 - antiestrogen activity -> ovarian adenomas
    "10540-29-1":[165],   # tamoxifen              - listed stressor (SERM)
    # AOP 136 - intracellular acidification -> nasal (olfactory) tumours
    "108-05-4":  [136],   # vinyl acetate          - listed stressor (nasal carcinogen)
    # AOP 303 - frustrated phagocytosis -> lung cancer (high-aspect-ratio materials)
    "1332-21-4": [303],   # asbestos               - canonical HARN lung carcinogen
    # AOP 451 - particle interaction with lung cells -> lung cancer
    "13463-67-7":[451],   # titanium dioxide       - listed stressor (respirable particulate)
    "1333-86-4": [451],   # carbon black           - listed stressor (IARC 2B)
    # AOP 37 / 166 - PPARalpha activation -> hepatocellular (37) / pancreatic acinar (166) tumours
    "335-67-1":  [37, 166],  # perfluorooctanoic acid (PFOA) - listed stressor
    "1763-23-1": [37],       # perfluorooctane sulfonate (PFOS) - PPARalpha, same MoA
    # AOP 162 - hepatic thyroid-hormone clearance -> thyroid follicular tumours
    "52645-53-1":[162],   # permethrin             - pyrethroid class stressor
    # AOP 168 / 169 - GnRH pulse disruption -> mammary (168) / pituitary (169) adenomas
    "1912-24-9": [168, 169],  # atrazine            - listed stressor
    # AOP 202 - topoisomerase II inhibition -> infant leukaemia
    "2921-88-2": [202],   # chlorpyrifos           - listed stressor
}

# --- map an AOP-Wiki event title onto a canonical KE ontology id (order = priority) ---
# ids must match the app's AOP_EVENTS vocabulary so Tier-0 events merge with curated ones.
_KE_RULES = [
    (("cyp2e1", "cyp 2e1"),                                   "cyp"),
    (("aryl hydrocarbon", "ahr ", "ah receptor", "ahr activation"), "ahr"),
    (("estrogen receptor", "agonism, estrogen", "er activation"),   "er"),
    (("ppar", "peroxisome prolifer"),                        "ppar"),
    (("bulky dna adduct", "dna adduct"),                     "adduct"),
    (("zinc finger", "zinc-finger", "arsenical", "thiol"),   "asbind"),
    (("oxidative dna", "8-oxo", "oxidised dna", "oxidized dna"), "oxdna"),
    (("oxidative stress", "reactive oxygen", "redox", " ros"), "oxstress"),
    (("strand break",),                                      "strand"),
    (("dna repair", "repair capacity", "inadequate repair",
      "nucleotide excision", "base excision", "inadequate dna"), "repair"),
    (("mutation", "chromosomal aberration", "mutagen"),      "mut"),
    (("epigenetic", "methylation", "histone"),               "epigen"),
    (("er-dna", "er–dna", "binding to dna", "er binding"), "erdna"),
    (("hepatocytotox",),                                     "hepcyto"),
    (("hepatotox", "liver injury"),                          "heptox"),
    (("apopto",),                                            "apop"),
    (("inflammat", "cytotox", "tissue injury"),              "inflam"),
    (("proliferation", "hyperplasia", "regenerative"),       "prolif"),
    (("clonal expansion",),                                  "clonal"),
]
# canonical ids the app already defines (do not re-emit their labels)
_CANON = {"cyp","ahr","er","ppar","adduct","asbind","oxdna","oxstress","strand","repair",
          "mut","epigen","erdna","hepcyto","heptox","apop","inflam","prolif","clonal",
          "reactive","recept","dnadmg","signal"}

_ORG_RULES = [
    (("hepato", "liver"),                    "liver"),
    (("nasal", "nose", "nasopharyn"),        "naso"),
    (("lung", "bronch", "pulmon", "respir"), "lung"),
    (("bladder", "urothel"),                 "bladder"),
    (("skin", "dermal", "keratin"),          "skin"),
    (("breast", "mammary"),                  "repro"),
    (("prostate", "ovar", "uter", "endometr", "reproduc"), "repro"),
    (("leukaem", "leukem", "myeloid", "marrow", "haemato", "hemato"), "marrow"),
    (("kidney", "renal"),                    "kidney"),
    (("thyroid", "follicular cell"),         "thyroid"),
    (("pancrea",),                           "gi"),
    (("pituitar",),                          "brain"),
]

def _match(title, rules, default=None):
    t = (title or "").lower()
    for keys, val in rules:
        if any(k in t for k in keys):
            return val
    return default

def map_event(title):
    return _match(title, _KE_RULES, None)

def map_organ(title):
    return _match(title, _ORG_RULES, "systemic")

def _organ_or_none(title):
    return _match(title, _ORG_RULES, None)


def parse_aop(data):
    """Pure parser: AOP-Wiki JSON -> {aop, title, chain, events, organ, ref, url}.
    chain = ordered canonical/novel KE ids (MIE..last KE, then 'clonal'); organ from the AO."""
    ev = {}
    for e in data.get("aop_mies", []):
        ev[e["event_id"]] = {"id": e["event_id"], "n": (e.get("event") or "").strip(), "t": "mie"}
    for e in data.get("aop_kes", []):
        ev[e["event_id"]] = {"id": e["event_id"], "n": (e.get("event") or "").strip(), "t": "ke"}
    for e in data.get("aop_aos", []):
        ev[e["event_id"]] = {"id": e["event_id"], "n": (e.get("event") or "").strip(), "t": "ao"}
    if not ev:
        return None
    preds = {k: [] for k in ev}
    for r in data.get("relationships", []):
        u, d = r.get("upstream_event_id"), r.get("downstream_event_id")
        if u in ev and d in ev:
            preds[d].append(u)
    depth = {k: 0 for k in ev}
    for _ in range(len(ev) + 2):
        chg = False
        for k in ev:
            for p in preds[k]:
                if depth[p] + 1 > depth[k]:
                    depth[k] = depth[p] + 1; chg = True
        if not chg:
            break
    trank = {"mie": 0, "ke": 1, "ao": 2}
    order = sorted(ev.values(), key=lambda e: (depth[e["id"]], trank[e["t"]]))
    chain, events, organ, seen = [], {}, None, set()
    for e in order:
        if e["t"] == "ao":
            org = _organ_or_none(e["n"])
            if org:
                organ = organ or org
                continue
            # molecular AO (e.g. "Mutations", "Chromosomal aberrations") -> a terminal KE, not an organ
        cid = map_event(e["n"])
        if not cid:
            cid = "w%s" % e["id"]
            events[cid] = {"n": e["n"][:42], "t": 0 if e["t"] == "mie" else 1}
        if cid not in seen:
            chain.append(cid); seen.add(cid)
    if "clonal" not in seen and chain:
        chain.append("clonal")   # funnel through the shared tissue KE (q-eAON field phi)
    return {"aop": data.get("id"), "title": data.get("title") or data.get("short_name") or "",
            "chain": chain, "events": events, "organ": organ or "systemic",
            "ref": "AOP %s" % data.get("id"), "url": "%s/aops/%s" % (AOPWIKI, data.get("id"))}


class AOPClient:
    def __init__(self, timeout=30.0):
        self.timeout = timeout
        self._cache = {}

    async def fetch_aop(self, aop_id):
        if aop_id in self._cache:
            return self._cache[aop_id]
        url = "%s/aops/%s.json" % (AOPWIKI, aop_id)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as c:
            r = await c.get(url, headers={"accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        self._cache[aop_id] = data
        return data

    async def resolve(self, casrn=None, dtxsid=None, name=None):
        """Return a merged Tier-0 AOP descriptor for a chemical, or an empty/covered=False result."""
        cas = (casrn or "").strip()
        ids = CAS_TO_AOPS.get(cas, [])
        out = {"covered": False, "casrn": cas, "dtxsid": dtxsid, "source": "aop-wiki",
               "aops": [], "chain": [], "events": {}, "organ": None, "ref": None,
               "conf": 0.6, "urls": []}
        if not ids:
            out["message"] = "No seeded AOP-Wiki pointer for CAS %s (Tier-0 pointer is extensible)." % (cas or "?")
            return out
        merged_chain, merged_events, organ, refs, arm = [], {}, None, [], "gen"
        for aid in ids:
            try:
                data = await self.fetch_aop(aid)
            except Exception as e:
                out.setdefault("errors", []).append("AOP %s: %s" % (aid, e))
                continue
            p = parse_aop(data)
            if not p:
                continue
            out["aops"].append({"id": p["aop"], "title": p["title"], "url": p["url"],
                                "chain": p["chain"], "organ": p["organ"]})
            out["urls"].append(p["url"])
            refs.append(p["ref"])
            organ = organ or (p["organ"] if p["organ"] != "systemic" else None)
            for k in p["chain"]:
                if k not in merged_chain:
                    merged_chain.append(k)
            merged_events.update(p["events"])
        if "clonal" in merged_chain:  # keep the shared tissue KE terminal when merging multiple AOPs
            merged_chain = [k for k in merged_chain if k != "clonal"] + ["clonal"]
        if merged_chain:
            # arm heuristic: genotoxic backbone vs receptor/non-genotoxic
            gen_ids = {"adduct", "oxdna", "strand", "repair", "mut"}
            arm = "gen" if any(k in gen_ids for k in merged_chain) else "prom"
            out.update({"covered": True, "chain": merged_chain, "events": merged_events,
                        "organ": organ, "ref": " + ".join(refs), "arm": arm})
        return out
