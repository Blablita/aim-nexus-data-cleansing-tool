"""Parsing of free-text name and address lines (typical NOBO/OBO list format).

Each row has 1 to 7 unstructured lines, for example:

    ["ARTHUR F FULLER", "LATIFAH YVETTE FULLER", "3519 E LOOKOUT DR", "COEUR D ALENE ID 83815-7905"]
      holder             extra line (co-holder)     street                city + state + ZIP

    ["42", "Grubetstrasse", "0", "Aichach", "Germany"]      <- international "app" format: NO name

Parsing goes from the BOTTOM UP, where the structure is most predictable:
    1) country (last line, if it is a known country)
    2) locality line (city / state / postal code)
    3) street (lines starting with a number, PO BOX, APT, SUITE...)
    4) whatever is left on top = holder name + registration lines (IRA, TOD, custodian...)

Each row gets `parse_status` (ok / partial / unparsed) and `parse_notes`
explaining what was assumed, so the result can be audited.
"""

import re
import pandas as pd

from .geo_reference import US_STATES, CA_PROVINCES, ISO2_COUNTRIES, USPS_ABBREVIATIONS, match_country

# ---------------------------------------------------------------- patterns
RE_US = re.compile(r"^(?P<city>.*?)[\s,]*\b(?P<state>[A-Z]{2})\.?[\s,]+(?P<zip>\d{5})(?:[- ]?(?P<zip4>\d{4}))?-?$")
RE_CA = re.compile(r"^(?P<city>.*?)[\s,]*\b(?P<prov>[A-Z]{2})\.?[\s,]+(?P<pc>[A-Z]\d[A-Z]\s?\d[A-Z]\d)$")
RE_CA_PC_ONLY = re.compile(r"^[A-Z]\d[A-Z]\s?\d[A-Z]\d$")
RE_ZIP_FIRST = re.compile(r"^(?P<zip>\d{5})(?:[- ]?(?P<zip4>\d{4}))?\s+(?P<state>[A-Z]{2})$")
RE_ZIP_ONLY = re.compile(r"^\d{5}(?:-\d{4})?-?$")
RE_US_COUNTRY_ZIP = re.compile(r"^(?:US|USA)\s+(\d{5}(?:-\d{4})?)$")
RE_TRAILING_US = re.compile(r"^(.*\S)\s+(?:US|USA|U\.S\.A\.?)$")
RE_ISO_TAIL = re.compile(r"(?:^|\s)(?P<code>[A-Z]{2})(?P<pc>\s+[A-Z0-9-]{3,8})?$")
RE_POSTAL_GENERIC = re.compile(r"\b(\d{4,6}(?:-\d{3,4})?|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b")
RE_STREET_START = re.compile(
    r"^(\d|#|P\.?\s?O\.?\s?BOX|POB\b|BOX\b|APT\b|APARTMENT|STE\b|SUITE|UNIT\b|FLAT\b|FL\b|FLOOR|"
    r"RR\s?\d|HC\s?\d|PMB\b|ONE\b|TWO\b|THREE\b|FOUR\b|FIVE\b|TEN\b|BLDG|BUILDING)", re.I)

# Words showing the holder is NOT an individual (checked on line 1 only)
ENTITY_RULES = [
    ("nominee_custodian", r"\bNOMINEES?\b|NOMINEES|\bOMNI(BUS)?\b|\bCEDE\b|\bFBO\b|\bCUST(ODIAN)?\b|\bC/F\b"),
    ("trust", r"\bTRUST\b|\bTR\b|\bTTEES?\b|\bTRUSTEES?\b|\bTST\b|\bU/A\b|\bUAD\b|\bU/T/A\b|\bESTATE\b|\bREV\s+TR"),
    ("company_institution",
     r"\bL\.?L\.?C\b|\bINC\b|\bCORP\b|\bCORPORATION\b|\bCO\b|\bLTD\b|\bLIMITED\b|\bL\.?P\b|\bLLP\b|\bPLC\b|"
     r"\bGMBH\b|\bS\.?A\b|\bAG\b|\bB\.?V\b|\bN\.?V\b|\bFUND\b|\bFUNDS\b|\bETF\b|\bCAPITAL\b|\bPARTNERS\b|"
     r"\bHOLDINGS?\b|\bINVESTMENTS?\b|\bADVISORS?\b|\bADVISERS?\b|\bMANAGEMENT\b|\bSECURITIES\b|\bBANK\b|"
     r"\bFOUNDATION\b|\bFDNTN\b|\bASSOCIATION\b|\bCOUNTY\b|\bUNIVERSITY\b|\bGROUP\b|\bPENSION\b|\bPLANS?\b|"
     r"\bFINANCIAL\b|\bVENTURES?\b|\bENTERPRISES?\b|\bSOCIETY\b|\bCHURCH\b|\bCOMPANY\b|\bAQR\b|\bLLC\b"),
]
ENTITY_RULES = [(label, re.compile(rx, re.I)) for label, rx in ENTITY_RULES]

