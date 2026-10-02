"""Manual verification loop.

Rows the toolkit could not resolve on its own (no holder name, address that could not be
parsed, empty or zero shares) are listed in the Manual_Verification sheet of the client file
and in master/manual_verification.csv, with:

    status              pending | checked | rejected   (the person edits this)
    corrected_*         columns where the person writes the right values (optional)

On the next run, rows with status "checked" are taken back into the database: the corrected
values (or the parsed ones, when nothing was corrected) are used, the row gets an investor_id
and it appears in the Investors sheet with data_quality = "manually verified". Rows marked
"rejected" stay out for good. The person can edit either the delivered Excel file or
master/manual_verification.csv; the run reads both and keeps the most recent decision.
"""

import hashlib
import os

import pandas as pd

LINE_COLUMNS = [f"line_{i}" for i in range(1, 8)]
CORRECTED = ["corrected_holder_name", "corrected_street_address", "corrected_city",
             "corrected_state_province", "corrected_postal_code", "corrected_country"]
SHEET_COLUMNS = (["verification_id", "status", "issuer_name", "record_date", "source_file", "source_row",
                  "shares", "review_reason"] + LINE_COLUMNS +
                 ["holder_name", "street_address", "city", "state_province", "postal_code", "country"] +
                 CORRECTED + ["reviewer_note"])
VALID_STATUS = {"pending", "checked", "rejected"}


def verification_id(row):
    """Stable key: same account in the same file always gets the same id."""
    def txt(v):
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA or pd.isna(v) else str(v)
    raw = "|".join([txt(row.get("issuer")) or txt(row.get("issuer_name"))] +
                   [txt(row.get(c)) for c in LINE_COLUMNS] + [txt(row.get("shares"))])
    return "MV-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10].upper()


def load(path):
    if os.path.isfile(path):
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        return df.reindex(columns=SHEET_COLUMNS, fill_value="")
    return pd.DataFrame(columns=SHEET_COLUMNS)


def load_from_excel(path, sheet="Manual_Verification"):
    """Reads the sheet back from a delivered Excel file the person may have edited."""
    try:
        df = pd.read_excel(path, sheet_name=sheet, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame(columns=SHEET_COLUMNS)
    return df.reindex(columns=SHEET_COLUMNS, fill_value="")


def merge_decisions(*tables):
    """Keeps one row per verification_id, preferring the one with a decision (checked/rejected)."""
    all_rows = pd.concat([t for t in tables if t is not None and len(t)], ignore_index=True) \
        if any(t is not None and len(t) for t in tables) else pd.DataFrame(columns=SHEET_COLUMNS)
    if not len(all_rows):
        return all_rows
    all_rows["_rank"] = all_rows["status"].str.strip().str.lower().map(
        {"checked": 0, "rejected": 1, "pending": 2}).fillna(3)
    all_rows = all_rows.sort_values("_rank").drop_duplicates("verification_id", keep="first")
    return all_rows.drop(columns="_rank")


def apply_decisions(accounts, decisions):
    """
    Puts "checked" rows back into the data: corrected values win, otherwise the parsed ones are
    accepted as they are. Returns (accounts, counts:dict).
    """
    accounts = accounts.copy()
    accounts["verification_id"] = accounts.apply(verification_id, axis=1)
    counts = {"checked_applied": 0, "checked_without_name": 0, "rejected": 0}
    if decisions is None or not len(decisions):
        return accounts, counts

    dec = decisions.copy()
    dec["status"] = dec["status"].astype(str).str.strip().str.lower()
    by_id = dec.set_index("verification_id")
    idx = accounts.index[accounts["verification_id"].isin(by_id.index)]
    for i in idx:
        d = by_id.loc[accounts.at[i, "verification_id"]]
        if isinstance(d, pd.DataFrame):
            d = d.iloc[0]
        if d["status"] == "rejected":
            accounts.at[i, "data_quality"] = "rejected in manual verification"
            counts["rejected"] += 1
            continue
        if d["status"] != "checked":
            continue
        for col in ["holder_name", "street_address", "city", "state_province", "postal_code", "country"]:
            value = str(d.get(f"corrected_{col}") or "").strip()
            if value:
                accounts.at[i, col] = value.upper() if col in ("holder_name", "country") else value
        if not str(accounts.at[i, "holder_name"] or "").strip():
            counts["checked_without_name"] += 1
            accounts.at[i, "data_quality"] = "checked but still no holder name"
            continue
        accounts.at[i, "data_quality"] = "manually verified"
        accounts.at[i, "parse_status"] = "ok"
        counts["checked_applied"] += 1
    return accounts, counts


def build_sheet(accounts, decisions):
    """Rows that still need a decision + the ones already decided, for the client file."""
    needs = accounts[accounts["data_quality"].isin(
        ["no holder name", "check address", "check shares", "checked but still no holder name"])].copy()
    rows = []
    for _, a in needs.iterrows():
        rows.append({"verification_id": a["verification_id"], "status": "pending",
                     "issuer_name": a.get("issuer"), "record_date": a.get("record_date"),
                     "source_file": a.get("source_file"), "source_row": a.get("source_row"),
                     "shares": a.get("shares"), "review_reason": a.get("data_quality"),
                     **{c: a.get(c) for c in LINE_COLUMNS if c in needs.columns},
                     **{c: a.get(c) for c in ["holder_name", "street_address", "city", "state_province",
                                              "postal_code", "country"]},
                     **{c: "" for c in CORRECTED}, "reviewer_note": ""})
    sheet = pd.DataFrame(rows, columns=SHEET_COLUMNS)
    if decisions is not None and len(decisions):
        dec = decisions.set_index("verification_id")
        for i in sheet.index:
            vid = sheet.at[i, "verification_id"]
            if vid in dec.index:
                d = dec.loc[vid]
                if isinstance(d, pd.DataFrame):
                    d = d.iloc[0]
                for c in ["status", "reviewer_note"] + CORRECTED:
                    if str(d.get(c) or "").strip():
                        sheet.at[i, c] = d[c]
        done = dec[~dec.index.isin(sheet["verification_id"])].reset_index()
        done = done[done["status"].str.strip().str.lower() == "checked"]
        if len(done):
            sheet = pd.concat([sheet, done.reindex(columns=SHEET_COLUMNS)], ignore_index=True)
    order = {"pending": 0, "checked": 1, "rejected": 2}
    sheet["_o"] = sheet["status"].str.lower().map(order).fillna(0)
    return sheet.sort_values(["_o", "issuer_name", "shares"], ascending=[True, True, False]).drop(columns="_o")
