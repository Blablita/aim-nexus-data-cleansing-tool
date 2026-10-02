# Deploying the AIM Nexus Data Cleansing Tool to Streamlit Community Cloud

This turns the app from "only runs on Blablita's laptop" into a public URL the client,
the professor and Kessia can open from their own computers. It is a **pilot/demo
version**: `master/` (the investor registry that keeps IDs consistent month to month)
is not wired to persistent cloud storage yet -- that piece is being worked on
separately with Kessia (UC3, Azure backend). Each session on this cloud version
starts from an empty registry, so duplicate/ID matching across months will not be
accurate here yet. Good for showing and testing the upload -> clean -> download flow;
not for the real monthly production run until storage is resolved.

The repo has already been prepared locally: `git init`, a `.gitignore` that excludes
every folder with client/investor data (`inbox/`, `deliverables/`, `qa/`, `master/`,
`output_*/`, `reference/`), and an initial commit. Only code and config went in --
no client files.

## 1. Create the GitHub repository (your account)

1. Go to https://github.com/new (sign in or create a free account first if needed).
2. Repository name: e.g. `aim-nexus-data-cleansing-tool`.
3. Visibility: **Private** is fine -- Streamlit Community Cloud can deploy from
   private repos for free once your GitHub account is linked to it.
4. Do **not** initialize with a README/.gitignore (this repo already has them).
5. Click **Create repository** and copy the URL it shows you
   (`https://github.com/<your-username>/aim-nexus-data-cleansing-tool.git`).

## 2. Push the code (run in Anaconda Prompt / PowerShell, in this folder)

```
cd "C:\Users\Blablita\Desktop\MMA\DataSphere\Data cleaning\data_cleaning_toolkit"
git remote add origin https://github.com/<your-username>/aim-nexus-data-cleansing-tool.git
git branch -M main
git push -u origin main
```

The first push will open a browser window asking you to log in to GitHub -- that is
normal, just sign in there.

## 3. Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **New app**.
3. Pick the repository, branch `main`, and main file path `app.py`.
4. Before clicking Deploy, open **Advanced settings** and paste this under **Secrets**:

   ```
   APP_PASSWORD = "AIMNexus-UC1-Fall26"
   ```

   (Change the password to anything you like -- this is the only thing that gates
   access, since the URL itself is public. You can edit it later anytime from
   **App settings > Secrets** on Streamlit Cloud without touching the code or
   redeploying from GitHub.)
5. Click **Deploy**. The first build takes a few minutes (installing pandas,
   openpyxl, etc.).

## 4. Share it

Send the client, the professor and Kessia:
- The app URL Streamlit Cloud gives you (looks like
  `https://aim-nexus-data-cleansing-tool-xxxxx.streamlit.app`).
- The password you set in step 3.

## 5. Updating the app later

Any time you change the code locally:

```
git add -A
git commit -m "describe the change"
git push
```

Streamlit Cloud redeploys automatically within a minute or two of the push.

## Known limitation (by design, for now)

`master/investor_registry.csv` and the other files in `master/` do not persist
between restarts of the cloud app (Streamlit Community Cloud's disk is temporary).
Until the storage question is resolved with Kessia's Azure backend, treat this
deployment as a flow demo, not the system of record -- the real monthly run stays
on Blablita's computer (`run_app.bat`) until that is wired in.