# Account registration types (searched on ALL lines of the registration).
# Based on common brokerage registration types (individual/joint, retirement, trust/estate,
# custodial, education) and the abbreviations found in the NOBO files.
ACCOUNT_TYPES = [
    ("ROTH_IRA", r"\bROTH\b"),
    ("ROLLOVER_IRA", r"ROLLOVER|\bR/O\b|\bRRA\b"),
    ("SEP_IRA", r"\bSEP\b"),
    ("SIMPLE_IRA", r"\bSIMPLE\b"),
    ("INHERITED_IRA", r"INHERIT|BENEF(ICIARY)?\s+IRA|\bBENE\s+IRA|\bDECD\b|DECEASED|\bBDA\b"),
    ("TRADITIONAL_IRA", r"\bIRA\b"),                       # generic IRA, dropped if a specific IRA type is found
    ("EMPLOYER_PLAN", r"401\s?\(?K|403\s?\(?B|KEOGH|PROFIT SHARING|PENSION PLAN|\bPSP\b|\bFRP\b|P/ADM|\bTSP\b|"
                      r"\bNGSP\b|SAVINGS\s*/?\s*PLAN|THRIFT"),
    ("HSA", r"\bHSA\b"),
    ("EDUCATION_529_ESA", r"\b529\b|COVERDELL|\bESA\b"),
    ("TOD_POD", r"\bTOD\b|DESIGNATED BENE|\bPOD\b|STA RULES"),
    ("JOINT_WROS", r"JTWROS|\bJT\b|WROS"),
    ("TENANTS_IN_COMMON", r"TEN COM|TENANTS IN COMMON|\bTIC\b"),
    ("TENANTS_BY_ENTIRETY", r"TEN ENT|TENANTS BY|\bA?TBE\b"),
    ("COMMUNITY_PROPERTY", r"COMM(UNITY)?\s+PROP"),
    ("CUSTODIAL_MINOR", r"UTMA|UGMA|UNTIL AGE"),
    ("TRUST", r"\bTRUST\b|\bTTEES?\b|\bTRUSTEES?\b|\bU/A\b|\bUAD\b|\bU/A/D\b|\bDTD\b|\bREV\s+TR|\bIRR\s+TR|"
              r"\bTRS\b|\bTRU\b|\bTRST\b|\bU/?DEC\b|CREDIT SHELTER"),
    ("ESTATE", r"ESTATE OF|\bEST OF\b|\bEXECUTOR\b|\bEX(?=\s*\||\s*$)"),
    ("FOUNDATION", r"\bFDN\b|\bFDNTN\b|FOUNDATION"),
    ("CANADA_REGISTERED", r"\bRRSP\b|\bRRIF\b|\bTFSA\b|\bLIRA\b|\bRESP\b"),
    ("PLEDGED", r"PLEDGE"),
    ("OMNIBUS", r"\bOMNI(BUS)?\b"),
    ("SECURITIES_LENDING", r"(?<!NON-)(?<!NON )\bFPSL\b|\bSLFP\b|SEC(URITIES)?\s+LEND|STOCK\s+BORROW|\bFPL\b|FULLY PAID LEND"),
    ("TAX_WITHHOLDING", r"\bW/H\b|\bUSWT\b|WITHHOLDING"),
    ("PROXY_AGENT", r"\bISS/|ISSGOVERNANCE|\bPEID\b|GLASS LEWIS|\bPVA\b|\bRMG/|PROXY\s?EDGE"),
    ("PROPRIETARY_TRADING", r"PROP\s+TRADER|DELTA\s*1\b|NON[\s-]*FLIP"),
    ("INDEX_FUND_ETF", r"\bETF\b|\bUIT\b|\bUCITS\b|\bMSCI\b|\bIMI\b"),
]
ACCOUNT_TYPES = [(label, re.compile(rx, re.I)) for label, rx in ACCOUNT_TYPES]
SPECIFIC_IRA = {"ROTH_IRA", "ROLLOVER_IRA", "SEP_IRA", "SIMPLE_IRA", "INHERITED_IRA"}

