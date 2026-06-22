"""
Upload Kentucky Minority Business CSV to Supabase
==================================================
Reads the cleaned CSV and inserts all records into the
Supabase 'businesses' table in batches.

Requirements:
    pip install supabase pandas python-dotenv

.env file (in the same folder as this script):
    SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co
    SUPABASE_KEY=your_anon_key_here

Usage:
    python upload_to_supabase.py
"""

import os
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

# ── CONFIG ───────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CSV_PATH     = r"C:\Users\jfraz\The_Peoples_Ledger\ky_minority_businesses_cleaned.csv"
BATCH_SIZE   = 100
# ─────────────────────────────────────────────────────────────────────────────


def main():
    # Load CSV
    print("Loading CSV...")
    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    print(f"Records to upload: {len(df)}")

    # Rename columns to match database schema
    df = df.rename(columns={
        "Business Name":    "business_name",
        "Address":          "address",
        "Phone":            "phone",
        "Services / Products": "services_products",
        "Website":          "website",
        "Minority Type":    "minority_type",
        "Status":           "status",
        "Kentucky Based":   "kentucky_based"
    })

    # Replace NaN with None so Supabase accepts nulls
    df = df.astype(object).where(pd.notna(df), None)

    # Connect to Supabase
    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Upload in batches
    records = df.to_dict(orient="records")
    total = len(records)
    uploaded = 0

    for i in range(0, total, BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        supabase.table("businesses").insert(batch).execute()
        uploaded += len(batch)
        print(f"  Uploaded {uploaded}/{total} records...")

    print(f"\nDone. {uploaded} records loaded into Supabase.")


if __name__ == "__main__":
    main()
