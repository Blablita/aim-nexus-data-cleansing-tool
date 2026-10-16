#!/usr/bin/env python3
"""Kept so older commands still work (python run_monthly.py --month ...). The script is now
run_cleaning.py: one run per dataset, identified by its run date (YYYY-MM-DD)."""
import os
import runpy
import sys

sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_cleaning.py")
runpy.run_path(sys.argv[0], run_name="__main__")
