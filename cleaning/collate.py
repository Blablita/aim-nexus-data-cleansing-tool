"""Analysis steps on the cleaned file:

    reconcile()    -> check against the control totals included in the file itself
    collate()      -> consolidate accounts into unique investors
    eda_tables()   -> descriptive statistics (EDA) for the report
    cross_file()   -> investors that appear in more than one file
"""

import hashlib

import pandas as pd


def investor_id(holder_key):
    """Stable ID for an investor: the same holder_key gives the same ID every month."""
    if not isinstance(holder_key, str) or not holder_key:
        return None
    return str(int(hashlib.sha1(holder_key.encode("utf-8")).hexdigest()[:10], 16))


def _num(values):
    return pd.to_numeric(pd.Series(values).astype(str).str.replace(",", "", regex=False), errors="coerce")


def reconcile(df, trailer, metadata, cfg, filename):
    """
    Compares (a) the number of rows and (b) the sum of a numeric column against the
    file's control totals. cfg (reconciliation section):
        count_label:  label of the row total in the trailing block (e.g. "# OF NOBOS")
        sum_label:    label of the column total (e.g. "NUMBER OF SHARES")
        sum_column:   standardized column to add up (e.g. "shares")
        broker_count_column: column with row counts in the broker table (e.g. "NUMBER OF NOBOS")
        tolerance:    accepted difference in the sum (default 0.01)
    If the file has no control totals, the result is "NO CONTROL TOTALS".
    """
    rows = []
    tol = float(cfg.get("tolerance", 0.01))
    control = {}
    for t in trailer:
        if len(t) == 1:
            control.update(t.iloc[0].to_dict())
    n_rows = len(df)
    col = cfg.get("sum_column")
    total = float(df[col].sum()) if col in df.columns else None

    def add(check, expected, obtained, ok):
        rows.append({"file": filename, "check": check, "expected_per_file": expected,
                     "obtained_after_cleaning": obtained,
                     "result": "OK" if ok else ("NO CONTROL TOTALS" if ok is None else "MISMATCH")})

    cl, sl = cfg.get("count_label"), cfg.get("sum_label")
    if cl and cl in control:
        exp = int(_num([control[cl]]).iloc[0])
        add(f"rows ({cl})", exp, n_rows, exp == n_rows)
    elif cl and cl in metadata:
        exp = int(_num([metadata[cl]]).iloc[0])
        add(f"rows ({cl}, header block)", exp, n_rows, exp == n_rows)
    else:
        add("rows", "not in file", n_rows, None)
    if sl and sl in control and total is not None:
        exp = float(_num([control[sl]]).iloc[0])
        add(f"sum of {col} ({sl})", round(exp, 5), round(total, 5), abs(exp - total) <= tol)
    else:
        add(f"sum of {col}", "not in file", total, None)
    # broker table: the sum of its counts must also equal the number of rows
    bcol = cfg.get("broker_count_column")
    for t in trailer:
        if len(t) > 1 and bcol and bcol in t.columns:
            exp = int(_num(t[bcol]).sum())
            add(f"rows (sum of broker table, {bcol})", exp, n_rows, exp == n_rows)
    return pd.DataFrame(rows)


def _join_types(values):
    return ",".join(sorted({x for v in values.dropna() for x in str(v).split(",") if x}))


def collate(df, cfg, filename, issuer=None):
    """
    Groups accounts by `holder_key` (normalized name + postal code).
    cfg (collation section): value_column (e.g. "shares").
    Rows without a key (no holder name) are left out and counted separately.
    """
    val = cfg.get("value_column", "shares")
    d = df[df["holder_key"].notna()].copy()
    total = df[val].sum() if val in df.columns else None
    g = (d.groupby("holder_key", dropna=False)
          .agg(holder_name=("holder_name", "first"), entity_type=("entity_type", "first"),
               street_address=("street_address", "first"),
               city=("city", "first"), state_province=("state_province", "first"),
               postal_code=("postal_code", "first"),
               country=("country", "first"), is_international=("is_international", "first"),
               n_accounts=("holder_name", "size"), total_shares=(val, "sum"),
               account_types=("account_type", _join_types),
               accounts_not_fully_parsed=("parse_status", lambda x: int((x != "ok").sum())))
          .reset_index())
    g.insert(1, "investor_id", g["holder_key"].map(investor_id))
    g["pct_of_shares"] = (g["total_shares"] / total * 100).round(4) if total else None
    g.insert(0, "file", filename)
    g.insert(1, "issuer", issuer)
    g = g.sort_values("total_shares", ascending=False).reset_index(drop=True)
    info = {"accounts": len(df), "accounts_without_name": int(df["holder_key"].isna().sum()),
            "unique_investors": len(g), "investors_with_multiple_accounts": int((g["n_accounts"] > 1).sum())}
    return g, info


def eda_tables(df, value_col, filename):
    """EDA tables: by country, by entity type, home vs international, holding size."""
    out = {}
    v = df[value_col]
    base = pd.DataFrame({
        "file": filename, "country": df["country"].fillna("(unknown)"),
        "entity_type": df["entity_type"], "value": v,
        "home_vs_international": df["is_international"].map({True: "international", False: "home country"}).fillna("(unknown)"),
    })

    def summarize(by):
        t = base.groupby(["file", by]).agg(accounts=("value", "size"), shares=("value", "sum")).reset_index()
        t["pct_accounts"] = (t["accounts"] / len(base) * 100).round(2)
        t["pct_shares"] = (t["shares"] / base["value"].sum() * 100).round(2)
        return t.sort_values("shares", ascending=False)

    out["by_country"] = summarize("country")
    out["by_entity"] = summarize("entity_type")
    out["home_vs_international"] = summarize("home_vs_international")
    bins = [-0.001, 0, 1, 10, 100, 1_000, 10_000, 100_000, float("inf")]
    labels = ["0", "(0-1]", "(1-10]", "(10-100]", "(100-1K]", "(1K-10K]", "(10K-100K]", ">100K"]
    base["holding_size"] = pd.cut(v, bins=bins, labels=labels)
    out["holding_size"] = (base.groupby(["file", "holding_size"], observed=False)
                               .agg(accounts=("value", "size"), shares=("value", "sum")).reset_index())
    stats = v.describe(percentiles=[.25, .5, .75, .9, .99]).to_dict()
    out["statistics"] = pd.DataFrame([{"file": filename, **{f"shares_{k}": round(x, 4) for k, x in stats.items()},
                                       "fractional_accounts": int((v % 1 != 0).sum())}])
    return out


def cross_file(investor_tables):
    """Investors (same key) present in more than one file.
    Note: if one file is an extract of another (e.g. NOBO-OP from NIAGEN), matches
    between those two are expected and do not mean the investor holds two issuers;
    see the `n_issuers` column (files with no issuer in the header count as unknown)."""
    if len(investor_tables) < 2:
        return pd.DataFrame()
    allv = pd.concat(investor_tables, ignore_index=True)
    g = (allv.groupby("holder_key")
             .agg(holder_name=("holder_name", "first"), entity_type=("entity_type", "first"),
                  country=("country", "first"), n_files=("file", "nunique"),
                  n_issuers=("issuer", lambda s: s.dropna().nunique()),
                  files=("file", lambda s: " | ".join(sorted(set(s)))),
                  issuers=("issuer", lambda s: " | ".join(sorted({str(x) for x in s if isinstance(x, str) and x}))),
                  total_shares=("total_shares", "sum"))
             .reset_index())
    return g[g["n_files"] > 1].sort_values(["n_issuers", "n_files", "total_shares"], ascending=False)
