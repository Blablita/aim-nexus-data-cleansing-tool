"""Builds the monthly client deliverable (the clean, sellable database).

The deliverable has a FIXED layout that does not change between months, and only
buyer-facing columns (no internal/debug columns). Sheets:

    Summary            one row per issuer: accounts, investors, control totals, changes
    Investors          one row per unique investor per issuer  (main product)
    Original_Database  every row of the original file, cleaned, linked by investor_id
    Manual_Verification rows that need a person to decide, with a status column
    Exited_Investors   investors present last month but not this month
    Data_Dictionary    definition of every column
"""

import fnmatch
import re

import pandas as pd

from .address import account_category, classify_entity, holder_key as make_holder_key
from .collate import collate
from .registry import assign_ids
from .verification import apply_decisions, build_sheet

# Internal name -> name shown in the client file
RENAME = {"issuer": "issuer_name", "cusip": "CUSIP", "job_number": "Job_number",
          "postal_code": "ZIP Code/Postal Code",
          "holder_extra_lines": "notes"}   # extra registration lines are kept, not deleted (client 2026-10-02)  # buyers are US-based (client feedback 2026-09-29)

# Duplicates are KEPT (client decision 2026-10-02): the same holder can appear several times because
# of different account types. duplicate_status explains each case.
DUPLICATE_STATUS = {
    "SINGLE_ACCOUNT": "The investor has only this account in the file.",
    "MULTIPLE_ACCOUNTS_DIFFERENT_TYPES": "The investor has several accounts and they have different account types "
                                         "(for example an IRA and a joint account). Each one is a real account.",
    "MULTIPLE_ACCOUNTS_SAME_TYPE": "The investor has several accounts of the same account type, with different shares "
                                   "or registration lines (for example two IRAs at two brokers).",
}
IDENTICAL_NOTE = ("Exact copy of another row of the same file (every column is the same). Moved here for "
                  "validation; not counted in Investors or total_shares.")

# Column order of the client file (internal names; renamed with RENAME when written)
INVESTOR_COLUMNS = [
    "record_date", "job_number", "cusip", "issuer", "investor_id", "total_shares", "n_accounts",
    "holder_name", "street_address", "city", "state_province", "postal_code", "country", "is_international",
    "data_quality", "change_vs_prev_month", "prev_month_shares", "shares_change",
    "entity_type", "account_types", "account_categories", "notes", "identical_copies", "id_match_method",
    "id_match_confidence", "address_changed",
]
ACCOUNT_COLUMNS = [
    "record_date", "job_number", "cusip", "issuer", "investor_id", "shares", "holder_name",
    "holder_extra_lines", "street_address", "city", "state_province", "postal_code", "country",
    "is_international", "data_quality", "entity_type", "account_type", "account_category",
    "account_number", "duplicate_status", "identical_group_id", "identical_copies", "parsing_notes",
    "original_lines", "source_file", "source_row", "verification_id",
]
IDENTICAL_COLUMNS = [c for c in ACCOUNT_COLUMNS if c not in ("account_number", "duplicate_status", "identical_copies",
                                                             "verification_id")] + ["validation_status", "validation_note"]
ID_COLUMNS = ["investor_id", "identical_group_id"]

