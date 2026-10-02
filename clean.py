#!/usr/bin/env python3
"""
Command line tool for the data cleaning toolkit.

Usage:
    python clean.py --input "sample_data/*.csv" --config config_example.yaml --output output_example
    python clean.py --input "../*.csv" "../*.xls" --config config_nobo.yaml --output output_nobo --verbose

--verbose  explains every step with before/after examples (to audit and debug).
Every run also saves the full console output to <output>/run_log_<timestamp>.txt
"""

import argparse
import glob
import os
import sys
import traceback
from datetime import datetime

from cleaning.collate import cross_file
from cleaning.config import load_config
from cleaning.pipeline import clean_file
from cleaning.report import build_excel_report

SUPPORTED = (".csv", ".txt", ".xls", ".xlsx", ".xlsm")


class _Tee:
    """Writes to the console and to a log file at the same time."""

    def __init__(self, console, logfile):
        self.console, self.logfile = console, logfile

    def write(self, text):
        self.console.write(text)
        self.logfile.write(text)

    def flush(self):
        self.console.flush()
        self.logfile.flush()


def resolve_input_files(patterns):
    files = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern)) or ([pattern] if os.path.isfile(pattern) else [])
        files.extend(m for m in matches if m.lower().endswith(SUPPORTED))
    return list(dict.fromkeys(files))   # no repeats, order kept


def main():
    # Windows consoles can fail on unusual characters in names: replace instead of crashing
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="Flexible cleaning of CSV/Excel files driven by a YAML config.")
    parser.add_argument("--input", nargs="+", required=True, help="File path(s) or glob pattern(s).")
    parser.add_argument("--config", required=True, help="YAML configuration file.")
    parser.add_argument("--output", default="output", help="Output folder (default: output).")
    parser.add_argument("--verbose", action="store_true", help="Explain every step with examples.")
    args = parser.parse_args()

    files = resolve_input_files(args.input)
    if not files:
        print(f"No files match: {args.input}", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)
    os.makedirs(args.output, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(args.output, f"run_log_{timestamp}.txt")
    logfile = open(log_path, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, logfile)

    summaries, all_logs = [], []
    extras = {"reconciliation": [], "investors": [], "collation_info": [], "eda": []}

    print(f"Processing {len(files)} file(s) with {args.config}...")
    for path in files:
        print(f"\n=== {os.path.basename(path)}")
        try:
            result = clean_file(path, config, args.output, verbose=args.verbose)
        except Exception as e:
            print(f"     ERROR while processing {path}: {e}")
            if args.verbose:
                traceback.print_exc(file=sys.stdout)
            summaries.append({"file": os.path.basename(path), "error": str(e)})
            continue
        s = result["summary"]
        summaries.append(s)
        all_logs.extend(result["detail_log"])
        x = result["extra"]
        for k in ("reconciliation", "investors", "eda"):
            if k in x:
                extras[k].append(x[k])
        if "collation_info" in x:
            extras["collation_info"].append(x["collation_info"])
        print(f"     {s['original_rows']:,} rows -> {s['final_rows']:,} | duplicates found: "
              f"{s['duplicates_found']:,} (removed {s['duplicates_removed']:,}) | manual review: "
              f"{s['rows_for_manual_review']:,}" + (f" | control totals: {s['control_totals']}" if "control_totals" in s else ""))

    extras["cross_file"] = cross_file(extras["investors"]) if extras["investors"] else None
    if extras["cross_file"] is not None and len(extras["cross_file"]):
        print(f"\nInvestors found in more than one file: {len(extras['cross_file']):,}")

    report_path = os.path.join(args.output, f"quality_report_{timestamp}.xlsx")
    build_excel_report(summaries, all_logs, report_path, extras)
    print(f"\nDone. Quality report: {report_path}")
    print(f"Run log: {log_path}")
    sys.stdout = sys.stdout.console
    logfile.close()


if __name__ == "__main__":
    main()