# Broad group of each account type (client glossary "NOBO List Acronym Glossary", 2026-09-28)
ACCOUNT_CATEGORY = {
    "ROTH_IRA": "RETIREMENT", "ROLLOVER_IRA": "RETIREMENT", "SEP_IRA": "RETIREMENT", "SIMPLE_IRA": "RETIREMENT",
    "INHERITED_IRA": "RETIREMENT", "TRADITIONAL_IRA": "RETIREMENT", "EMPLOYER_PLAN": "RETIREMENT",
    "CANADA_REGISTERED": "RETIREMENT",
    "HSA": "HEALTH_EDUCATION", "EDUCATION_529_ESA": "HEALTH_EDUCATION",
    "JOINT_WROS": "JOINT_OWNERSHIP", "TENANTS_IN_COMMON": "JOINT_OWNERSHIP", "TENANTS_BY_ENTIRETY": "JOINT_OWNERSHIP",
    "COMMUNITY_PROPERTY": "JOINT_OWNERSHIP",
    "TOD_POD": "TRANSFER_ON_DEATH",
    "CUSTODIAL_MINOR": "CUSTODIAL_MINOR",
    "TRUST": "TRUST_ESTATE", "ESTATE": "TRUST_ESTATE", "FOUNDATION": "TRUST_ESTATE",
    "PLEDGED": "LENDING_COLLATERAL", "SECURITIES_LENDING": "LENDING_COLLATERAL",
    "OMNIBUS": "INSTITUTIONAL", "TAX_WITHHOLDING": "INSTITUTIONAL", "PROXY_AGENT": "INSTITUTIONAL",
    "PROPRIETARY_TRADING": "INSTITUTIONAL", "INDEX_FUND_ETF": "INSTITUTIONAL",
    "NOT_SPECIFIED": "STANDARD",
}


def account_category(account_type_value):
    """'ROTH_IRA,TOD_POD' -> 'RETIREMENT,TRANSFER_ON_DEATH' (unique, in order)."""
    out = []
    for t in str(account_type_value or "NOT_SPECIFIED").split(","):
        c = ACCOUNT_CATEGORY.get(t.strip(), "STANDARD")
        if c not in out:
            out.append(c)
    return ",".join(out)

# Line 1 that names the custodian/trustee instead of the owner (e.g. "FMT CO CUST IRA ROLLOVER",
# "CITI RETIREMENT ACCOUNT", "PTC CUST IRA FBO"). The owner is then on the "FBO <name>" line.
RE_CUSTODIAN_FIRST = re.compile(
    r"\bCUST\b|CUSTODIAN|\bC/F\b|\bTTEE\b|RETIREMENT ACCOUNT|\bFMTC?\b|\bFPT\b|\bFPO\b|\bFBO\b", re.I)
RE_FBO = re.compile(r"\bFBO\b\s*(?P<name>.*)$", re.I)
TRAILING_ACCOUNT_WORDS = {"IRA", "ROTH", "ROLLOVER", "ACCOUNT", "ACCT", "SEP", "SIMPLE", "TOD", "R/O", "BDA",
                          "CONTRIBUTORY", "TRADITIONAL", "A/C", "CONT", "CONV", "CONVERSION", "BENE"}


def _owner_from_fbo(name_lines):
    """Returns (owner_name, index_of_fbo_line) when the registration is 'custodian FBO owner'."""
    if not name_lines or not RE_CUSTODIAN_FIRST.search(name_lines[0]):
        return None, None
    for i, line in enumerate(name_lines):
        m = RE_FBO.search(line)
        if not m:
            continue
        owner = m.group("name").strip(" ,-")
        if not owner and i + 1 < len(name_lines):        # "PTC CUST IRA FBO" | "JOHN SMITH"
            owner, i = name_lines[i + 1], i + 1
        words = owner.upper().split()
        while words and words[-1] in TRAILING_ACCOUNT_WORDS:
            words.pop()
        owner = " ".join(words)
        if sum(ch.isalpha() for ch in owner) >= 3:
            return owner, i
    return None, None


