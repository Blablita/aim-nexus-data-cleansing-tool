"""Client columns added in Oct 2026 (Manuj's before/after example, 2026-10-15):

    type               individual / corporate / institutional / unknown (from entity_type + the holder name)
    primary_contact    the person on the account (individual holders only), cleaned of registration words
    secondary_contact  the second person on the account (joint holder), from the holder name or an extra line
    Custodian          broker/bank that holds the account as custodian ("CHARLES SCHWAB & CO INC CUST ...")
    Type of Account    account description exactly as written in the file ("ROTH CONTRIBUTORY IRA")
    holder_extra_lines whatever is left of the extra registration lines after the split (shown as `notes`)

Lines that do not fit a rule are not changed: they stay in holder_extra_lines ("MSSB FBO ...",
"VBN GENERAL MEETINGS", fund sub-account labels, trust dates...). Nothing is lost: the full
original lines are kept in `original_lines`.
"""

import re
from collections import Counter

# ------------------------------------------------------------------ type
# Words that mark a professional investor managing money (institutional). Checked on company names.
RE_INSTITUTIONAL = re.compile(
    r"\b(FUNDS?|ETF|CAPITAL|PARTNERS|INVESTMENTS?|INVSTMNT|INVESTORS?|ADVISORS?|ADVISERS?|ADVISORY|MANAGEMENT|"
    r"MNGMNT|MGMT|MGT|ASSET|ASSETS|SECURITIES|BANK|BANKING|BANCO|BANQUE|BANCA|SPARKASSE|PENSION|RETIREMENT|INSURANCE|"
    r"ASSURANCE|LIFE|FOUNDATION|FDNTN|FDN|ENDOWMENT|UNIVERSITY|COLLEGE|SCHOOL|CHURCH|SOCIETY|ASSOCIATION|"
    r"FINANCIAL|FINANCE|BROKERS?|BROKERAGE|NOMINEES?|CUSTODY|CLEARING|WEALTH|TRADING|MARKETS|VENTURES?|"
    r"EQUITY|EQUITIES|PORTFOLIO|HEDGE|ISS|GLASS LEWIS|PROXY|SOCIETE GENERALE|HSBC|CREDIT|EUROBANK|STATE STREET|"
    r"VANGUARD|BLACKROCK|FIDELITY|SCHWAB|MORGAN STANLEY|GOLDMAN|JPMORGAN|CITI|UBS|BARCLAYS|COUNTY|CITY OF)\b")
RE_FAMILY = re.compile(r"\bFAMILY\b")
# nominee_custodian names that are really one person's retirement/custodial account: "ATC AS CUST FOR IRA",
# "JOHN PAUL SAMPLE CUST FOR", "FMTC CUSTODIAN - IRA BDA"
RE_PERSONAL_CUSTODY = re.compile(r"\bCUST(ODIAN)?\b|\bC/F\b|\bFBO\b|\bIRA\b|\bROTH\b|\bUTMA\b|\bUGMA\b")
RE_POOLED = re.compile(r"\bOMNI(BUS)?\b|\bNOMINEES?\b|\bCEDE\b|\bDTC\b|\bCLIENTS?\b")
# holder line that is a mailing instruction or a proxy service code, not a person:
# "ATTN:LOA@ISSGOVERNANCE.COM", "ATTN:EMAIL", "ISS/152/GSAM", "RMG/CLARIVEST/3692"
RE_NOT_PERSON_HOLDER = re.compile(r"^\s*ATTN\b|@|^\s*(?:ISS|RMG)\s*/")
# account categories that belong to a person; an institutional category next to them is not used for `type`
# (for example ROTH_IRA + OMNIBUS read from an address)
PERSONAL_CATEGORIES = {"RETIREMENT", "HEALTH_EDUCATION", "JOINT_OWNERSHIP", "TRANSFER_ON_DEATH", "CUSTODIAL_MINOR"}


RE_FBO_PERSON = re.compile(r"\bFBO\s+(?P<name>[A-Z][A-Z .'-]{2,})")


def holder_type(entity_type, holder_name, account_category=None, extra=None, original_lines=None):
    """individual / corporate / institutional / unknown (draft rules, to confirm with the client).

    Step 1, the account first (account_category = account_type mapped with the client's NOBO glossary):
      a. INSTITUTIONAL account (proxy service, omnibus, tax withholding, proprietary trading, ETF/UIT, foundation)
         -> institutional, unless the account is also a personal one (IRA, joint, TOD, UTMA, HSA)
      b. personal account (RETIREMENT, JOINT_OWNERSHIP, TRANSFER_ON_DEATH, CUSTODIAL_MINOR, HEALTH_EDUCATION)
         -> individual, when the holder is a person, a custodian holding for one person, or a company line
         with "FBO <person>". Pooled accounts (DTC, CEDE, OMNIBUS, NOMINEES) and companies without FBO keep step 2.
    Step 2, the holder name (entity_type + keywords), as before."""
    name = holder_name.upper() if isinstance(holder_name, str) else ""
    cats = set(str(account_category or "").split(","))
    blob = " | ".join(x.upper() for x in (original_lines, extra) if isinstance(x, str))
    personal = bool(cats & PERSONAL_CATEGORIES)
    # --- step 1: the account
    if not personal and ("INSTITUTIONAL" in cats or "FOUNDATION" in str(account_category or "")):
        return "institutional"
    if personal and name and not RE_POOLED.search(name) and not RE_NOT_PERSON_HOLDER.search(name):
        if entity_type in ("individual", "joint_individual", "trust", "unknown"):
            return "individual"
        if entity_type == "nominee_custodian":
            return "individual"            # "FMTC CUSTODIAN - ROTH IRA | FBO <person>", "ATC AS CUST FOR IRA"
        if entity_type == "company_institution" and RE_FBO_PERSON.search(blob):
            return "individual"            # "TRUIST INVESTMENT SERVICES INC | TRADITIONAL IRA | FBO <person>"
    # --- step 2: the holder name
    if name and RE_NOT_PERSON_HOLDER.search(name):
        # the organization is named on the next lines ("ATTN:EMAIL | SAXO BANK AS-HELLERUP HEAD OFFICE")
        return "institutional" if RE_INSTITUTIONAL.search(name + " " + blob) else "corporate"
    if entity_type in ("individual", "joint_individual", "trust"):
        return "individual"
    if entity_type == "nominee_custodian":
        if RE_PERSONAL_CUSTODY.search(name) and not RE_POOLED.search(name):
            return "individual"
        return "institutional"
    if entity_type == "company_institution":
        if RE_FAMILY.search(name):
            return "corporate"             # family holding company / family LP
        return "institutional" if RE_INSTITUTIONAL.search(name) else "corporate"
    return "unknown"


