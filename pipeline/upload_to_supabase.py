"""
Upload Kentucky Minority Business records to Supabase
=====================================================
Reads data/businesses_prepared.csv and inserts ONLY the rows marked
"Good to go" into the Supabase 'businesses' table, in batches.

The prepared file carries Disposition, Reason, and Source columns for your
review. Those are dropped here; they never reach the database.

.env (repo root):
    SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=<the service role key>

This script always needed INSERT rights, and the docstring used to say so while telling you
to put the service role key in SUPABASE_KEY. Other pipeline scripts documented the opposite
("publishable key is fine") for the SAME variable, which is why .env ended up defining
SUPABASE_KEY twice -- once secret, once publishable. dotenv keeps only the last occurrence,
so which key you actually got depended on line order. It now reads the unambiguous name.

Usage:
    python upload_to_supabase.py
"""

import os
import sys
import math
import pandas as pd
from supabase import create_client

# Portable paths: data/ sits next to the pipeline/ folder this script lives in.
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE_DIR)
import common  # noqa: E402

DATA_DIR     = common.DATA_DIR
SUPABASE_URL, SUPABASE_KEY = common.require_service_credentials("inserts into `businesses`")
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
    archive_scrape_files(uploaded)
    print("\nNext: python pipeline/enrich.py  ->  node generate-business-pages.js")


def archive_scrape_files(uploaded):
    """Move the consumed scrape output aside once its rows are safely in Supabase.

    These files used to persist forever, so every later run re-processed them. In the
    July 2026 cycle businesses_scraped.csv held 1,786 rows of which only 116 were new:
    prepare.py re-filtered, re-deduped and re-skip-known the other 1,670 every single
    time, and 800 of them were dropped as "already in directory" -- businesses uploaded
    in PREVIOUS cycles.

    Archived rather than deleted: the scrape is the audit trail for how a business was
    found. The progress file is LEFT ALONE here -- if this upload was a partial batch
    mid-cycle, the scraper still needs it to resume without paying for searches again.
    `ledger.py new-cycle` archives it when a new quarterly cycle starts.
    """
    if not uploaded:
        print("  (nothing uploaded, scrape files left in place)")
        return
    # Record manual rejections BEFORE the prepared file moves. prepare.py harvests them
    # from the previous businesses_prepared.csv at the start of its next run -- but once
    # this function archived that file, there was nothing left to harvest, so every
    # review pass that ended in an upload lost its drops and they came back next cycle.
    import prepare
    prepare.commit_drops(explicit=False)
    consumed = [n for n in common.CYCLE_FILES if n != "scraper_progress.json"]
    archive_dir, moved = common.archive_files(consumed, "uploaded")
    if moved:
        print(f"  Archived {len(moved)} consumed file(s) -> {os.path.relpath(archive_dir, common.REPO_ROOT)}")
        print(f"    {', '.join(moved)}")
        print("  The next prepare.py will see only NEW scrape output.")


if __name__ == "__main__":
    main()
