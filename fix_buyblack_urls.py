"""
BuyBlack.org URL Resolver
==========================
Finds the real website or Instagram for businesses whose URL
points to buyblack.org, using SerpApi to search the web.

Requirements:
    pip install supabase python-dotenv requests

.env file:
    SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co
    SUPABASE_KEY=your_publishable_key_here
    SERPAPI_KEY=your_serpapi_key_here

Usage:
    python fix_buyblack_urls.py
"""

import os
import time
import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ───────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SERPAPI_KEY  = os.getenv("SERPAPI_KEY")
SLEEP        = 1.5
# ─────────────────────────────────────────────────────────────────────────────

SKIP_DOMAINS = ["buyblack.org", "facebook.com/search", "google.com"]


def search_real_url(business_name, address):
    """Use SerpApi to find the business's real website or Instagram."""
    city = ""
    if address:
        parts = address.split(",")
        if len(parts) >= 2:
            city = parts[1].strip()

    query = f'"{business_name}" {city} official website OR instagram'

    params = {
        "q": query,
        "api_key": SERPAPI_KEY,
        "num": 5,
        "gl": "us",
        "hl": "en",
    }

    try:
        resp = requests.get("https://serpapi.com/search", params=params, timeout=10)
        data = resp.json()

        results = data.get("organic_results", [])
        for r in results:
            link = r.get("link", "")
            # Skip buyblack and generic directories
            if any(skip in link for skip in SKIP_DOMAINS):
                continue
            # Prefer Instagram if no website
            if "instagram.com" in link:
                return link, "instagram"
            # Prefer direct business website
            if link.startswith("http"):
                return link, "website"

        return None, None

    except Exception as e:
        print(f"    SerpApi error: {e}")
        return None, None


def main():
    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("Fetching buyblack.org records...")
    response = supabase.table("businesses")\
        .select("id, business_name, address, website")\
        .like("website", "%buyblack.org%")\
        .execute()

    records = response.data
    print(f"Records to fix: {len(records)}")
    print()

    updated  = 0
    no_match = 0

    for i, record in enumerate(records, 1):
        name    = record.get("business_name", "")
        address = record.get("address", "")
        old_url = record.get("website", "")

        print(f"[{i}/{len(records)}] {name}")
        print(f"  Current: {old_url}")

        new_url, url_type = search_real_url(name, address)

        if new_url:
            supabase.table("businesses")\
                .update({"website": new_url})\
                .eq("id", record["id"])\
                .execute()
            print(f"  Updated ({url_type}): {new_url}")
            updated += 1
        else:
            print(f"  No better URL found -- leaving as is")
            no_match += 1

        print()
        time.sleep(SLEEP)

    print(f"Done.")
    print(f"  Updated:       {updated}")
    print(f"  No match found: {no_match}")


if __name__ == "__main__":
    main()