def fbo_person(original_lines):
    """'... | FBO LARRY TUCKER | ...' -> LARRY TUCKER (the person a company/custodian account is for)."""
    m = RE_FBO_PERSON.search(original_lines.upper()) if isinstance(original_lines, str) else None
    return clean_person(m.group("name").split("|")[0].strip()) if m else None


# ------------------------------------------------------------------ person names
# Everything from the first of these words to the end of the name is registration text, not the name.
RE_CUT = re.compile(
    r"\s+(?:TOD|POD|TIC|A?TBE|JT|JTWROS|WROS|JT\s*/?\s*TEN|TEN\s*/?\s*COM|TEN\s*/?\s*ENT|TENANTS|COMM\s+PROP|IRA|ROTH|SEP|401\s?K|403\s?B|"
    r"PSP|DMD|MD|DDS|PHD|CPA|ESQ|CUST(?:ODIAN)?|C/F|AS|FBO|TTEES?|TRUSTEES?|CO-TTEE|TR|TRS|TRUST|REV|REVOCABLE|"
    r"IRREVOCABLE|IRREV|LIVING|FAMILY|U/A|UA|UAD|U/A/D|DTD|ESTATE|EST|EXEC|EXECUTOR|BENE|BENEF|DECD|DECEASED|INH|INHERITED|"
    r"UTMA|UGMA|UNTIL|ACCOUNT|ACCT|A/C|DESIGNATED|PLAN)\b.*$")
RE_PREFIX = re.compile(r"^(?:MR|MRS|MS|MISS|DR|NSPS|SPS|FAO|FBO)\.?\s+")      # NSPS/SPS = (non-)spouse beneficiary
NOT_A_NAME = {
    "ACCOUNT", "ACCT", "MANAGED", "RULES", "SUBJECT", "INDEX", "INDEXING", "CUSTOM", "BLEND", "PORTFOLIO",
    "CLIENT", "CLIENTS", "DOMESTIC", "TAX", "TAXABLE", "STRATEGY", "GROWTH", "VALUE", "INCOME", "EQUITY",
    "MARGIN", "DEALS", "DIRECT", "SOCIALLY", "RESPONSIBLE", "GLOBAL", "INTERNATIONAL", "FUND", "CAPITAL",
    "SECURITIES", "BANK", "TRUST", "ESTATE", "IRA", "ROTH", "PLAN", "ATTN", "CO", "INC", "LLC", "LTD", "CORP",
    "CENTRE", "CENTER", "BUILDING", "FLOOR", "SUITE", "URBANIZACION", "CALLE", "AVENIDA", "EDIFICIO", "TORRE",
    "PISO", "COLONIA", "BARRIO", "RESIDENCIAL", "APARTADO", "OFFSET", "SALES", "OVERLAY", "LONG", "SHORT",
    "WEALTH", "ADVISORS", "MANAGEMENT", "PARTNERS", "SERVICES", "NOMINEE", "NOMINEES", "OMNIBUS", "MEETINGS",
    "GENERAL", "DEPT", "DEPARTMENT", "OPERATIONS", "PROXY", "CORPORATE", "ACTIONS", "UNITED", "STATES",
    "UTMA", "UGMA", "TOD", "POD", "SEP", "HSA", "BENE", "BENEF", "JT", "TEN", "WROS", "JTWROS", "COM", "ENT",
    "CUST", "CUSTODIAN", "TTEE", "TTEES", "TRUSTEE", "ROLLOVER", "CONTRIBUTORY", "RETIREMENT", "UNTIL", "AGE",
    "DESIGNATED", "STA", "FBO", "C/O", "C/F", "U/A", "DTD", "REV", "LIVING", "FAMILY", "DECD", "EXEC",
    # organizations seen on the extra lines of real files (employer plans, banks, schools...)
    "THE", "ALL", "OF", "FOR", "BANKING", "CITIBANK", "NA", "N.A.", "CORPORATION", "COMPANY", "SCHOOL", "SYSTEMS",
    "EMPLOYEES", "EMPLOYEE", "SAVINGS", "COVERDELL", "ESA", "DEPOT", "FUTUREBUILDER", "TBE", "DBP", "SSP",
    "PARTNERSHIP", "EDUCATION", "HIGHER", "UNIVERSITY", "COLLEGE", "HOSPITAL", "HEALTH", "MEDICAL", "CHURCH",
    "FOUNDATION", "SOCIETY", "ASSOCIATION", "INSTITUTE", "PROGRAM", "PENSION", "PROFIT", "SHARING", "BENEFIT",
    "BENEFITS", "INVESTMENT", "INVESTMENTS", "FINANCIAL", "INSURANCE", "HOLDINGS", "GROUP", "INTL", "LP", "LLP",
    "PLC", "SA", "AG", "GMBH", "NV", "BV", "LIMITED", "CLEARING", "BROKERAGE", "CONTROL", "PILOTS", "TRST",
    "CHILDREN'S", "CHILDRENS", "FIDELITY", "TERMINATED", "PARTICIPAN", "PARTICIPANT", "PARTICIPANTS", "RETIREE",
    "RETIREES", "UNION", "LOCAL", "STAFF", "MEMBERS", "SAVINGS/PLAN", "ASSIGNS",
}
RE_NAME_TOKEN = re.compile(r"^[A-Z][A-Z.'-]*$")


