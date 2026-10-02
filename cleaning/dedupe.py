"""Duplicate detection: remove duplicates or only flag them (set in the config)."""


def remove_duplicates(df, subset=None, keep="first", action="remove"):
    """
    subset: columns that define a duplicate (None = all columns).
    action:
        "remove" -> drops the copies (they are saved separately for traceability)
        "flag"   -> drops NOTHING; adds two columns:
                      dup_exact    True on the 2nd, 3rd... copy of a row
                      dup_copies   how many identical rows exist in total
                    Useful when the file has control totals that count every row
                    (e.g. NOBO lists: two identical accounts of the same holder are
                    two real accounts for the broker).

    Returns: (result_df, removed_duplicates_df, log:dict)
    """
    cols = subset if subset else [c for c in df.columns if not c.startswith("dup_")]
    cols = [c for c in cols if c in df.columns]

    is_dup = df.duplicated(subset=cols, keep=keep)
    log = {"duplicates_found": int(is_dup.sum()), "columns_used": cols, "kept": keep, "action": action}

    if action == "flag":
        out = df.copy()
        out["dup_exact"] = is_dup
        out["dup_copies"] = out.groupby(cols, dropna=False)[cols[0]].transform("size")
        log["duplicates_removed"] = 0
        return out, df.iloc[0:0], log

    log["duplicates_removed"] = int(is_dup.sum())
    return df[~is_dup].copy(), df[is_dup].copy(), log
