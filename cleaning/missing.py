"""Missing value handling, with one strategy per column."""

import pandas as pd


def handle_missing(df, strategy_map, default_strategy="flag_only", fill_values=None):
    """
    Strategies per column:
        drop_row     -> drops the row if that column is empty
        fill_value   -> fills with a fixed value (fill_values[col])
        fill_mean    -> fills with the column mean (numeric)
        fill_median  -> fills with the column median (numeric)
        fill_mode    -> fills with the most frequent value
        flag_only    -> leaves the value as is, but the row is flagged for manual review
        ignore       -> leaves the value as is and does NOT flag the row (for columns
                        that are normally empty, e.g. address lines 5-7)

    Columns not listed in strategy_map use 'default_strategy'.

    Returns: (df, flagged_index:set, log:list[dict])
    """
    fill_values = fill_values or {}
    df = df.copy()
    flagged_index, log, rows_to_drop = set(), [], set()

    for col in df.columns:
        n_missing = int(df[col].isna().sum())
        if n_missing == 0:
            continue
        strategy = strategy_map.get(col, default_strategy)
        if strategy == "ignore":
            continue

        if strategy == "drop_row":
            rows_to_drop.update(df.index[df[col].isna()].tolist())
        elif strategy == "fill_value":
            df[col] = df[col].fillna(fill_values.get(col))
        elif strategy in ("fill_mean", "fill_median"):
            if pd.api.types.is_numeric_dtype(df[col]):
                value = df[col].mean() if strategy == "fill_mean" else df[col].median()
                df[col] = df[col].fillna(value)
            else:
                strategy = f"flag_only (non-numeric column, {strategy} not possible)"
                flagged_index.update(df.index[df[col].isna()].tolist())
        elif strategy == "fill_mode":
            mode = df[col].mode(dropna=True)
            if not mode.empty:
                df[col] = df[col].fillna(mode.iloc[0])
        else:  # flag_only
            flagged_index.update(df.index[df[col].isna()].tolist())

        log.append({"column": col, "missing_values": n_missing, "strategy_applied": strategy})

    if rows_to_drop:
        df = df.drop(index=list(rows_to_drop))
    return df, flagged_index, log
