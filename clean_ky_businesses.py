"""
Kentucky Minority Business Database - Duplicate Cleaner
=======================================================
Merges duplicate business entries using the following rules:
  - Address:           Keep the most complete (longest non-null)
  - Phone:             Keep first non-null
  - Services/Products: Keep the most complete (longest non-null)
  - Website:           Keep first non-null
  - Minority Type:     Prefer specific over 'Minority-Owned (general)'; pick longest
  - Status:            Active > No Website > Inactive > null
  - Kentucky Based:    Yes > No > null

Usage:
    python clean_ky_businesses.py
"""

import pandas as pd
import os

# ── CONFIG ───────────────────────────────────────────────────────────────────
INPUT_FILE  = r"C:\Users\jfraz\The_Peoples_Ledger\ky_minority_businesses.csv"
OUTPUT_FILE = r"C:\Users\jfraz\The_Peoples_Ledger\ky_minority_businesses_cleaned.csv"
# ─────────────────────────────────────────────────────────────────────────────


def most_complete(vals):
    """Return the longest non-null, non-empty value from a list."""
    vals = [v for v in vals if pd.notna(v) and str(v).strip() not in ("", "nan", "N/A")]
    if not vals:
        return None
    return max(vals, key=len)


def best_minority_type(types):
    """Prefer specific categories over 'Minority-Owned (general)'; pick longest."""
    types = [t for t in types if pd.notna(t)]
    if not types:
        return None
    specific = [t for t in types if "Minority-Owned (general)" not in t]
    pool = specific if specific else types
    return max(pool, key=len)


def best_status(statuses):
    """Prefer Active > No Website > Inactive > null."""
    statuses = [s for s in statuses if pd.notna(s)]
    if not statuses:
        return None
    priority = {"Active": 0, "No Website": 1, "Inactive": 2}
    return min(statuses, key=lambda s: priority.get(s, 99))


def best_ky(vals):
    """Prefer Yes > No > null."""
    vals = [v for v in vals if pd.notna(v)]
    if not vals:
        return None
    if "Yes" in vals:
        return "Yes"
    return vals[0]


def merge_group(group):
    return pd.Series({
        "Address":             most_complete(group["Address"].tolist()),
        "Phone":               most_complete(group["Phone"].tolist()),
        "Services / Products": most_complete(group["Services / Products"].tolist()),
        "Website":             most_complete(group["Website"].tolist()),
        "Minority Type":       best_minority_type(group["Minority Type"].tolist()),
        "Status":              best_status(group["Status"].tolist()),
        "Kentucky Based":      best_ky(group["Kentucky Based"].tolist()),
    })


def main():
    print(f"Loading: {INPUT_FILE}")
    df = pd.read_csv(INPUT_FILE, encoding="utf-8-sig")
    print(f"Rows before cleaning: {len(df)}")
    print(f"Unique business names: {df['Business Name'].nunique()}")

    df_clean = df.groupby("Business Name", sort=False).apply(merge_group).reset_index()

    # Restore original column order
    df_clean = df_clean[[
        "Business Name", "Address", "Phone",
        "Services / Products", "Website",
        "Minority Type", "Status", "Kentucky Based"
    ]]

    print(f"\nRows after cleaning:   {len(df_clean)}")
    print(f"Duplicates removed:    {len(df) - len(df_clean)}")
    print(f"\nNull counts after cleaning:")
    print(df_clean.isnull().sum().to_string())

    df_clean.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"\nSaved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
