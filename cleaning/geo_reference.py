"""Geographic reference lists (no external dependencies).

They can be extended without touching the rest of the code. Everything in UPPER CASE.
"""

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
    # territories and military addresses
    "PR", "VI", "GU", "AS", "MP", "AA", "AE", "AP",
}

CA_PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}

COUNTRIES = [
    "AFGHANISTAN", "ALBANIA", "ALGERIA", "ANDORRA", "ANGOLA", "ANTIGUA AND BARBUDA", "ARGENTINA",
    "ARMENIA", "ARUBA", "AUSTRALIA", "AUSTRIA", "AZERBAIJAN", "BAHAMAS", "BAHRAIN", "BANGLADESH",
    "BARBADOS", "BELARUS", "BELGIUM", "BELIZE", "BENIN", "BERMUDA", "BHUTAN", "BOLIVIA",
    "BOSNIA AND HERZEGOVINA", "BOTSWANA", "BRAZIL", "BRITISH VIRGIN ISLANDS", "BRUNEI", "BULGARIA",
    "BURKINA FASO", "BURUNDI", "CAMBODIA", "CAMEROON", "CANADA", "CAPE VERDE", "CAYMAN ISLANDS",
    "CHAD", "CHILE", "CHINA", "COLOMBIA", "COSTA RICA", "CROATIA", "CUBA", "CURACAO", "CYPRUS",
    "CZECH REPUBLIC", "CZECHIA", "DENMARK", "DOMINICA", "DOMINICAN REPUBLIC", "ECUADOR", "EGYPT",
    "EL SALVADOR", "ESTONIA", "ETHIOPIA", "FIJI", "FINLAND", "FRANCE", "GABON", "GEORGIA",
    "GERMANY", "GHANA", "GIBRALTAR", "GREECE", "GRENADA", "GUATEMALA", "GUERNSEY", "GUYANA",
    "HAITI", "HONDURAS", "HONG KONG", "HUNGARY", "ICELAND", "INDIA", "INDONESIA", "IRAN", "IRAQ",
    "IRELAND", "ISLE OF MAN", "ISRAEL", "ITALY", "IVORY COAST", "JAMAICA", "JAPAN", "JERSEY",
    "JORDAN", "KAZAKHSTAN", "KENYA", "KOSOVO", "KUWAIT", "KYRGYZSTAN", "LAOS", "LATVIA", "LEBANON",
    "LIECHTENSTEIN", "LITHUANIA", "LUXEMBOURG", "MACAU", "MACEDONIA", "NORTH MACEDONIA",
    "MADAGASCAR", "MALAWI", "MALAYSIA", "MALDIVES", "MALI", "MALTA", "MAURITIUS", "MEXICO",
    "MOLDOVA", "MONACO", "MONGOLIA", "MONTENEGRO", "MOROCCO", "MOZAMBIQUE", "MYANMAR", "NAMIBIA",
    "NEPAL", "NETHERLANDS", "NEW ZEALAND", "NICARAGUA", "NIGER", "NIGERIA", "NORWAY", "OMAN",
    "PAKISTAN", "PANAMA", "PAPUA NEW GUINEA", "PARAGUAY", "PERU", "PHILIPPINES", "POLAND",
    "PORTUGAL", "PUERTO RICO", "QATAR", "REUNION", "ROMANIA", "RUSSIA", "RWANDA", "SAINT LUCIA",
    "SAN MARINO", "SAUDI ARABIA", "SENEGAL", "SERBIA", "SEYCHELLES", "SIERRA LEONE", "SINGAPORE",
    "SLOVAKIA", "SLOVENIA", "SOUTH AFRICA", "SOUTH KOREA", "SPAIN", "SRI LANKA", "SUDAN",
    "SURINAME", "SWEDEN", "SWITZERLAND", "SYRIA", "TAIWAN", "TAJIKISTAN", "TANZANIA", "THAILAND",
    "TOGO", "TRINIDAD AND TOBAGO", "TUNISIA", "TURKEY", "TURKIYE", "TURKS AND CAICOS ISLANDS",
    "UGANDA", "UKRAINE", "UNITED ARAB EMIRATES", "UNITED KINGDOM", "UNITED STATES", "URUGUAY",
    "UZBEKISTAN", "VENEZUELA", "VIETNAM", "ZAMBIA", "ZIMBABWE",
]

