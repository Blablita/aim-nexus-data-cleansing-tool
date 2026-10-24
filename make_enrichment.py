"""Step after the manual review: builds the file to send to the data broker from the REVIEWED clean file.

    python make_enrichment.py "deliverables\\2026-09-22\\clean_database_2026-09-22_reviewed.xlsx"

Writes enrichment_request_<date>.xlsx and .csv next to the reviewed file (or in --out). One row per investor
with a US address, type individual and a primary_contact. investor_id is the key to join the enriched file
back: the broker must return it unchanged.
"""
import argparse
import os
import re
import sys
from datetime import date

from cleaning.enrichment import build_enrichment_request, read_reviewed, write_outputs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reviewed_file", help="reviewed clean_database .xlsx (or investors .csv)")
    ap.add_argument("--out", help="output folder (default: the folder of the reviewed file)")
    a = ap.parse_args()
    if not os.path.isfile(a.reviewed_file):
        sys.exit(f"File not found: {a.reviewed_file}")
    m = re.search(r"\d{4}-\d{2}-\d{2}", os.path.basename(a.reviewed_file))
    label = m.group(0) if m else date.today().isoformat()
    enrich, counts = build_enrichment_request(read_reviewed(a.reviewed_file))
    xlsx, csv = write_outputs(enrich, a.out or os.path.dirname(os.path.abspath(a.reviewed_file)), label)
    print(f"Enrichment request: {counts['sent']:,} US individuals; not sent: {counts['international_not_sent']:,} "
          f"international, {counts['us_not_a_person_or_no_contact']:,} US companies/institutions or without a "
          f"primary contact.\n  {xlsx}\n  {csv}\nThe broker must return investor_id unchanged in every row.")


if __name__ == "__main__":
    main()
