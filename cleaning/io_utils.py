"""Robust reading of tabular files (CSV / XLS / XLSX) whose structure is not known in advance.

Besides plain tables, this module understands "report style" files such as NOBO lists:

    [metadata block]          <- e.g. JOB NUMBER, CUSIP, ISSUER NAME, RECORD DATE
    [blank row]
    [header row]              <- found by a keyword (e.g. "SHARES")
    [data]                    <- the body that gets cleaned
    [blank row]
    [trailing blocks]         <- e.g. control totals, breakdown by broker

Everything is controlled from the `input` section of the config.
"""

import csv
import os

import pandas as pd

# Encodings to try, in order, if the one in the config fails.
FALLBACK_ENCODINGS = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]


# --------------------------------------------------------------------------
# 1. Raw read: everything as text, no assumption about where the header is
# --------------------------------------------------------------------------
def _read_raw_csv(path, encoding, delimiter):
    """Reads the whole CSV as a grid of text (header=None)."""
    warnings, tried, last_error = [], [], None
    for enc in [encoding] + [e for e in FALLBACK_ENCODINGS if e != encoding]:
        tried.append(enc)
        try:
            raw = pd.read_csv(path, encoding=enc, sep=delimiter, dtype=str, header=None,
                              keep_default_na=False, skip_blank_lines=False)
            if enc != encoding:
                warnings.append(f"Encoding '{encoding}' failed; '{enc}' was used automatically.")
            return raw, enc, warnings
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
        except pd.errors.ParserError:
            # rows with a different number of columns: read tolerantly
            raw = _read_ragged_csv(path, enc, delimiter)
            warnings.append("Rows have different numbers of columns; file was read in tolerant mode.")
            return raw, enc, warnings
    raise ValueError(f"Could not read '{path}' with any of the encodings {tried}. Last error: {last_error}")


def _read_ragged_csv(path, enc, delimiter):
    with open(path, encoding=enc, newline="") as f:
        rows = list(csv.reader(f, delimiter=delimiter))
    width = max(len(r) for r in rows)
    return pd.DataFrame([r + [""] * (width - len(r)) for r in rows]).astype(str)


def _cell_to_text(v):
    """Excel stores 171077407 as 171077407.0; return it as '171077407'."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v)


def _read_raw_excel(path, sheet=0):
    ext = os.path.splitext(path)[1].lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object, engine=engine)
    raw = raw.apply(lambda col: col.map(lambda v: "" if pd.isna(v) else _cell_to_text(v)))
    return raw, f"excel ({engine})", []


# --------------------------------------------------------------------------
# 2. Split metadata / header / body / trailing blocks
# --------------------------------------------------------------------------
def _blank_mask(raw):
    return raw.apply(lambda r: all(str(v).strip() == "" for v in r), axis=1)


def _find_header_row(raw, keyword):
    """First row where any cell (trimmed, upper case) equals `keyword`."""
    if not keyword:
        return 0
    kw = keyword.strip().upper()
    for i, row in raw.iterrows():
        if any(str(v).strip().upper() == kw for v in row):
            return i
    raise ValueError(f"Header row not found (no cell says '{keyword}').")


def _split_blocks(df):
    """Splits a DataFrame into blocks separated by blank rows."""
    blocks, current = [], []
    for i, is_blank in _blank_mask(df).items():
        if is_blank:
            if current:
                blocks.append(df.loc[current])
                current = []
        else:
            current.append(i)
    if current:
        blocks.append(df.loc[current])
    return blocks


def _block_to_table(block):
    """A trailing block: its first row is the header."""
    block = block.loc[:, block.apply(lambda c: (c.str.strip() != "").any())]
    table = block.iloc[1:].copy()
    table.columns = [str(h).strip() for h in block.iloc[0]]
    return table.apply(lambda c: c.str.strip()).reset_index(drop=True)


def read_table_robust(path, input_cfg):
    """
    Reads a file and returns a dict with:
        data      -> DataFrame with the body (text, header applied)
        metadata  -> dict with the top block (e.g. ISSUER NAME, CUSIP, RECORD DATE)
        trailer   -> list of DataFrames with the trailing blocks (totals, brokers)
        encoding  -> encoding / engine used
        warnings  -> warnings for the log
        layout    -> text describing how the file was interpreted (--verbose mode)
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xls", ".xlsx", ".xlsm"):
        raw, enc, warnings = _read_raw_excel(path, input_cfg.get("sheet", 0))
    else:
        raw, enc, warnings = _read_raw_csv(path, input_cfg.get("encoding", "utf-8"),
                                           input_cfg.get("delimiter", ","))
    raw = raw.reset_index(drop=True)
    raw.columns = range(raw.shape[1])

    header_row = _find_header_row(raw, input_cfg.get("header_keyword"))

    # --- metadata: rows above the header, read as header/value pairs
    metadata = {}
    top = raw.iloc[:header_row]
    for blk in (_split_blocks(top) if len(top) else []):
        if len(blk) >= 2:
            for k, v in zip(blk.iloc[0], blk.iloc[1]):
                if str(k).strip():
                    metadata[str(k).strip()] = str(v).strip()

    # --- body: from the header to the first blank row AFTER data has started
    header = [str(h).strip() for h in raw.iloc[header_row]]
    after = raw.iloc[header_row + 1:]
    body_idx, end = [], None
    stop_at_blank = input_cfg.get("body_ends_at_blank_row", True)
    for i, is_blank in _blank_mask(after).items():
        if is_blank:
            if body_idx and stop_at_blank:
                end = i
                break
            continue  # blank rows before the data are skipped
        body_idx.append(i)
    data = after.loc[body_idx].copy()
    # unnamed columns that are always empty (trailing commas) are dropped
    data.columns = [h if h else f"_col{j}" for j, h in enumerate(header)]
    data = data[[c for c in data.columns if not (c.startswith("_col") and (data[c].str.strip() == "").all())]]
    data = data.reset_index(drop=True)

    trailer = [_block_to_table(b) for b in _split_blocks(raw.iloc[end:])] if end is not None else []

    layout = (f"header on row {header_row + 1}; {len(metadata)} metadata field(s); "
              f"{len(data)} data rows; {len(trailer)} trailing block(s)"
              + (f" ({', '.join(str(list(t.columns)[:2]) for t in trailer)})" if trailer else ""))
    return {"data": data, "metadata": metadata, "trailer": trailer,
            "encoding": enc, "warnings": warnings, "layout": layout}


def read_csv_robust(path, encoding="utf-8", delimiter=","):
    """Backward compatible with the Milestone 2 version."""
    res = read_table_robust(path, {"encoding": encoding, "delimiter": delimiter})
    return res["data"], res["encoding"], res["warnings"]