def looks_like_person(text):
    """'MARY Q EXAMPLE' -> True; 'SUBJECT TO STA RULES', 'AQR FLEX 250 R3', 'SMITH' -> False."""
    if not isinstance(text, str):
        return False
    tokens = text.upper().split()
    if not 2 <= len(tokens) <= 5:
        return False
    if any(not RE_NAME_TOKEN.match(t) for t in tokens) or any(t.strip(".") in NOT_A_NAME for t in tokens):
        return False
    sizes = [len(t.strip(".'-")) for t in tokens]
    return any(n >= 3 for n in sizes) or sum(n >= 2 for n in sizes) >= 2      # 'LI WU' is a name too


def clean_person(text):
    """'ANNA M SAMPLE TOD' -> 'ANNA M SAMPLE'; 'PETER J EXAMPLE DMD 401K & PSP' -> 'PETER J EXAMPLE';
    'ROBERT T SAMPLE TTEE' -> 'ROBERT T SAMPLE'. Returns None when what is left is not a person's name."""
    if not isinstance(text, str):
        return None
    t = " ".join(re.sub(r"[,()]", " ", text.upper()).split())      # "ANTHONY SAMPLE (BENE)" -> ANTHONY SAMPLE
    t = RE_PREFIX.sub("", t)
    t = RE_CUT.sub("", " " + t).strip()
    t = t.strip(" &-/")
    t = RE_TRAILING_AND.sub("", t).strip(" &-/")     # "JOHN SCOTT BRADLEY AND" -> JOHN SCOTT BRADLEY
    return t if looks_like_person(t) else None


RE_TRAILING_AND = re.compile(r"(?:^|\s+)(?:AND/OR|AND|OR)$")
# two people joined by "&", "AND" or "AND/OR": "J DOE & L DOE", "JOHN SCOTT BRADLEY AND" (2nd person on the next line)
RE_JOIN = re.compile(r"\s*&\s*|\s+AND/OR(?:\s+|$)|\s+AND(?:\s+|$)|\s+OR(?:\s+|$)")


def split_holder_name(holder_name):
    """Holder name -> (person 1, person 2). 'J DOE & L DOE TTEE' -> ('J DOE', 'L DOE');
    'MARK A SAMPLE &' -> ('MARK A SAMPLE', None) (the 2nd person is on the next line)."""
    if not isinstance(holder_name, str):
        return None, None
    parts = [p for p in RE_JOIN.split(holder_name.upper()) if p.strip()]
    if not parts:
        return None, None
    if len(parts) > 1 and len(parts[0].split()) == 1 and len(parts[1].split()) >= 2:
        # shared last name: "DUANE AND ANNE BARKER" -> DUANE BARKER and ANNE BARKER
        last = RE_CUT.sub("", " " + parts[1]).split()
        if len(last) >= 2:
            parts[0] = f"{parts[0]} {last[-1]}"
    first = clean_person(parts[0])
    second = clean_person(parts[1]) if len(parts) > 1 else None
    return first, second



# ------------------------------------------------------------------ holder_name shown in the client file
# Client feedback 2026-10-24: holder_name without "&" / "AND" and without account-type words (TRUST, TOD, CUST,
# TTEE, FAO, EXEC...). The removed text goes to notes. The original holder line is kept in original_lines, and
# the investor ID is still matched on the original holder line (nothing changes in the registry).
_END = r"(?=[\s,)]|$)"
# account-type words, all holders. AS before CUST is removed with it ("ATC AS CUST FOR IRA" -> ATC)
_REG_STRONG = (r"\(?(?:TOD|POD|BENE|BENEF)\)?|(?:CO-?)?TTEES?|(?:CO-?)?TRUSTEES?|CUST(?:ODIAN)?|C/F|FAO|FBO|"
               r"EXEC|EXECUTOR|EXECUTRIX|U/A|UAD|U/A/D|DTD")
# more account-type words, only for individual holders (in a company name TRUST, ESTATE, IRA... can be part of the name)
_REG_PERSON = (r"JTWROS|JT\s*/?\s*TEN|JT|WROS|TEN\s*/?\s*COM|TEN\s*/?\s*ENT|TIC|A?TBE|IRA|ROTH|SEP|401\s?K|403\s?B|UTMA|UGMA|"
               r"TRUST(?!\s+(?:CO|COMPANY|N\.?A\.?|BANK|FSB|LTD|CORP)\b)|TR|TRS|TRST|REV|REVOCABLE|IRREVOCABLE|IRREV|"
               r"DECD|DECEASED|INH|INHERITED|ESTATE|EST")