JUNK_CHARS = re.compile(r"^[>\*\s]+|[\*]+|\s+$")


def _clean_line(x):
    if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NA:
        return ""
    return " ".join(JUNK_CHARS.sub(" ", str(x)).split())


def _looks_like_name(line):
    """A name line has letters and does not look like a street."""
    letters = sum(ch.isalpha() for ch in line)
    return letters >= 3 and not RE_STREET_START.match(line)


def classify_entity(name, extra_lines):
    if not name:
        return "unknown"
    for label, rx in ENTITY_RULES:
        if rx.search(name):
            return label
    blob = " ".join([name] + extra_lines)
    if re.search(r"&\s*$|\bJT\b|WROS", name) or " & " in name:
        return "joint_individual"
    if len(re.findall(r"[A-Za-z]{2,}", name)) >= 2:
        return "individual"
    return "unknown"


def account_type(lines):
    blob = " | ".join(lines).upper()
    found = [label for label, rx in ACCOUNT_TYPES if rx.search(blob)]
    if "TRADITIONAL_IRA" in found and SPECIFIC_IRA & set(found):
        found.remove("TRADITIONAL_IRA")
    return ",".join(found) if found else "NOT_SPECIFIED"


def standardize_street(street, country):
    """USPS Pub. 28 abbreviations for US addresses: '4700 TIETON DRIVE, SUITE C' -> '4700 TIETON DR, STE C'."""
    if not street or country != "UNITED STATES":
        return street
    s = re.sub(r"\bP\.?\s?O\.?\s+BOX\b", "PO BOX", street.upper())
    return re.sub(r"\b[A-Z]+\b", lambda m: USPS_ABBREVIATIONS.get(m.group(0), m.group(0)), s)


def _normalize_tail(work, notes):
    """Rewrites the last address lines into 'CITY ST ZIP' form when they come in other layouts:
         'YAKIMA' | '98908 WA US'          -> 'YAKIMA' | 'WA 98908'   (ZIP first, trailing US)
         'HONOLULU HI 96817' | 'US 96817'  -> drops the repeated ZIP line
         'ROCKVILLE' | 'MD' | '20850-5774' -> 'ROCKVILLE' | 'MD 20850-5774'
         'Burlington ON' | 'CANADA' | 'L7T 1A4' -> 'BURLINGTON ON L7T 1A4'
    """
    w = list(work)
    if not w:
        return w
    m = RE_US_COUNTRY_ZIP.match(w[-1].upper())
    if m:
        if len(w) >= 2 and m.group(1)[:5] in w[-2]:
            w = w[:-1]
        else:
            w[-1] = m.group(1)
        notes.append("'US <zip>' line normalized")
    m = RE_TRAILING_US.match(w[-1].upper())
    if m and re.search(r"\d{5}|\b[A-Z]{2}$", m.group(1)):
        w[-1] = m.group(1)
    m = RE_ZIP_FIRST.match(w[-1].upper())
    if m and m.group("state") in US_STATES:
        w[-1] = f"{m.group('state')} {m.group('zip')}" + (f"-{m.group('zip4')}" if m.group("zip4") else "")
        notes.append("ZIP written before state; reordered")
    last = w[-1].upper()
    if (RE_ZIP_ONLY.match(last) or RE_CA_PC_ONLY.match(last)) and len(w) >= 2:
        if w[-2].upper() == "CANADA" and len(w) >= 3:
            w = w[:-2] + [w[-1]]
        prev = re.sub(r"\s+CANADA$", "", w[-2].upper())
        if not re.search(r"\d", prev) and not RE_STREET_START.match(prev):
            w = w[:-2] + [f"{prev} {last}"]
            notes.append("postal code on its own line; joined with the line above")
    w[-1] = re.sub(r"\bCANADA\s+(?=[A-Z]\d[A-Z])", "", w[-1], flags=re.I)
    return w


