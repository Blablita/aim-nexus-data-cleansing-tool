"""Builds the client deliverable of one run (the clean, sellable database). A run = one dataset,
identified by its run date (YYYY-MM-DD); target: one dataset per week.

The deliverable has a FIXED layout that does not change between runs, and only
buyer-facing columns (no internal/debug columns). Sheets:

    Summary            one row per issuer: accounts, investors, control totals, changes
    Investors          one row per unique investor per issuer  (main product)
    Original_Database  every row of the original file, cleaned, linked by investor_id
    Manual_Verification rows that need a person to decide, with a status column
    Exited_Investors   investors in the previous list of the same issuer but not in this one
    Data_Dictionary    definition of every column
"""

import fnmatch
import re

import pandas as pd

from .address import account_category, classify_entity, holder_key as make_holder_key
from .collate import collate
from .cusip import normalize_cusip
from .contacts import add_client_columns, add_clean_holder_name
from .registry import assign_ids
from .verification import apply_decisions, build_sheet

# Internal name -> name shown in the client file
RENAME = {"issuer": "issuer_name", "cusip": "CUSIP", "job_number": "Job_number",
          "postal_code": "ZIP Code/Postal Code",
          "holder_extra_lines": "notes",
          "custodian": "Custodian", "type_of_account": "Type of Account",
          "entity_type": "holder_type_detail", "account_types": "account_type_code",
          "account_type": "account_type_code"}   # names as in the client's example (2026-10-15)   # extra registration lines are kept, not deleted (client 2026-10-02)  # buyers are US-based (client feedback 2026-09-29)

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
    # keys
    "record_date", "job_number", "cusip", "issuer", "investor_id",
    # main columns for the reviewer (AIM Nexus)
    "type", "entity_type", "total_shares", "n_accounts", "holder_name", "primary_contact", "secondary_contact",
    "street_address", "city", "state_province", "postal_code", "country", "is_international", "custodian",
    "type_of_account", "notes", "feedback_from_reviewer", "data_quality",
    # internal: rules, comparison, ID matching, future enhancements
    "account_types", "change_vs_previous_list", "previous_record_date", "previous_shares", "shares_change",
    "account_categories", "identical_copies", "id_match_method", "id_match_confidence", "address_changed",
]
ACCOUNT_COLUMNS = [
    "record_date", "job_number", "cusip", "issuer", "investor_id",
    "type", "entity_type", "shares", "holder_name", "primary_contact", "secondary_contact",
    "street_address", "city", "state_province", "postal_code", "country", "is_international", "custodian",
    "type_of_account", "holder_extra_lines", "feedback_from_reviewer", "data_quality",
    "account_type", "account_category", "account_number", "duplicate_status", "identical_group_id", "identical_copies",
    "parsing_notes", "original_lines", "source_file", "source_row", "verification_id",
]
# Header colors in the client file (client 2026-10-24): main columns for the reviewer in blue, the rest in grey
MAIN_COLUMNS = {"type", "holder_type_detail", "total_shares", "shares", "n_accounts", "holder_name", "primary_contact",
                "secondary_contact", "street_address", "city", "state_province", "ZIP Code/Postal Code", "country",
                "is_international", "Custodian", "Type of Account", "notes", "feedback_from_reviewer", "data_quality"}
IDENTICAL_COLUMNS = [c for c in ACCOUNT_COLUMNS if c not in ("account_number", "duplicate_status", "identical_copies",
                                                             "verification_id")] + ["validation_status", "validation_note"]
ID_COLUMNS = ["identical_group_id"]      # investor_id is a 9-digit code kept as text (client 2026-10-15)

