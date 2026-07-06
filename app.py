"""q-eAON connector - FastAPI proxy (3-tier hybrid).

Tier 1  curated IARC key-characteristics table -> authoritative g AND p
        (deterministic; overrides lower tiers when the chemical is listed).
Tier 2  authoritative GeneTox (genotoxicity) -> g ; cancer class -> prior.
Tier 3  ToxCast/Tox21 bioactivity -> p (and g fallback).
Every value is tagged with its source + a confidence label.

Run:  uvicorn app:app --reload --port 8000
The EPA API key stays server-side (env CTX_API_KEY); never shipped to the browser.
"""
from __future__ import annotations
import os, pathlib
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
import httpx

_HERE = pathlib.Path(__file__).resolve().parent

from ctx_client import CTXClient
from aop_client import AOPClient
from kc_mapping import aggregate, KC
from hazard_map import genetox_to_g, classify_cancer, confidence
from tier1 import lookup as tier1_lookup, iris_pod
from integra_client import (IntegraClient, ENDPOINTS as INTEGRA_ENDPOINTS,
    dietary_intake_ugday, inhalation_intake_ugday, nondietary_intake_ugday, dose_mgkgday, sig)
from pydantic import BaseModel
from potency import channel_pods, pick_css, aed_from_pod, pick_seem, ber, _to_float as _tf

app = FastAPI(title="q-eAON connector", version="3.0")
_GP_CACHE = {}

origins = os.environ.get("ALLOW_ORIGINS", "*").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"ok": True,
            "has_key": bool(os.environ.get("CTX_API_KEY")),
            "has_integra_key": bool(os.environ.get("INTEGRA_API_KEY"))}


@app.get("/")
def index():
    """Serve the single-file decision-support app (same-origin front end)."""
    f = _HERE / "index.html"
    if f.exists():
        return FileResponse(str(f))
    return {"service": "q-eAON connector", "health": "/health", "app": "index.html not bundled"}


@app.get("/body_atlas.png")
def body_atlas():
    """Static anatomical atlas image for the target-organ body map (same-origin)."""
    f = _HERE / "body_atlas.png"
    if f.exists():
        return FileResponse(str(f), media_type="image/png")
    raise HTTPException(404, "body_atlas.png not bundled")


