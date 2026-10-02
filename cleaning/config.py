"""Loads and validates the project/client YAML configuration file."""

import yaml

DEFAULTS = {
    "input": {"encoding": "utf-8", "delimiter": ",", "sheet": 0,
              "header_keyword": None, "body_ends_at_blank_row": True},
    "columns": {"rename": {}, "types": {}, "date_format": None,
                "number_format": "us", "trim_whitespace": True, "zfill": {}},
    "duplicates": {"subset": None, "keep": "first", "action": "remove"},
    "missing_values": {"strategy": {}, "default_strategy": "flag_only", "fill_values": {}},
    "validation": {"ranges": {}},
    # Optional steps for shareholder / contact lists
    "address_parsing": None,   # see config_nobo.yaml
    "reconciliation": None,    # check against the file's own control totals
    "collation": None,         # consolidate accounts into unique investors
    "deliverable": {"issuer_overrides": {}},   # monthly client file (run_monthly.py)
    "output": {"suffix": "_clean"},
}

VALID_STRATEGIES = {"drop_row", "fill_value", "fill_mean", "fill_median", "fill_mode", "flag_only", "ignore"}
VALID_TYPES = {"integer", "float", "date", "string", "boolean"}


def _deep_merge(base, override):
    """Merges 'override' onto 'base' without losing keys that were not defined."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path):
    """
    Reads a YAML config and merges it with the defaults, so a client config only
    needs to specify what differs from the standard behavior.
    """
    with open(path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}
    config = _deep_merge(DEFAULTS, user_config)

    # Minimal checks to fail fast with a clear message
    for col, strat in config["missing_values"]["strategy"].items():
        if strat not in VALID_STRATEGIES:
            raise ValueError(f"Invalid missing-value strategy for column '{col}': '{strat}'. "
                             f"Valid options: {sorted(VALID_STRATEGIES)}")
    if config["missing_values"]["default_strategy"] not in VALID_STRATEGIES:
        raise ValueError("Invalid 'default_strategy' in missing_values.")
    for col, t in config["columns"]["types"].items():
        if t not in VALID_TYPES:
            raise ValueError(f"Invalid type for column '{col}': '{t}'. Valid options: {sorted(VALID_TYPES)}")
    if config["duplicates"]["action"] not in ("remove", "flag"):
        raise ValueError("duplicates.action must be 'remove' or 'flag'.")
    if config["columns"]["number_format"] not in ("es", "us"):
        raise ValueError("columns.number_format must be 'us' (1,250.00) or 'es' (1.250,00).")
    return config