def parse_lines(raw_lines):
    """Parses a list of raw lines. Returns a dict with the standardized fields."""
    lines = [_clean_line(x) for x in raw_lines]
    lines = [x for x in lines if x]
    notes = []
    out = {"holder_name": None, "holder_extra_lines": None, "street_address": None,
           "city": None, "state_province": None, "postal_code": None, "country": None}
    if not lines:
        return {**out, "parse_status": "unparsed", "parse_notes": "row has no name and no address",
                "entity_type": "unknown", "account_type": "NOT_SPECIFIED"}

    # --- international "app" format: [number, street, (street2), "0", city, country]
    app_format = "0" in lines[1:] and not _looks_like_name(lines[0])
    work = list(lines)

    work = [x for x in work if x != "0"]          # "0" = empty field in the app format
    work = _normalize_tail(work, notes)

    # 1) country ---------------------------------------------------------
    country, rest = match_country(work[-1])
    if not country:
        # 2-letter country code at the end: "1080 BRUSSELS BE", "HONG KONG HK", "LIMASSOL CY 3076"
        tail = work[-1].upper()
        m = RE_ISO_TAIL.search(tail)
        if m and m.group("code") in ISO2_COUNTRIES and not RE_US.match(tail) and not RE_CA.match(tail):
            country = ISO2_COUNTRIES[m.group("code")]
            rest = (tail[:m.start()] + (m.group("pc") or "")).strip()
            notes.append(f"country from 2-letter code '{m.group('code')}'")
    if country:
        work = work[:-1] + ([rest] if rest else [])
    if not work:
        out["country"] = country
        return {**out, "parse_status": "unparsed", "parse_notes": "country only",
                "entity_type": "unknown", "account_type": "NOT_SPECIFIED"}

    # 2) locality line ---------------------------------------------------
    loc = work[-1].upper()
    used = 1
    m_us, m_ca = RE_US.match(loc), RE_CA.match(loc)
    if m_us and m_us.group("state") in US_STATES and country in (None, "UNITED STATES"):
        out.update(city=m_us.group("city").strip(" ,") or None, state_province=m_us.group("state"),
                   postal_code=m_us.group("zip") + (f"-{m_us.group('zip4')}" if m_us.group("zip4") else ""))
        country = country or "UNITED STATES"
    elif m_ca and m_ca.group("prov") in CA_PROVINCES and country in (None, "CANADA"):
        out.update(city=m_ca.group("city").strip(" ,") or None, state_province=m_ca.group("prov"),
                   postal_code=m_ca.group("pc").replace(" ", ""))
        country = country or "CANADA"
    elif RE_CA_PC_ONLY.match(loc) and len(work) >= 2 and work[-2].upper() in CA_PROVINCES:
        # "... | TORONTO | ON | M5V 2T6" : province and postal code on separate lines
        out.update(state_province=work[-2].upper(), postal_code=loc.replace(" ", ""))
        used = 2
        if len(work) >= 3 and not RE_STREET_START.match(work[-3]):
            out["city"] = work[-3]
            used = 3
        country = country or "CANADA"
        notes.append("province/postal code on separate lines")
    elif RE_ZIP_ONLY.match(loc) and country in (None, "UNITED STATES"):
        out["postal_code"] = loc.rstrip("-")
        country = "UNITED STATES"
        notes.append("only a ZIP code; city and state missing")
    elif country:
        # international: locality is free text; try to extract the postal code
        pc = RE_POSTAL_GENERIC.search(loc)
        if pc:
            out["postal_code"] = pc.group(1)
            loc_city = (loc[:pc.start()] + loc[pc.end():]).strip(" ,-")
        else:
            loc_city = loc
        out["city"] = loc_city.title() if loc_city else None
    else:
        notes.append(f"last line not recognized as a locality: '{work[-1]}'")

    # city on the previous line (e.g. "GREENWICH" | "CT 06830-")
    if out["state_province"] and not out["city"] and len(work) > used:
        prev = work[-used - 1]
        if not RE_STREET_START.match(prev) and not re.search(r"\d", prev):
            out["city"] = prev.upper()
            used += 1
            notes.append("city taken from the previous line")

    out["country"] = country
    upper_block = work[:-used] if (out["state_province"] or country) else work

    # 3) street vs. name -------------------------------------------------
    if app_format:
        out["street_address"] = " ".join(upper_block) or None
        name_lines = []
        notes.append("international app format: the file has no holder name")
    else:
        first_street = next((i for i, x in enumerate(upper_block) if i > 0 and RE_STREET_START.match(x)), None)
        if first_street is None:
            # no line with a number: assume the street is the last line of the block
            first_street = len(upper_block) - 1 if len(upper_block) >= 2 else len(upper_block)
            if len(upper_block) >= 2:
                notes.append("street has no number; assumed the line right above the locality")
        name_lines = upper_block[:first_street]
        out["street_address"] = ", ".join(upper_block[first_street:]) or None

    out["street_address"] = standardize_street(out["street_address"], out["country"])
    name = name_lines[0] if name_lines else None
    extra = name_lines[1:]
    owner, fbo_idx = _owner_from_fbo(name_lines)
    if owner:
        extra = [x for j, x in enumerate(name_lines) if j != fbo_idx or j == 0]
        name = owner
        notes.append("owner taken from the 'FBO' line (line 1 is the custodian)")
    if name and not _looks_like_name(name):
        notes.append(f"line 1 does not look like a name: '{name}'")
    out["holder_name"] = name.upper() if name else None
    out["holder_extra_lines"] = " | ".join(extra) if extra else None

    # 4) parse status ----------------------------------------------------
    us_or_ca = out["country"] in ("UNITED STATES", "CANADA")
    if (out["holder_name"] and out["country"] and (out["city"] or out["postal_code"]) and out["street_address"]
            and (out["state_province"] or not us_or_ca)):
        status = "ok"
    elif out["country"] or out["postal_code"]:
        status = "partial"
    else:
        status = "unparsed"
    if not out["holder_name"]:
        notes.append("no holder name")

    return {**out, "parse_status": status, "parse_notes": "; ".join(notes) or None,
            "entity_type": classify_entity(out["holder_name"], extra),
            "account_type": account_type(lines)}


