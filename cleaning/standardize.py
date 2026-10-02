"""Header normalization, whitespace cleanup and data type conversion."""

import re

import pandas as pd

# Text values that must be treated as empty even though they are not blank.
NULL_LIKE = {"", "na", "n/a", "nan", "null", "none", "s/i", "sin dato", "-", "--", "?"}


def is_null_like(v):
    return v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)) or \
        (isinstance(v, str) and v.strip().lower() in NULL_LIKE)


def slugify_column(name):
    """'Sale  Date' / 'SALE_DATE' -> 'sale_date'."""
    name = str(name).strip().lower()
    name = re.sub(r"[^\w\s]", "", name)   # remove punctuation
    name = re.sub(r"\s+", "_", name)       # spaces -> underscore
    return re.sub(r"_+", "_", name).strip("_")


def standardize_columns(df, rename_map):
    """
    Applies the explicit mapping from the config first (rename_map, using the ORIGINAL
    names as they appear in the file), then normalizes (slugify) everything not mapped,
    so a file with new, unknown headers still ends up in a consistent format.

    Returns: (df, unmapped:list[(original, new)])
    """
    new_columns, unmapped = {}, []
    for col in df.columns:
        if col in rename_map:
            new_columns[col] = rename_map[col]
        else:
            slug = slugify_column(col)
            new_columns[col] = slug
            if col.strip() != slug:
                unmapped.append((col, slug))
    return df.rename(columns=new_columns), unmapped


def trim_whitespace(df):
    """Removes leading/trailing spaces and collapses double spaces in all text columns
    (fixed-width exports come padded with spaces).

    Returns: (df, cells_changed:int)
    """
    df = df.copy()
    changed = 0
    for col in df.columns:
        if df[col].dtype == object or str(df[col].dtype) == "string":
            before = df[col].astype("string")
            after = before.str.strip().str.replace(r"\s{2,}", " ", regex=True)
            changed += int(((before != after) & before.notna()).sum())
            df[col] = after
    return df, changed


def zero_pad(df, zfill_config):
    """Pads with leading zeros (e.g. a 9-character CUSIP that Excel shortened)."""
    df = df.copy()
    for col, width in (zfill_config or {}).items():
        if col in df.columns:
            s = df[col].astype("string")
            df[col] = s.str.zfill(int(width)).where(s.notna() & (s != ""), pd.NA)
    return df


def _clean_null_like(series):
    """Turns 'empty' variants ('N/A', '-', etc.) into real missing values."""
    return series.apply(lambda v: pd.NA if is_null_like(v) else v)


def coerce_types(df, types_config, date_format=None, number_format="us"):
    """
    Converts each column to the type declared in the config. The file is read entirely
    as text, so this step controls explicitly what fails instead of letting pandas guess.

    number_format: "us" (1,250.00) or "es" (1.250,00), set in columns.number_format.
    WARNING: the wrong format silently corrupts amounts (e.g. "10994.96491" read as
    "es" becomes 1,099,496,491).

    Returns: (df, log) where log is a list of dicts with:
        column, target_type, not_convertible (count), detail
    """
    log = []
    df = df.copy()

    for col, dtype in types_config.items():
        if col not in df.columns:
            log.append({"column": col, "target_type": dtype, "not_convertible": None,
                        "detail": "Column not found in the file (check 'rename' in the config)."})
            continue

        original = _clean_null_like(df[col])
        original_non_null = original.notna().sum()

        if dtype in ("integer", "float"):
            cleaned = original.astype(str).str.replace(r"[^\d,.\-]", "", regex=True)
            if number_format == "es":
                # "1.250,00" -> 1250.00  (dot = thousands, comma = decimals)
                cleaned = cleaned.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
            else:
                # "3,175,000.50" -> 3175000.50  (comma = thousands, dot = decimals)
                cleaned = cleaned.str.replace(",", "", regex=False)
            converted = pd.to_numeric(cleaned, errors="coerce")
            if dtype == "integer":
                converted = converted.round().astype("Int64")
            df[col] = converted.where(original.notna(), pd.NA)
        elif dtype == "date":
            df[col] = pd.to_datetime(original, format=date_format, errors="coerce")
        elif dtype == "string":
            df[col] = original.astype("string").str.strip()
        elif dtype == "boolean":
            true_vals = {"true", "1", "yes", "y", "si", "sí"}
            false_vals = {"false", "0", "no", "n"}

            def to_bool(v):
                if pd.isna(v):
                    return pd.NA
                v = str(v).strip().lower()
                return True if v in true_vals else (False if v in false_vals else pd.NA)
            df[col] = original.apply(to_bool)

        not_convertible = int(original_non_null - df[col].notna().sum())
        if not_convertible > 0:
            log.append({"column": col, "target_type": dtype, "not_convertible": not_convertible,
                        "detail": f"{not_convertible} value(s) could not be converted to {dtype} and were set to empty."})
    return df, log
