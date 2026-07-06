# q-eAON ToxCast connector (Tier-2 live backend)

A small FastAPI proxy that turns a chemical identifier into the q-eAON channel
weights **g** (genotoxic / variation) and **p** (promoter / selective value),
by pulling EPA **ToxCast/Tox21** bioactivity from the CTX API and mapping the
active assays onto the **IARC ten key characteristics of carcinogens**.

The EPA API key stays server-side and is never shipped to the browser; the
front-end app calls this proxy instead of EPA directly (avoids the API-key
exposure and CORS problems of a pure static page).

```
chemical name/CAS/DTXSID
        │  /gp?query=…
        ▼
  resolve (CTX chemical API) ──► bioactivity by DTXSID (CTX Bioactivity API)
        ▼
  assay → key-characteristic mapping (kc_mapping.py)
        ▼
  aggregate → { g, p, per-KC evidence }  ──►  q-eAON app
```

## 1. Get an EPA CTX API key
Request a free key from EPA: **ccte_api@epa.gov** (docs: https://comptox.epa.gov/ctx-api/docs/).

## 2. Run locally
```bash
cd qeaon-connector
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export CTX_API_KEY=your-epa-key                         # Windows: set CTX_API_KEY=...
uvicorn app:app --reload --port 8000
```
Check it: open http://localhost:8000/health  →  `{"ok":true,"has_key":true}`
Try it:   http://localhost:8000/gp?query=bisphenol%20A

## 3. Connect the app
Open `qeAON_DecisionSupport.html`, in **ToxCast auto-fill** set
**Backend = http://localhost:8000**, type a chemical, click **Load g/p**.
A stressor is added with g and p filled from ToxCast; set its exposure window and run.

## 4. Endpoints
- `GET /health` — readiness + whether a key is configured.
- `GET /gp?query=<name|CAS|DTXSID>` — returns `{dtxsid, name, g, p, n_active_assays, kc[], evidence[]}`.

## 5. The mapping is the science — edit it
`kc_mapping.py` holds the assay-annotation → key-characteristic rules and the
aggregation. The g/p scale and KC partition are identical to the app's manual
scorer, so an evidenced KC equals a ticked KC. **Curate and validate these
rules with a toxicologist before any real use.**

> **Note — ToxCast under-covers genotoxicity.** It is rich in receptor/oxidative/
> proliferation assays (→ p) but sparse on direct genotoxicity. For genotoxic
> agents, supplement **g** with a dedicated source (Ames, in vitro micronucleus,
> a mutagenicity QSAR such as VEGA), rather than trusting ToxCast alone.

## 6. Tests
```bash
pytest -q          # 9 tests, mocked CTX responses — no key needed
```

## 7. Deploy (so the GitHub Pages app can reach it over HTTPS)
Because the app is served over HTTPS, the backend must also be HTTPS (mixed
content is blocked). Containerise and deploy to any host:
```bash
docker build -t qeaon-connector .
docker run -e CTX_API_KEY=… -e ALLOW_ORIGINS=https://spyros-sbml.github.io -p 8000:8000 qeaon-connector
```
Or push to Render / Fly.io / Google Cloud Run / an institutional server. Set:
- `CTX_API_KEY` as a secret,
- `ALLOW_ORIGINS=https://spyros-sbml.github.io` (lock CORS to your app's origin).
Then in the app set Backend to your HTTPS URL.

## Caveats
- Pin the invitrodb release for reproducibility; respect EPA rate limits.
- Endpoint paths follow the documented CTX API; if EPA changes them, adjust
  `ctx_client.py` (verify against the live swagger at the docs URL above).
- Research prototype — not a validated regulatory instrument.
