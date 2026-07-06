"""Async client for EPA's CTX API (base confirmed: https://comptox.epa.gov/ctx-api).

Endpoints (authenticated with x-api-key header):
  GET /chemical/search/equal/{value}               -> resolve name/CAS/DTXSID
  GET /bioactivity/data/search/by-dtxsid/{dtxsid}   -> per-assay results (detail)
  GET /bioactivity/assay/                           -> assay annotations (cached)

Field access is alias-based so it tolerates EPA's exact key spellings.
Request a free key from EPA (ccte_api@epa.gov) and set CTX_API_KEY.
"""
from __future__ import annotations
import os, httpx
from kc_mapping import AssayHit

CTX_BASE = os.environ.get("CTX_BASE", "https://comptox.epa.gov/ctx-api")
_ASSAY_CATALOG = None   # downloaded once, shared across requests


def _first(d, *names, default=None):
    """Return the first present (case-insensitive) key value from aliases."""
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] not in (None, ""):
            return low[n.lower()]
    return default


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ac50_of(row):
    """AC50 (uM) lives inside the mc5Param model-fit dict, not at top level."""
    mp = row.get("mc5Param")
    if isinstance(mp, dict):
        v = _to_float(mp.get("ac50"))
        if v is not None:
            return v
        return _to_float(mp.get("acc"))
    return _to_float(_first(row, "ac50", "acc"))