DATA_DICTIONARY = [
    ("record_date", "Record date of the NOBO list (YYYY-MM-DD), from the file header."),
    ("Job_number", "Job number of the NOBO list request, from the file header."),
    ("CUSIP", "9-character CUSIP of the security, from the file header."),
    ("issuer_name", "Company whose shares are held, from the file header (or config issuer_overrides)."),
    ("investor_id", "Permanent investor ID, numbers only (1, 2, 3...), assigned the first time the investor appears and kept in "
                    "the investor registry. It does not change when the investor moves. One ID per investor per issuer."),
    ("id_match_method", "How this month's row was linked to the registry: manual (confirmed by a person), exact (same "
                        "name + street + postal code), strong (same name + same postal code or same street), "
                        "probable_move (same name and same shares, new address), new (first time seen)."),
    ("id_match_confidence", "confirmed / high / medium / new, from id_match_method."),
    ("address_changed", "True if the address is different from the one in the registry (the investor moved or the "
                        "address was written differently)."),
    ("total_shares", "Shares held by the investor in this issuer, summed over all of the investor's accounts. "
                     "The file is sorted by this column, largest first."),
    ("n_accounts", "Number of accounts (rows of the NOBO list, one per account at one broker) grouped under this "
                   "investor. total_shares is the sum of those accounts. An account can hold 0 shares on the record date."),
    ("shares", "(Original_Database sheet) Shares held in that one account, as reported in the NOBO list."),
    ("holder_name", "Name of the account holder (first name line of the registration)."),
    ("notes", "Registration lines that are not the name or the address (co-holder, custodian, ATTN, C/O, account "
              "description). Kept as they are for a later validation step. In Investors: the notes of all the "
              "investor's accounts, separated by ' ; '."),
    ("parsing_notes", "(Original_Database sheet) What the parser had to assume to read the address (for example 'city "
                      "taken from the previous line'). Empty when the address was read directly."),
    ("original_lines", "(Original_Database sheet) The 7 name and address lines exactly as in the source file, "
                       "separated by ' | '. Nothing is lost by the cleaning."),
    ("street_address", "Street lines of the mailing address. US addresses use USPS standard abbreviations (DR, STE, BLVD...)."),
    ("city, state_province, ZIP Code/Postal Code, country", "Parsed from the last lines of the registration. "
     "ZIP Code/Postal Code is the 5-digit ZIP or ZIP+4 for US addresses, the postal code for other countries."),
    ("is_international", "True if the country is not the United States."),
    ("data_quality", "complete = name and full address parsed; check address = part of the address could not be read; "
                     "check shares = shares empty or 0; no holder name = the file has no name; manually verified = a "
                     "person checked it in the Manual_Verification sheet; rejected in manual verification = left out on purpose."),
    ("status (Manual_Verification sheet)", "pending = waiting for a decision; checked = accepted, comes back into the "
                                           "Investors sheet on the next run; rejected = stays out."),
    ("corrected_* (Manual_Verification sheet)", "Optional columns where the reviewer writes the right name or address. "
                                                "If left empty, a 'checked' row is accepted as it was parsed."),
    ("verification_id", "Key of a row in the Manual_Verification sheet. The same account always gets the same key."),
    ("change_vs_prev_month", "FIRST MONTH (no earlier file to compare), NEW (not in last month's file), "
                             "CONTINUING (also in last month's file) or EXITED (only in Exited_Investors: in last month's file, not in this one). "
                             "Compared by issuer_name + investor_id."),
    ("prev_month_shares", "total_shares in the previous month's file (empty for NEW and FIRST MONTH)."),
    ("shares_change", "total_shares minus prev_month_shares (positive = bought, negative = sold)."),
    ("entity_type", "Type of holder, from keywords in the holder name: nominee_custodian, trust, company_institution, "
                    "joint_individual, individual or unknown (see the Account_Types sheet for the rules)."),
    ("account_types", "Registration types found in any line of the investor's accounts (see the Account_Types sheet)."),
    ("account_type", "(Original_Database sheet) Registration type(s) of this one account, from keywords in all its "
                     "registration lines, based on the client's NOBO List Acronym Glossary (see the Account_Types sheet)."),
    ("account_category, account_categories", "Broad group of the account type(s): RETIREMENT, HEALTH_EDUCATION, "
     "JOINT_OWNERSHIP, TRANSFER_ON_DEATH, CUSTODIAL_MINOR, TRUST_ESTATE, LENDING_COLLATERAL, INSTITUTIONAL or "
     "STANDARD (no registration keyword: usually a regular individual or company account)."),
    ("account_number", "(Original_Database sheet) 1, 2, 3... numbers the accounts of the same investor, largest "
                       "first. n_accounts in Investors is the highest number."),
    ("duplicate_status", "(Original_Database sheet) Why the same investor appears more than once. "
     + " ".join(f"{k} = {v}" for k, v in DUPLICATE_STATUS.items())),
    ("identical_group_id", "Number shared by a row in Original_Database and its exact copies in the Identical_Rows "
                           "sheet. Empty for rows without copies."),
    ("identical_copies", "How many exact copies of this row (Original_Database) or of the investor's accounts "
                         "(Investors) were moved to the Identical_Rows sheet."),
    ("validation_status, validation_note", "(Identical_Rows sheet) " + IDENTICAL_NOTE + " pending = waiting for the "
     "client to confirm whether the copy is a real separate account."),
    ("identical_rows_moved, shares_in_identical_rows", "(Summary sheet) Exact copies moved to Identical_Rows and the "
     "shares they hold. source_total_shares = total_shares + shares_in_identical_rows (matches the source file)."),
    ("source_file, source_row", "(Original_Database sheet) Original file and row number, for traceability."),
]

