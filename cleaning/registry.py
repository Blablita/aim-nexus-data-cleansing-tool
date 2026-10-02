"""Investor ID registry: permanent IDs that survive address changes.

Instead of calculating the ID from the name and ZIP every month (which changes when an
investor moves), each investor gets a sequential ID the first time it appears
(1, 2, 3 ...; numbers only, client request 2026-10-02). The ID is stored in master/investor_registry.csv together with
the investor's latest name, address and shares. Every month, the new investors are matched
against the registry, from the surest rule to the least sure:

    1. manual         a decision saved in master/id_overrides.csv            (confirmed)
    2. exact          same name + same street + same ZIP5 / postal code      (high)
    3. strong         same name + same ZIP5, or same name + same street     (high)
    4. probable_move  same name, not matched by the rules above, the old address
                      is not in this month's file, and the shares are the same
                      (within 1%). Only when there is exactly one candidate and
                      the name is not common in the issuer's list.           (medium)
    5. new            no match: a new ID is created                          (new)

Cases that look like a possible match but do not meet rule 4 (e.g. same name, different
address, different shares) go to qa/<month>/id_review_<month>.csv. A person decides and,
if they are the same investor, adds a line to master/id_overrides.csv; the next run
respects it. The scope is one issuer: the same person in two issuers gets two IDs.
"""

import os
import re

import pandas as pd

REGISTRY_COLUMNS = ["investor_id", "issuer_name", "holder_name", "name_key", "street_key", "loc_key",
                    "street_address", "city", "state_province", "postal_code", "country",
                    "first_seen_month", "last_seen_month", "last_total_shares", "address_history"]
OVERRIDE_COLUMNS = ["issuer_name", "holder_name", "postal_code", "investor_id", "note"]


# ------------------------------------------------------------------ keys
def name_key(name):
    if not isinstance(name, str) or not name.strip():
        return ""
    n = re.sub(r"[^A-Z0-9& ]", " ", name.upper())
    n = re.sub(r"\b(TOD|JT TEN|JTWROS|WROS|MR|MRS|MS|DR)\b", " ", n)
    return " ".join(n.split())


def street_key(street):
    if not isinstance(street, str):
        return ""
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", street.upper()).split())


def loc_key(row):
    pc = row.get("postal_code")
    if isinstance(pc, str) and pc.strip():
        return pc[:5] if row.get("country") == "UNITED STATES" else pc.replace(" ", "").upper()
    city = row.get("city")
    return f"{row.get('country') or ''}|{(city or '').upper()}" if isinstance(city, str) else str(row.get("country") or "")


def _shares_close(a, b, tol=0.01):
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(a - b) <= max(tol * max(abs(a), abs(b)), 1e-6)


