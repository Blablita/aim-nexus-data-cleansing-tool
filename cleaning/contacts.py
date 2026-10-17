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
    r"\s+(?:TOD|POD|JT|JTWROS|WROS|JT\s*TEN|TEN\s+COM|TEN\s+ENT|TENANTS|COMM\s+PROP|IRA|ROTH|SEP|401\s?K|403\s?B|"
    r"PSP|DMD|MD|DDS|PHD|CPA|ESQ|CUST(?:ODIAN)?|C/F|AS|FBO|TTEES?|TRUSTEES?|CO-TTEE|TR|TRS|TRUST|REV|REVOCABLE|"
    r"IRREVOCABLE|IRREV|LIVING|FAMILY|U/A|UA|UAD|U/A/D|DTD|ESTATE|EST|EXEC|EXECUTOR|BENE|BENEF|DECD|DECEASED|INH|INHERITED|"
    r"UTMA|UGMA|UNTIL|ACCOUNT|ACCT|A/C|DESIGNATED|PLAN)\b.*$")
RE_PREFIX = re.compile(r"^(?:MR|MRS|MS|DR|NSPS|SPS)\s+")      # NSPS/SPS = (non-)spouse beneficiary
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
    t = " ".join(text.upper().replace(",", " ").split())
    t = RE_PREFIX.sub("", t)
    t = RE_CUT.sub("", " " + t).strip()
    t = t.strip(" &-/")
    return t if looks_like_person(t) else None


def split_holder_name(holder_name):
    """Holder name -> (person 1, person 2). 'J DOE & L DOE TTEE' -> ('J DOE', 'L DOE');
    'MARK A SAMPLE &' -> ('MARK A SAMPLE', None) (the 2nd person is on the next line)."""
    if not isinstance(holder_name, str):
        return None, None
    parts = [p for p in re.split(r"\s*&\s*|\s+AND\s+", holder_name.upper()) if p.strip()]
    if not parts:
        return None, None
    first = clean_person(parts[0])
    second = clean_person(parts[1]) if len(parts) > 1 else None
    return first, second


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
    r"JT TEN|TEN COM|TEN ENT|COMM/?\s?PROP|COMMUNITY PROPERTY|TBE|TIC)\b")
RE_JOINT_LINE = re.compile(r"^(?P<name>.*?)\s+(?P<reg>(?:JT\s*TEN|JTWROS|JT\s*WROS|WROS|TEN\s*COM|TEN\s*ENT|"
                           r"COMM(?:UNITY)?\s+PROP)\b.*)$")
# lines left as they are (client example: "MSSB FBO CO-DIAGNOSTICS INC." stays in holder_extra_lines)
RE_KEEP_ALWAYS = re.compile(r"\bATTN\b|\bC/O\b|\bAS BENE OF\b|\bMGR:")
RE_KEEP = re.compile(r"\bFBO\b|\bU/A\b|\bUAD\b|\bU/A/D\b|\bDTD\b|\bTRUST\b|\bTTEE\b|\bTRUS\b|\bTRST\b|\bTR\b|"
                     r"\bLIVING\b|\bREVOCABLE\b|\bIRREVOCABLE\b|\bREV\b")      # trust names and dates stay in notes
# "PAUL SMITH TTEE ET AL", "A DOE & B DOE CO-TTEES": the trustee is the person behind a trust account
RE_TRUSTEE_LINE = re.compile(r"^(?P<name>.+?)\s+(?:CO-?)?(?:TTEES?|TRUSTEES?)\b")
RE_FBO_LINE = re.compile(r"^FBO\s+(?P<name>.+)$")
RE_COMPANY_WORD = re.compile(r"\b(INC|CORP|CO|COMPANY|LLC|LTD|LP|LLP|PLC|NA|N\.A\.|TRUST|FSB)\b")


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
    out = {"custodian": None, "type_of_account": None, "secondary_contact": None, "leftover": None, "people": []}
    lines = [x.strip() for x in extra.split("|")] if isinstance(extra, str) and extra.strip() else []
    lines = [x for x in lines if x]
    types, left, used = [], [], set()
    person = holder_type_value == "individual"

    for i, line in enumerate(lines):
        if i in used:
            continue
        up = line.upper()
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
    prim, sec, cust, toa, left = [], [], [], [], []
    for name, extra, t, ent, o in zip(a["holder_name"], a["holder_extra_lines"], a["type"], a["entity_type"], orig):
        p1 = p2 = None
        if t == "individual":
            if ent == "company_institution":    # company line holding for one person: "... | FBO LARRY TUCKER"
                p1 = fbo_person(o)
            elif ent == "nominee_custodian":
                m = RE_CUST_LINE.match(name.upper()) if isinstance(name, str) else None
                p1 = clean_person(m.group("cust")) if m else None
            else:
                p1, p2 = split_holder_name(name)
        s = split_extra_lines(extra, t, name)
        p2 = p2 or s["secondary_contact"]
        if t == "individual" and not p1 and p2:
            # the holder line names the custodian or the trust, the person is on the next line
            # ("ATC AS CUST FOR IRA | SUSAN K EXAMPLE") -> that person is the primary contact
            p1, p2 = p2, None
        for person_name in s["people"]:     # trustee / FBO lines fill the contacts that are still empty
            if not p1:
                p1 = person_name
            elif not p2 and not same_person(person_name, p1):
                p2 = person_name
        if same_person(p1, p2):             # the same person read twice (holder line and trust line)
            p2 = None
        prim.append(p1)
        sec.append(p2)
        cust.append(s["custodian"])
        toa.append(s["type_of_account"])
        left.append(s["leftover"])
    a["primary_contact"], a["secondary_contact"] = prim, sec
    a["custodian"], a["type_of_account"], a["holder_extra_lines"] = cust, toa, left
    return a
