# Putting the new version online - the easy way

You already did the hard part once: the GitHub repository, the Render service and
your two keys are all set up. Updating to the new version is just **replacing the
files** and letting Render rebuild. About 5 minutes of clicking, no command line.

## The one thing to understand
- Your two keys - EPA `CTX_API_KEY` and INTEGRA `INTEGRA_API_KEY` - live *inside
  Render*, not in the files.
- When you upload new files, Render notices and rebuilds by itself, **reusing the
  same keys**.
- So: new code = upload files. Keys = leave them completely alone.

---

## Step 1 - Replace the files on GitHub
1. Open your repository:
   **https://github.com/Spyros-SBML/qeaon-decision-support**
2. Click **Add file** -> **Upload files**.
3. On your computer, open the new bundle folder
   (`qeaon-decision-support_release`). Select everything inside it and **drag it
   into the GitHub page**. Drag the **`data`** folder in as well.
   - When GitHub says these files already exist, that is fine - you are updating
     them.
   - The ones that matter most: **index.html**, **app.py**, **aop_client.py**,
     and the **data** folder.
4. Scroll down, type a short note like "update to AON/AOP version", and click
   **Commit changes**.

## Step 2 - Let Render redeploy (nothing to type)
- Render is watching the repository. The moment you commit, it starts rebuilding
  - about 3 to 5 minutes.
- To watch it: **https://dashboard.render.com** -> click your service -> it shows
  "Deploying..." and then "Live".
- **Do not re-enter your keys.** They are still there from last time.

## Step 3 - Check it worked
- Open **https://YOUR-URL/health** (your service's address followed by `/health`).
  You should see:
  `{"ok": true, "has_key": true, "has_integra_key": true}`
  Both `true` means both keys are still connected.
- Open the main URL -> the app loads with the new network (AON / AOP) panels and
  the Save/Load runs box.

## Step 4 - Refresh the Zenodo archive (so the paper's DOI matches this code)
Your manuscript cites a Zenodo DOI. Publish a new version so that DOI points at
the code you just uploaded:
1. Go to **https://zenodo.org** and sign in.
2. Open your existing q-eAON record (Zenodo -> your uploads -> the q-eAON one).
3. Click **New version**.
4. Remove the old zip and **upload the new
   `qeaon-decision-support_release.zip`**.
5. Click **Publish**.
   - The **concept DOI** already in your paper stays the same and now resolves to
     this new version - you do not need to edit the manuscript.
   - Zenodo also mints a fresh version-specific DOI, in case you ever want to cite
     exactly this build.

---

## If something looks off
- **Health shows `has_key: false`** - a key box got emptied. In Render: your
  service -> **Environment** -> check `CTX_API_KEY` and `INTEGRA_API_KEY` are
  still filled; re-paste if blank, then **Save** (it redeploys).
- **App loads but "Load g/p" fails the first time** - the free service just woke
  from sleep. Wait a minute and try again.
- **Wrong repository name?** If your Render service is actually connected to a
  differently named repository, just update whichever repository Render is
  watching - the files are identical either way. (For consistency, the name used
  in the paper is `qeaon-decision-support`.)

That's the whole thing: upload files -> wait -> check `/health` -> make a Zenodo
version. Keys are never touched.
