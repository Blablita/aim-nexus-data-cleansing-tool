# UC1 Cleaning Engine: Integration Notes for Azure (UC3)

Prepared by Maria Claudia (Blablita), UC1, for Kessia, UC3, AIM Nexus / DataSphere Lab project.

This note explains what the UC1 data-cleaning engine is, which files you actually need, how to call it, and where the one hard dependency (local persistent storage) lives, so you can design the Azure side around it.

## 1. What you need vs. what you don't

You do not need the Streamlit front end at all. That part (app.py, run_app.bat, .streamlit/) is only the UI built for the client to test manually; it has no role in the cleaning logic and should not be part of the Azure integration.

What you do need is the cleaning engine itself:

- The whole cleaning/ package (all modules inside it: address.py, collate.py, config.py, dedupe.py, deliverable.py, geo_reference.py, io_utils.py, missing.py, pipeline.py, registry.py, report.py, standardize.py, validate.py, verification.py, __init__.py)
- run_cleaning.py (the orchestrator script; run_monthly.py is kept as a wrapper for older commands)
- config_nobo.yaml (the client-specific cleaning rules file)

All of this is already pushed to the GitHub repo (aim-nexus-data-cleansing-tool), so you can pull it from there directly instead of getting files sent separately.

## 2. Dependencies

The engine only needs:

pandas>=2.0
PyYAML>=6.0
openpyxl>=3.1
xlrd>=2.0

streamlit is listed in requirements.txt too, but that is only for the front end. The Azure environment does not need it.

## 3. How it's called today

Right now the engine is folder-based and CLI-based, not an importable function. It's invoked like this:

python run_cleaning.py --run-date 2026-10-16 --config config_nobo.yaml --base <folder>

- --run-date: YYYY-MM-DD (one run per dataset; target one dataset per week). If omitted, it picks the latest folder it finds under <folder>/inbox/. --month still works as an alias.
- --config: path to the YAML rules file (config_nobo.yaml for this client).
- --base: the folder that contains the inbox/, deliverables/, qa/ and master/ subfolders described below. Defaults to the script's own folder if not given.

### Folder structure it expects

<base>/
  inbox/<run date>/           <- raw client files go here (input)
  deliverables/<run date>/    <- clean_database_<run date>.xlsx + CSVs (output, client-facing; also read by later runs to compare each issuer with its previous list)
  qa/<run date>/              <- quality_report, run_log, id_review (output, internal only)
  master/                     <- see section 4, this is the important one

## 4. The part that matters most for your Azure design: master/

Inside <base>/master/ the script keeps three files that persist across runs (not just within one run):

- investor_registry.csv: the running registry of every investor we've ever assigned an ID to, so the same person gets the same investor ID in every run.
- id_migration.csv: old ID -> new 9-digit ID pairs (one-time conversion, Oct 2026). Must persist with the registry.
- id_overrides.csv: manual corrections to specific investor ID assignments.
- manual_verification.csv: records flagged for manual review instead of being auto-matched.
- master/snapshots/: a snapshot of the registry taken before each run, so a run can be safely redone if something goes wrong.

This is the one piece of state the engine depends on between runs, and today it only exists as local files on disk. On Streamlit Community Cloud this already caused a problem (the filesystem there is ephemeral, so master/ does not survive a restart), which is why this was not solved on the UC1 side. It is squarely a UC3/Azure decision: Blob Storage, a database table, or an Azure File Share would all work. Whatever is chosen, the only requirement from the UC1 side is that these same three CSVs (or an equivalent data structure holding the same fields) need to be readable and writable by the script before and after each monthly run.

## 5. A design note, not a request

Because the script is currently CLI/folder-based rather than exposing a clean Python function (e.g. clean_dataset(files, config) -> result), it may be worth wrapping it in something more Azure-Function-friendly (reading from Blob, writing a temp folder, calling the equivalent of run_monthly.main(), writing results back to Blob) rather than shelling out to the script as-is. That is a design choice to align on together, not something already changed unilaterally. Happy to adjust the script's interface (e.g. add an importable function) if that makes the integration cleaner, just say what shape is needed.

## 6. Quick reference

| Item | Value |
|---|---|
| Entry point | run_cleaning.py |
| Config file | config_nobo.yaml |
| Input | <base>/inbox/<run date>/ (raw files) |
| Output (client) | <base>/deliverables/<run date>/clean_database_<run date>.xlsx + CSVs |
| Output (internal QA) | <base>/qa/<run date>/ |
| Persistent state | <base>/master/ (investor_registry.csv, id_overrides.csv, id_migration.csv, manual_verification.csv, snapshots/) |
| Dependencies | pandas, PyYAML, openpyxl, xlrd |
| Not needed | app.py, run_app.bat, .streamlit/, streamlit package |
