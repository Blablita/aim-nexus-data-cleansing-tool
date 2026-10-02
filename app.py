"""
AIM Nexus Data Cleansing Tool - local web app (Streamlit).

Upload the month's raw files, click Run, review the Summary and download the
clean database. The app calls run_monthly.py, so the cleaning logic lives in one place.

Start it with run_app.bat (double-click), or:
    python -m streamlit run app.py

Everything runs on this computer: files are saved to inbox/<month>/ and the results
go to deliverables/<month>/ and qa/<month>/, exactly like run_monthly.bat.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date, datetime

import pandas as pd
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
# TOOLKIT_BASE lets you point the app at a test copy of inbox/, deliverables/, qa/ and master/
BASE = os.environ.get("TOOLKIT_BASE", HERE)
INBOX = os.path.join(BASE, "inbox")
DELIV = os.path.join(BASE, "deliverables")
QA = os.path.join(BASE, "qa")
RUN_SCRIPT = os.path.join(HERE, "run_monthly.py")

SUPPORTED = ["csv", "txt", "xls", "xlsx", "xlsm"]
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ---------------------------------------------------------------- helpers

def months_in(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(m for m in os.listdir(folder)
                  if MONTH_RE.match(m) and os.path.isdir(os.path.join(folder, m)))


def files_in(folder, exts=None):
    if not os.path.isdir(folder):
        return []
    out = []
    for f in sorted(os.listdir(folder)):
        p = os.path.join(folder, f)
        if not os.path.isfile(p) or f.startswith("~$"):
            continue
        if exts and f.lower().rsplit(".", 1)[-1] not in exts:
            continue
        out.append(p)
    return out


def size_label(path):
    mb = os.path.getsize(path) / 1_048_576
    return f"{mb:.1f} MB" if mb >= 0.1 else f"{os.path.getsize(path) / 1024:.0f} KB"


def safe_name(name):
    """Keeps the original file name but removes characters Windows does not allow."""
    name = os.path.basename(name)
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "upload"


def move_old_inbox_files(month_dir):
    """Moves files already in inbox/<month>/ to a _replaced_<time> subfolder (nothing is deleted)."""
    old = files_in(month_dir)
    if not old:
        return None
    target = os.path.join(month_dir, f"_replaced_{datetime.now():%Y%m%d_%H%M%S}")
    os.makedirs(target, exist_ok=True)
    for p in old:
        shutil.move(p, os.path.join(target, os.path.basename(p)))
    return target


def latest_log(month):
    logs = sorted(f for f in files_in(os.path.join(QA, month), ["txt"])
                  if os.path.basename(f).startswith("run_log_"))
    return logs[-1] if logs else None


@st.cache_data(show_spinner=False)
def read_summary(xlsx_path, mtime):
    """Reads only the Summary sheet of the client file (mtime is part of the cache key)."""
    return pd.read_excel(xlsx_path, sheet_name="Summary", dtype=str).fillna("")


def control_totals_status(summary):
    """Returns 'OK', 'CHECK' or 'UNKNOWN' for the whole run."""
    if summary is None or "control_totals" not in summary.columns:
        return "UNKNOWN"
    values = " | ".join(summary["control_totals"].astype(str)).upper()
    parts = [v.strip() for v in values.split("|") if v.strip()]
    if parts and all(p == "OK" for p in parts):
        return "OK"
    return "CHECK"


def zip_folder(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, arcname=os.path.basename(p))
    return buf.getvalue()


def run_pipeline(month, config_path, log_box):
    """Runs run_monthly.py and streams its output into the page. Returns (exit_code, output)."""
    cmd = [sys.executable, "-u", RUN_SCRIPT, "--month", month, "--config", config_path,
           "--base", BASE, "--quiet"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    lines = []
    proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=env)
    for line in proc.stdout:
        lines.append(line.rstrip("\n"))
        log_box.code("\n".join(lines[-40:]), language=None)
    proc.wait()
    return proc.returncode, "\n".join(lines)


def check_password():
    """Shared-password gate for the public Streamlit Cloud URL.
    Only active when APP_PASSWORD is set in st.secrets (Streamlit Cloud > Settings > Secrets).
    With no secrets configured (local run via run_app.bat) the app skips the gate entirely,
    so local use needs no extra setup.
    """
    try:
        expected = st.secrets.get("APP_PASSWORD")
    except Exception:
        expected = None
    if not expected:
        return True

    if st.session_state.get("authed"):
        return True

    st.title("AIM Nexus Data Cleansing Tool")
    st.caption("This link is shared with the client, the professor and Kessia only. Ask Blablita for the password.")
    pwd = st.text_input("Password", type="password")
    if st.button("Enter"):
        if pwd == expected:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# ---------------------------------------------------------------- page

st.set_page_config(page_title="AIM Nexus Data Cleansing Tool", page_icon="🧹", layout="wide")

if not check_password():
    st.stop()

st.title("AIM Nexus Data Cleansing Tool")
st.caption("Upload the month's raw files, click **Run**, then download the clean database. "
           "Test version: files are not kept permanently between sessions yet.")

# sidebar: settings and history
with st.sidebar:
    st.header("Settings")
    month = st.text_input("Month (YYYY-MM)", value=date.today().strftime("%Y-%m"),
                          help="The record month of the files. Results go to deliverables/<month>/.").strip()
    configs = sorted(f for f in os.listdir(HERE) if f.lower().endswith((".yaml", ".yml")))
    default_cfg = configs.index("config_nobo.yaml") if "config_nobo.yaml" in configs else 0
    config_name = st.selectbox("Configuration", configs, index=default_cfg) if configs else None

    st.divider()
    st.header("Previous runs")
    done = months_in(DELIV)
    if done:
        for m in reversed(done):
            st.write(f"• {m}")
    else:
        st.write("No months delivered yet.")

month_ok = bool(MONTH_RE.match(month))
if not month_ok:
    st.error("Month must look like 2026-10.")
    st.stop()
if not config_name:
    st.error("No configuration file (.yaml) found next to app.py.")
    st.stop()

month_inbox = os.path.join(INBOX, month)
month_deliv = os.path.join(DELIV, month)
existing_inbox = files_in(month_inbox, SUPPORTED)
delivered = months_in(DELIV)
later_months = [m for m in delivered if m > month]

# ---------------------------------------------------------------- step 1: files

st.subheader("1. Files")
uploads = st.file_uploader("Raw files for this month (.csv, .txt, .xls, .xlsx, .xlsm)",
                           type=SUPPORTED, accept_multiple_files=True)

use_existing = False
if existing_inbox:
    names = ", ".join(os.path.basename(p) for p in existing_inbox)
    if uploads:
        st.info(f"inbox/{month}/ already has {len(existing_inbox)} file(s): {names}. "
                f"They will be moved to a `_replaced_...` subfolder and replaced by the new upload "
                f"(nothing is deleted).")
    else:
        use_existing = True
        st.info(f"No new upload. The run will use the {len(existing_inbox)} file(s) already in "
                f"inbox/{month}/: {names}")

if later_months:
    st.warning(f"Later months are already delivered ({', '.join(later_months)}). Investor IDs and the "
               f"month-over-month comparison assume months are run in order. Run {month} only if you "
               f"are re-running an old month on purpose.")
if month in delivered:
    st.warning(f"{month} was already delivered. Running again will replace the files in "
               f"deliverables/{month}/. If one of them is open in Excel, the new version is saved with a "
               f"time stamp instead.")

# ---------------------------------------------------------------- step 2: run

st.subheader("2. Run")
can_run = bool(uploads) or use_existing
if st.button("Run", type="primary", disabled=not can_run):
    os.makedirs(month_inbox, exist_ok=True)
    if uploads:
        moved = move_old_inbox_files(month_inbox)
        if moved:
            st.write(f"Previous files moved to `{os.path.relpath(moved, BASE)}`.")
        for up in uploads:
            with open(os.path.join(month_inbox, safe_name(up.name)), "wb") as f:
                f.write(up.getbuffer())

    with st.status(f"Processing {month}...", expanded=True) as status:
        log_box = st.empty()
        try:
            code, output = run_pipeline(month, os.path.join(HERE, config_name), log_box)
        except Exception as e:  # e.g. Python could not start the script
            code, output = 1, f"Could not start run_monthly.py: {e}"
            log_box.code(output, language=None)
        if code == 0:
            status.update(label=f"{month} processed.", state="complete", expanded=False)
        else:
            status.update(label=f"The run stopped with an error (exit code {code}). See the log below.",
                          state="error", expanded=True)
    st.session_state["last_run"] = {"month": month, "code": code, "output": output,
                                    "time": datetime.now().strftime("%Y-%m-%d %H:%M")}
elif not can_run:
    st.caption("Upload at least one file to enable Run.")

# ---------------------------------------------------------------- step 3: results

st.subheader("3. Results")
last = st.session_state.get("last_run")
xlsx = os.path.join(month_deliv, f"clean_database_{month}.xlsx")

if last and last["month"] == month and last["code"] != 0:
    st.error("The last run did not finish. Nothing new was delivered. Check the log, fix the file "
             "and run again.")
    with st.expander("Run log", expanded=True):
        st.code(last["output"] or "(empty)", language=None)

if not os.path.isfile(xlsx):
    st.write(f"No results for {month} yet.")
    st.stop()

summary = None
try:
    summary = read_summary(xlsx, os.path.getmtime(xlsx))
except Exception as e:
    st.warning(f"Could not read the Summary sheet: {e}")

ct = control_totals_status(summary)
if ct == "OK":
    st.success("Control totals: OK. The file is ready to deliver.")
elif ct == "CHECK":
    st.error("Control totals: CHECK. Do not deliver this file until the difference is explained. "
             "See the quality report in the QA section.")
else:
    st.warning("Control totals not found in the Summary sheet. Review the file before delivery.")

if summary is not None:
    st.dataframe(summary, width="stretch", hide_index=True)

if last and last["month"] == month:
    notes = [l[len("NOTE: "):] for l in last["output"].splitlines() if l.startswith("NOTE: ")]
    for n in notes:
        st.info(n)

st.markdown("**Client files**")
client_files = files_in(month_deliv)
cols = st.columns(min(len(client_files), 3) or 1)
for i, p in enumerate(client_files):
    name = os.path.basename(p)
    mime = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if name.endswith(".xlsx") else "text/csv")
    with cols[i % len(cols)]:
        with open(p, "rb") as f:
            st.download_button(f"{name} ({size_label(p)})", f.read(), file_name=name, mime=mime,
                               key=f"dl_{name}", width="stretch")
if len(client_files) > 1:
    st.download_button(f"All client files (.zip)", zip_folder(client_files),
                       file_name=f"clean_database_{month}.zip", mime="application/zip", key="dl_zip")

with st.expander("QA files (internal, do not send to the client)"):
    qa_files = files_in(os.path.join(QA, month))
    report = os.path.join(QA, month, f"quality_report_{month}.xlsx")
    if os.path.isfile(report):
        with open(report, "rb") as f:
            st.download_button(f"Quality report ({size_label(report)})", f.read(),
                               file_name=os.path.basename(report), key="dl_qa_report")
    others = [p for p in qa_files if p != report and not os.path.basename(p).startswith("run_log_")]
    if others:
        st.download_button(f"All QA files (.zip, {len(others) + (1 if os.path.isfile(report) else 0)} files)",
                           zip_folder(others + ([report] if os.path.isfile(report) else [])),
                           file_name=f"qa_{month}.zip", mime="application/zip", key="dl_qa_zip")
    log = latest_log(month)
    if log:
        st.markdown(f"Latest run log: `{os.path.basename(log)}`")
        with open(log, encoding="utf-8", errors="replace") as f:
            st.code(f.read(), language=None)
    st.caption(f"Folder: {os.path.join(QA, month)}")
