"""Runs every cleaning step for one file.

With verbose=True it prints, step by step, WHAT it did plus a few before/after examples,
so the result can be audited and code or config errors can be spotted.
"""

import os
from collections import defaultdict
from datetime import datetime

import pandas as pd

from .io_utils import read_table_robust
from .standardize import standardize_columns, coerce_types, trim_whitespace, zero_pad, is_null_like
from .dedupe import remove_duplicates
from .missing import handle_missing
from .validate import validate_ranges, out_of_range_mask
from .address import parse_addresses
from .collate import reconcile, collate, eda_tables


class _Narrator:
    """Prints the explanation of each step only when verbose=True."""

    def __init__(self, verbose):
        self.verbose = verbose
        self.n = 0

    def step(self, title):
        self.n += 1
        if self.verbose:
            print(f"\n  [{self.n}] {title}")

    def say(self, text):
        if self.verbose:
            print(f"      {text}")

    def show(self, df, cols=None, n=3, title=None):
        if self.verbose and len(df):
            if title:
                print(f"      {title}")
            sub = df[cols] if cols else df
            with pd.option_context("display.max_columns", 20, "display.width", 200, "display.max_colwidth", 40):
                txt = sub.head(n).to_string()
            print("\n".join("        " + line for line in txt.splitlines()))


