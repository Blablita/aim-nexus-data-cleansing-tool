# Data Cleaning Toolkit

Flexible, reusable toolkit to clean and organize client CSV/Excel files. The code does not change between clients: you only adjust one configuration file (`config_<client>.yaml`).

Current use: AIM Nexus, UC1 (Data PreProcessing Automation), NOBO shareholder lists.

## One run per dataset (how the client uses it)

Target: one dataset processed per week. Each run is identified by its **run date** (`YYYY-MM-DD`), so several datasets can be processed in the same month without overwriting each other.

1. Create a folder for the run inside `inbox`, named with the run date (for example `inbox\2026-10-16`).
2. Put that dataset's raw files in it (`.csv`, `.xls`, `.xlsx`).
3. Double-click **`run_cleaning.bat`** (it takes the latest run folder in `inbox`), or use the front end (`run_app.bat`).

Result:

| Folder | Content | Audience |
|---|---|---|
| `deliverables\2026-10-16\` | `clean_database_2026-10-16.xlsx` (Summary, Investors, Original_Database, Identical_Rows, Manual_Verification, Exited_Investors, Data_Dictionary, Account_Types) + `investors_2026-10-16.csv` + `original_database_2026-10-16.csv` | Client / buyer |
| `qa\2026-10-16\` | quality report, manual review files, per-file clean files, run log | Internal |

- The deliverable has a fixed layout in every run. The full rules are in `UC1_Data_Requirements_and_Rules.docx`.
- **Investor IDs** are permanent 9-digit codes (numbers only, kept as text, not in sequence) and stored in `master\investor_registry.csv`. IDs from earlier runs (1, 2, 3...) are converted once; the old -> new pairs are kept in `master\id_migration.csv` (do not delete it). In each run, investors are matched to the registry: manual (from `master\id_overrides.csv`), exact (name + street + postal code), strong (name + postal code, or name + street: the same address written differently), otherwise new. A new address is a new ID (client rule 2026-10-24: an investor who moves is technically a new account); the same name at an earlier address is only listed for information. Unsure cases go to `qa\<run date>\id_review_<run date>.csv`. To link one, add a line to `master\id_overrides.csv` (issuer_name, holder_name, postal_code, investor_id, note) and run it again. A registry snapshot is kept before each run, so re-running a run gives the same IDs. Run datasets in date order.
- If the Excel file is open while running, a copy with a time stamp is saved instead. Close Excel and run again.
- Each issuer is compared with **its previous list** (the latest earlier run that has the same issuer, which can be several runs back): each investor is marked `FIRST LIST`, `NEW` or `CONTINUING` with `previous_record_date`, `previous_shares` and `shares_change`, and investors who left are listed in `Exited_Investors`.
- `control_totals` in the Summary sheet must say `OK` before the file is delivered. `CHECK` means the rows or shares do not match the totals printed in the raw file.
- To run a specific date: `run_cleaning.bat --run-date 2026-10-16` (from a command window) or `python run_cleaning.py --run-date 2026-10-16`. The old commands (`run_monthly.bat`, `--month`) still work; older run folders named `YYYY-MM` are still read.
- The Investors sheet is sorted by `total_shares`, largest first. `reference\\NOBO-OP_client_example.csv` is the example of what the client wants to see; it is not an input file.
- Files with no issuer in their header show as `UNKNOWN ISSUER`. Assign one in `config_nobo.yaml` under `deliverable: issuer_overrides` once the client confirms it.

**Before selling or sharing the deliverable:** NOBO information has use restrictions (US: SEC Rule 14a-13(b)(4), "exclusively for purposes of corporate communications"; Canada: NI 54-101, s. 7.1). Confirm with the client who receives the file and for what purpose.

## After the manual review: file for the data broker

The cleaning run does not create the broker file. After the manual review of the clean file (Silver reviewed),
build it from the REVIEWED file, so the reviewer's corrections are what is sent:

- App: step 4 "Enrichment file (after the manual review)": upload the reviewed clean_database .xlsx and download
  enrichment_request_<date>.xlsx / .csv.
- Command line: `python make_enrichment.py "deliverables\<run date>\clean_database_<run date>_reviewed.xlsx"`
  (the files are written next to the reviewed file).

One row per investor with a US address, type individual and a primary_contact: investor_id, issuer_name, CUSIP,
holder_name, primary_contact, first / middle / last name, suffix, address. Rows rejected in the manual verification
are not sent. The broker must return investor_id unchanged: it is the key to join the enriched file back.

## One-off run on any files (NOBO)

1. Put the client files (`.csv`, `.xls`, `.xlsx`) in the folder **above** this one (`Data cleaning`).
2. Double-click **`run_nobo.bat`**.
3. Open the `output_nobo` folder when it finishes.

The `.bat` file finds Python (python on PATH, the `py` launcher or Anaconda), installs the packages in `requirements.txt` if they are missing, and runs the cleaning with `--verbose`.

If it cannot find Python, open **Anaconda Prompt** and run:

```
cd "C:\Users\Blablita\Desktop\MMA\DataSphere\Data cleaning\data_cleaning_toolkit"
pip install -r requirements.txt
python clean.py --input "..\*.csv" "..\*.xls" --config config_nobo.yaml --output output_nobo --verbose
```

## What the pipeline does (in order)

| # | Step | What happens |
|---|---|---|
| 1 | Read | Reads CSV or Excel. Tries several encodings (utf-8, cp1252, latin-1). Finds the header row by keyword (`SHARES`), keeps the top block as metadata (issuer, CUSIP, record date) and separates the trailing blocks (control totals, broker table). |
| 2 | Headers | Applies the `rename` mapping; anything else is normalized to `lower_case_with_underscores`. |
| 3 | Whitespace | Removes padding and double spaces (fixed-width exports). |
| 4 | Data types | Converts columns to number/date/text. `number_format` must match the file (`us` = 1,250.00, `es` = 1.250,00). |
| 5 | Duplicates | `remove` drops them (saved in a separate file) or `flag` keeps them with `dup_exact` / `dup_copies`. |
| 6 | Missing values | One strategy per column: `drop_row`, `fill_value`, `fill_mean`, `fill_median`, `fill_mode`, `flag_only`, `ignore`. |
| 7 | Ranges | Values outside `{min, max}` go to manual review (never deleted). |
| 8 | Name & address (optional) | Free-text lines become `holder_name`, `street_address`, `city`, `state_province`, `postal_code`, `country`, `is_international`, `entity_type`, `account_type`, with `parse_status` and `parse_notes`. |
| 9 | Control totals (optional) | Checks the row count and total shares against the totals printed in the file. |
| 10 | Investors + EDA (optional) | Groups accounts by `holder_key` (normalized name + postal code) into unique investors; builds EDA tables. |
| 11 | Outputs | Writes the files listed below. |

## Outputs

For each input file:
- `<file>_clean.csv`: all rows, cleaned, with the new columns.
- `<file>_manual_review.csv`: rows that need a human decision, with `review_reason` and `original_row`.
- `<file>_investors.csv`: one row per unique investor (when collation is on).
- `<file>_removed_duplicates.csv`: only when `duplicates.action: remove`.

For the whole run:
- `quality_report_<timestamp>.xlsx`: Summary, Detailed_Log, Control_Totals, Investor_Summary, Top_Investors, Cross_File, EDA_* sheets.
- `run_log_<timestamp>.txt`: the full console output, step by step.

## Design choices

- **Flag, do not guess.** Ambiguous cases (empty name, zero shares, an address that cannot be parsed) go to the manual review file instead of being "fixed" automatically.
- **Identical rows are flagged, not removed, for NOBO lists.** The broker counts them as separate accounts; removing them would break the control totals.
- **Conservative investor matching.** The same name with a different postal code is NOT merged.

## Adapting it to a new client

1. Copy `config_example.yaml` to `config_<client>.yaml`.
2. Set `input` (header keyword, encoding), `columns` (rename, types, number format), `duplicates`, `missing_values`, `validation`.
3. Turn on `address_parsing`, `reconciliation` and `collation` only if they apply.
4. Run: `python clean.py --input "path\to\files\*.csv" --config config_<client>.yaml --output output_<client> --verbose`

## Folder structure

```
data_cleaning_toolkit/
├── run_cleaning.bat        # double-click: one run (inbox/<run date> -> deliverables/<run date>)
├── run_cleaning.py         # run script (run_monthly.* kept for older commands)
├── run_nobo.bat            # one-off run on the files in the folder above
├── clean.py                # command line entry point (one-off runs)
├── inbox/ deliverables/ qa/   # one folder per run date (created automatically)
├── config_nobo.yaml        # config for the AIM Nexus NOBO lists
├── config_example.yaml     # template / demo config
├── notebook_example.ipynb  # same flow, step by step, in Jupyter
├── cleaning/               # the toolkit itself
│   ├── io_utils.py  standardize.py  dedupe.py  missing.py  validate.py
│   ├── address.py  contacts.py  geo_reference.py  collate.py  deliverable.py  registry.py
│   ├── report.py  pipeline.py  config.py
├── sample_data/sales_example.csv   # small synthetic "dirty" file for testing
└── requirements.txt
```

## Known limitations (first run on real data, Sep 22, 2026)

- Addresses are standardized with USPS abbreviations, but not validated against the USPS database: a missing directional (e.g. the 'W' in '4700 W TIETON DR') cannot be added without an address validation service.
- Two street lines without house numbers can be split wrongly between name and street (see `parse_notes`).
- NOBO-OP has no issuer in its header, so its overlap with the NIAGEN list shows up in `Cross_File` (see `n_issuers`).
- If two files of the same issuer arrive in the same run and one is an extract of the other, accounts are counted twice (the run prints a NOTE).
