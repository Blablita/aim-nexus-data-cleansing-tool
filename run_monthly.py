#!/usr/bin/env python3
"""
Monthly run: new client files in -> one clean database out.

Folder layout (created automatically next to this script):

    inbox/2026-10/          <- put the month's raw files here (.csv, .xls, .xlsx)
    deliverables/2026-10/   <- clean_database_2026-10.xlsx + CSVs   (goes to the client)
    qa/2026-10/             <- quality report, manual review files, run log (internal)

Usage:
    python run_monthly.py                      # processes the latest month in inbox/
    python run_monthly.py --month 2026-10
    python run_monthly.py --month 2026-10 --config config_nobo.yaml
"""

import argparse
import glob
import os
import re
import sys
import traceback
from datetime import datetime

import pandas as pd

from cleaning.collate import cross_file
from cleaning.config import load_config
from cleaning.deliverable import build
from cleaning.registry import load_overrides, load_registry, normalize_id
from cleaning import verification
from cleaning.pipeline import clean_file
from cleaning.report import build_excel_report, _style

SUPPORTED = (".csv", ".txt", ".xls", ".xlsx", ".xlsm")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class _Tee:
    def __init__(self, console, logfile):
        self.console, self.logfile = console, logfile

    def write(self, text):
        self.console.write(text)
        self.logfile.write(text)

    def flush(self):
        self.console.flush()
        self.logfile.flush()


