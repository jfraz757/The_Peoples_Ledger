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
import math
import shutil
from datetime import datetime
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

# Portable paths: data/ sits next to the pipeline/ folder this script lives in.
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
DATA_DIR     = os.path.join(REPO_ROOT, "data")

load_dotenv(os.path.join(REPO_ROOT, ".env"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
CSV_PATH     = os.path.join(DATA_DIR, "businesses_prepared.csv")

if not SUPABASE_KEY:
    raise SystemExit(
        "SUPABASE_SERVICE_ROLE_KEY is missing from .env. This script inserts into `businesses`, "
        "which anon can no longer do. Do not substitute the publishable key."
    )
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
    print("\nNext: python pipeline/enrich.py  ->  python pipeline/maintain.py  ->  "
          "node generate-business-pages.js")


def archive_scrape_files(uploaded):
    """Move the consumed scrape output aside once its rows are safely in Supabase.

    These files used to persist forever, so every later run re-processed them. In the
    July 2026 cycle businesses_scraped.csv held 1,786 rows of which only 116 were new:
    prepare.py re-filtered, re-deduped and re-skip-known the other 1,670 every single
    time, and 800 of them were dropped as "already in directory" -- businesses uploaded
    in PREVIOUS cycles.

    Archived rather than deleted: the scrape is the audit trail for how a business was
    found, and re-running the scraper does not reproduce a deleted file. The progress
    file is deliberately LEFT ALONE -- it records which SerpApi searches have been paid
    for, and losing it means paying for them again.
    """
    if not uploaded:
        print("  (nothing uploaded, scrape files left in place)")
        return
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    archive_dir = os.path.join(DATA_DIR, "archive", stamp)
    moved = []
    for name in ("businesses_scraped.csv", "businesses_scraped_checkpoint.csv",
                 "businesses_scraped_sources.csv", "businesses_prepared.csv"):
        src = os.path.join(DATA_DIR, name)
        if os.path.exists(src):
            os.makedirs(archive_dir, exist_ok=True)
            shutil.move(src, os.path.join(archive_dir, name))
            moved.append(name)
    if moved:
        print(f"  Archived {len(moved)} consumed file(s) -> data/archive/{stamp}/")
        print(f"    {', '.join(moved)}")
        print("  The next prepare.py will see only NEW scrape output.")
        print("  scraper_progress.json kept: it records already-paid SerpApi searches.")


if __name__ == "__main__":
    main()
