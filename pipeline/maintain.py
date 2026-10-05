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
    SUPABASE_SERVICE_ROLE_KEY=...   # this script WRITES status/website back to businesses
    SERPAPI_KEY=...        # only needed for --buyblack

Needs the service role key: anon's UPDATE grant on `businesses` was revoked in July 2026
(restrict_anon_grants.sql) because it let anyone with the key from index.html rewrite the
directory. Do not re-grant anon write access to make this run.

Usage:
    python pipeline/maintain.py               # link status only
    python pipeline/maintain.py --buyblack    # also fix buyblack URLs
    python pipeline/maintain.py --links --buyblack
"""

import os
import sys
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import requests

# Business names contain non-ASCII characters (accents, curly apostrophes, CJK). On
# Windows stdout defaults to cp1252, so printing one raises UnicodeEncodeError -- and
# because the crash happened INSIDE the except block that reports a fetch failure, it
# killed the whole run mid-pass. July 2026: a run died at ~1,400 of 2,066 this way,
# leaving 660 rows with no status. Errors must never be less printable than successes.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from supabase import create_client

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE_DIR)
import common  # noqa: E402

DATA_DIR      = common.DATA_DIR
# Service role required -- see module docstring.
SUPABASE_URL, SUPABASE_KEY = common.require_service_credentials("writes link status")
SERPAPI_KEY   = common.env("SERPAPI_KEY")

TIMEOUT        = 8
RETRY_TIMEOUT  = 20   # second, slower attempt for anything that failed the first pass
# Websites are checked this many at a time. Each check goes to a DIFFERENT business's
# server, so parallel checks put no extra load on any one site. This used to be one at
# a time with a 0.5 s sleep after each, so ~2,300 sites took the better part of an hour.
WORKERS        = 16
SLEEP_BUYBLACK = 1.5
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
BUYBLACK_SKIP = ["buyblack.org", "facebook.com/search", "google.com"]

# Facebook returns 400 to every scripted request regardless of whether the page
# is real (confirmed: a live page, a public figure's page, and a nonexistent
# page all return 400). Instagram returns 200 for every URL including fake ones.
# Neither status code carries any liveness signal, so we don't penalize a
# business for a check we can't actually perform — default to Active instead
# of falsely flagging real, active businesses as "Link Inactive".
SOCIAL_SKIP = ["facebook.com", "instagram.com", "fb.com"]


# ── link status ───────────────────────────────────────────────────────────────
def check_url(url, timeout=TIMEOUT):
    if any(domain in url.lower() for domain in SOCIAL_SKIP):
        return "Active"
    try:
        if not url.startswith("http"):
            url = "https://" + url
        # stream=True: only the status line and headers are needed. Without it every
        # check downloaded the whole homepage, images-in-HTML and all.
        with requests.get(url, headers=HEADERS, timeout=timeout,
                          allow_redirects=True, stream=True) as resp:
            return "Active" if resp.status_code < 400 else "Inactive"
    except Exception:
        return "Inactive"


STATE_FILE = os.path.join(DATA_DIR, ".maintain_state.json")
RECHECK_AFTER_DAYS = 25          # monthly cadence, with slack so a re-run does not redo everything


def _load_checked():
    """id -> ISO timestamp of the last successful link check."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_checked(state):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"  [could not write {os.path.basename(STATE_FILE)}: {e}]")


def run_links(supabase, force_all=False):
    print("\n[Links] Fetching records with websites...")
    records, offset, page = [], 0, 1000
    while True:
        resp = (supabase.table("businesses").select("id, business_name, website, status")
                .not_.is_("website", "null").range(offset, offset + page - 1).execute())
        records.extend([r for r in resp.data if str(r.get("website", "")).strip()])
        if len(resp.data) < page:
            break
        offset += page
    total_found = len(records)

    # Skip anything checked recently. Every run used to re-fetch every website: the
    # July 2026 cycle checked all 2,066 twice in one day, the second pass repeating
    # ~1,400 sites that had been checked hours earlier. A link status does not change
    # hour to hour, so re-checking is pure wall-clock cost and pointless load on the
    # businesses' servers. --all forces a full sweep.
    checked = _load_checked()
    if not force_all:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RECHECK_AFTER_DAYS)
        fresh = set()
        for r in records:
            ts = checked.get(str(r["id"]))
            if not ts:
                continue
            try:
                if datetime.fromisoformat(ts) > cutoff:
                    fresh.add(r["id"])
            except ValueError:
                pass
        records = [r for r in records if r["id"] not in fresh]
        if fresh:
            print(f"[Links] skipping {len(fresh)} checked within {RECHECK_AFTER_DAYS} days "
                  f"(use --all to force)")
    print(f"[Links] to check: {len(records)} of {total_found}  ({WORKERS} at a time)")

    # Pass 1: check everything in parallel.
    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(check_url, r["website"]): r for r in records}
        for n, fut in enumerate(as_completed(futures), 1):
            r = futures[fut]
            results[r["id"]] = fut.result()
            if n % 100 == 0 or n == len(records):
                print(f"  checked {n}/{len(records)}")

    # Pass 2: a site that failed once gets a second, slower attempt before it is shown
    # publicly as "Link Inactive". One timeout used to be enough to flip a working
    # business's listing -- a slow small-business host or a momentary outage on the
    # morning of the check.
    failed = [r for r in records if results[r["id"]] != "Active"]
    if failed:
        print(f"[Links] re-checking {len(failed)} failure(s) with a {RETRY_TIMEOUT}s timeout...")
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(check_url, r["website"], RETRY_TIMEOUT): r for r in failed}
            for fut in as_completed(futures):
                results[futures[fut]["id"]] = fut.result()

    # Write only rows whose status actually changed. Every row used to get an UPDATE
    # even when the status was the same as last month, i.e. ~2,300 writes for a handful
    # of real changes.
    active = inactive = changed = err = 0
    now = datetime.now(timezone.utc).isoformat()
    for r in records:
        status = results[r["id"]]
        active += status == "Active"
        inactive += status != "Active"
        if status != (r.get("status") or ""):
            try:
                supabase.table("businesses").update({"status": status}).eq("id", r["id"]).execute()
                changed += 1
                print(f"  {str(r['business_name'])[:42]:<42} {r.get('status') or '(none)'} -> {status}")
            except Exception as e:
                err += 1
                print(f"  ERROR {r.get('business_name','?')}: {e}")
                continue
        checked[str(r["id"])] = now

    _save_checked(checked)
    print(f"[Links] {changed} status change(s) written.")

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
    ap.add_argument("--all", action="store_true",
                    help="re-check every website, ignoring the 25-day skip window")
    args = ap.parse_args()
    do_links = args.links or not args.buyblack   # default to links if nothing chosen

    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    if do_links:
        run_links(supabase, force_all=args.all)
    if args.buyblack:
        run_buyblack(supabase)
    print("\nMaintenance complete.")


if __name__ == "__main__":
    main()