# ------------------------------------------------------------------ files
def normalize_id(value):
    """Investor IDs are numbers only. Old IDs keep their number: 'INV-000123' -> '123'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    v = str(value).strip()
    m = re.fullmatch(r"(?:INV-)?0*(\d+)(?:\.0)?", v, flags=re.I)
    return str(int(m.group(1))) if m else v


def load_registry(path):
    if os.path.isfile(path):
        reg = pd.read_csv(path, dtype=str, keep_default_na=False).reindex(columns=REGISTRY_COLUMNS, fill_value="")
        reg["investor_id"] = reg["investor_id"].map(normalize_id)
        return reg
    return pd.DataFrame(columns=REGISTRY_COLUMNS)


def load_overrides(path):
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pd.DataFrame(columns=OVERRIDE_COLUMNS).to_csv(path, index=False, encoding="utf-8-sig")
        return pd.DataFrame(columns=OVERRIDE_COLUMNS)
    ov = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig").reindex(columns=OVERRIDE_COLUMNS, fill_value="")
    ov["investor_id"] = ov["investor_id"].map(lambda v: normalize_id(v) if v else v)
    return ov


def _next_number(reg):
    nums = reg["investor_id"].map(normalize_id).str.extract(r"^(\d+)$")[0].dropna().astype(int)
    return (nums.max() + 1) if len(nums) else 1


# ------------------------------------------------------------------ matching
def assign_ids(investors, month, registry, overrides):
    """
    investors: this month's Investors table (one row per investor per issuer, internal column names:
               issuer, holder_name, street_address, city, state_province, postal_code, country, total_shares)
    Returns: (investors with investor_id / id_match_method / id_match_confidence / address_changed,
              updated registry, review DataFrame, counts dict)
    """
    inv = investors.copy()
    inv["_name"] = inv["holder_name"].map(name_key)
    inv["_street"] = inv["street_address"].map(street_key)
    inv["_loc"] = inv.apply(loc_key, axis=1)
    inv["investor_id"] = None
    inv["id_match_method"] = None
    inv["address_changed"] = False

    reg = registry.copy()
    next_no = _next_number(reg)
    used = set()                     # registry IDs already matched this month
    review = []

    # 1) manual decisions
    ov = overrides.copy()
    if len(ov):
        ov["_name"] = ov["holder_name"].map(name_key)
        ov["_loc"] = ov.apply(lambda r: (r["postal_code"] or "")[:5] if re.match(r"^\d{5}", r["postal_code"] or "")
                              else (r["postal_code"] or "").replace(" ", "").upper(), axis=1)
        for _, o in ov.iterrows():
            hit = (inv["issuer"] == o["issuer_name"]) & (inv["_name"] == o["_name"]) & (inv["_loc"] == o["_loc"]) \
                & inv["investor_id"].isna()
            for i in inv.index[hit]:
                if o["investor_id"]:
                    inv.at[i, "investor_id"] = o["investor_id"]
                    used.add(o["investor_id"])
                inv.at[i, "id_match_method"] = "manual"

    for issuer, idx in inv.groupby("issuer").groups.items():
        R = reg[reg["issuer_name"] == issuer]
        common = set(R["name_key"].value_counts()[lambda s: s > 1].index) | \
            set(inv.loc[idx, "_name"].value_counts()[lambda s: s > 1].index)
        by_exact = {(r.name_key, r.street_key, r.loc_key): r.investor_id for r in R.itertuples()}
        by_name_loc, by_name_street, by_name = {}, {}, {}
        for r in R.itertuples():
            by_name_loc.setdefault((r.name_key, r.loc_key), []).append(r.investor_id)
            by_name_street.setdefault((r.name_key, r.street_key), []).append(r.investor_id)
            by_name.setdefault(r.name_key, []).append(r)

        def first_free(ids):
            free = [x for x in ids if x not in used]
            return free[0] if len(free) == 1 else None

        todo = [i for i in idx if inv.at[i, "investor_id"] is None and inv.at[i, "_name"]]
        # 2) exact
        for i in list(todo):
            k = (inv.at[i, "_name"], inv.at[i, "_street"], inv.at[i, "_loc"])
            rid = by_exact.get(k)
            if rid and rid not in used:
                inv.at[i, "investor_id"], inv.at[i, "id_match_method"] = rid, "exact"
                used.add(rid)
                todo.remove(i)
        # 3) strong
        for i in list(todo):
            n = inv.at[i, "_name"]
            rid = first_free(by_name_loc.get((n, inv.at[i, "_loc"]), [])) if inv.at[i, "_loc"] else None
            if not rid and inv.at[i, "_street"]:
                rid = first_free(by_name_street.get((n, inv.at[i, "_street"]), []))
            if rid:
                inv.at[i, "investor_id"], inv.at[i, "id_match_method"] = rid, "strong"
                inv.at[i, "address_changed"] = True
                used.add(rid)
                todo.remove(i)
        # 4) probable move
        current_locs = set(zip(inv.loc[idx, "_name"], inv.loc[idx, "_loc"]))
        for i in list(todo):
            n = inv.at[i, "_name"]
            cands = [r for r in by_name.get(n, []) if r.investor_id not in used
                     and (r.name_key, r.loc_key) not in current_locs]
            if not cands:
                continue
            close = [r for r in cands if _shares_close(r.last_total_shares, inv.at[i, "total_shares"])]
            if len(close) == 1 and n not in common:
                rid = close[0].investor_id
                inv.at[i, "investor_id"], inv.at[i, "id_match_method"] = rid, "probable_move"
                inv.at[i, "address_changed"] = True
                used.add(rid)
                todo.remove(i)
            else:
                for r in cands:
                    review.append({
                        "issuer_name": issuer, "holder_name": inv.at[i, "holder_name"],
                        "street_address": inv.at[i, "street_address"], "city": inv.at[i, "city"],
                        "postal_code": inv.at[i, "postal_code"], "total_shares": inv.at[i, "total_shares"],
                        "candidate_investor_id": r.investor_id, "candidate_street": r.street_address,
                        "candidate_city": r.city, "candidate_postal_code": r.postal_code,
                        "candidate_last_shares": r.last_total_shares, "candidate_last_seen": r.last_seen_month,
                        "why_not_linked": ("common name" if n in common else
                                           "shares differ" if not close else "more than one candidate"),
                    })

    # same name already registered at another address, and that one is still present this month:
    # could be a second address of the same person, or a different person -> listed for review
    for issuer, idx in inv.groupby("issuer").groups.items():
        R = reg[reg["issuer_name"] == issuer]
        names = {}
        for r in R.itertuples():
            names.setdefault(r.name_key, []).append(r)
        for i in idx:
            if inv.at[i, "investor_id"] is not None or not inv.at[i, "_name"]:
                continue
            for r in names.get(inv.at[i, "_name"], []):
                if r.investor_id in used and not any(x["candidate_investor_id"] == r.investor_id and
                                                     x["holder_name"] == inv.at[i, "holder_name"] for x in review):
                    review.append({
                        "issuer_name": issuer, "holder_name": inv.at[i, "holder_name"],
                        "street_address": inv.at[i, "street_address"], "city": inv.at[i, "city"],
                        "postal_code": inv.at[i, "postal_code"], "total_shares": inv.at[i, "total_shares"],
                        "candidate_investor_id": r.investor_id, "candidate_street": r.street_address,
                        "candidate_city": r.city, "candidate_postal_code": r.postal_code,
                        "candidate_last_shares": r.last_total_shares, "candidate_last_seen": r.last_seen_month,
                        "why_not_linked": "same name still present at the other address (second address or different person)",
                    })

    # 5) new IDs (and "manual" rows that asked for a new ID)
    for i in inv.index[inv["investor_id"].isna() & (inv["_name"] != "")]:
        inv.at[i, "investor_id"] = str(next_no)
        next_no += 1
        if inv.at[i, "id_match_method"] is None:
            inv.at[i, "id_match_method"] = "new"
    conf = {"manual": "confirmed", "exact": "high", "strong": "high", "probable_move": "medium", "new": "new"}
    inv["id_match_confidence"] = inv["id_match_method"].map(conf)

    # ---- update the registry
    reg = reg.set_index("investor_id", drop=False)
    for _, r in inv[inv["investor_id"].notna()].iterrows():
        rid = r["investor_id"]
        addr = f"{month}:{r['_street']}|{r['_loc']}"
        fields = {"issuer_name": r["issuer"], "holder_name": r["holder_name"], "name_key": r["_name"],
                  "street_key": r["_street"], "loc_key": r["_loc"], "street_address": r["street_address"] or "",
                  "city": r["city"] or "", "state_province": r["state_province"] or "",
                  "postal_code": r["postal_code"] or "", "country": r["country"] or "",
                  "last_seen_month": month, "last_total_shares": str(r["total_shares"])}
        if rid in reg.index:
            old = reg.loc[rid]
            hist = old["address_history"]
            if (old["street_key"], old["loc_key"]) != (r["_street"], r["_loc"]):
                hist = f"{hist} ; {addr}" if hist else addr
            for k, v in fields.items():
                reg.at[rid, k] = v
            reg.at[rid, "address_history"] = hist
        else:
            reg.loc[rid] = {**fields, "investor_id": rid, "first_seen_month": month, "address_history": addr}
    reg = reg.reset_index(drop=True).reindex(columns=REGISTRY_COLUMNS)

    counts = inv["id_match_method"].value_counts().to_dict()
    counts["sent_to_review"] = len(review)
    out = inv.drop(columns=["_name", "_street", "_loc"])
    return out, reg, pd.DataFrame(review), counts
