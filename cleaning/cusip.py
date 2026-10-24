"""CUSIP rule (client 2026-10-24): the CUSIP is a 9-character code (letters and digits) that identifies a security
in the US and Canada. Positions 1-6 = issuer, 7-8 = issue, 9 = check digit. The CUSIP is always kept as TEXT and
never has more than 9 characters.

Excel can damage a CUSIP when a file is opened and saved:
  - a CUSIP with an "E" between digits is read as a number in scientific notation:
    65344E206 (NEXGEL, Inc.) -> 6.5344E+210
  - a CUSIP that is all digits loses its leading zeros: 023135106 -> 23135106, or gets ".0": 189763204.0
normalize_cusip() repairs these cases when the result passes the check digit, and flags anything else.
"""

import re

RE_SCI = re.compile(r"^(?P<int>\d)(?:[.,](?P<dec>\d+))?E\+?(?P<exp>\d+)$")


def check_digit(first8):
    """Check digit (position 9) of a CUSIP, from its first 8 characters (standard modulus 10 'double add double')."""
    total = 0
    for i, c in enumerate(first8):
        if c.isdigit():
            v = int(c)
        elif c.isalpha():
            v = ord(c) - 55              # A = 10 ... Z = 35
        else:
            v = {"*": 36, "@": 37, "#": 38}.get(c)
            if v is None:
                return None
        if i % 2 == 1:                  # every second character is doubled
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def is_valid(cusip):
    return isinstance(cusip, str) and len(cusip) == 9 and check_digit(cusip[:8]) == cusip[8]


def normalize_cusip(value):
    """-> (cusip, note). cusip is a 9-character text or the cleaned input when it cannot be repaired;
    note is None when the CUSIP is valid as received, otherwise what was fixed or what to check.
    '65344E206' -> ('65344E206', None); '6.5344E+210' -> ('65344E206', 'repaired from scientific notation ...');
    '23135106' -> ('023135106', 'leading zero restored ...'); 'US65344E2063' (ISIN) -> ('65344E206', ...)."""
    if value is None or (isinstance(value, float) and value != value):
        return None, None
    raw = str(value).strip()
    s = re.sub(r"[\s\-]", "", raw.upper())
    if not s or s in ("NAN", "NONE"):
        return None, None
    s = re.sub(r"\.0+$", "", s)                         # 189763204.0 -> 189763204
    if is_valid(s):
        return s, (None if s == raw else f"CUSIP cleaned from '{raw}'")
    m = RE_SCI.match(s)
    if m:                                               # 6.5344E+210 -> 65344E206
        digits = m.group("int") + (m.group("dec") or "")
        cand = f"{digits}E{int(m.group('exp')) - (len(digits) - 1)}"
        if is_valid(cand):
            return cand, f"CUSIP repaired from scientific notation '{raw}' (Excel)"
    if s.isdigit() and len(s) < 9 and is_valid(s.zfill(9)):
        return s.zfill(9), f"CUSIP leading zero(s) restored from '{raw}' (Excel)"
    if len(s) == 12 and s[:2].isalpha() and is_valid(s[2:11]):   # ISIN: country code + CUSIP + 1 check digit
        return s[2:11], f"CUSIP taken from the ISIN '{raw}'"
    if len(s) > 9 and is_valid(s[:9]):
        return s[:9], f"CUSIP cut to 9 characters from '{raw}'"
    return s[:9] if len(s) > 9 else s, f"CHECK CUSIP: '{raw}' is not a valid 9-character CUSIP"
