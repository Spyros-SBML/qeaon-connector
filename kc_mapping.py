"""Map EPA ToxCast/Tox21 (CTX Bioactivity) assay results onto the IARC ten
key characteristics of carcinogens, then aggregate to the q-eAON channel
weights g (genotoxic / variation) and p (promoter / selective value).

The g/p scale and the KC partition match the front-end app exactly, so values
from this connector are interchangeable with manual KC scoring.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Iterable

# ---- KC partition (identical to the app) ----
# index -> (label, g_weight, p_weight)
KC = [
    ("1. Electrophilic / metabolically activated", 1.0, 0.0),
    ("2. Genotoxic",                               1.0, 0.0),
    ("3. Alters DNA repair / genomic instability", 1.0, 0.0),
    ("4. Induces epigenetic alterations",          0.5, 0.5),
    ("5. Induces oxidative stress",                0.5, 0.5),
    ("6. Induces chronic inflammation",            0.0, 1.0),
    ("7. Is immunosuppressive",                    0.0, 1.0),
    ("8. Modulates receptor-mediated effects",     0.0, 1.0),
    ("9. Causes immortalisation",                  0.0, 1.0),
    ("10. Alters proliferation / death / nutrient",0.0, 1.0),
]
KC_DIV = 3.0          # ~3 evidenced characteristics in a channel saturate it
KC_HIT_SAT = 1.0      # >=1 active assay evidences a KC (same footing as a manual tick)

HIT_THRESHOLD = 0.5   # EPA invitrodb v4 continuous hitcall: active if >= 0.5

# ---- assay-annotation keyword rules -> KC index (0-based) ----
RULES = [
    # KC2 genotoxicity & DNA damage
    (r"\bgenotox|dna damage|dna_damage|p53|tp53|gadd45|h2ax|atad5|micronucleus|comet\b", 1),
    # KC3 DNA repair / genomic instability
    (r"\bdna repair|genomic instability|mismatch repair|rad54|brca|atm\b", 2),
    # KC1 electrophilic / metabolically activated TO electrophiles.
    # Reserved for DIRECT reactivity / bioactivation-to-electrophile evidence.
    # CYP induction/expression or CYP enzyme inhibition is deliberately NOT mapped
    # here: CYP transcription is a downstream reporter of AhR/CAR/PXR activation
    # (already captured by the KC8 receptor rule) and does not show the chemical
    # itself is electrophilic. Mapping CYP to KC1 spuriously inflated g.
    (r"reactive metabolite|electrophil|dna adduct|glutathione depletion|nitroreduct", 0),
    # KC5 oxidative stress (Nrf2/ARE)
    (r"\boxidative stress|nrf2|nfe2l2|\bare\b|hmox1|antioxidant response\b", 4),
    # KC6 inflammation (NFkB, cytokines)
    (r"\binflammat|nf-?kb|nfkb|rela|cytokine|il-?6|\bil6\b|\btnf\b|cox-?2|ptgs2|il1a|cxcl", 5),
    # KC7 immunosuppression
    (r"\bimmunosuppress|immune|t-?cell|nfat\b", 6),
    # KC8 receptor-mediated (nuclear receptors, AhR, GPCR)
    (r"\bnuclear receptor|steroid hormone|androgen|estrogen\b|\bar\b|\ber\b|esr1|esr2|"
     r"ahr|aryl hydrocarbon|\bpxr\b|nr1i2|\bcar\b|nr1i3|ppar|gpcr|thyroid receptor|"
     r"glucocorticoid|retinoic", 7),
    # KC9 immortalisation (telomerase)
    (r"\btelomeras|tert\b|immortal", 8),
    # KC10 proliferation / death / nutrient
    (r"\bcell cycle|proliferat|apoptos|cell death|growth factor|igf1|mitochondri|"
     r"cell viability|cytotox\b", 9),
]
_COMPILED = [(re.compile(rx), kc) for rx, kc in RULES]


@dataclass
class AssayHit:
    aeid: object
    endpoint: str
    family: str = ""
    gene: str = ""
    bio_process: str = ""
    hitc: float = 1.0
    ac50: float | None = None


def assay_to_kcs(a: AssayHit) -> list:
    hay = " ".join(str(x).lower() for x in (a.family, a.gene, a.bio_process, a.endpoint))
    return sorted({kc for rx, kc in _COMPILED if rx.search(hay)})


@dataclass
class GPResult:
    g: float
    p: float
    kc_weight: list = field(default_factory=lambda: [0.0] * len(KC))
    kc_counts: list = field(default_factory=lambda: [0] * len(KC))
    evidence: list = field(default_factory=list)
    n_active: int = 0


def aggregate(hits: Iterable) -> GPResult:
    # Collapse replicate sample records to one per assay endpoint (aeid): the CTX
    # detail endpoint returns one row per tested sample (spid), so one assay can
    # appear many times; an endpoint is active if ANY replicate is a hit.
    by_aeid = {}
    for a in hits:
        key = a.aeid if a.aeid is not None else id(a)
        cur = by_aeid.get(key)
        if cur is None or (a.hitc or 0.0) > (cur.hitc or 0.0):
            by_aeid[key] = a
    hits = list(by_aeid.values())

    counts = [0] * len(KC)
    evidence = []
    n_active = 0
    for a in hits:
        if a.hitc is None or a.hitc < HIT_THRESHOLD:
            continue
        n_active += 1
        kcs = assay_to_kcs(a)
        for kc in kcs:
            counts[kc] += 1
        if kcs:
            evidence.append({"aeid": a.aeid, "endpoint": a.endpoint,
                             "family": a.family, "gene": a.gene,
                             "ac50": a.ac50, "kcs": [kc + 1 for kc in kcs]})
    kc_w = [min(1.0, c / KC_HIT_SAT) for c in counts]
    gs = sum(kc_w[i] * KC[i][1] for i in range(len(KC)))
    ps = sum(kc_w[i] * KC[i][2] for i in range(len(KC)))
    g = round(min(1.0, gs / KC_DIV), 3)
    p = round(min(1.0, ps / KC_DIV), 3)
    return GPResult(g=g, p=p, kc_weight=[round(w, 3) for w in kc_w],
                    kc_counts=counts, evidence=evidence, n_active=n_active)