RE_REG_ANY = re.compile(r"(?:^|\s+)(?:AS\s+)?(?:" + _REG_STRONG + ")" + _END + r".*$")
RE_REG_PERSON = re.compile(r"(?:^|\s+)(?:AS\s+)?(?:" + _REG_STRONG + "|" + _REG_PERSON + ")" + _END + r".*$")
RE_ESTATE_OF = re.compile(r"^(?:(?:THE\s+)?(?:ESTATE|EST)\s+OF|FAO|FBO)\s+")      # "ESTATE OF ...", "FAO TERRELL SAMPLE"
CONNECTORS = {"&", "AND", "AND/OR", "OR"}
TITLES = {"MR", "MRS", "MS", "MISS", "DR", "MR.", "MRS.", "MS.", "DR."}


def _tokens(text):
    return " ".join(re.sub(r"[,()]", " ", text.upper()).replace("&", " & ").split()).split()


def _trim_connectors(text):
    words = (text or "").split()
    while words and words[0] in CONNECTORS:
        words.pop(0)
    while words and words[-1] in CONNECTORS:
        words.pop()
    return " ".join(words)


def clean_holder_name(holder_name, type_value=None, primary=None, secondary=None, trust_name=None,
                      primary_from_contact=False):
    """holder_name for the client file -> (clean name, removed text for notes).

    individual with a primary contact: the holder name is the primary contact (first person), the second
      person stays in secondary_contact, and the rest of the line (TTEE, TOD, a third name...) goes to notes:
        'WILLIAM SAMPLE AND LISA C SAMPLE'  -> ('WILLIAM SAMPLE', None)
        'J DOE & L DOE TTEE'                -> ('J DOE', 'TTEE')
        'ANNA M SAMPLE TOD'                 -> ('ANNA M SAMPLE', 'TOD')
        'ATC AS CUST FOR IRA' (person on the next line) -> ('MARY J SAMPLE', 'ATC AS CUST FOR IRA')
    any other holder: the text from the first account-type word to the end goes to notes:
        'SMITH FAMILY TRUST U/A DTD'        -> ('SMITH FAMILY', 'TRUST U/A DTD')
        'TIAA TRUST N.A. CUST'              -> ('TIAA TRUST N.A.', 'CUST')
      company names keep '&' / 'AND' (part of the legal name: 'GLASS LEWIS & CO')."""
    if not isinstance(holder_name, str) or not holder_name.strip():
        return holder_name, None
    name = " ".join(holder_name.split())
    person = type_value == "individual"
    if person and isinstance(trust_name, str) and trust_name.strip():
        return trust_name.strip(), None         # personal trust: the trust is the legal holder (2026-10-28)
    if person and isinstance(primary, str) and primary.strip() and primary_from_contact is not True:
        left = Counter(_tokens(primary) + (_tokens(secondary) if isinstance(secondary, str) else []))
        rest = []
        for t in _tokens(name):
            if left[t] > 0:
                left[t] -= 1
            elif t not in CONNECTORS and t not in TITLES:
                rest.append(t)
        return primary.strip(), _trim_connectors(" ".join(rest)) or None
    up = " ".join(re.sub(r"[()]", " ", name.upper()).split()) if person else name.upper()
    removed = []
    m = RE_ESTATE_OF.match(up) if person else None
    if m:
        removed.append(m.group(0).strip())
        up = up[m.end():]
    rx = RE_REG_PERSON if person else RE_REG_ANY
    m = rx.search(up)
    if m and up[:m.start()].strip(" -,&"):
        removed.append(m.group(0).strip())
        up = up[:m.start()]
    up = _trim_connectors(up.strip(" -,"))
    if person:                          # two people and no primary contact: first one is the holder
        parts = [p.strip() for p in RE_JOIN.split(up) if p.strip()]
        if len(parts) > 1:
            up = parts[0]
            removed.insert(0, " ".join(parts[1:]))
    if not up:
        return name, None
    if up == name.upper():
        return name, None
    return up, " ".join(removed) or None


def add_clean_holder_name(df, extra_col):
    """Replaces holder_name with the clean name and puts the removed text first in the notes column."""
    if not len(df) or "holder_name" not in df.columns:
        return df
    get = lambda c: df[c] if c in df.columns else [None] * len(df)
    names, extras = [], []
    for n, t, p, s, x, tr, fc in zip(df["holder_name"], get("type"), get("primary_contact"), get("secondary_contact"),
                                     get(extra_col), get("trust_name"), get("primary_from_contact")):
        clean, removed = clean_holder_name(n, t, p, s, tr, fc)
        names.append(clean)
        x = x if isinstance(x, str) and x.strip() else None
        extras.append(" | ".join(v for v in (removed, x) if v) or None)
    df["holder_name"], df[extra_col] = names, extras
    return df

# ------------------------------------------------------------------ extra lines
# "<custodian> [AS] CUST|CUSTODIAN|C/F [FOR|OF] [-:] <account description>"
RE_CUST_LINE = re.compile(r"^(?P<cust>.*?)\s*\b(?:AS\s+)?(?:CUST(?:ODIAN)?|C/F)\b\.?\s*(?:FOR|OF)?\s*[-:]?\s*(?P<rest>.*)$")
# account words that can come before the custodian code (Vanguard: "ROTH IRA VFTC AS CUSTODIAN")
TYPE_WORDS = {"AS", "IRA", "ROTH", "TRAD", "TRADITIONAL", "R/O", "RO", "ROLLOVER", "SEP", "SEP-IRA", "SIMPLE",
              "HSA", "BENE", "BENEF", "INHERITED", "CONTRIBUTORY", "CONT", "CONV", "CONVERSION", "401K", "403B",
              "BDA", "SRA", "ACCOUNT"}