class CTXClient:
    def __init__(self, api_key=None, base=None, timeout=180.0):
        self.key = api_key or os.environ.get("CTX_API_KEY", "")
        self.base = (base or CTX_BASE).rstrip("/")
        self.timeout = timeout
        self.diagnostics = {}
        self._assay_url = "?"

    def _h(self):
        return {"x-api-key": self.key, "accept": "application/json"}

    async def resolve(self, query: str):
        q = query.strip()
        if q.upper().startswith("DTXSID"):
            return {"dtxsid": q, "preferredName": q, "casrn": None}
        url = f"{self.base}/chemical/search/equal/{httpx.URL(q)}"
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(url, headers=self._h()); r.raise_for_status()
            data = r.json()
        if not data:
            return None
        hit = data[0] if isinstance(data, list) else data
        return {"dtxsid": _first(hit, "dtxsid"),
                "preferredName": _first(hit, "preferredName", "searchValue", default=q),
                "casrn": _first(hit, "casrn")}

    async def structure_png(self, dtxsid: str):
        """Return a 2D chemical-structure PNG (bytes) for a DTXSID, or None.
        Tries the authenticated CTX Chemical API image endpoint first, then the
        public DSSTox dashboard image (no key) as a fallback so structures still
        render if the CTX key is unset."""
        dtxsid = (dtxsid or "").strip()
        if not dtxsid.upper().startswith("DTXSID"):
            return None
        attempts = [
            (f"{self.base}/chemical/file/image/search/by-dtxsid/{dtxsid}",
             {"x-api-key": self.key, "accept": "image/png"}),
            (f"https://comptox.epa.gov/dashboard-api/ccdapp1/chemical-files/image/by-dtxsid/{dtxsid}",
             {"accept": "image/png"}),
        ]
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            for url, headers in attempts:
                try:
                    r = await c.get(url, headers=headers)
                    if r.status_code == 200 and r.content and \
                       r.headers.get("content-type", "").startswith("image"):
                        return r.content
                except httpx.HTTPError:
                    continue
        return None

    async def bioactivity(self, dtxsid: str):
        """Per-assay hit-calls (detail endpoint) joined with assay target/gene
        annotations (assay catalog, cached). Only aeid + hitc are kept from the
        large detail payload; targets come from the catalog."""
        url = f"{self.base}/bioactivity/data/search/by-dtxsid/{dtxsid}"
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(url, headers=self._h()); r.raise_for_status()
            rows = r.json() or []
            ann = await self._assays(c)          # aeid -> annotation (cached)
        if rows:
            self.diagnostics = {
                "n_records": len(rows),
                "detail_keys": sorted(rows[0].keys()),
                "catalog_keys": (sorted(next(iter(ann.values())).keys()) if ann else "none"),
                "n_catalog": len(ann),
                "catalog_url": self._assay_url,
            }
        hits = []
        for row in rows:
            aeid = _first(row, "aeid", "assayEndpointId")
            a = ann.get(aeid, {})
            hits.append(AssayHit(
                aeid=aeid,
                endpoint=_first(a, "assayComponentEndpointName", "aenm", "assayName", default="") or "",
                family=_first(a, "intendedTargetFamily", "intendedTargetFamilySub",
                              "assayDesignType", "biologicalProcessTarget", default="") or "",
                gene=_first(a, "gene", "geneSymbol", "intendedTargetGeneSymbol", default="") or "",
                bio_process=_first(a, "biologicalProcessTarget", "intendedTargetType", default="") or "",
                hitc=_to_float(_first(row, "hitc", "hitcall", "actp")),
                ac50=_ac50_of(row),
            ))
        return hits

    async def _assays(self, client):
        """Download the full assay-endpoint catalog once (aeid -> annotation).
        The CTX route needs a trailing slash, so try the known variants and keep
        the first that returns a non-empty list; record the winner in diagnostics."""
        global _ASSAY_CATALOG
        if _ASSAY_CATALOG is not None:
            return _ASSAY_CATALOG
        candidates = [
            f"{self.base}/bioactivity/assay/",
            f"{self.base}/bioactivity/assay",
            f"{self.base}/bioactivity/assay/search/all",
        ]
        last_status = None
        for url in candidates:
            try:
                r = await client.get(url, headers=self._h())
            except httpx.HTTPError:
                continue
            last_status = r.status_code
            if r.status_code == 200:
                data = r.json() or []
                if isinstance(data, dict):
                    data = data.get("data", data.get("content", [])) or []
                if data:
                    _ASSAY_CATALOG = {_first(a, "aeid", "assayEndpointId"): a for a in data}
                    self._assay_url = url
                    return _ASSAY_CATALOG
        self._assay_url = f"FAILED (last status {last_status})"
        _ASSAY_CATALOG = {}
        return _ASSAY_CATALOG

    async def genetox(self, dtxsid: str):
        """Authoritative genetic-toxicity summary (EPA GeneTox).
        One summary record per chemical; returns a list (possibly empty)."""
        url = f"{self.base}/hazard/genetox/summary/search/by-dtxsid/{dtxsid}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(url, headers=self._h())
                if r.status_code != 200:
                    return []
                data = r.json() or []
                return data if isinstance(data, list) else [data]
        except httpx.HTTPError:
            return []

    async def cancer(self, dtxsid: str):
        """Authoritative cancer classifications (IARC / NTP / EPA via ToxValDB).
        One record per source; returns a list (possibly empty)."""
        url = f"{self.base}/hazard/cancer-summary/search/by-dtxsid/{dtxsid}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(url, headers=self._h())
                if r.status_code != 200:
                    return []
                data = r.json() or []
                return data if isinstance(data, list) else [data]
        except httpx.HTTPError:
            return []

    async def httk(self, dtxsid: str):
        """High-throughput toxicokinetics records (Css etc.) from CTX Exposure.
        Returns a list (empty if the chemical is not in the httk set)."""
        url = f"{self.base}/exposure/httk/search/by-dtxsid/{dtxsid}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(url, headers=self._h())
                if r.status_code != 200:
                    return []
                data = r.json() or []
                return data if isinstance(data, list) else [data]
        except httpx.HTTPError:
            return []

    async def mol_weight(self, dtxsid: str):
        """Average molecular weight (g/mol) for uM<->mg/L conversion. Best effort."""
        for path in (f"/chemical/detail/search/by-dtxsid/{dtxsid}",
                     f"/chemical/search/equal/{dtxsid}"):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as c:
                    r = await c.get(f"{self.base}{path}", headers=self._h())
                    if r.status_code != 200:
                        continue
                    d = r.json()
                    rec = d[0] if isinstance(d, list) and d else d
                    if isinstance(rec, dict):
                        mw = _to_float(_first(rec, "averageMass", "monoisotopicMass",
                                              "molWeight", "mw"))
                        if mw:
                            return mw
            except httpx.HTTPError:
                continue
        return None

    async def bioactivity_summary(self, dtxsid: str):
        """Per-chemical bioactivity summary (nhit/ntested + cytotoxMedianUm burst
        boundary). Returns a dict (empty on failure)."""
        url = f"{self.base}/bioactivity/data/summary/search/by-dtxsid/{dtxsid}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(url, headers=self._h())
                if r.status_code != 200:
                    return {}
                d = r.json()
                return (d[0] if isinstance(d, list) and d else d) or {}
        except httpx.HTTPError:
            return {}

    async def seem_exposure(self, dtxsid: str):
        """SEEM3 predicted human exposure (mg/kg/day), demographic-resolved.
        Returns a list (empty on failure)."""
        url = f"{self.base}/exposure/seem/demographic/search/by-dtxsid/{dtxsid}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(url, headers=self._h())
                if r.status_code != 200:
                    return []
                data = r.json() or []
                return data if isinstance(data, list) else [data]
        except httpx.HTTPError:
            return []