DATA_DICTIONARY = [
    ("record_date", "Record date of the NOBO list (YYYY-MM-DD), from the file header."),
    ("Job_number", "Job number of the NOBO list request, from the file header."),
    ("CUSIP", "CUSIP of the security (Committee on Uniform Securities Identification Procedures): 9 characters, letters "
              "and digits, kept as text, never more than 9 characters. From the file header. The 9th character is a check "
              "digit and is verified. Excel damage is repaired when the result passes the check digit: scientific "
              "notation (6.5344E+210 -> 65344E206) and lost leading zeros (23135106 -> 023135106)."),
    ("CUSIP_check (Summary sheet)", "OK = valid CUSIP as received; otherwise what was repaired, or CHECK CUSIP when the "
                                    "code is not a valid 9-character CUSIP (the file should be checked before delivery)."),
    ("issuer_name", "Company whose shares are held, from the file header (or config issuer_overrides)."),
    ("investor_id", "Permanent investor ID: 9 digits, numbers only, stored as text, assigned at random (not in sequence) "
                    "the first time the investor appears and kept in the investor registry. One ID per investor per "
                    "issuer. A new address is a new ID (an investor who moves is technically a new account)."),
    ("type", "Holder type (main classification): individual (a person, a joint account, a personal trust or a person's custodial/IRA "
             "account), corporate (a company investing its own money, including family holding companies), "
             "institutional (a professional investor or intermediary: fund, asset manager, bank, broker, pension, "
             "insurance, foundation, endowment, omnibus/nominee account) or unknown. Two steps: first the account "
             "(account_category: an INSTITUTIONAL account such as a proxy service, omnibus or tax withholding "
             "account is institutional; a personal account such as an IRA, TOD, joint or UTMA account held by a "
             "person or for one person (FBO) is individual), then the holder name. See the Account_Types sheet."),
    ("primary_contact", "The person on the account, for individual holders only (empty for corporate, institutional "
                        "and unknown). The holder name without registration words and titles: 'ANNA M SAMPLE TOD' -> ANNA "
                        "M SAMPLE, 'ROBERT T SAMPLE TTEE' -> ROBERT T SAMPLE, 'MR. JOHN SAMPLE AND' -> JOHN SAMPLE. Empty when the registration names no person "
                        "(for example 'ATC AS CUST FOR IRA' or 'SMITH FAMILY TRUST')."),
    ("secondary_contact", "Second person on the account (joint holder), from the holder name ('J DOE & L DOE "
                          "TTEE', 'DUANE AND ANNE BARKER' -> DUANE BARKER and ANNE BARKER; & , AND, AND/OR and OR all "
                          "join two people) or from an extra registration line ('MARY Q EXAMPLE', 'JANE R SAMPLE JT TEN')."),
    ("Custodian", "Broker or bank holding the account as custodian, from the text before CUST / CUSTODIAN / C/F: "
                  "'CHARLES SCHWAB & CO INC CUST' -> CHARLES SCHWAB & CO INC. In Investors: all custodians of the "
                  "investor's accounts, separated by ' ; '."),
    ("Type of Account", "Account description exactly as written in the file ('ROTH CONTRIBUTORY IRA', 'IRA ROLLOVER', "
                        "'DESIGNATED BENE PLAN/TOD', 'JT TEN'). The standard code is in account_type_code."),
    ("id_match_method", "How this run's row was linked to the registry: manual (confirmed by a person), exact (same "
                        "name + street + postal code), strong (same name and the same address written differently: "
                        "same postal code or same street), new (first time seen, or a new address: a new address is a "
                        "new ID)."),
    ("id_match_confidence", "confirmed / high / medium / new, from id_match_method."),
    ("address_changed", "True if the address is written differently from the one in the registry (strong match). "
                        "An investor who moves gets a new ID, so this is not a move."),
    ("total_shares", "Shares held by the investor in this issuer, summed over all of the investor's accounts and "
                     "rounded to a whole number (some brokers report fractional shares). "
                     "The file is sorted by this column, largest first."),
    ("n_accounts", "Number of accounts (rows of the NOBO list, one per account at one broker) grouped under this "
                   "investor. total_shares is the sum of those accounts. An account can hold 0 shares on the record date."),
    ("shares", "(Original_Database sheet) Shares held in that one account, as reported in the NOBO list."),
    ("holder_name", "Name of the account holder, without '&' / 'AND' and without account-type words (TRUST, TOD, "
                    "CUST, TTEE, FAO, EXEC, JT TEN, IRA...). For a person it is the first person on the account (the "
                    "same as primary_contact); the second person is in secondary_contact. Example: 'WILLIAM SAMPLE AND "
                    "LISA C SAMPLE' -> WILLIAM SAMPLE; 'J DOE & L DOE TTEE' -> J DOE (TTEE goes to notes); 'SMITH FAMILY "
                    "TRUST U/A DTD' -> SMITH FAMILY (TRUST U/A DTD goes to notes). Company names keep '&' when it is part "
                    "of the legal name ('GLASS LEWIS & CO'). The holder line exactly as in the file is in original_lines."),
    ("notes", "First, the words removed from holder_name (for example TTEE, TOD, 'ATC AS CUST FOR IRA'). Then the registration lines that are not the name or the address and that were not moved to secondary_contact, "
              "Custodian or Type of Account (for example 'MSSB FBO ...', ATTN, C/O, trust dates, fund sub-account "
              "labels). Kept as they are for a later validation step. In Investors: the notes of all the "
              "investor's accounts, separated by ' ; '."),
    ("feedback_from_reviewer", "Quality control notes written by the reviewer during the manual review: what the "
                               "script should do differently for this kind of row (for example 'FAO should not be in "
                               "primary_contact', 'this line is a custodian'). These notes are used to improve the "
                               "rules for future runs, not to fix this file. Empty when the script creates the file."),
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
    ("change_vs_previous_list", "Compared with the previous list of the same issuer (the latest earlier record date, "
                                "from any earlier run): FIRST LIST (no earlier list of this issuer), NEW (not in the "
                                "previous list), CONTINUING (also in the previous list) or EXITED (only in Exited_Investors: "
                                "in the previous list, not in this one). Compared by issuer_name + investor_id."),
    ("previous_record_date", "Record date of the previous list of the same issuer used for the comparison."),
    ("previous_shares", "total_shares in the previous list of the same issuer (empty for NEW and FIRST LIST)."),
    ("shares_change", "total_shares minus previous_shares (positive = bought, negative = sold)."),
    ("holder_type_detail", "Detail of the holder type (used by the rules to set `type`), from keywords in the holder name: nominee_custodian, trust, company_institution, "
                    "joint_individual, individual or unknown (see the Account_Types sheet for the rules). The 4-value classification is in `type`."),
    ("account_type_code (Investors)", "Standard codes of the registration types found in any line of the investor's accounts, from the client's NOBO List Acronym Glossary (see the Account_Types sheet). The text as written in the file is in Type of Account."),
    ("account_type_code (Original_Database)", "Standard code(s) of the registration type of this one account, from keywords in all its "
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
    ("run_date (Summary sheet)", "Date of this run (one dataset). Target: one dataset per week."),
    ("compared_with_run, previous_record_date (Summary sheet)", "Run and record date of the previous list of the same "
     "issuer used for NEW / CONTINUING / EXITED. '-' when this is the first list of the issuer."),
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
    ("type", "individual", "Step 1 (account first): a personal account (account_category RETIREMENT, JOINT_OWNERSHIP, TRANSFER_ON_DEATH, CUSTODIAL_MINOR, HEALTH_EDUCATION) held by a person, by a custodian for one person, or by a company line with FBO <person>; not pooled accounts (DTC, CEDE, OMNIBUS, NOMINEES). Step 2 (holder name): holder_type_detail individual, joint_individual or trust, or a nominee_custodian name that is one person's custodial/IRA account.", "ATC AS CUST FOR IRA, FMTC CUSTODIAN - IRA, <name> CUST FOR"),
    ("type", "corporate", "A company investing its own money (company_institution without investment words), including family holding companies.", "INC, LLC, LTD, CORP, LP... without the words below; FAMILY"),
    ("type", "institutional", "A professional investor or intermediary: fund, asset manager, bank, broker, pension, insurance, foundation, endowment, omnibus/nominee. Step 1 (account first): any account whose account_category is INSTITUTIONAL (proxy service such as ISS, omnibus, tax withholding, proprietary trading, ETF/UIT) or account_type FOUNDATION, unless it is also a personal account (IRA, joint, TOD, UTMA). Step 2: the holder name keywords. A holder line that is a mailing instruction (ATTN:..., an email) is never a person: the organization on the next line decides institutional or corporate.", "FUND, ETF, CAPITAL, PARTNERS, INVESTMENT(S), ADVISORS, MANAGEMENT, ASSET, SECURITIES, BANK, PENSION, INSURANCE, FOUNDATION, FINANCIAL, BROKERS, NOMINEES, OMNIBUS, CEDE, DTC..."),
    ("type", "unknown", "holder_type_detail unknown (no name or a name that cannot be classified).", "-"),
]


def _account_types_sheet():
    from .address import ACCOUNT_CATEGORY
    g = pd.DataFrame(ACCOUNT_TYPE_GUIDE, columns=["column", "value", "meaning", "keywords searched"])
    g.insert(2, "account_category", [ACCOUNT_CATEGORY.get(v, "") if c == "account_type" else ""
                                     for c, v in zip(g["column"], g["value"])])
    g["column"] = g["column"].replace({"account_type": "account_type_code", "entity_type": "holder_type_detail"})
    status = pd.DataFrame([("duplicate_status", k, "", v, "-") for k, v in DUPLICATE_STATUS.items()],
                          columns=g.columns)
    # keyword lists are internal method: they live in the Data Requirements & Rules document, not in the client file
    return pd.concat([g, status], ignore_index=True).drop(columns=["keywords searched"])


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


def _join_unique(values):
    return " ; ".join(dict.fromkeys(v for v in values if isinstance(v, str) and v))


def _account_quality(df):
    q = pd.Series("complete", index=df.index)
    q[df["parse_status"] != "ok"] = "check address"
    q[df["shares"].isna() | (df["shares"] <= 0)] = "check shares"
    q[df["holder_name"].isna()] = "no holder name"
    return q


def build(run, results, prev_investors, collation_cfg, overrides=None, registry=None, id_overrides=None,
          manual_decisions=None):
    """
    run:             run date "YYYY-MM-DD" (older runs used "YYYY-MM")
    results:         list of dicts returned by pipeline.clean_file (one per input file)
    prev_investors:  for each issuer, the Investors rows of its previous list (any earlier run), with a
                     `previous_run` column; DataFrame or None
    Returns: dict of DataFrames (sheet name -> table) + list of notes
    """
    notes = []
    frames = []
    for r in results:
        d = r["data"].copy()
        fname = r["summary"]["file"]
        d["issuer"] = resolve_issuer(fname, r["metadata"], overrides)
        raw_cusip = r["metadata"].get("CUSIP") or (d["cusip"].dropna().iloc[0] if "cusip" in d and d["cusip"].notna().any() else None)
        # CUSIP: always text, 9 characters, check digit verified; Excel damage repaired (client 2026-10-24)
        cusip_value, cusip_note = normalize_cusip(raw_cusip)
        d["cusip"] = cusip_value
        d["cusip_check"] = cusip_note or ("OK" if cusip_value else "CHECK CUSIP: missing")
        if cusip_note:
            notes.append(f"{fname}: {cusip_note}")
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
        keys = accounts.loc[has_copies, key_cols].fillna("").astype(str).agg("|".join, axis=1)
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

    # --- client columns: type, primary/secondary contact, Custodian, Type of Account (client 2026-10-15)
    accounts = add_client_columns(accounts)
    if len(identical):
        identical = add_client_columns(identical)
    if any(vcounts.values()):
        notes.append("Manual verification: " + ", ".join(f"{k} {v:,}" for k, v in vcounts.items() if v))

    # --- investors: collate per issuer (all files of the same issuer together)
    inv_tables, summary_rows = [], []
    for issuer, sub in accounts.groupby("issuer", sort=False):
        files = sorted(sub["source_file"].unique())
        if len(files) > 1:
            notes.append(f"{issuer}: {len(files)} files in the same run ({', '.join(files)}). "
                         f"If one is an extract of the other, accounts are counted twice.")
        inv, info = collate(sub, collation_cfg, " | ".join(files), issuer)
        inv["cusip"] = sub["cusip"].iloc[0]
        inv["record_date"] = sub["record_date"].iloc[0]
        inv["job_number"] = sub["job_number"].iloc[0]
        inv["data_quality"] = inv["accounts_not_fully_parsed"].map(lambda n: "complete" if n == 0 else "check address")
        verified_keys = set(sub.loc[sub["data_quality"] == "manually verified", "holder_key"].dropna())
        inv.loc[inv["holder_key"].isin(verified_keys), "data_quality"] = "manually verified"
        inv_tables.append(inv)
        summary_rows.append({"run_date": run, "record_date": sub["record_date"].iloc[0],
                             "job_number": sub["job_number"].iloc[0],
                             "cusip": inv["cusip"].iloc[0] if len(inv) else None, "issuer": issuer,
                             "source_files": " | ".join(files),
                             "CUSIP_check": " | ".join(dict.fromkeys(sub["cusip_check"].dropna())) or None,
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
    investors, registry_out, review, id_counts = assign_ids(investors, run, registry, id_overrides)
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
    # --- holder_name without "&"/"AND" and account-type words; removed words go to notes (client 2026-10-24).
    # Done after the investor IDs, which are still matched on the original holder line.
    accounts["holder_name_raw"] = accounts["holder_name"]
    accounts = add_clean_holder_name(accounts, "holder_extra_lines")
    raw_to_clean = {(i, v, r): c for i, v, r, c in zip(accounts["issuer"], accounts["investor_id"],
                                                       accounts["holder_name_raw"], accounts["holder_name"])}
    investors["holder_name"] = [raw_to_clean.get((i, v, r), r) for i, v, r in
                                zip(investors["issuer"], investors["investor_id"], investors["holder_name"])]
    per_inv = accounts.groupby(["issuer", "investor_id"]).agg(
        account_categories=("account_category", lambda x: ",".join(dict.fromkeys(",".join(x).split(",")))),
        identical_copies=("identical_copies", "sum"),
        notes=("holder_extra_lines", _join_unique),
        type=("type", "first"),
        primary_contact=("primary_contact", lambda x: next((v for v in x if isinstance(v, str) and v), None)),
        secondary_contact=("secondary_contact", lambda x: next((v for v in x if isinstance(v, str) and v), None)),
        custodian=("custodian", _join_unique),
        type_of_account=("type_of_account", _join_unique))
    idx = pd.MultiIndex.from_frame(investors[["issuer", "investor_id"]])
    investors["account_categories"] = per_inv["account_categories"].reindex(idx).values
    investors["identical_copies"] = per_inv["identical_copies"].reindex(idx).fillna(0).astype(int).values
    for col in ("notes", "type", "primary_contact", "secondary_contact", "custodian", "type_of_account"):
        investors[col] = per_inv[col].reindex(idx).replace("", None).values
    identical["investor_id"] = [key_to_id.get((i, k)) for i, k in zip(identical["issuer"], identical["holder_key"])]
    identical = add_clean_holder_name(identical, "holder_extra_lines")
    identical = identical.sort_values(["identical_group_id", "shares"], kind="stable")

    # --- comparison with the previous list of the SAME issuer (it can come from any earlier run: with one
    # dataset per week, the previous list of an issuer is often several runs back)
    exited = pd.DataFrame(columns=INVESTOR_COLUMNS + ["holder_key"])
    investors["change_vs_previous_list"] = "FIRST LIST"
    investors["previous_record_date"] = None
    investors["previous_shares"] = None
    investors["shares_change"] = None
    for s in summary_rows:
        s.update(compared_with_run="-", previous_record_date="-", new_investors=None, continuing_investors=None,
                 exited_investors=None)
    if prev_investors is not None and len(prev_investors):
        back = {v: k for k, v in RENAME.items()}
        back["account_type_code"] = "account_types"          # Investors level
        prev_investors = prev_investors.rename(columns=back)
        prev_investors = prev_investors[prev_investors["issuer"].isin(set(investors["issuer"]))]
    if prev_investors is not None and len(prev_investors):
        prev = prev_investors.set_index(["issuer", "investor_id"])["total_shares"]
        prev_date = prev_investors.groupby("issuer")["record_date"].first()
        has_prev = investors["issuer"].isin(set(prev_investors["issuer"]))
        idx = pd.MultiIndex.from_frame(investors.loc[has_prev, ["issuer", "investor_id"]])
        investors.loc[has_prev, "previous_shares"] = prev.reindex(idx).values
        investors.loc[has_prev, "previous_record_date"] = investors.loc[has_prev, "issuer"].map(prev_date)
        investors.loc[has_prev, "change_vs_previous_list"] = investors.loc[has_prev, "previous_shares"].map(
            lambda v: "NEW" if pd.isna(v) else "CONTINUING")
        investors["previous_shares"] = pd.to_numeric(investors["previous_shares"], errors="coerce")
        investors["shares_change"] = investors["total_shares"] - investors["previous_shares"]
        cur_keys = set(zip(investors["issuer"], investors["investor_id"]))
        mask = [(i, v) not in cur_keys for i, v in zip(prev_investors["issuer"], prev_investors["investor_id"])]
        exited = prev_investors.loc[pd.Series(mask, index=prev_investors.index, dtype=bool)].copy()
        exited["change_vs_previous_list"] = "EXITED"
        exited["previous_record_date"] = exited["record_date"]
        exited["previous_shares"] = exited["total_shares"]
        exited["shares_change"] = -exited["total_shares"]
        exited["total_shares"] = 0
        prev_run = prev_investors.groupby("issuer")["previous_run"].first() if "previous_run" in prev_investors else {}
        for s in summary_rows:
            if s["issuer"] not in prev_date.index:
                continue
            sub = investors[investors["issuer"] == s["issuer"]]
            s["compared_with_run"] = prev_run.get(s["issuer"], "-")
            s["previous_record_date"] = prev_date[s["issuer"]]
            s["new_investors"] = int((sub["change_vs_previous_list"] == "NEW").sum())
            s["exited_investors"] = int((exited["issuer"] == s["issuer"]).sum())
            s["continuing_investors"] = int((sub["change_vs_previous_list"] == "CONTINUING").sum())
    first = [s["issuer"] for s in summary_rows if s["compared_with_run"] == "-"]
    if first:
        notes.append("First list of " + ", ".join(first) + ": changes are shown from the next list of the same issuer.")

    investors = investors.sort_values("total_shares", ascending=False, kind="stable")
    accounts = accounts.sort_values("shares", ascending=False, kind="stable")
    if len(exited):
        exited = exited.sort_values("previous_shares", ascending=False, kind="stable")
    if len(exited):
        exited = add_clean_holder_name(exited, "notes")
    # the reviewer sees the holder line as it is in the file
    manual_sheet = build_sheet(accounts.assign(holder_name=accounts["holder_name_raw"]), manual_decisions)
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
        if "investor_id" in sheets[name].columns:     # 9-digit text code
            sheets[name]["investor_id"] = sheets[name]["investor_id"].map(
                lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
        for col in ID_COLUMNS:
            if col in sheets[name].columns:          # numbers only, so Excel shows them as numbers
                sheets[name][col] = pd.to_numeric(sheets[name][col], errors="coerce").astype("Int64")
    # share totals as whole numbers, no decimals (client 2026-10-17). Some brokers report fractional shares
    # (144359.96491); the total is rounded to the nearest share (.5 up). The account-level `shares` column keeps
    # the value exactly as reported, and the control totals are checked on the exact values.
    for name, cols in (("Summary", ("total_shares", "shares_in_identical_rows", "source_total_shares")),
                       ("Investors", ("total_shares", "previous_shares", "shares_change")),
                       ("Exited_Investors", ("total_shares", "previous_shares", "shares_change"))):
        for col in cols:
            if col in sheets[name].columns:
                sheets[name][col] = _whole_shares(sheets[name][col])
    return sheets, notes, registry_out, review, manual_sheet


def _whole_shares(values):
    """144359.96491 -> 144360, -2.5 -> -3 (nearest whole share, .5 away from zero); empty stays empty."""
    v = pd.to_numeric(values, errors="coerce")
    return ((v.abs() + 0.5) // 1 * v.apply(lambda x: -1 if x < 0 else 1)).astype("Int64")