ACCOUNT_TYPE_GUIDE = [
    ("account_type", "ROTH_IRA", "Roth IRA (after-tax retirement account).", "ROTH"),
    ("account_type", "ROLLOVER_IRA", "IRA funded by a rollover from an employer plan.", "ROLLOVER, R/O, RRA"),
    ("account_type", "SEP_IRA", "Simplified Employee Pension IRA (self-employed / small business).", "SEP"),
    ("account_type", "SIMPLE_IRA", "SIMPLE IRA (small employer plan).", "SIMPLE"),
    ("account_type", "INHERITED_IRA", "IRA inherited by a beneficiary.", "INHERITED, BENE IRA, BENEFICIARY IRA, DECD, BDA"),
    ("account_type", "TRADITIONAL_IRA", "Traditional / contributory IRA (any IRA not identified as a more specific type).", "IRA"),
    ("account_type", "EMPLOYER_PLAN", "Employer retirement plan: 401(k), 403(b), Keogh, profit sharing, pension plan.", "401K, 403B, KEOGH, PROFIT SHARING, PENSION PLAN, PSP, FRP, P/ADM, TSP, NGSP, SAVINGS PLAN, THRIFT"),
    ("account_type", "HSA", "Health Savings Account.", "HSA"),
    ("account_type", "EDUCATION_529_ESA", "Education savings: 529 plan or Coverdell ESA.", "529, COVERDELL, ESA"),
    ("account_type", "TOD_POD", "Transfer on Death / Payable on Death: passes to named beneficiaries outside probate.", "TOD, DESIGNATED BENE PLAN/TOD, POD, STA RULES"),
    ("account_type", "JOINT_WROS", "Joint Tenants With Right of Survivorship.", "JTWROS, JT TEN, JT, WROS"),
    ("account_type", "TENANTS_IN_COMMON", "Joint Tenants in Common (each owner's share goes to their estate).", "TEN COM, TENANTS IN COMMON, TIC"),
    ("account_type", "TENANTS_BY_ENTIRETY", "Tenants by the Entirety (married couples, some states).", "TEN ENT, TENANTS BY, ATBE, TBE"),
    ("account_type", "COMMUNITY_PROPERTY", "Community property (married couples, community property states).", "COMM PROP, COMMUNITY PROPERTY"),
    ("account_type", "CUSTODIAL_MINOR", "Custodial account for a minor (UTMA / UGMA).", "UTMA, UGMA, UNTIL AGE"),
    ("account_type", "TRUST", "Account held by a trust (trustee named).", "TRUST, TTEE, CO-TTEE, TRUSTEE, TRS, TRU, TRST, U/A, UAD, U/A/D, U/DEC, DTD, REV TR, IRR TR, CREDIT SHELTER"),
    ("account_type", "ESTATE", "Account of a deceased person's estate.", "ESTATE OF, EST OF, EXECUTOR, EX (at the end of a line)"),
    ("account_type", "CANADA_REGISTERED", "Canadian registered plan: RRSP, RRIF, TFSA, LIRA, RESP.", "RRSP, RRIF, TFSA, LIRA, RESP"),
    ("account_type", "PLEDGED", "Securities pledged as collateral.", "PLEDGE"),
    ("account_type", "FOUNDATION", "Charitable foundation (institutional holder).", "FDN, FDNTN, FOUNDATION"),
    ("account_type", "OMNIBUS", "Omnibus account: pools many clients; the real owners are not shown.", "OMNI, OMNIBUS"),
    ("account_type", "SECURITIES_LENDING", "Shares that may be lent or were borrowed; can inflate the apparent position.", "FPSL (not NON-FPSL), SLFP, SEC LEND, STOCK BORROW, FPL"),
    ("account_type", "TAX_WITHHOLDING", "Sub-account for US dividend tax withheld (non-US holder).", "W/H, USWT, WITHHOLDING"),
    ("account_type", "PROXY_AGENT", "Institutional fund voted through a proxy service (ISS, Glass Lewis, ProxyEdge).", "ISS/, ISSGOVERNANCE, PEID, GLASS LEWIS, PVA, RMG/, PROXYEDGE"),
    ("account_type", "PROPRIETARY_TRADING", "Firm trading with its own money or a trading-desk sub-account.", "PROP TRADER, DELTA 1, NON FLIP"),
    ("account_type", "INDEX_FUND_ETF", "Index fund, ETF, unit investment trust or UCITS fund.", "ETF, UIT, UCITS, MSCI, IMI"),
    ("account_type", "NOT_SPECIFIED", "No registration keyword found (usually a standard individual or company account).", "-"),
    ("entity_type", "nominee_custodian", "A broker, bank or nominee holding for someone else (checked first).", "NOMINEE, OMNIBUS, CEDE, FBO, CUST, CUSTODIAN, C/F in the holder name"),
    ("entity_type", "trust", "A trust or estate.", "TRUST, TR, TTEE, TRUSTEE, U/A, UAD, ESTATE in the holder name"),
    ("entity_type", "company_institution", "A company, fund or institution.", "INC, LLC, LTD, CORP, CO, LP, FUND, ETF, CAPITAL, PARTNERS, HOLDINGS, BANK, FOUNDATION, PLAN... in the holder name"),
    ("entity_type", "joint_individual", "Two or more people on the same account.", "'&' in the holder name, JT, WROS"),
    ("entity_type", "individual", "A person: none of the words above and at least two words in the name.", "-"),
    ("entity_type", "unknown", "No holder name, or a name that cannot be classified.", "-"),
]