RE_ACCOUNT_TEXT = re.compile(
    r"\b(IRA|ROTH|SEP|SEP-IRA|SIMPLE|401\s?\(?K\)?|403\s?\(?B\)?|HSA|TOD|POD|BENE|BENEF|ROLLOVER|R/O|RRSP|RRIF|"
    r"TFSA|ESA|529|UTMA|UGMA|UNTIL AGE|DESIGNATED|STA RULES|KEOGH|PROFIT SHARING|PENSION PLAN|PSP|SAVINGS PLAN|"
    r"DEFERRAL|RETIREMENT ACCOUNT|ROTH ACCOUNT|ROLLOVER ACCOUNT|SEP ACCOUNT|TOD ACCOUNT|PLEDGE|JT|JTWROS|WROS|"
    r"JT\s*/?\s*TEN|TEN\s*/?\s*COM|TEN\s*/?\s*ENT|COMM/?\s?PROP|COMMUNITY PROPERTY|TBE|TIC)\b")
RE_JOINT_LINE = re.compile(r"^(?P<name>.*?)\s+(?P<reg>(?:JT\s*/?\s*TEN|JTWROS|JT\s*WROS|WROS|TEN\s*/?\s*COM|TEN\s*/?\s*ENT|JT\b|TE\s+TOD|TIC\b|A?TBE\b|"
                           r"COMM(?:UNITY)?\s+PROP)\b.*)$")
# lines left as they are (client example: "MSSB FBO CO-DIAGNOSTICS INC." stays in holder_extra_lines)
RE_KEEP_ALWAYS = re.compile(r"\bATTN\b|\bC/O\b|\bAS BENE OF\b|\bMGR:")
RE_KEEP = re.compile(r"\bFBO\b|\bU/A\b|\bUAD\b|\bU/A/D\b|\bDTD\b|\bTRUST\b|\bTTEE\b|\bTRUS\b|\bTRST\b|\bTR\b|"
                     r"\bLIVING\b|\bREVOCABLE\b|\bIRREVOCABLE\b|\bREV\b")      # trust names and dates stay in notes
# "PAUL SMITH TTEE ET AL", "A DOE & B DOE CO-TTEES": the trustee is the person behind a trust account
RE_TRUSTEE_LINE = re.compile(r"^(?P<name>.+?)\s+(?:CO-?)?(?:TTEES?|TRUSTEES?)\b")
RE_FBO_LINE = re.compile(r"^FBO\s+(?P<name>.+)$")
RE_COMPANY_WORD = re.compile(r"\b(INC|CORP|CO|COMPANY|LLC|LTD|LP|LLP|PLC|NA|N\.A\.|TRUST|FSB)\b")


# ------------------------------------------------------------------ contact lines (client 2026-10-28)
# "ATTN: WILLIAM J. DEZELLEM", "KA: KRISTEN DRAKE", "C/O GEOFFREY B DURRELL | & ANDREA DURRELL":
# the person named after ATTN / KA: / C/O is the contact of the account (primary, then secondary).
RE_CONTACT_LINE = re.compile(r"^(?P<before>.*?)\s*(?:\b(?:ATTN|C/O)\b\s*[:.]?|\bKA\s*:)\s*(?P<name>.*)$")
RE_AND_LINE = re.compile(r"^\s*(?:&|AND)\s+(?P<name>.+)$")
JOB_TITLES = {"CEO", "CFO", "COO", "CTO", "CIO", "PRESIDENT", "VP", "CHAIRMAN", "DIRECTOR", "MANAGER", "TREASURER",
              "SECRETARY", "PARTNER", "OWNER", "PRINCIPAL", "ADMIN", "ESQ", "ESQ.", "CPA", "MD"}
CONTACT_STOP = (NOT_A_NAME - {"ROTH", "LONG", "SHORT", "SALES", "GENERAL", "UNITED", "CHURCH"}) | {
    "STOCK", "LOAN", "LOANS", "BORROWED", "DEPT", "DEPT.", "DEPARTMENT", "OFFICE", "DESK", "TEAM", "REORG",
    "PROXY", "ACCOUNTING", "LEGAL", "TAX", "EXTENSION", "AMERICA", "NORTH", "SOUTH", "EAST", "WEST", "REF", "PEID"}


def looks_like_contact(text):
    """'WILLIAM J. DEZELLEM', 'BYRON ROTH' -> True; 'PROXY DEPARTMENT', 'STOCK LOAN', 'TRUST SECURITIES' -> False."""
    if not isinstance(text, str):
        return False
    tokens = text.upper().split()
    if not 2 <= len(tokens) <= 4 or any(not RE_NAME_TOKEN.match(t) for t in tokens):
        return False
    if any(t.strip(".") in CONTACT_STOP for t in tokens) or RE_INSTITUTIONAL.search(text) or RE_COMPANY_WORD.search(text):
        return False
    return any(len(t.strip(".'-")) >= 3 for t in tokens)


