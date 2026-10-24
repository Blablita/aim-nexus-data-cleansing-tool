#!/usr/bin/env python3
"""
One run = one dataset in -> one clean database out. Each run is identified by its run date
(YYYY-MM-DD), so several datasets can be processed in the same run_id (target: one per week).

Folder layout (created automatically next to this script):

    inbox/2026-10-16/          <- put the dataset's raw files here (.csv, .xls, .xlsx)
    deliverables/2026-10-16/   <- clean_database_2026-10-16.xlsx + CSVs   (goes to the client)
    qa/2026-10-16/             <- quality report, manual review files, run log (internal)

Each issuer is compared with ITS previous list (the latest earlier run that has that issuer).
Older runs named YYYY-MM (before Oct 2026) are still read.

Usage:
    python run_cleaning.py                         # processes the latest run folder in inbox/
    python run_cleaning.py --run-date 2026-10-16
    python run_cleaning.py --run-date 2026-10-16 --config config_nobo.yaml
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
from cleaning.registry import load_overrides, load_registry, map_id, migrate_ids, normalize_id
from cleaning import verification
from cleaning.pipeline import clean_file
from cleaning.report import build_excel_report, _style

SUPPORTED = (".csv", ".txt", ".xls", ".xlsx", ".xlsm")
RUN_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")     # YYYY-MM-DD (YYYY-MM = runs before Oct 2026)


class _Tee:
    def __init__(self, console, logfile):
        self.console, self.logfile = console, logfile

    def write(self, text):
        self.console.write(text)
        self.logfile.write(text)

    def flush(self):
        self.console.flush()
        self.logfile.flush()


def _runs(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(m for m in os.listdir(folder) if RUN_RE.match(m) and os.path.isdir(os.path.join(folder, m)))


def _previous_lists(deliv_root, run):
    """For every issuer, its Investors rows from the latest earlier run that has that issuer.
    Returns a DataFrame with a `previous_run` column (or None)."""
    frames, seen = [], set()
    for m in reversed([m for m in _runs(deliv_root) if m < run]):
        p = os.path.join(deliv_root, m, f"investors_{m}.csv")
        if not os.path.isfile(p):
            continue
        prev = pd.read_csv(p, dtype={"investor_id": str, "CUSIP": str, "Job_number": str, "postal_code": str,
                                     "ZIP Code/Postal Code": str, "record_date": str}, encoding="utf-8-sig")
        prev = prev[~prev["issuer_name"].isin(seen)].copy()
        if not len(prev):
            continue
        seen |= set(prev["issuer_name"])
        prev["investor_id"] = prev["investor_id"].map(normalize_id)    # old files used INV-000123
        prev["previous_run"] = m
        frames.append(prev)
    return pd.concat(frames, ignore_index=True) if frames else None


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="One run: inbox/<run date> -> deliverables/<run date>.")
    ap.add_argument("--run-date", "--run_id", dest="run", help="YYYY-MM-DD (default: latest folder in inbox/)")
    ap.add_argument("--config", default=os.path.join(here, "config_nobo.yaml"))
    ap.add_argument("--base", default=here, help="Folder that contains inbox/, deliverables/ and qa/")
    ap.add_argument("--quiet", action="store_true", help="Do not explain every step.")
    args = ap.parse_args()

    inbox_root = os.path.join(args.base, "inbox")
    deliv_root = os.path.join(args.base, "deliverables")
    qa_root = os.path.join(args.base, "qa")
    os.makedirs(inbox_root, exist_ok=True)

    run_id = args.run or (_runs(inbox_root)[-1] if _runs(inbox_root) else None)
    if not run_id or not RUN_RE.match(run_id):
        print(f"No run folder found. Create a folder like inbox{os.sep}2026-10-16 (the run date) and put the files in it.")
        sys.exit(1)
    files = sorted(f for f in glob.glob(os.path.join(inbox_root, run_id, "*")) if f.lower().endswith(SUPPORTED))
    if not files:
        print(f"No files in {os.path.join(inbox_root, run_id)}")
        sys.exit(1)

    deliv_dir = os.path.join(deliv_root, run_id)
    qa_dir = os.path.join(qa_root, run_id)
    os.makedirs(deliv_dir, exist_ok=True)
    os.makedirs(qa_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile = open(os.path.join(qa_dir, f"run_log_{stamp}.txt"), "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, logfile)

    config = load_config(args.config)
    print(f"Run {run_id} | {len(files)} file(s) | config: {os.path.basename(args.config)}")

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
    qa_report = os.path.join(qa_dir, f"quality_report_{run_id}.xlsx")
    build_excel_report(summaries, logs, qa_report, extras)

    # client deliverable
    prev_inv = _previous_lists(deliv_root, run_id)

    # investor registry (permanent IDs). A snapshot is kept before each run so that
    # re-running the same run starts from the same registry and gives the same IDs.
    master = os.path.join(args.base, "master")
    snapshots = os.path.join(master, "snapshots")
    os.makedirs(snapshots, exist_ok=True)
    reg_path = os.path.join(master, "investor_registry.csv")
    snap_path = os.path.join(snapshots, f"investor_registry_before_{run_id}.csv")
    if os.path.isfile(snap_path):
        registry = load_registry(snap_path)
        print(f"\nRe-run of {run_id}: registry restored from the snapshot taken before this run.")
    else:
        registry = load_registry(reg_path)
        registry.to_csv(snap_path, index=False, encoding="utf-8-sig")
    later = [m for m in registry.get("last_seen_run", pd.Series(dtype=str)).unique() if isinstance(m, str) and m > run_id]
    if later:
        print(f"WARNING: the registry already has later runs ({', '.join(sorted(later))}). Run datasets in date order.")
    id_overrides = load_overrides(os.path.join(master, "id_overrides.csv"))

    # 9-digit investor IDs (client 2026-10-15): old IDs (1, 2, 3...) are converted once; the pairs are kept in
    # master/id_migration.csv so the registry, the overrides and earlier files all use the same new ID
    registry, id_map, n_migrated = migrate_ids(registry, os.path.join(master, "id_migration.csv"), run_id)
    if n_migrated:
        print(f"Investor IDs: {n_migrated:,} old IDs converted to 9-digit IDs (see master{os.sep}id_migration.csv).")
    id_overrides["investor_id"] = id_overrides["investor_id"].map(lambda v: map_id(v, id_map))
    if prev_inv is not None:
        prev_inv["investor_id"] = prev_inv["investor_id"].map(lambda v: map_id(v, id_map))

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

    sheets, notes, registry, review, manual_sheet = build(run_id, results, prev_inv, config.get("collation") or {},
                                                          (config.get("deliverable") or {}).get("issuer_overrides"),
                                                          registry, id_overrides, manual_decisions)
    manual_sheet.to_csv(manual_path, index=False, encoding="utf-8-sig")
    registry.to_csv(reg_path, index=False, encoding="utf-8-sig")
    review_path = os.path.join(qa_dir, f"id_review_{run_id}.csv")
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
        from openpyxl.styles import Font, PatternFill
        from cleaning.deliverable import MAIN_COLUMNS
        main_fill, other_fill = PatternFill("solid", fgColor="31869B"), PatternFill("solid", fgColor="BFBFBF")
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            for name, df in sheets.items():
                df.to_excel(w, sheet_name=name, index=False)
                _style(w.sheets[name])
                if name in ("Investors", "Original_Database", "Identical_Rows", "Exited_Investors"):
                    # main columns for the reviewer in blue, internal columns (rules, QA, future use) in grey
                    for cell in w.sheets[name][1]:
                        main = cell.value in MAIN_COLUMNS
                        cell.fill = main_fill if main else other_fill
                        cell.font = Font(bold=True, color="FFFFFF" if main else "000000")

    xlsx = _write(os.path.join(deliv_dir, f"clean_database_{run_id}.xlsx"), _write_xlsx)
    _write(os.path.join(deliv_dir, f"investors_{run_id}.csv"),
           lambda p: sheets["Investors"].to_csv(p, index=False, encoding="utf-8-sig"))
    _write(os.path.join(deliv_dir, f"original_database_{run_id}.csv"),
           lambda p: sheets["Original_Database"].to_csv(p, index=False, encoding="utf-8-sig"))

    print("\n" + "=" * 70)
    print(f"DELIVERABLE {run_id}")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        cols = [c for c in ["issuer_name", "accounts", "unique_investors", "compared_with_run", "new_investors", "exited_investors",
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
