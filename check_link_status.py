"""
Website Status Checker
=======================
Re-checks every business website URL and updates the 'status' field
in Supabase with the current link status.

Status values:
  Active   -- URL returned a live response (200-399)
  Inactive -- URL returned an error or redirect to dead page
  No Website -- No URL on record

Requirements:
    pip install supabase python-dotenv requests

Usage:
    python check_link_status.py

Notes:
    - Processes all records with a website URL
    - Skips records with no website (status stays "No Website")
    - Run this monthly or after major scraper updates
    - Estimated time: ~20-30 minutes for 1,000+ records
"""

import os
import time
import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ───────────────────────────────────────────────────────────────────
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
TIMEOUT       = 8    # seconds before marking as Inactive
SLEEP_BETWEEN = 0.5  # seconds between requests
BATCH_SIZE    = 50
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
}


def check_url(url):
    """Return 'Active' or 'Inactive' based on HTTP response."""
    try:
        if not url.startswith("http"):
            url = "https://" + url
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        return "Active" if resp.status_code < 400 else "Inactive"
    except Exception:
        return "Inactive"


def main():
    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Fetching all records with websites...")
    response = supabase.table("businesses")\
        .select("id, business_name, website, status")\
        .not_.is_("website", "null")\
        .execute()

    records = [r for r in response.data if r.get("website", "").strip()]
    print(f"Records to check: {len(records)}")
    print()

    active   = 0
    inactive = 0
    errors   = 0

    for i, record in enumerate(records, 1):
        url    = record["website"]
        status = check_url(url)

        try:
            supabase.table("businesses")\
                .update({"status": status})\
                .eq("id", record["id"])\
                .execute()

            if status == "Active":
                active += 1
            else:
                inactive += 1

            print(f"  [{i}/{len(records)}] {record['business_name'][:45]:<45} {status}")

        except Exception as e:
            errors += 1
            print(f"  ERROR on {record.get('business_name', 'unknown')}: {e}")

        time.sleep(SLEEP_BETWEEN)

        # Progress summary every 100 records
        if i % 100 == 0:
            print(f"\n  --- Progress: {i}/{len(records)} checked | Active: {active} | Inactive: {inactive} | Errors: {errors} ---\n")

    # Mark no-website records explicitly
    print("\nMarking no-website records...")
    supabase.table("businesses")\
        .update({"status": "No Website"})\
        .is_("website", "null")\
        .execute()

    print(f"\nDone.")
    print(f"  Active:   {active}")
    print(f"  Inactive: {inactive}")
    print(f"  Errors:   {errors}")


if __name__ == "__main__":
    main()