# Variants found in the files -> standard name
COUNTRY_ALIASES = {
    "USA": "UNITED STATES", "U.S.A.": "UNITED STATES", "US": "UNITED STATES",
    "UNITED STATES OF AMERICA": "UNITED STATES",
    "UK": "UNITED KINGDOM", "U.K.": "UNITED KINGDOM", "ENGLAND": "UNITED KINGDOM",
    "SCOTLAND": "UNITED KINGDOM", "WALES": "UNITED KINGDOM", "NORTHERN IRELAND": "UNITED KINGDOM",
    "GREAT BRITAIN": "UNITED KINGDOM",
    "UAE": "UNITED ARAB EMIRATES", "U.A.E.": "UNITED ARAB EMIRATES",
    "KOREA": "SOUTH KOREA", "REPUBLIC OF KOREA": "SOUTH KOREA", "KOREA, REPUBLIC OF": "SOUTH KOREA",
    "CZECHIA": "CZECH REPUBLIC", "TURKIYE": "TURKEY", "HOLLAND": "NETHERLANDS",
    "THE NETHERLANDS": "NETHERLANDS", "JERSEY ISLAND": "JERSEY", "REUNION ISLAND": "REUNION",
    "SINGAPORE SN": "SINGAPORE", "HONG KONG SAR": "HONG KONG", "PRC": "CHINA",
    "ITALIA": "ITALY", "DEUTSCHLAND": "GERMANY", "ESPANA": "SPAIN", "SCHWEIZ": "SWITZERLAND",
    "MACEDONIA": "NORTH MACEDONIA", "IVORY COAST": "IVORY COAST",
}

# To match "ends with <country>" we try the longest names first
_ALL_NAMES = sorted(set(COUNTRIES) | set(COUNTRY_ALIASES), key=len, reverse=True)


def match_country(text):
    """
    If `text` is (or ends with) a country, returns (standard_country, rest_of_text).
    Otherwise returns (None, text).
      "GERMANY"                        -> ("GERMANY", "")
      "LONDON E14 5JP UNITED KINGDOM"  -> ("UNITED KINGDOM", "LONDON E14 5JP")
      "DOMINICAN REPU" (truncated)      -> ("DOMINICAN REPUBLIC", "")
    """
    t = " ".join(str(text).upper().replace(",", " ").split())
    if not t:
        return None, text
    for name in _ALL_NAMES:
        # "US"/"UK" only count if they are the whole line (avoids false positives)
        if len(name) <= 2 and t != name:
            continue
        if t == name or t.endswith(" " + name):
            std = COUNTRY_ALIASES.get(name, name)
            return std, t[: len(t) - len(name)].strip()
    # names cut off by the fixed width of the file (at least 8 letters)
    if len(t) >= 8:
        for name in COUNTRIES:
            if name.startswith(t):
                return name, ""
    return None, text


# 2-letter country codes seen at the end of address lines (ISO 3166, plus a few FIPS codes
# brokers use: JA = Japan, EZ = Czech Republic, SZ = Switzerland).
# Codes that are also US states/territories or Canadian provinces are left out on purpose
# (e.g. DE, IL, IN, CO, PA, MA, AR), otherwise "WILMINGTON DE" would become Germany.
# AE (UAE) is kept: the US military "AE" always comes with a ZIP code, which is matched first.
ISO2_COUNTRIES = {
    "AE": "UNITED ARAB EMIRATES", "AT": "AUSTRIA", "AU": "AUSTRALIA", "BE": "BELGIUM", "BR": "BRAZIL",
    "CH": "SWITZERLAND", "SZ": "SWITZERLAND", "CN": "CHINA", "CY": "CYPRUS", "CZ": "CZECH REPUBLIC",
    "EZ": "CZECH REPUBLIC", "DK": "DENMARK", "ES": "SPAIN", "FI": "FINLAND", "FR": "FRANCE",
    "GB": "UNITED KINGDOM", "UK": "UNITED KINGDOM", "GR": "GREECE", "HK": "HONG KONG", "IE": "IRELAND",
    "IT": "ITALY", "JP": "JAPAN", "JA": "JAPAN", "KR": "SOUTH KOREA", "KW": "KUWAIT", "LT": "LITHUANIA",
    "LU": "LUXEMBOURG", "MX": "MEXICO", "MY": "MALAYSIA", "NL": "NETHERLANDS", "NO": "NORWAY",
    "NZ": "NEW ZEALAND", "PH": "PHILIPPINES", "PL": "POLAND", "PT": "PORTUGAL", "QA": "QATAR",
    "SA": "SAUDI ARABIA", "SE": "SWEDEN", "SG": "SINGAPORE", "TH": "THAILAND", "TW": "TAIWAN",
    "ZA": "SOUTH AFRICA", "IS": "ICELAND",
}

# USPS Publication 28 standard abbreviations (street suffixes and secondary unit designators)
USPS_ABBREVIATIONS = {
    "AVENUE": "AVE", "BOULEVARD": "BLVD", "CIRCLE": "CIR", "COURT": "CT", "DRIVE": "DR",
    "EXPRESSWAY": "EXPY", "FREEWAY": "FWY", "HIGHWAY": "HWY", "LANE": "LN", "PARKWAY": "PKWY",
    "PLACE": "PL", "PLAZA": "PLZ", "ROAD": "RD", "SQUARE": "SQ", "STREET": "ST", "TERRACE": "TER",
    "TRAIL": "TRL", "POINT": "PT", "CROSSING": "XING", "CENTER": "CTR", "HEIGHTS": "HTS",
    "SUITE": "STE", "APARTMENT": "APT", "BUILDING": "BLDG", "FLOOR": "FL", "ROOM": "RM",
    "DEPARTMENT": "DEPT",
}
