"""Range validation and detection of out-of-range values."""

import pandas as pd


def out_of_range_mask(series, bounds):
    """True where the value is present and outside [min, max]."""
    lo, hi = bounds.get("min"), bounds.get("max")
    mask = pd.Series(False, index=series.index)
    present = series.notna()
    if lo is not None:
        mask |= present & (series < lo)
    if hi is not None:
        mask |= present & (series > hi)
    return mask


def validate_ranges(df, ranges_config):
    """
    For each column with a range ({min, max}) in the config, flags (does not delete)
    the rows whose value is out of range, so they go to the manual review file.

    Returns: (flagged_index:set, log:list[dict])
    """
    flagged_index, log = set(), []
    for col, bounds in ranges_config.items():
        if col not in df.columns:
            continue
        bad = out_of_range_mask(df[col], bounds)
        if bad.any():
            flagged_index.update(df.index[bad].tolist())
            lo, hi = bounds.get("min"), bounds.get("max")
            log.append({"column": col,
                        "expected_range": f"[{lo if lo is not None else '-inf'}, {hi if hi is not None else '+inf'}]",
                        "out_of_range_values": int(bad.sum())})
    return flagged_index, log
