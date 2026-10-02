"""
Data cleaning toolkit: flexible, configured through one YAML file per client.

Modules:
    io_utils       -> robust file reading (CSV / Excel, encoding, header detection,
                      metadata block, trailing control-total blocks)
    standardize    -> header normalization, whitespace, data types
    dedupe         -> duplicate detection (remove or flag)
    missing        -> missing value handling
    validate       -> range validation / outliers
    address        -> free-text name & address lines -> structured fields
    geo_reference  -> US states, Canadian provinces, country names
    collate        -> control-total reconciliation, unique investors, EDA
    report         -> Excel data quality report
    pipeline       -> runs all steps above for one file
"""
