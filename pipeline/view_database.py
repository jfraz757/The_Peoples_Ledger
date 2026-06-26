"""
View a pipeline CSV in D-Tale
=============================
Opens a data/ CSV in an interactive browser-based explorer for sorting,
filtering, and charting. Defaults to the prepared file; pass another name to
view a different one.

Usage:
    python pipeline/view_database.py                       # data/businesses_prepared.csv
    python pipeline/view_database.py businesses_scraped.csv
"""

import os
import sys
import pandas as pd
import dtale

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
DATA_DIR     = os.path.join(REPO_ROOT, "data")

filename = sys.argv[1] if len(sys.argv) > 1 else "businesses_prepared.csv"
csv_path = filename if os.path.isabs(filename) else os.path.join(DATA_DIR, filename)

print(f"Loading {csv_path} ...")
df = pd.read_csv(csv_path, encoding="utf-8-sig")
print(f"Loaded {len(df)} rows.")

print("Opening D-Tale in your browser...")
d = dtale.show(df, open_browser=True)
d.open_browser()

input("Press Enter to close D-Tale...\n")