def _trim_structure(png):
    """Trim the black border frame that EPA/DSSTox structure PNGs carry (otherwise the
    app's red recolour turns the frame red too). Iteratively crops any uniform-colour
    border down to the molecule, then re-pads with a little white so lines aren't
    edge-to-edge. Falls back to the raw bytes if Pillow is unavailable."""
    try:
        import io
        from PIL import Image, ImageChops, ImageOps
        im = Image.open(io.BytesIO(png)).convert("RGB")
        for _ in range(3):
            bg = im.getpixel((0, 0))
            bbox = ImageChops.difference(im, Image.new("RGB", im.size, bg)).getbbox()
            if not bbox or bbox == (0, 0, im.width, im.height):
                break
            im = im.crop(bbox)
        im = ImageOps.expand(im, border=max(3, im.width // 18), fill=(255, 255, 255))
        out = io.BytesIO(); im.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return png


@app.get("/structure/{dtxsid}")
async def structure(dtxsid: str):
    """2D chemical-structure image (PNG) for the body-map nodes. Same-origin so the
    browser <img> avoids CORS and the EPA key stays server-side. Cached 1 day.
    The frame border is trimmed so the app's red recolour draws only the molecule."""
    png = await CTXClient().structure_png(dtxsid)
    if not png:
        raise HTTPException(404, "no structure image for " + dtxsid)
    return Response(content=_trim_structure(png), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


def _kc_array_from_weights(weights):
    out = []
    for i in range(len(KC)):
        w = weights[i] if i < len(weights) else 0
        out.append({"kc": i + 1, "label": KC[i][0], "weight": w,
                    "active_assays": 1 if w > 0 else 0,
                    "evidence": "strong" if w >= 1.0 else ("some" if w > 0 else "none")})
    return out


@app.get("/gp")
async def gp(query: str = Query(..., description="chemical name, CAS, or DTXSID")):
    if query in _GP_CACHE:
        return _GP_CACHE[query]
    client = CTXClient()
    if not client.key:
        raise HTTPException(500, "CTX_API_KEY not configured on the server")

    try:
        ident = await client.resolve(query)
    except httpx.HTTPStatusError:
        ident = None
    dtxsid = (ident or {}).get("dtxsid")
    name = (ident or {}).get("preferredName")
    casrn = (ident or {}).get("casrn")

    # ---------- Tier 1: curated IARC key characteristics ----------
    t1 = tier1_lookup(casrn=casrn, dtxsid=dtxsid, name=name) or tier1_lookup(name=query)
    if t1:
        cancer = {"iarc": None, "carcinogen_prior": False, "classifications": []}
        if dtxsid:
            try:
                cancer = classify_cancer(await client.cancer(dtxsid))
            except httpx.HTTPError:
                pass
        if not cancer.get("iarc") and t1.get("iarc_group"):
            cancer["iarc"] = f"Group {t1['iarc_group']} (IARC {t1.get('monograph','')})"
            cancer["carcinogen_prior"] = True
        # authoritative IRIS/OEHHA dose anchor + SEEM exposure -> BER
        ip = iris_pod(t1.get("iris"))
        seem = pick_seem(await client.seem_exposure(dtxsid)) if dtxsid else None
        exp = (seem or {}).get("exposure_mgkgday")
        aed = ip["aed_pod_mgkgday"] if ip else None
        if ip or seem:
            src = ("IRIS/OEHHA %s" % ip["basis"]) if ip else "no authoritative cancer potency"
            if ip and ip.get("risk_benchmark"):
                src += " (risk-specific dose at %g excess risk)" % ip["risk_benchmark"]
            if ip:
                src += " [%s]" % (ip.get("source") or "")
            t1_dose = {
                "pod_g_uM": None, "pod_p_uM": None, "pod_overall_uM": None,
                "n_g": 0, "n_p": 0, "n_pod": 0, "cytotox_uM": None, "cytotox_filtered": False,
                "aed_pod_g_mgkgday": aed, "aed_pod_p_mgkgday": aed, "aed_pod_overall_mgkgday": aed,
                "css": None, "mw_g_per_mol": None,
                "exposure": seem, "predicted_exposure_mgkgday": exp, "ber": ber(aed, exp),
                "source": src, "iris": t1.get("iris"),
                "note": ("Tier-1 dose anchor = IRIS/OEHHA cancer-potency risk-specific dose "
                         "(shared by both channels). BER = AED_POD / predicted exposure."),
            }
        else:
            t1_dose = None
        result = {
            "query": query, "dtxsid": dtxsid or t1.get("dtxsid"),
            "name": t1["name"], "casrn": casrn or t1.get("casrn"),
            "g": t1["g"], "p": t1["p"],
            "g_source": t1["source"], "p_source": t1["source"],
            "confidence": "high", "tier": "Tier 1 - IARC key characteristics (curated)",
            "mechanism": t1["mechanism"], "dominance": t1.get("dominance"),
            "kc_strong": t1["kc_strong"], "kc_some": t1["kc_some"],
            "genetox": None, "cancer": cancer, "n_active_assays": 0,
            "kc": _kc_array_from_weights(t1["kc"]), "evidence": [],
            "dose": t1_dose,
            "provenance": "Tier 1: curated IARC KC evaluation + IRIS/OEHHA dose anchor",
            "diagnostics": {},
        }
        _GP_CACHE[query] = result
        return result

    if not dtxsid:
        raise HTTPException(404, f"chemical not found: {query}")

    # ---------- Tier 3: ToxCast (leads p, fallback g) ----------
    try:
        hits = await client.bioactivity(dtxsid)
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"CTX bioactivity error: {e}")
    res = aggregate(hits)

    # ---------- Dose anchor: AC50 point-of-departure + httk Css (IVIVE) ----------
    summ = await client.bioactivity_summary(dtxsid)
    cytotox = _tf(summ.get("cytotoxMedianUm")) if summ else None
    pods = channel_pods(hits, cytotox_uM=cytotox)
    css = pick_css(await client.httk(dtxsid))
    mw = await client.mol_weight(dtxsid)
    css_val = (css or {}).get("css_mgL_per_mgkgday")
    seem = pick_seem(await client.seem_exposure(dtxsid))
    aed_g = aed_from_pod(pods["pod_g_uM"], css_val, mw)
    aed_p = aed_from_pod(pods["pod_p_uM"], css_val, mw)
    aed_o = aed_from_pod(pods["pod_overall_uM"], css_val, mw)
    exp = (seem or {}).get("exposure_mgkgday")
    dose = {**pods, "css": css, "mw_g_per_mol": mw,
            "aed_pod_g_mgkgday": aed_g, "aed_pod_p_mgkgday": aed_p,
            "aed_pod_overall_mgkgday": aed_o,
            "exposure": seem, "predicted_exposure_mgkgday": exp,
            "ber": ber(aed_o, exp),
            "source": "ToxCast AC50 point-of-departure + EPA httk Css (IVIVE); SEEM3 exposure",
            "note": ("intensity = internal dose / POD_uM (or external dose / AED_POD). "
                     "g channel linear/no-threshold; p channel saturating at the POD. "
                     "BER = AED_POD / predicted exposure (higher = ample margin).")}

    # ---------- Tier 2: authoritative GeneTox (leads g) + cancer prior ----------
    gt = await client.genetox(dtxsid)
    cz = await client.cancer(dtxsid)
    genetox = genetox_to_g(gt[0] if gt else None)
    cancer = classify_cancer(cz)

    if genetox is not None:
        g = genetox["g"]
        g_source = genetox["source"] + f" ({genetox['verdict']}; " \
                   f"{genetox['reportsPositive']}+/{genetox['reportsNegative']}-)"
        tier = "Tier 2/3 - GeneTox (g) + ToxCast (p)"
    else:
        g = res.g
        g_source = "ToxCast genotox assays (no authoritative GeneTox record)"
        tier = "Tier 3 - ToxCast only"
    p = res.p
    p_source = f"ToxCast bioactivity ({res.n_active} active assays)"
    conf = confidence(genetox is not None, res.n_active, cancer["carcinogen_prior"])

    kc = [{"kc": i + 1, "label": KC[i][0], "weight": res.kc_weight[i],
           "active_assays": res.kc_counts[i]} for i in range(len(KC))]
    if genetox is not None and genetox["verdict"] == "positive":
        kc[1]["weight"] = 1.0
        kc[1]["active_assays"] = max(kc[1]["active_assays"], genetox["reportsPositive"])

    result = {
        "query": query, "dtxsid": dtxsid, "name": name, "casrn": casrn,
        "g": g, "p": p, "g_source": g_source, "p_source": p_source,
        "confidence": conf, "tier": tier,
        "genetox": genetox,
        "cancer": {"iarc": cancer["iarc"], "carcinogen_prior": cancer["carcinogen_prior"],
                   "classifications": cancer["classifications"][:12]},
        "n_active_assays": res.n_active, "dose": dose,
        "kc": kc, "evidence": res.evidence[:50],
        "provenance": "EPA CTX: ToxCast/Tox21 (p) + GeneTox & cancer hazard (g, prior)",
        "diagnostics": client.diagnostics,
    }
    _GP_CACHE[query] = result
    return result


class IntegraReq(BaseModel):
    models: list = ["dietary"]          # any of dietary/inhalation/nondietary/multimedia
    bodyweight_kg: float = 70.0
    period_h: int = 24
    scenarios: dict = {}                # {model: {override fields}}


_AOP_CACHE = {}

@app.get("/aop")
async def aop(query: str = Query(..., description="chemical name, CAS, or DTXSID")):
    """Tier-0 AOP: resolve a chemical to AOP-Wiki adverse-outcome-pathway chain(s) and map
    the events onto the q-eAON canonical KE ontology. Chains + citations are pulled live from
    AOP-Wiki; the chemical->AOP pointer is an extensible seed (see aop_client.CAS_TO_AOPS)."""
    if query in _AOP_CACHE:
        return _AOP_CACHE[query]
    dtxsid = casrn = name = None
    try:
        chem = await CTXClient().resolve(query)
        dtxsid = chem.get("dtxsid"); casrn = chem.get("casrn"); name = chem.get("preferredName")
    except Exception:
        # allow a bare CAS to work without CTX (e.g. offline / no key)
        if "-" in query and query.replace("-", "").isdigit():
            casrn = query
    res = await AOPClient().resolve(casrn=casrn, dtxsid=dtxsid, name=name)
    res.update({"query": query, "name": name, "dtxsid": dtxsid, "casrn": casrn})
    _AOP_CACHE[query] = res
    return res


@app.post("/exposure/integra")
async def exposure_integra(req: IntegraReq):
    """Aggregate INTEGRA exposure -> mean daily dose (mg/kg/day) for a stressor.
    Sums dietary + inhalation(near-field) + non-dietary intakes; multimedia is
    returned as environmental context (it feeds the other routes, not summed)."""
    client = IntegraClient()
    if not client.key:
        raise HTTPException(500, "INTEGRA_API_KEY not configured on the server")
    per, diagnostics, multimedia, total = {}, {}, None, 0.0
    for m in req.models:
        if m not in INTEGRA_ENDPOINTS:
            continue
        try:
            resp = await client.run(m, req.scenarios.get(m, {}))
        except httpx.HTTPStatusError as e:
            diagnostics[m] = f"HTTP {e.response.status_code} (model unavailable/error)"
            continue
        except httpx.HTTPError as e:
            diagnostics[m] = f"unreachable: {e}"
            continue
        diagnostics[m] = sorted(resp.keys()) if isinstance(resp, dict) else str(type(resp))
        if m == "dietary":
            v = dietary_intake_ugday(resp); per["dietary"] = v; total += v
        elif m == "inhalation":
            v = inhalation_intake_ugday(resp, req.period_h); per["inhalation"] = v; total += v
        elif m == "nondietary":
            v = nondietary_intake_ugday(resp, req.period_h); per["nondietary"] = v; total += v
        elif m == "multimedia":
            multimedia = resp.get("environmental_concentrations") if isinstance(resp, dict) else resp
    dose = dose_mgkgday(total, req.bodyweight_kg)
    return {
        "models": req.models, "bodyweight_kg": req.bodyweight_kg,
        "intake_ugday_total": sig(total, 6), "dose_mgkgday": dose,
        "per_pathway_ugday": {k: sig(v, 6) for k, v in per.items()},
        "multimedia_concentrations": multimedia,
        "provenance": "INTEGRA (AUTH) aggregate exposure to mean daily dose",
        "diagnostics": diagnostics,
    }
