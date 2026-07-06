# Put q-eAON online (one service = app + connector together)

**What you get:** one public web address. Open it and the full decision-support app
loads in the browser, and its "Load g/p" and "Compute exposure" buttons work for
anyone, with your EPA and INTEGRA keys kept safely on the server (never in the page).

Everything is already wired for this: the connector now **serves the app itself** at `/`,
so there is only ONE thing to deploy and **no CORS to configure**. Host = **Render**
(free tier). Every step below is web-UI clicking — no command line.

Time: about 10 minutes. You will do three things: (1) put the folder on GitHub,
(2) create the Render service and paste your two keys, (3) open the URL.

---

## Before you start — have these two keys ready (copy them somewhere)

- **EPA CTX key** (`CTX_API_KEY`) — the one you already use locally.
- **INTEGRA key** (`INTEGRA_API_KEY`) — your AUTH INTEGRA X-API-Key.

You will paste them into Render's secret boxes. They are never written into any file.

---

## Step 1 — Put the connector folder on GitHub

1. Go to **https://github.com/new** and sign in (make a free account if you don't have one).
2. Repository name: **`qeaon-connector`**. Leave it **Public** (the code has no secrets).
   Click **Create repository**.
3. On the next page click the link **"uploading an existing file"**.
4. Open your `qeaon-connector` folder on your computer and **drag these in**:
   `app.py`, `index.html`, `ctx_client.py`, `kc_mapping.py`, `hazard_map.py`,
   `tier1.py`, `integra_client.py`, `potency.py`, `kc_agent.py`,
   `requirements.txt`, `Dockerfile`, `render.yaml`, `.gitignore`, `.dockerignore`,
   and the whole **`data`** folder.
   - **Do NOT drag in:** `.venv`, `__pycache__`, `.pytest_cache`, `.env` (there is no
     `.env`, only `.env.example`, which is fine to include), or the `probe_*` / `run_*_api.py`
     files (harmless, just clutter — skip them).
   - The two that MATTER most: **`app.py`**, **`index.html`**, and the **`data`** folder.
5. Click **Commit changes**.

> Safety check: your keys are NOT in any of these files. They only ever live in Render's
> secret boxes (Step 2) and on your own machine. Keep it that way — never commit a key.

## Step 2 — Create the Render service and paste your keys

1. Go to **https://render.com** and click **Get Started** → **Sign in with GitHub** (free).
2. Click **New +** (top right) → **Blueprint**.
3. Pick your **`qeaon-connector`** repository from the list and click **Connect**.
   Render reads `render.yaml` and shows one service called **qeaon-connector**.
4. It will ask you to fill in the two secret values (they show as blank because they are
   marked secret):
   - **`CTX_API_KEY`** → paste your EPA key.
   - **`INTEGRA_API_KEY`** → paste your INTEGRA key.
   (`INTEGRA_BASE_URL` and `ALLOW_ORIGINS` are already filled in — leave them.)
5. Click **Apply** / **Create**. Render builds the image and deploys — about **3–5 minutes**.
6. When it finishes, Render shows a public URL near the top, like
   **`https://qeaon-connector.onrender.com`** (your exact name may differ).

## Step 3 — Open it

- **The app:** open the URL itself → the full q-eAON app loads. Leave the **Backend** box
  in the "Hazard auto-fill" card **blank** — because the app is served by the connector,
  blank means "use this same server," and everything just works.
- **Quick health check:** open `https://YOUR-URL/health` → you should see
  `{"ok": true, "has_key": true, "has_integra_key": true}`.
  Both `true` means both keys were picked up.

That's it — it's online. Share the URL with anyone.

---

## Good to know

- **Free tier sleeps.** After ~15 min with no visitors the service spins down. The next
  visit wakes it (~30–60 s), and the first "Load g/p" also downloads the EPA assay catalog
  once (another minute), then it's fast until it idles again. Upgrading to Render's $7/mo
  instance keeps it always awake.
- **Your keys, your usage.** Public requests use your EPA and INTEGRA keys server-side.
  The EPA key is free and read-only. To lock down who may call it, change `ALLOW_ORIGINS`
  in `render.yaml` from `"*"` to your own address (a browser-level guard; the hazard data
  itself is public).
- **Changing a key later:** edit the value in the Render dashboard → **Environment** tab.
  No code change, no re-upload.
- **Updating the app or connector later:** re-upload the changed file to the GitHub repo
  ("Add file → Upload files" → commit). Render redeploys automatically within a minute.
- **HTTPS is automatic** on Render, so the ToxCast/INTEGRA calls are secure end-to-end.
