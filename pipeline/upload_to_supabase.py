"""
Upload Kentucky Minority Business records to Supabase
=====================================================
Reads data/businesses_prepared.csv and inserts ONLY the rows marked
"Good to go" into the Supabase 'businesses' table, in batches.

The prepared file carries Disposition, Reason, and Source columns for your
review. Those are dropped here; they never reach the database.

.env (same folder as this script):
    SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co
    SUPABASE_KEY=<key with INSERT rights - the service role key, NOT the
                  read-only publishable key used by the scraper>

Usage:
    python upload_to_supabase.py
"""

import os
import math
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

# Portable paths: data/ sits next to the pipeline/ folder this script lives in.
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
DATA_DIR     = os.path.join(REPO_ROOT, "data")

load_dotenv(os.path.join(REPO_ROOT, ".env"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CSV_PATH     = os.path.join(DATA_DIR, "businesses_prepared.csv")
BATCH_SIZE   = 100

DB_RENAME = {
    "Business Name":       "business_name",
    "Address":             "address",
    "Phone":               "phone",
    "Services / Products":  "services_products",
    "Website":             "website",
    "Minority Type":       "minority_type",
    "Status":              "status",
    "Kentucky Based":      "kentucky_based",
}


def main():
    print(f"Loading: {CSV_PATH}")
    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")

    # Upload only the approved rows. After you review, anything you want kept
    # must read exactly "Good to go" in the Disposition column.
    if "Disposition" in df.columns:
        before = len(df)
        df = df[df["Disposition"].astype(str).str.strip() == "Good to go"].copy()
        print(f"Filtered to Good to go: {len(df)} of {before} rows")
    else:
        print("No Disposition column found; uploading all rows.")

    # Keep only the eight database columns; drop Disposition/Reason/Source.
    df = df[[c for c in DB_RENAME if c in df.columns]].rename(columns=DB_RENAME)

    if df.empty:
        print("Nothing to upload. Did you mark any rows 'Good to go'?")
        return

    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Build records, then scrub every value so no NaN or empty string reaches the
    # JSON body. NaN is not JSON-compliant and Supabase will reject the batch.
    # industry, services_products, and certification_type are meant to be empty
    # here; they are filled by the post-upload enrich step.
    def clean(v):
        if v is None:
            return None
        if isinstance(v, float) and math.isnan(v):
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    records = [{k: clean(v) for k, v in r.items()}
               for r in df.to_dict(orient="records")]
    total, uploaded = len(records), 0
    for i in range(0, total, BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        supabase.table("businesses").insert(batch).execute()
        uploaded += len(batch)
        print(f"  Uploaded {uploaded}/{total} records...")

    print(f"\nDone. {uploaded} records loaded into Supabase.")
    print("Now run the enrichment steps: categorize_industries.py, "
          "fill_missing_services.py, then check_link_status.py.")


if __name__ == "__main__":
    main()