def _months(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(m for m in os.listdir(folder) if MONTH_RE.match(m) and os.path.isdir(os.path.join(folder, m)))


def _previous_investors(deliv_root, month):
    for m in reversed([m for m in _months(deliv_root) if m < month]):
        p = os.path.join(deliv_root, m, f"investors_{m}.csv")
        if os.path.isfile(p):
            prev = pd.read_csv(p, dtype={"investor_id": str, "CUSIP": str, "Job_number": str, "postal_code": str,
                                         "ZIP Code/Postal Code": str})
            prev["investor_id"] = prev["investor_id"].map(normalize_id)    # old files used INV-000123
            return m, prev
    return None, None


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Monthly run: inbox/<month> -> deliverables/<month>.")
    ap.add_argument("--month", help="YYYY-MM (default: latest folder in inbox/)")
    ap.add_argument("--config", default=os.path.join(here, "config_nobo.yaml"))
    ap.add_argument("--base", default=here, help="Folder that contains inbox/, deliverables/ and qa/")
    ap.add_argument("--quiet", action="store_true", help="Do not explain every step.")
    args = ap.parse_args()

    inbox_root = os.path.join(args.base, "inbox")
    deliv_root = os.path.join(args.base, "deliverables")
    qa_root = os.path.join(args.base, "qa")
    os.makedirs(inbox_root, exist_ok=True)

    month = args.month or (_months(inbox_root)[-1] if _months(inbox_root) else None)
    if not month or not MONTH_RE.match(month):
        print(f"No month folder found. Create a folder like inbox{os.sep}2026-10 and put the files in it.")
        sys.exit(1)
    files = sorted(f for f in glob.glob(os.path.join(inbox_root, month, "*")) if f.lower().endswith(SUPPORTED))
    if not files:
        print(f"No files in {os.path.join(inbox_root, month)}")
        sys.exit(1)

    deliv_dir = os.path.join(deliv_root, month)
    qa_dir = os.path.join(qa_root, month)
    os.makedirs(deliv_dir, exist_ok=True)
    os.makedirs(qa_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile = open(os.path.join(qa_dir, f"run_log_{stamp}.txt"), "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, logfile)

    config = load_config(args.config)
    print(f"Monthly run {month} | {len(files)} file(s) | config: {os.path.basename(args.config)}")

    results, summaries, logs = [], [], []
    extras = {"reconciliation": [], "investors": [], "collation_info": [], "eda": []}
    for path in files:
        print(f"\n=== {os.path.basename(path)}")
        try:
            r = clean_file(path, config, qa_dir, verbose=not args.quiet)
        except Exception as e:
            print(f"     ERROR: {e}")
            traceback.print_exc(file=sys.stdout)
            summaries.append({"file": os.path.basename(path), "error": str(e)})
            continue
        results.append(r)
        summaries.append(r["summary"])
        logs.extend(r["detail_log"])
        for k in ("reconciliation", "investors", "eda"):
            if k in r["extra"]:
                extras[k].append(r["extra"][k])
        if "collation_info" in r["extra"]:
            extras["collation_info"].append(r["extra"]["collation_info"])
        s = r["summary"]
        print(f"     {s['original_rows']:,} rows | manual review: {s['rows_for_manual_review']:,}"
              + (f" | control totals: {s['control_totals']}" if "control_totals" in s else ""))

    if not results:
        print("\nNo file could be processed. See the errors above.")
        sys.exit(1)

    # internal QA report
    extras["cross_file"] = cross_file(extras["investors"]) if extras["investors"] else None
    qa_report = os.path.join(qa_dir, f"quality_report_{month}.xlsx")
    build_excel_report(summaries, logs, qa_report, extras)

    # client deliverable
    prev_month, prev_inv = _previous_investors(deliv_root, month)

    # investor registry (permanent IDs). A snapshot is kept before each month so that
    # re-running the same month starts from the same registry and gives the same IDs.
    master = os.path.join(args.base, "master")
    snapshots = os.path.join(master, "snapshots")
    os.makedirs(snapshots, exist_ok=True)
    reg_path = os.path.join(master, "investor_registry.csv")
    snap_path = os.path.join(snapshots, f"investor_registry_before_{month}.csv")
    if os.path.isfile(snap_path):
        registry = load_registry(snap_path)
        print(f"\nRe-run of {month}: registry restored from the snapshot taken before this month.")
    else:
        registry = load_registry(reg_path)
        registry.to_csv(snap_path, index=False, encoding="utf-8-sig")
    later = [m for m in registry.get("last_seen_month", pd.Series(dtype=str)).unique() if isinstance(m, str) and m > month]
    if later:
        print(f"WARNING: the registry already has later months ({', '.join(sorted(later))}). Run months in order.")
    id_overrides = load_overrides(os.path.join(master, "id_overrides.csv"))

    # manual verification decisions: from master/ and from the delivered Excel file, in case the
    # person edited the Manual_Verification sheet there
    manual_path = os.path.join(master, "manual_verification.csv")
    # the delivered Excel is only re-read when the person saved it after the last run (it is slow to open)
    from_excel = []
    csv_time = os.path.getmtime(manual_path) if os.path.isfile(manual_path) else 0
    for f in sorted(glob.glob(os.path.join(deliv_root, "*", "clean_database_*.xlsx"))):
        if os.path.basename(f).startswith("~$"):
            continue
        if os.path.getmtime(f) > csv_time:
            print(f"Reading decisions back from {os.path.basename(f)} (edited after the last run)...")
            from_excel.append(verification.load_from_excel(f))
    manual_decisions = verification.merge_decisions(verification.load(manual_path), *from_excel)
    decided = manual_decisions["status"].astype(str).str.strip().str.lower().isin(["checked", "rejected"]).sum() \
        if len(manual_decisions) else 0
    if decided:
        print(f"Manual verification: {decided:,} decision(s) found.")

    sheets, notes, registry, review, manual_sheet = build(month, results, prev_inv, config.get("collation") or {},
                                                          (config.get("deliverable") or {}).get("issuer_overrides"),
                                                          registry, id_overrides, manual_decisions)
    manual_sheet.to_csv(manual_path, index=False, encoding="utf-8-sig")
    registry.to_csv(reg_path, index=False, encoding="utf-8-sig")
    review_path = os.path.join(qa_dir, f"id_review_{month}.csv")
    if len(review):
        review.to_csv(review_path, index=False, encoding="utf-8-sig")
        notes.append(f"{len(review):,} possible matches need a decision: {review_path} "
                     f"(to link them, add a line to master{os.sep}id_overrides.csv)")
    rec = pd.concat(extras["reconciliation"], ignore_index=True) if extras["reconciliation"] else None
    if rec is not None:
        status = rec.groupby("file")["result"].agg(
            lambda r: "OK" if (r == "OK").all() else ("NOT IN FILE" if (r == "NO CONTROL TOTALS").all() else "CHECK"))
        sheets["Summary"]["control_totals"] = sheets["Summary"]["source_files"].map(
            lambda fs: " | ".join(status.get(f, "-") for f in fs.split(" | ")))
    sheets["Summary"]["compared_with_month"] = prev_month or "-"

    def _write(path, writer):
        """Writes a file; if it is open in Excel (locked), writes a copy with a time stamp instead."""
        try:
            writer(path)
            return path
        except PermissionError:
            root, ext = os.path.splitext(path)
            alt = f"{root}_{stamp}{ext}"
            writer(alt)
            notes.append(f"{os.path.basename(path)} is open in another program (Excel?); saved as {os.path.basename(alt)}. "
                         f"Close the file and run again to replace it.")
            return alt

    def _write_xlsx(path):
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            for name, df in sheets.items():
                df.to_excel(w, sheet_name=name, index=False)
                _style(w.sheets[name])

    xlsx = _write(os.path.join(deliv_dir, f"clean_database_{month}.xlsx"), _write_xlsx)
    _write(os.path.join(deliv_dir, f"investors_{month}.csv"),
           lambda p: sheets["Investors"].to_csv(p, index=False, encoding="utf-8-sig"))
    _write(os.path.join(deliv_dir, f"original_database_{month}.csv"),
           lambda p: sheets["Original_Database"].to_csv(p, index=False, encoding="utf-8-sig"))

    print("\n" + "=" * 70)
    print(f"DELIVERABLE {month}")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        cols = [c for c in ["issuer_name", "accounts", "unique_investors", "new_investors", "exited_investors",
                            "control_totals"] if c in sheets["Summary"].columns]
        print(sheets["Summary"][cols].to_string(index=False))
    for n in notes:
        print(f"NOTE: {n}")
    print(f"\nClient file : {xlsx}")
    print(f"QA (internal): {qa_dir}")
    sys.stdout = sys.stdout.console
    logfile.close()


if __name__ == "__main__":
    main()