def _account_types_sheet():
    from .address import ACCOUNT_CATEGORY
    g = pd.DataFrame(ACCOUNT_TYPE_GUIDE, columns=["column", "value", "meaning", "keywords searched"])
    g.insert(2, "account_category", [ACCOUNT_CATEGORY.get(v, "") if c == "account_type" else ""
                                     for c, v in zip(g["column"], g["value"])])
    status = pd.DataFrame([("duplicate_status", k, "", v, "-") for k, v in DUPLICATE_STATUS.items()],
                          columns=g.columns)
    return pd.concat([g, status], ignore_index=True)


def resolve_issuer(filename, metadata, overrides):
    """Issuer name from the header block, a config override, or UNKNOWN."""
    for pattern, issuer in (overrides or {}).items():
        if fnmatch.fnmatch(filename.lower(), pattern.lower()):
            return issuer
    name = metadata.get("ISSUER NAME")
    if name:
        return " ".join(name.split())
    return f"UNKNOWN ISSUER ({filename})"


def normalize_record_date(value):
    """'092225' / '62626' (leading zero lost by Excel) -> '2025-09-22' / '2026-06-26'."""
    v = re.sub(r"\D", "", str(value or ""))
    if len(v) == 5:
        v = "0" + v
    if len(v) == 6:
        return f"20{v[4:6]}-{v[0:2]}-{v[2:4]}"
    return value or None


