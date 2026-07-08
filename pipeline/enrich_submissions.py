"""
pipeline/enrich_submissions.py
===============================
Targeted enrichment for newly-approved business submissions only.

enrich.py's default pass is already cheap (it filters in Python before
spending any Claude calls), but it still scans the whole `businesses` table
every time. This script instead looks at the `submissions` table for rows
that were approved since the last time it ran, matches each one to its live
`businesses` row by exact business_name (the same lookup admin.html uses
when approving an "update" submission), and runs the same industry +
services enrichment on just those rows.

Why this exists: when admin.html approves a "new business" submission, it
inserts straight into `businesses` with no `industry` value and whatever
`services_products` text the submitter typed (often blank or thin). That
business has no static SEO page yet either — this script only handles the
industry/services fields; still run `node generate-business-pages.js` and
push afterward.

Watermark
---------
Progress is tracked in data/.enrich_submissions_state.json (gitignored,
working file only), storing the latest `submitted_at` seen. Each run only
looks at submissions after that watermark, so you can just re-run this
after every batch of admin.html approvals without re-processing old ones.

Usage
-----
    python pipeline/enrich_submissions.py                # enrich since last run
    python pipeline/enrich_submissions.py --dry-run       # preview only, no writes, no watermark advance
    python pipeline/enrich_submissions.py --since 2026-07-01T00:00:00Z   # override the watermark
    python pipeline/enrich_submissions.py --limit 5        # cap rows processed, for testing

.env (repo root):
    SUPABASE_URL=...
    SUPABASE_KEY=...          # publishable key is fine (read + update under your RLS)
    ANTHROPIC_API_KEY=...
"""

import os
import sys
import json
import time
import argparse
import anthropic
from dotenv import load_dotenv

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PIPELINE_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")
STATE_PATH = os.path.join(DATA_DIR, ".enrich_submissions_state.json")

sys.path.insert(0, PIPELINE_DIR)
import enrich  # reuse classify_industry, infer_services, is_blank, etc.

load_dotenv(os.path.join(REPO_ROOT, ".env"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")


# --- watermark -----------------------------------------------------------
def load_last_run():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f).get("last_submitted_at")
    return None


def save_last_run(ts):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump({"last_submitted_at": ts}, f, indent=2)


# --- supabase access -------------------------------------------------------
def fetch_new_approved_submissions(supabase, since):
    q = (supabase.table("submissions")
         .select("id, business_name, submission_type, status, submitted_at")
         .eq("status", "approved")
         .eq("submission_type", "new")
         .order("submitted_at", desc=False))
    if since:
        q = q.gt("submitted_at", since)
    return q.execute().data


def find_business(supabase, business_name):
    res = (supabase.table("businesses")
           .select("id, business_name, industry, services_products, address")
           .eq("business_name", business_name)
           .limit(1).execute())
    return res.data[0] if res.data else None


# --- main ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Fill industry + services_products for businesses whose "
                     "submission was approved since the last run.")
    ap.add_argument("--dry-run", action="store_true",
                     help="print proposed changes without writing; does not advance the watermark")
    ap.add_argument("--limit", type=int, default=0,
                     help="cap submissions processed (0 = no cap)")
    ap.add_argument("--since", metavar="ISO_TIMESTAMP",
                     help="override the stored watermark, e.g. 2026-07-01T00:00:00Z")
    args = ap.parse_args()

    since = args.since or load_last_run()
    print(f"Watermark: {since or '(none set — processing every approved submission)'}")

    print("Connecting to Supabase...")
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    subs = fetch_new_approved_submissions(supabase, since)
    if args.limit:
        subs = subs[:args.limit]

    if not subs:
        print("No newly-approved submissions to enrich.")
        return

    print(f"Found {len(subs)} approved submission(s) since watermark"
          f"{' (DRY RUN)' if args.dry_run else ''}.\n")

    latest_seen = since
    ind_done = svc_done = skipped = err = 0

    for i, s in enumerate(subs, 1):
        name = s.get("business_name", "")
        try:
            biz = find_business(supabase, name)
            if not biz:
                print(f"  [{i}/{len(subs)}] SKIP {name!r} — no exact-name match in "
                      f"businesses (check manually; name may differ slightly)")
                skipped += 1
            else:
                changed = []

                if enrich.industry_needs_work(biz.get("industry")):
                    new_ind = enrich.classify_industry(
                        claude, name, biz.get("services_products"))
                    changed.append(f"industry: {biz.get('industry') or '(blank)'} -> {new_ind}")
                    if not args.dry_run:
                        enrich.update_field(supabase, biz["id"], "industry", new_ind)
                    biz["industry"] = new_ind
                    ind_done += 1
                    time.sleep(enrich.SLEEP)

                if enrich.services_needs_work(biz.get("services_products")):
                    new_svc = enrich.infer_services(
                        claude, name, biz.get("industry"), biz.get("address"),
                        biz.get("services_products"))
                    changed.append(f"services: -> {new_svc[:60]}")
                    if not args.dry_run:
                        enrich.update_field(supabase, biz["id"], "services_products", new_svc)
                    svc_done += 1
                    time.sleep(enrich.SLEEP)

                tag = "; ".join(changed) if changed else "(already complete, nothing to do)"
                print(f"  [{i}/{len(subs)}] {name[:38]:<38} {tag}")

        except Exception as e:
            err += 1
            print(f"  ERROR {name!r}: {e}")
            time.sleep(2)

        submitted_at = s.get("submitted_at")
        if submitted_at and (not latest_seen or submitted_at > latest_seen):
            latest_seen = submitted_at

    print(f"\nDone. industries filled: {ind_done}, services filled: {svc_done}, "
          f"skipped (no match): {skipped}, errors: {err}")

    if args.dry_run:
        print("(dry run — watermark not advanced)")
    elif latest_seen:
        save_last_run(latest_seen)
        print(f"Watermark advanced to {latest_seen}")

    print("\nReminder: this only fills industry/services. Still run:")
    print("    node generate-business-pages.js")
    print('    git add -A && git commit -m "Publish new submissions" && git push')


if __name__ == "__main__":
    main()
