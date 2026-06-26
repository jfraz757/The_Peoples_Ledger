"""
Kentucky Minority Business - Maintain
=====================================
URL maintenance on the Supabase `businesses` table.
Replaces check_link_status.py + fix_buyblack_urls.py.

Two jobs:
  --links     (default) re-check every website and set status to Active,
              Inactive, or No Website. Free. Run monthly.
  --buyblack  resolve buyblack.org placeholder URLs to a real site or Instagram
              via SerpApi. Costs SerpApi searches, so it is OFF unless asked.
              Run as needed.

Run with no flags = links only (the safe monthly job).

.env (repo root):
    SUPABASE_URL=...
    SUPABASE_KEY=...
    SERPAPI_KEY=...        # only needed for --buyblack

Usage:
    python pipeline/maintain.py               # link status only
    python pipeline/maintain.py --buyblack    # also fix buyblack URLs
    python pipeline/maintain.py --links --buyblack
"""

import os
import time
import argparse
import requests
from supabase import create_client
from dotenv import load_dotenv

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
load_dotenv(os.path.join(REPO_ROOT, ".env"))

SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
SERPAPI_KEY   = os.getenv("SERPAPI_KEY")
TIMEOUT       = 8
SLEEP_LINKS   = 0.5
SLEEP_BUYBLACK = 1.5
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
BUYBLACK_SKIP = ["buyblack.org", "facebook.com/search", "google.com"]


# ── link status ───────────────────────────────────────────────────────────────
def check_url(url):
    try:
        if not url.startswith("http"):
            url = "https://" + url
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        return "Active" if resp.status_code < 400 else "Inactive"
    except Exception:
        return "Inactive"


def run_links(supabase):
    print("\n[Links] Fetching records with websites...")
    records, offset, page = [], 0, 1000
    while True:
        resp = (supabase.table("businesses").select("id, business_name, website, status")
                .not_.is_("website", "null").range(offset, offset + page - 1).execute())
        records.extend([r for r in resp.data if str(r.get("website", "")).strip()])
        if len(resp.data) < page:
            break
        offset += page
    print(f"[Links] to check: {len(records)}")

    active = inactive = err = 0
    for i, r in enumerate(records, 1):
        status = check_url(r["website"])
        try:
            supabase.table("businesses").update({"status": status}).eq("id", r["id"]).execute()
            active += status == "Active"
            inactive += status != "Active"
            print(f"  [{i}/{len(records)}] {str(r['business_name'])[:42]:<42} {status}")
        except Exception as e:
            err += 1
            print(f"  ERROR {r.get('business_name','?')}: {e}")
        time.sleep(SLEEP_LINKS)

    print("[Links] Marking no-website records...")
    supabase.table("businesses").update({"status": "No Website"}).is_("website", "null").execute()
    print(f"[Links] Active: {active}, Inactive: {inactive}, Errors: {err}")


# ── buyblack URL resolver ──────────────────────────────────────────────────────
def search_real_url(business_name, address):
    city = ""
    if address and "," in address:
        parts = address.split(",")
        if len(parts) >= 2:
            city = parts[1].strip()
    params = {"q": f'"{business_name}" {city} official website OR instagram',
              "api_key": SERPAPI_KEY, "num": 5, "gl": "us", "hl": "en"}
    try:
        data = requests.get("https://serpapi.com/search", params=params, timeout=10).json()
        for r in data.get("organic_results", []):
            link = r.get("link", "")
            if any(skip in link for skip in BUYBLACK_SKIP):
                continue
            if "instagram.com" in link:
                return link, "instagram"
            if link.startswith("http"):
                return link, "website"
        return None, None
    except Exception as e:
        print(f"    SerpApi error: {e}")
        return None, None


def run_buyblack(supabase):
    if not SERPAPI_KEY:
        print("\n[BuyBlack] SERPAPI_KEY not set; skipping.")
        return
    print("\n[BuyBlack] Fetching buyblack.org records...")
    records = (supabase.table("businesses").select("id, business_name, address, website")
               .like("website", "%buyblack.org%").execute()).data
    print(f"[BuyBlack] to fix: {len(records)}")
    updated = no_match = 0
    for i, r in enumerate(records, 1):
        print(f"  [{i}/{len(records)}] {r.get('business_name','')}")
        new_url, kind = search_real_url(r.get("business_name", ""), r.get("address", ""))
        if new_url:
            supabase.table("businesses").update({"website": new_url}).eq("id", r["id"]).execute()
            print(f"    updated ({kind}): {new_url}")
            updated += 1
        else:
            print("    no better URL found, leaving as is")
            no_match += 1
        time.sleep(SLEEP_BUYBLACK)
    print(f"[BuyBlack] updated: {updated}, no match: {no_match}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", action="store_true", help="re-check website statuses (default)")
    ap.add_argument("--buyblack", action="store_true", help="resolve buyblack.org URLs (uses SerpApi)")
    args = ap.parse_args()
    do_links = args.links or not args.buyblack   # default to links if nothing chosen

    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    if do_links:
        run_links(supabase)
    if args.buyblack:
        run_buyblack(supabase)
    print("\nMaintenance complete.")


if __name__ == "__main__":
    main()
