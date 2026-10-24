"""File to send to the data broker for enrichment (client meeting 2026-10-24).

Separate step, AFTER the manual review (not part of the cleaning run): Raw -> cleaning run (Silver) ->
manual review (Silver reviewed) -> this step -> broker -> enriched file (Bronze). It reads the REVIEWED clean
file, so the reviewer's corrections are what is sent. Run it from the app (step 4) or with:
    python make_enrichment.py "<reviewed clean_database file>.xlsx"

The broker only covers the United States and finds contact details for PEOPLE, so the file has one row per
investor that is: US address (is_international = False), type individual, and a primary_contact.
investor_id is the key that joins the enriched file back to the clean database: the broker must return it
unchanged in every row. Columns sent: investor ID, holder name, primary contact (also split into first /
middle / last name) and address. Nothing else (no shares, no notes).
"""

import os

import pandas as pd

ENRICHMENT_COLUMNS = ["investor_id", "issuer_name", "CUSIP", "holder_name", "primary_contact", "first_name",
                      "middle_name", "last_name", "name_suffix", "street_address", "city", "state_province",
                      "ZIP Code/Postal Code", "country"]
SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


def split_name(full):
    """'JOHN Q PUBLIC JR' -> ('JOHN', 'Q', 'PUBLIC', 'JR'); 'LI WU' -> ('LI', '', 'WU', '')."""
    words = str(full or "").replace(",", " ").split()
    suffix = ""
    if len(words) > 2 and words[-1].strip(".") in SUFFIXES:
        suffix = words.pop().strip(".")
    if not words:
        return "", "", "", ""
    if len(words) == 1:
        return words[0], "", "", suffix
    return words[0], " ".join(words[1:-1]), words[-1], suffix


def build_enrichment_request(investors):
    """investors = the Investors sheet of the client file (client column names). Returns (DataFrame, counts)."""
    inv = investors
    us = ~inv["is_international"].astype(str).str.upper().isin(["TRUE", "1"])
    person = inv["type"].eq("individual") & inv["primary_contact"].fillna("").astype(str).str.strip().ne("")
    out = inv[us & person].copy()
    parts = out["primary_contact"].map(split_name)
    out["first_name"] = [p[0] for p in parts]
    out["middle_name"] = [p[1] for p in parts]
    out["last_name"] = [p[2] for p in parts]
    out["name_suffix"] = [p[3] for p in parts]
    out = out.reindex(columns=ENRICHMENT_COLUMNS)
    counts = {"sent": len(out), "international_not_sent": int((~us).sum()),
              "us_not_a_person_or_no_contact": int((us & ~person).sum())}
    return out, counts


def read_reviewed(path_or_buffer, filename=None):
    """Reads the reviewed clean file: the Investors sheet of the .xlsx (one row per investor), or the
    Original_Database sheet / an account-level CSV (then one row per investor is kept). Rows rejected in
    the manual verification are not sent."""
    name = (filename or str(path_or_buffer)).lower()
    if name.endswith(".csv"):
        df = pd.read_csv(path_or_buffer, dtype=str, encoding="utf-8-sig")
    else:
        sheets = pd.read_excel(path_or_buffer, sheet_name=None, dtype=str)
        df = sheets.get("Investors")
        if df is None:
            df = sheets.get("Original_Database", next(iter(sheets.values())))
    need = {"investor_id", "issuer_name", "holder_name", "primary_contact", "type", "is_international"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError("This is not a clean database file from the tool (missing columns: "
                         + ", ".join(sorted(missing)) + ").")
    if "data_quality" in df.columns:
        df = df[~df["data_quality"].fillna("").str.startswith("rejected")]
    df = df[df["investor_id"].fillna("").str.strip().ne("")]
    return df.drop_duplicates(["issuer_name", "investor_id"], keep="first")


def write_outputs(enrich, folder, label):
    """Writes enrichment_request_<label>.xlsx (investor_id and CUSIP as text) and .csv. Returns the paths."""
    os.makedirs(folder, exist_ok=True)
    xlsx = os.path.join(folder, f"enrichment_request_{label}.xlsx")
    csv = os.path.join(folder, f"enrichment_request_{label}.csv")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        enrich.to_excel(w, sheet_name="Enrichment_Request", index=False)
        ws = w.sheets["Enrichment_Request"]
        for row in ws.iter_rows(min_row=2, max_col=3):
            for c in row:
                c.number_format = "@"
    enrich.to_csv(csv, index=False, encoding="utf-8-sig")
    return xlsx, csv


def to_xlsx_bytes(enrich):
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        enrich.to_excel(w, sheet_name="Enrichment_Request", index=False)
        ws = w.sheets["Enrichment_Request"]
        for row in ws.iter_rows(min_row=2, max_col=3):
            for c in row:
                c.number_format = "@"
    return buf.getvalue()