def _normalize_name(name):
    if not name:
        return ""
    n = re.sub(r"[^A-Z0-9& ]", " ", name.upper())
    n = re.sub(r"\b(TOD|JT TEN|JTWROS|WROS)\b", " ", n)
    return " ".join(n.split())


def holder_key(row):
    """Key used to consolidate accounts of the same holder: normalized name + ZIP5 (or country+city).
    It is conservative: the same name with a different postal code is NOT merged (could be two people)."""
    name = _normalize_name(row["holder_name"])
    if not name:
        return None
    if row["country"] == "UNITED STATES" and row["postal_code"]:
        loc = row["postal_code"][:5]
    else:
        loc = f"{row['country'] or ''}|{(row['postal_code'] or row['city'] or '').upper()}"
    return f"{name}|{loc}"


def parse_addresses(df, cfg):
    """
    Applies the parsing to the whole DataFrame.
    cfg (address_parsing section of the config):
        line_columns: list of columns with the name/address lines, in order
                      (if not given, columns whose name contains "line" are used)
        home_country: "home" country used to flag international holders (default UNITED STATES)

    Returns: (df_with_new_columns, flagged_index:set, log:list[dict])
    """
    cols = cfg.get("line_columns") or [c for c in df.columns if "line" in c.lower()]
    cols = [c for c in cols if c in df.columns]
    home = cfg.get("home_country", "UNITED STATES").upper()

    parsed = pd.DataFrame([parse_lines(r) for r in df[cols].values.tolist()], index=df.index)
    parsed["is_international"] = parsed["country"].map(lambda p: None if p is None else p != home)
    parsed["holder_key"] = parsed.apply(holder_key, axis=1)
    out = pd.concat([df, parsed], axis=1)

    flagged = set(parsed.index[(parsed["parse_status"] != "ok") | parsed["holder_name"].isna()])
    log = [{"step": "address_parsing", "column": "parse_status", "detail": f"{k}: {v} rows"}
           for k, v in parsed["parse_status"].value_counts().items()]
    log += [{"step": "address_parsing", "column": "entity_type", "detail": f"{k}: {v} rows"}
            for k, v in parsed["entity_type"].value_counts().items()]
    log.append({"step": "address_parsing", "column": "is_international",
                "detail": f"international: {int((parsed['is_international'] == True).sum())}; "
                          f"unknown country: {int(parsed['is_international'].isna().sum())}"})
    return out, flagged, log