def clean_file(path, config, output_dir, verbose=False):
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.basename(path)
    base_name = os.path.splitext(filename)[0]
    log = []
    reasons = defaultdict(list)          # row index -> reasons for manual review
    nar = _Narrator(verbose)

    def add_log(step, **kw):
        log.append({"file": filename, "step": step, **kw})

    # 1. Read -----------------------------------------------------------------
    nar.step("Read the file")
    res = read_table_robust(path, config["input"])
    df, metadata, trailer = res["data"], res["metadata"], res["trailer"]
    original_rows = len(df)
    nar.say(f"Encoding/engine: {res['encoding']}")
    nar.say(f"Detected structure: {res['layout']}")
    if metadata:
        nar.say(f"Metadata: {metadata}")
    for w in res["warnings"]:
        add_log("read", detail=w)
        nar.say(f"NOTE: {w}")
    add_log("read", detail=res["layout"])

    # 2. Headers --------------------------------------------------------------
    nar.step("Standardize headers")
    before_cols = list(df.columns)
    df, unmapped = standardize_columns(df, config["columns"]["rename"])
    for original, slug in unmapped:
        add_log("headers", detail=f"'{original}' -> '{slug}' (automatic)")
    for a, b in zip(before_cols, df.columns):
        nar.say(f"'{a}'  ->  '{b}'")

    # 3. Whitespace -----------------------------------------------------------
    if config["columns"].get("trim_whitespace", True):
        nar.step("Clean whitespace (leading/trailing and double spaces)")
        before = df.copy()
        df, changed = trim_whitespace(df)
        add_log("whitespace", detail=f"{changed} cells changed")
        nar.say(f"{changed:,} cells changed. Examples (before -> after):")
        shown = 0
        for c in df.columns:
            diff = before[c].astype("string").ne(df[c]) & before[c].notna()
            for i in df.index[diff][:2 - shown]:
                nar.say(f"  [{c}] {repr(before.at[i, c])}  ->  {repr(df.at[i, c])}")
                shown += 1
            if shown >= 2:
                break
    df = df.replace({"": pd.NA})
    df = zero_pad(df, config["columns"].get("zfill"))

    # 4. Data types -----------------------------------------------------------
    nar.step(f"Convert data types (number format: {config['columns']['number_format']})")
    type_cols = [c for c in config["columns"]["types"] if c in df.columns]
    raw_types = df[type_cols].copy()
    df, type_log = coerce_types(df, config["columns"]["types"], config["columns"]["date_format"],
                                config["columns"]["number_format"])
    type_errors = 0
    for entry in type_log:
        add_log("data_types", **entry)
        type_errors += entry.get("not_convertible") or 0
        nar.say(f"{entry['column']}: {entry.get('detail')}")
    for col, dtype in config["columns"]["types"].items():
        if col in df.columns and dtype in ("integer", "float", "date"):
            # a real conversion failure = the raw value was NOT empty-like but the result is empty
            failed = raw_types[col].map(lambda v: not is_null_like(v)) & df[col].isna()
            for i in df.index[failed]:
                reasons[i].append(f"{col} not a valid {dtype}: '{raw_types.at[i, col]}'")
            idx = raw_types[col].dropna().head(3).index
            nar.say(f"{col}: examples {raw_types.loc[idx, col].tolist()} -> {df.loc[idx, col].tolist()}")
            if failed.any():
                nar.show(raw_types.loc[failed, [col]], title=f"Values in '{col}' that could not be converted:")

    # 5. Duplicates -----------------------------------------------------------
    dcfg = config["duplicates"]
    nar.step(f"Duplicates (action: {dcfg.get('action', 'remove')})")
    df, removed_df, dup_log = remove_duplicates(df, dcfg["subset"], dcfg["keep"], dcfg.get("action", "remove"))
    add_log("duplicates", **{k: str(v) for k, v in dup_log.items()})
    nar.say(f"{dup_log['duplicates_found']:,} identical rows found; {dup_log['duplicates_removed']:,} removed.")
    if dcfg.get("action") == "flag" and dup_log["duplicates_found"]:
        nar.say("They are kept (columns dup_exact / dup_copies). Example:")
        nar.show(df[df["dup_copies"] > 1].head(2), list(df.columns[:3]) + ["dup_exact", "dup_copies"])

    # 6. Missing values -------------------------------------------------------
    mcfg = config["missing_values"]
    nar.step("Missing values")
    null_counts = df.isna().sum()
    df, _flagged_missing, missing_log = handle_missing(df, mcfg["strategy"], mcfg["default_strategy"],
                                                       mcfg["fill_values"])
    missing_handled = 0
    for entry in missing_log:
        add_log("missing_values", **entry)
        missing_handled += entry.get("missing_values") or 0
        nar.say(f"{entry['column']}: {entry['missing_values']:,} empty -> {entry['strategy_applied']}")
        if str(entry["strategy_applied"]).startswith("flag_only"):
            for i in df.index[df[entry["column"]].isna()]:
                # skip if the value was not empty but failed conversion (already reported)
                if not any(r.startswith(f"{entry['column']} not a valid") for r in reasons[i]):
                    reasons[i].append(f"{entry['column']} empty")
    ignored = [c for c in df.columns
               if null_counts.get(c, 0) and mcfg["strategy"].get(c, mcfg["default_strategy"]) == "ignore"]
    if ignored:
        nar.say(f"Ignored on purpose (usually empty): {', '.join(ignored)}")

    # 7. Ranges ---------------------------------------------------------------
    nar.step("Range validation")
    _flagged_ranges, range_log = validate_ranges(df, config["validation"]["ranges"])
    for entry in range_log:
        add_log("ranges", **entry)
        nar.say(f"{entry['column']}: {entry['out_of_range_values']} outside {entry['expected_range']}")
    for col, bounds in config["validation"]["ranges"].items():
        if col in df.columns:
            bad = out_of_range_mask(df[col], bounds)
            for i in df.index[bad]:
                reasons[i].append(f"{col} out of range ({df.at[i, col]})")
    if not range_log:
        nar.say("All values within the defined ranges.")

    # 8. Name/address parsing (optional) --------------------------------------
    extra = {}
    if config.get("address_parsing"):
        nar.step("Parse name and address (free-text lines -> fields)")
        df, flagged_addr, addr_log = parse_addresses(df, config["address_parsing"])
        for e in addr_log:
            add_log(e.pop("step"), **e)
            nar.say(f"{e['column']} -> {e['detail']}")
        for i in flagged_addr:
            st, note = df.at[i, "parse_status"], df.at[i, "parse_notes"]
            if isinstance(note, str) and "app format" in note:
                reasons[i].append("no holder name (international app format)")
            else:
                reasons[i].append(f"address {st}" + (f": {note}" if isinstance(note, str) and note else ""))
        show_cols = ["holder_name", "entity_type", "city", "state_province", "postal_code", "country", "parse_status"]
        nar.show(df[df["parse_status"] == "ok"], show_cols, 3, "Examples parsed OK:")
        nar.show(df[df["parse_status"] != "ok"], show_cols + ["parse_notes"], 3, "Examples NOT OK (sent to review):")

    # 9. Control totals (optional) --------------------------------------------
    if config.get("reconciliation"):
        nar.step("Reconcile against the file's control totals")
        rec = reconcile(df, trailer, metadata, config["reconciliation"], filename)
        extra["reconciliation"] = rec
        for _, r in rec.iterrows():
            nar.say(f"{r['check']}: expected {r['expected_per_file']} | obtained "
                    f"{r['obtained_after_cleaning']} -> {r['result']}")
            add_log("reconciliation", column=r["check"],
                    detail=f"{r['result']} (expected {r['expected_per_file']}, obtained {r['obtained_after_cleaning']})")

    # 10. Unique investors + EDA (optional) -----------------------------------
    if config.get("collation") and "holder_key" in df.columns:
        nar.step("Consolidate unique investors + EDA")
        ccfg = config["collation"]
        issuer = metadata.get(ccfg.get("issuer_field", "ISSUER NAME"))
        inv, info = collate(df, ccfg, filename, issuer)
        extra["investors"] = inv
        extra["collation_info"] = {"file": filename, "issuer": issuer, **info}
        extra["eda"] = eda_tables(df, ccfg.get("value_column", "shares"), filename)
        nar.say(f"{info['accounts']:,} accounts -> {info['unique_investors']:,} unique investors "
                f"({info['investors_with_multiple_accounts']:,} with more than one account; "
                f"{info['accounts_without_name']:,} accounts with no name are left out).")
        nar.show(inv, ["holder_name", "entity_type", "country", "n_accounts", "total_shares", "pct_of_shares"], 5,
                 "Top 5 by shares:")

    # 11. Write outputs -------------------------------------------------------
    nar.step("Write outputs")
    flagged = sorted(i for i in reasons if i in df.index)
    review_df = df.loc[flagged].copy()
    review_df.insert(0, "review_reason", [" ; ".join(dict.fromkeys(reasons[i])) for i in flagged])
    review_df.insert(0, "original_row", [i + 1 for i in flagged])

    suffix = config["output"].get("suffix", "_clean")
    clean_path = os.path.join(output_dir, f"{base_name}{suffix}.csv")
    df.to_csv(clean_path, index=False, encoding="utf-8-sig")   # utf-8-sig: Excel opens accents correctly
    written = [clean_path]
    review_path = None
    if len(review_df):
        review_path = os.path.join(output_dir, f"{base_name}_manual_review.csv")
        review_df.to_csv(review_path, index=False, encoding="utf-8-sig")
        written.append(review_path)
    if len(removed_df):
        p = os.path.join(output_dir, f"{base_name}_removed_duplicates.csv")
        removed_df.to_csv(p, index=False, encoding="utf-8-sig")
        written.append(p)
    if "investors" in extra:
        p = os.path.join(output_dir, f"{base_name}_investors.csv")
        extra["investors"].to_csv(p, index=False, encoding="utf-8-sig")
        written.append(p)
    for p in written:
        nar.say(os.path.basename(p))
    if len(review_df):
        top = pd.Series([m.split(":")[0].split(" (")[0] for r in review_df["review_reason"]
                         for m in r.split(" ; ")]).value_counts()
        nar.say("Most frequent manual review reasons:")
        for k, v in top.head(8).items():
            nar.say(f"  {v:>6,}  {k}")

    summary = {
        "file": filename,
        "issuer": metadata.get("ISSUER NAME", "-"),
        "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "encoding_used": res["encoding"],
        "original_rows": original_rows,
        "final_rows": len(df),
        "duplicates_found": dup_log["duplicates_found"],
        "duplicates_removed": dup_log["duplicates_removed"],
        "missing_values_handled": missing_handled,
        "type_errors": type_errors,
        "rows_for_manual_review": len(review_df),
        "clean_file": os.path.basename(clean_path),
        "manual_review_file": os.path.basename(review_path) if review_path else "-",
    }
    if "reconciliation" in extra:
        r = extra["reconciliation"]["result"]
        summary["control_totals"] = ("OK" if (r == "OK").all()
                                     else ("NOT IN FILE" if (r == "NO CONTROL TOTALS").all() else "CHECK"))
    if "collation_info" in extra:
        summary["unique_investors"] = extra["collation_info"]["unique_investors"]

    return {"summary": summary, "detail_log": log, "clean_path": clean_path,
            "review_path": review_path, "metadata": metadata, "extra": extra, "data": df}
