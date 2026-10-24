"""File to send to the data broker for enrichment (client meeting 2026-10-24).

The broker only covers the United States and finds contact details for PEOPLE, so the file has one row per
investor that is: US address (is_international = False), type individual, and a primary_contact.
investor_id is the key that joins the enriched file back to the clean database: the broker must return it
unchanged in every row. Columns sent: investor ID, holder name, primary contact (also split into first /
middle / last name) and address. Nothing else (no shares, no notes).
"""

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