def _account_quality(df):
    q = pd.Series("complete", index=df.index)
    q[df["parse_status"] != "ok"] = "check address"
    q[df["shares"].isna() | (df["shares"] <= 0)] = "check shares"
    q[df["holder_name"].isna()] = "no holder name"
    return q


def build(month, results, prev_investors, collation_cfg, overrides=None, registry=None, id_overrides=None,
          manual_decisions=None):
    """
    month:           "YYYY-MM"
    results:         list of dicts returned by pipeline.clean_file (one per input file)
    prev_investors:  previous month's Investors table (DataFrame) or None
    Returns: dict of DataFrames (sheet name -> table) + list of notes
    """
    notes = []
    frames = []
    for r in results:
        d = r["data"].copy()
        fname = r["summary"]["file"]
        d["issuer"] = resolve_issuer(fname, r["metadata"], overrides)
        d["cusip"] = r["metadata"].get("CUSIP") or (d["cusip"].dropna().iloc[0] if "cusip" in d and d["cusip"].notna().any() else None)
        d["record_date"] = normalize_record_date(r["metadata"].get("RECORD DATE"))
        d["job_number"] = r["metadata"].get("JOB NUMBER")
        d["source_file"] = fname
        d["source_row"] = d.index + 1
        frames.append(d)
    accounts = pd.concat(frames, ignore_index=True)
    accounts["data_quality"] = _account_quality(accounts)
    accounts["identical_to_other_account"] = accounts.get("dup_copies", pd.Series(1, index=accounts.index)).fillna(1) > 1

    # --- extra information is kept for validation (client 2026-10-02)
    accounts["account_category"] = accounts["account_type"].map(account_category)
    accounts["parsing_notes"] = accounts.get("parse_notes")
    line_cols = [c for c in [f"line_{i}" for i in range(1, 8)] if c in accounts.columns]
    accounts["original_lines"] = accounts[line_cols].apply(
        lambda r: " | ".join(" ".join(str(v).split()) for v in r if pd.notna(v) and str(v).strip()), axis=1) \
        if line_cols else None

    # --- exact copies (every column of the source row is the same): the first stays, the copies go to
    # the Identical_Rows sheet for validation and are not counted (client 2026-10-02)
    accounts["identical_group_id"] = None
    accounts["identical_copies"] = 0
    has_copies = accounts["identical_to_other_account"]
    if has_copies.any():
        key_cols = [c for c in ["source_file", "shares"] + line_cols + ["zip_sort", "cusip"] if c in accounts.columns]
        keys = accounts.loc[has_copies, key_cols].astype(str).agg("|".join, axis=1)
        accounts.loc[has_copies, "identical_group_id"] = (pd.factorize(keys)[0] + 1).astype(str)
        accounts.loc[has_copies, "identical_copies"] = \
            accounts.loc[has_copies, "dup_copies"].fillna(1).astype(int) - 1
    is_copy = accounts["dup_exact"].fillna(False).astype(bool) if "dup_exact" in accounts.columns \
        else pd.Series(False, index=accounts.index)
    identical = accounts[is_copy].copy()
    identical["identical_copies"] = 0
    identical["validation_status"] = "pending"
    identical["validation_note"] = IDENTICAL_NOTE
    accounts = accounts[~is_copy].copy()
    if len(identical):
        notes.append(f"Identical_Rows: {len(identical):,} exact copies moved for validation "
                     f"({float(identical['shares'].sum()):,.3f} shares, not counted in Investors).")

    # --- manual verification decisions from earlier runs
    accounts, vcounts = apply_decisions(accounts, manual_decisions)
    fixed = accounts.index[accounts["data_quality"] == "manually verified"]
    for i in fixed:
        accounts.at[i, "holder_key"] = make_holder_key(accounts.loc[i])
        accounts.at[i, "entity_type"] = classify_entity(accounts.at[i, "holder_name"], [])
    if any(vcounts.values()):
        notes.append("Manual verification: " + ", ".join(f"{k} {v:,}" for k, v in vcounts.items() if v))

    # --- investors: collate per issuer (all files of the same issuer together)
    inv_tables, summary_rows = [], []
    for issuer, sub in accounts.groupby("issuer", sort=False):
        files = sorted(sub["source_file"].unique())
        if len(files) > 1:
            notes.append(f"{issuer}: {len(files)} files in the same month ({', '.join(files)}). "
                         f"If one is an extract of the other, accounts are counted twice.")
        inv, info = collate(sub, collation_cfg, " | ".join(files), issuer)
        inv["cusip"] = sub["cusip"].iloc[0]
        inv["record_date"] = sub["record_date"].iloc[0]
        inv["job_number"] = sub["job_number"].iloc[0]
        inv["data_quality"] = inv["accounts_not_fully_parsed"].map(lambda n: "complete" if n == 0 else "check address")
        verified_keys = set(sub.loc[sub["data_quality"] == "manually verified", "holder_key"].dropna())
        inv.loc[inv["holder_key"].isin(verified_keys), "data_quality"] = "manually verified"
        inv_tables.append(inv)
        summary_rows.append({"month": month, "record_date": sub["record_date"].iloc[0],
                             "job_number": sub["job_number"].iloc[0],
                             "cusip": inv["cusip"].iloc[0] if len(inv) else None, "issuer": issuer,
                             "source_files": " | ".join(files),
                             "accounts": info["accounts"], "accounts_without_holder_name": info["accounts_without_name"],
                             "unique_investors": info["unique_investors"],
                             "total_shares": float(sub["shares"].sum()),
                             "identical_rows_moved": int((identical["issuer"] == issuer).sum()),
                             "shares_in_identical_rows": float(identical.loc[identical["issuer"] == issuer, "shares"].sum()),
                             "source_total_shares": float(sub["shares"].sum())
                                 + float(identical.loc[identical["issuer"] == issuer, "shares"].sum()),
                             "international_accounts_pct": round(float((sub["is_international"] == True).mean() * 100), 2)})
    investors = pd.concat(inv_tables, ignore_index=True)

    # permanent investor IDs from the registry
    investors, registry_out, review, id_counts = assign_ids(investors, month, registry, id_overrides)
    notes.append("Investor IDs: " + ", ".join(f"{k} {v:,}" for k, v in id_counts.items()))

    # link accounts to investor_id (same issuer + same holder_key)
    key_to_id = investors.set_index(["issuer", "holder_key"])["investor_id"]
    accounts["investor_id"] = [key_to_id.get((i, k)) for i, k in zip(accounts["issuer"], accounts["holder_key"])]

    # --- why an investor appears more than once
    accounts = accounts.sort_values("shares", ascending=False, kind="stable")
    grp = accounts.groupby(["issuer", "investor_id"], dropna=True)
    n_acc = grp["shares"].transform("size")
    n_types = grp["account_type"].transform("nunique")
    accounts["account_number"] = grp.cumcount() + 1
    accounts.loc[accounts["investor_id"].isna(), "account_number"] = None
    status = pd.Series("SINGLE_ACCOUNT", index=accounts.index)
    status[(n_acc > 1) & (n_types > 1)] = "MULTIPLE_ACCOUNTS_DIFFERENT_TYPES"
    status[(n_acc > 1) & (n_types == 1)] = "MULTIPLE_ACCOUNTS_SAME_TYPE"
    accounts["duplicate_status"] = status
    per_inv = accounts.groupby(["issuer", "investor_id"]).agg(
        account_categories=("account_category", lambda x: ",".join(dict.fromkeys(",".join(x).split(",")))),
        identical_copies=("identical_copies", "sum"),
        notes=("holder_extra_lines", lambda x: " ; ".join(dict.fromkeys(v for v in x if isinstance(v, str) and v))))
    idx = pd.MultiIndex.from_frame(investors[["issuer", "investor_id"]])
    investors["account_categories"] = per_inv["account_categories"].reindex(idx).values
    investors["identical_copies"] = per_inv["identical_copies"].reindex(idx).fillna(0).astype(int).values
    investors["notes"] = per_inv["notes"].reindex(idx).replace("", None).values
    identical["investor_id"] = [key_to_id.get((i, k)) for i, k in zip(identical["issuer"], identical["holder_key"])]
    identical = identical.sort_values(["identical_group_id", "shares"], kind="stable")

    # --- month over month
    exited = pd.DataFrame(columns=INVESTOR_COLUMNS + ["holder_key"])
    if prev_investors is not None and len(prev_investors):
        prev_investors = prev_investors.rename(columns={v: k for k, v in RENAME.items()})
        prev = prev_investors.set_index(["issuer", "investor_id"])["total_shares"]
        idx = pd.MultiIndex.from_frame(investors[["issuer", "investor_id"]])
        investors["prev_month_shares"] = prev.reindex(idx).values
        investors["change_vs_prev_month"] = investors["prev_month_shares"].map(lambda v: "NEW" if pd.isna(v) else "CONTINUING")
        investors["shares_change"] = investors["total_shares"] - investors["prev_month_shares"]
        cur_keys = set(zip(investors["issuer"], investors["investor_id"]))
        # only issuers that are present this month can have "exited" investors
        issuers_now = set(investors["issuer"])
        mask = [((i, v) not in cur_keys) and (i in issuers_now)
                for i, v in zip(prev_investors["issuer"], prev_investors["investor_id"])]
        exited = prev_investors[mask].copy()
        exited["change_vs_prev_month"] = "EXITED"
        exited["prev_month_shares"] = exited["total_shares"]
        exited["shares_change"] = -exited["total_shares"]
        exited["total_shares"] = 0
        for s in summary_rows:
            sub = investors[investors["issuer"] == s["issuer"]]
            s["new_investors"] = int((sub["change_vs_prev_month"] == "NEW").sum())
            s["exited_investors"] = int((exited["issuer"] == s["issuer"]).sum())
            s["continuing_investors"] = int((sub["change_vs_prev_month"] == "CONTINUING").sum())
    else:
        investors["change_vs_prev_month"] = "FIRST MONTH"
        investors["prev_month_shares"] = None
        investors["shares_change"] = None
        notes.append("No previous month deliverable found: month-over-month changes start next month.")

    investors = investors.sort_values("total_shares", ascending=False, kind="stable")
    accounts = accounts.sort_values("shares", ascending=False, kind="stable")
    if len(exited):
        exited = exited.sort_values("prev_month_shares", ascending=False, kind="stable")
    manual_sheet = build_sheet(accounts, manual_decisions)
    sheets = {
        "Summary": pd.DataFrame(summary_rows).rename(columns=RENAME),
        "Investors": investors.reindex(columns=INVESTOR_COLUMNS).rename(columns=RENAME),
        "Original_Database": accounts.reindex(columns=ACCOUNT_COLUMNS).rename(columns=RENAME),
        "Identical_Rows": identical.reindex(columns=IDENTICAL_COLUMNS).rename(columns=RENAME),
        "Manual_Verification": manual_sheet,
        "Exited_Investors": exited.reindex(columns=INVESTOR_COLUMNS).rename(columns=RENAME),
        "Data_Dictionary": pd.DataFrame(DATA_DICTIONARY, columns=["column", "definition"]),
        "Account_Types": _account_types_sheet(),
    }
    for name in ("Investors", "Original_Database", "Identical_Rows", "Exited_Investors"):
        for col in ID_COLUMNS:
            if col in sheets[name].columns:          # numbers only, so Excel shows them as numbers
                sheets[name][col] = pd.to_numeric(sheets[name][col], errors="coerce").astype("Int64")
    return sheets, notes, registry_out, review, manual_sheet
