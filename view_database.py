"""
View Kentucky Minority Business Database in D-Tale
===================================================
Opens ky_minority_businesses.csv in an interactive browser-based
data explorer. Allows sorting, filtering, charting, and exporting.

Usage:
    python view_database.py
"""

import pandas as pd
import dtale

CSV_PATH = r"C:\Users\jfraz\Claude_KY_Biz_Databse\ky_minority_businesses.csv"

print("Loading database...")
df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
print(f"Loaded {len(df)} businesses.")

print("Opening D-Tale in your browser...")
d = dtale.show(df, open_browser=True)
d.open_browser()

# Keep the script running so the browser session stays alive
input("Press Enter to close D-Tale...\n")