def contact_people(text):
    """Text after ATTN / KA: / C/O -> list of people (empty when it is not a person)."""
    if not isinstance(text, str):
        return []
    t = re.split(r"\s+(?:C/O|ATTN)\b", text.upper())[0]     # "DINA BIBAS C/O FITZROY INVESTMENTS" -> DINA BIBAS
    t = re.split(r"[/;,]", t)[0]                            # "NADINE MERCKX/ HAVENLAAN 12" -> NADINE MERCKX
    t = RE_PREFIX.sub("", t.strip())                       # "MRS. RIZWANA BANU" -> RIZWANA BANU
    words = t.split()
    while words and words[-1].strip(".") in JOB_TITLES:   # "VINCENT CHEUNG CEO" -> VINCENT CHEUNG
        words.pop()
    t = " ".join(words)
    parts = [p.strip() for p in RE_JOIN.split(t) if p.strip()]
    if len(parts) > 1 and len(parts[0].split()) == 1 and len(parts[1].split()) >= 2:
        parts[0] = f"{parts[0]} {parts[1].split()[-1]}"   # "JOHN AND MARY SMITH"
    if not parts or not all(looks_like_contact(p) for p in parts):
        return []
    return parts[:2]


# ------------------------------------------------------------------ trust legal name (client 2026-10-28)
# The legal holder of a personal trust is the trust itself: its name up to the agreement date, e.g.
#   "JOHN M LYNN TTEE | JOHN M LYNN REVOCABLE TRUST | U/A DTD 08/03/1984" -> JOHN M LYNN REVOCABLE TRUST U/A DTD 08/03/1984
#   "JEFFREY POPPEL REVOCABLE | TRUST AGREEMENT | U/A DTD 04/23/2020 | JEFFREY POPPEL TTEE"
#       -> JEFFREY POPPEL REVOCABLE TRUST AGREEMENT U/A DTD 04/23/2020
# The trustees stay as primary / secondary contact. Trusts without U/A / DTD are not changed (manual review).
RE_UA = re.compile(r"\bU/?A\b|\bUAD\b|\bU/A/D\b|\bDTD\b")
RE_DATE = re.compile(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b")
RE_TRUST_WORD = re.compile(r"\b(?:TRUST|TR|TRT|TRST|REV|REVOC|REVOCABLE|IRREVOCABLE|LIVING|LIV|FAMILY|AMENDED|"
                           r"RESTATED|AGREEMENT|DECLARATION|SURVIVORS?|MARITAL|BYPASS|GIFT)\b|\d")
RE_TTEE_WORD = re.compile(r"\b(?:CO-?)?(?:TTEES?|TRUSTEES?|TRUSTE)\b")
RE_TRUST_STOP = re.compile(r"\bCUST(?:ODIAN)?\b|\bC/F\b|\bFBO\b|\bATTN\b|\bC/O\b|\bIRA\b|\bROTH\b|\bPLAN\b|\b40[13]")


def trust_segment(holder_name, lines):
    """-> dict(name, used, tail, trustees, trustee_used) or None. Line 0 is the holder line, then the extra lines."""
    L = [(holder_name or "").upper()] + [x.upper() for x in lines]
    k = next((i for i, u in enumerate(L) if RE_UA.search(u)), None)
    if k is None or RE_TTEE_WORD.search(L[k]):
        return None
    end = k
    if not RE_DATE.search(L[k]) and k + 1 < len(L) and RE_DATE.search(L[k + 1]) and \
            (len(L[k + 1].split()) <= 4 or RE_DATE.match(L[k + 1].strip())):
        end = k + 1                                       # "... REV TRUST U/A | DTD 01/28/2022"
    m = RE_DATE.search(L[end])
    end_text = L[end][:m.end()] if m else L[end]
    tail = (L[end][m.end():].strip(" ,-") or None) if m else None
    start = k
    if not L[k][:RE_UA.search(L[k]).start()].strip(" ,-"):    # the line starts with U/A: the name is on the line before
        if k == 0 or RE_TTEE_WORD.search(L[k - 1]) or RE_TRUST_STOP.search(L[k - 1]):
            return None
        start = k - 1
    while start > 0 and not RE_TTEE_WORD.search(L[start - 1]) and not RE_TRUST_STOP.search(L[start - 1]) \
            and (not looks_like_person(L[start - 1]) or RE_TRUST_WORD.search(L[start - 1])):
        start -= 1
    if any(RE_TRUST_STOP.search(L[i]) for i in range(start, end + 1)):
        return None
    lead = None
    mt = re.match(r"^(?:CO-?)?(?:TTEES?|TRUSTEES?)\s+(?P<rest>.+)$", L[start - 1]) if start > 0 else None
    if mt and not RE_TRUST_STOP.search(L[start - 1]):
        lead, start = mt.group("rest"), start - 1        # the line starts with CO-TTEE, the rest is the trust name
    parts = L[start:end] + [end_text]
    if lead:
        parts[0] = lead
    name = " ".join(" ".join(parts).split())
    name = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\b", "", name)      # "1996-11-06 00:00:00" (Excel date)
    name = re.sub(r"^(?:AS\s+)?(?:TTEES?\s+)?OF\s+", "", name)
    name = re.sub(r"\bTHE\s+THE\b", "THE", name)
    if len(name.split()) < 3:
        return None
    # trustees written after the trust name over two lines: "MONICA M. COLEMAN & MATTHEW | COLEMAN TRUSTEES"
    # or "... DTD 8/16/2021 JEFFREY B. | STRAUBEL TRUSTEE"
    trustees, t_used = [], set()
    chunk, idx = [tail] if tail else [], []
    for j in range(end + 1, min(end + 3, len(L))):
        chunk.append(L[j])
        idx.append(j)
        if RE_TTEE_WORD.search(L[j]):
            joined = RE_TTEE_WORD.split(" ".join(chunk))[0]
            found = [x for x in split_holder_name(joined) if x]
            if found and (len(chunk) > 1):
                trustees, t_used, tail = found, set(idx), None
            break
    return {"name": name, "used": set(range(start, end + 1)), "tail": tail, "trustees": trustees,
            "trustee_used": t_used, "from_holder": start == 0}


def same_person(a, b):
    """'ELLEN JOAN RIEBACK' and 'ELLEN J RIEBACK' are the same person (same first and last word)."""
    if not a or not b:
        return False
    x, y = a.split(), b.split()
    return x[0] == y[0] and x[-1] == y[-1]


def _split_custodian(line):
    """'CHARLES SCHWAB & CO INC CUST' -> ('CHARLES SCHWAB & CO INC', '');
    'FMT CO CUST IRA ROLLOVER' -> ('FMT CO', 'IRA ROLLOVER'); 'ROTH IRA VFTC AS CUSTODIAN' -> ('VFTC', 'ROTH IRA')."""
    m = RE_CUST_LINE.match(line)
    if not m:
        return None
    cust, rest = m.group("cust").strip(" -,"), m.group("rest").strip(" -,")
    words = cust.split()
    lead = []
    while words and words[0] in TYPE_WORDS:
        lead.append(words.pop(0))
    while words and words[-1] in TYPE_WORDS - {"AS"}:      # "U.S. BANK N.A. IRA" -> custodian U.S. BANK N.A.
        lead.append(words.pop())
    cust = " ".join(words)
    pre_type = " ".join(w for w in lead if w != "AS")
    acct = " ".join(x for x in (pre_type, rest) if x)
    if not cust and not acct:
        return None
    return cust, acct


def split_extra_lines(extra, holder_type_value, holder_name=None):
    """Splits the extra registration lines (joined with ' | ').
    Returns dict: custodian, type_of_account, secondary_contact, leftover."""
    out = {"custodian": None, "type_of_account": None, "secondary_contact": None, "leftover": None, "people": [],
           "contacts": [], "trust_name": None, "trust_from_holder": False}
    lines = [x.strip() for x in extra.split("|")] if isinstance(extra, str) and extra.strip() else []
    lines = [x for x in lines if x]
    types, left, used = [], [], set()
    person = holder_type_value == "individual"

    if person:                              # personal trust: holder_name = the trust's legal name (2026-10-28)
        seg = trust_segment(holder_name, lines)
        if seg:
            out["trust_name"] = seg["name"]
            out["trust_from_holder"] = seg["from_holder"]
            used |= {i - 1 for i in seg["used"] | seg["trustee_used"] if i >= 1}
            out["people"] += seg["trustees"]
            if seg["tail"]:
                left.append(seg["tail"])

    for i, line in enumerate(lines):
        if i in used:
            continue
        up = line.upper()
        mc = RE_CONTACT_LINE.match(up)
        if mc:                              # ATTN / KA: / C/O <person> -> contact (2026-10-28)
            names = contact_people(mc.group("name"))
            if names and len(names) == 1 and i + 1 < len(lines) and i + 1 not in used:
                nxt = lines[i + 1].upper()
                ma = RE_AND_LINE.match(nxt)                    # "C/O GEOFFREY B DURRELL | & ANDREA DURRELL"
                if not ma and re.search(r"(?:&|\bAND)\s*$", mc.group("name")):
                    ma = re.match(r"^(?P<name>.+)$", nxt)       # "C/O LEON BIBAS & | DINA BIBAS"
                if ma and contact_people(ma.group("name")):
                    names += contact_people(ma.group("name"))[:1]
                    used.add(i + 1)
            if names:
                out["contacts"] += names
                before = mc.group("before").strip(" -,|")
                if before:
                    left.append(before)
                continue
        if RE_KEEP_ALWAYS.search(up):
            left.append(line)
            continue
        if person and not re.search(r"\bCUST(ODIAN)?\b|\bC/F\b", up):
            mt, mf = RE_TRUSTEE_LINE.match(up), RE_FBO_LINE.match(up)
            if mt and (RE_INSTITUTIONAL.search(mt.group("name")) or RE_COMPANY_WORD.search(mt.group("name"))):
                mt = None                   # a bank or trust company as trustee is not a contact person
            names = [x for x in split_holder_name(mt.group("name")) if x] if mt else \
                ([clean_person(mf.group("name"))] if mf and clean_person(mf.group("name")) else [])
            if names:                       # trustee or beneficial owner named on its own line
                out["people"] += names
                continue
        # a custodian line can mention FBO / TRUST CO ("PTC CUST IRA FBO", "EDWARD JONES TRUST CO AS CUST")
        c = _split_custodian(re.sub(r"\s+FBO\s*$", "", up)) if re.search(r"\bCUST(ODIAN)?\b|\bC/F\b", up) else None
        if not c and RE_KEEP.search(up):
            left.append(line)
            continue
        if c and (c[0] or c[1]):
            cust, acct = c
            if cust and not out["custodian"]:
                out["custodian"] = cust
            elif cust:
                left.append(line)
                continue
            if not acct:      # description on the next line ("... CUST | ROTH CONTRIBUTORY IRA")
                j = i + 1
                if j < len(lines) and j not in used and RE_ACCOUNT_TEXT.search(lines[j].upper()) \
                        and not RE_KEEP.search(lines[j].upper()) and not RE_CUST_LINE.match(lines[j].upper()):
                    acct = lines[j].strip()
                    used.add(j)
            if acct:
                types.append(acct)
            continue
        m = RE_JOINT_LINE.match(up)
        if m and person and not out["secondary_contact"] and clean_person(m.group("name")):
            out["secondary_contact"] = clean_person(m.group("name"))
            types.append(m.group("reg").strip())
            continue
        if RE_ACCOUNT_TEXT.search(up) and not looks_like_person(up):
            types.append(line)
            continue
        if person and not out["secondary_contact"] and clean_person(up):
            out["secondary_contact"] = clean_person(up)
            continue
        left.append(line)

    # custodian written in the holder name itself: "ATC AS CUST FOR IRA" (personal custodial account)
    if not out["custodian"] and isinstance(holder_name, str) and holder_type_value == "individual":
        c = _split_custodian(holder_name.upper())
        if c and c[0] and not looks_like_person(c[0]):
            out["custodian"] = c[0]
            if c[1]:
                types.insert(0, c[1])

    out["type_of_account"] = " | ".join(dict.fromkeys(types)) or None
    out["leftover"] = " | ".join(left) or None
    return out


def add_client_columns(accounts):
    """Adds type, primary_contact, secondary_contact, custodian, type_of_account to the account table and
    replaces holder_extra_lines with what is left after the split. Returns the same DataFrame."""
    a = accounts
    none = [None] * len(a)
    cats = a["account_category"] if "account_category" in a.columns else none
    orig = a["original_lines"] if "original_lines" in a.columns else none
    a["type"] = [holder_type(e, n, c, x, o) for e, n, c, x, o in zip(a["entity_type"], a["holder_name"], cats,
                                                                      a["holder_extra_lines"], orig)]
    # entity_type follows: a holder that is not a person is not "individual" ("ISS/152/GSAM", "ATTN:EMAIL"),
    # and a named holder of a personal account (IRA, TOD...) is not "unknown" ("C SOLOTOFF")
    not_person = (a["type"].isin(["institutional", "corporate"])) & \
        a["entity_type"].isin(["individual", "joint_individual", "unknown"])
    a.loc[not_person, "entity_type"] = "company_institution"
    a.loc[(a["type"] == "individual") & (a["entity_type"] == "unknown"), "entity_type"] = "individual"
    prim, sec, cust, toa, left, trust, types, from_contact = [], [], [], [], [], [], [], []
    for name, extra, t, ent, o in zip(a["holder_name"], a["holder_extra_lines"], a["type"], a["entity_type"], orig):
        p1 = p2 = None
        plan_person = None
        if t != "individual" and ent == "nominee_custodian" and isinstance(extra, str) \
                and re.search(r"\bPLAN\b", extra.upper()):
            # "DTC #997 | IBM | IBM DEFERRED SAVINGS PLAN | CLAUDIA BARCLAY": the plan participant is the holder
            found = [x.strip() for x in extra.split("|") if looks_like_contact(x.strip().upper())]
            if len(found) == 1:
                plan_person, t = found[0].upper(), "individual"
        if t == "individual":
            if ent == "company_institution":    # company line holding for one person: "... | FBO LARRY TUCKER"
                p1 = fbo_person(o)
            elif ent == "nominee_custodian":
                m = RE_CUST_LINE.match(name.upper()) if isinstance(name, str) else None
                p1 = clean_person(m.group("cust")) if m else None
            else:
                p1, p2 = split_holder_name(name)
        s = split_extra_lines(extra, t, name)
        if plan_person:
            p1 = plan_person
            rest = [x for x in (s["leftover"] or "").split(" | ") if x and x.strip().upper() != plan_person]
            s["leftover"] = " | ".join(rest) or None
            s["secondary_contact"] = None
        p2 = p2 or s["secondary_contact"]
        if s["trust_from_holder"] and s["people"]:
            p1 = p2 = None
        if t == "individual" and not p1 and p2:
            # the holder line names the custodian or the trust, the person is on the next line
            # ("ATC AS CUST FOR IRA | SUSAN K EXAMPLE") -> that person is the primary contact
            p1, p2 = p2, None
        for person_name in s["people"]:     # trustee / FBO lines fill the contacts that are still empty
            if not p1:
                p1 = person_name
            elif not p2 and not same_person(person_name, p1):
                p2 = person_name
        had_p1 = bool(p1)
        if t != "individual" or not p1:     # ATTN / KA: / C/O person fills the empty contacts (2026-10-28)
            for person_name in s["contacts"]:
                if not p1:
                    p1 = person_name
                elif not p2 and not same_person(person_name, p1):
                    p2 = person_name
        if same_person(p1, p2):             # the same person read twice (holder line and trust line)
            p2 = None
        types.append(t)
        from_contact.append(bool(p1) and not had_p1 and p1 in s["contacts"])
        trust.append(s["trust_name"])
        prim.append(p1)
        sec.append(p2)
        cust.append(s["custodian"])
        toa.append(s["type_of_account"])
        left.append(s["leftover"])
    a["type"], a["trust_name"], a["primary_from_contact"] = types, trust, from_contact
    a["primary_contact"], a["secondary_contact"] = prim, sec
    a["custodian"], a["type_of_account"], a["holder_extra_lines"] = cust, toa, left
    return a
