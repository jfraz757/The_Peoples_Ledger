"""
remove_listings.py  --  remove specific listings from the directory, by id

    python pipeline/remove_listings.py data/<list>.csv            # DRY RUN: shows the rows, backs them up
    python pipeline/remove_listings.py data/<list>.csv --apply    # delete them

The CSV needs an `id` column (other columns are ignored). Use it for listings a human has
decided do not belong -- national chains, government offices, organizations that are not
businesses. Before anything is deleted:

  * every row is backed up in full to data/removed_listings_backup_<timestamp>.csv
    (restore by re-inserting those rows), and
  * each business's name and website are added to data/denylist.csv, which prepare.py
    reads, so a future scrape cannot quietly add it back.

After --apply, run `node generate-business-pages.js` -- it deletes the pages of businesses
that are no longer in the database -- then commit and push.

For duplicates use dedupe_live.py; for out-of-state rows, purge_out_of_state.py.
"""

import argparse
import csv
import os
import sys
from datetime import datetime

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE_DIR)
import common  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Remove listings by id (dry run unless --apply).")
    ap.add_argument("csv_path", help="CSV with an `id` column")
    ap.add_argument("--apply", action="store_true", help="actually delete")
    args = ap.parse_args()

    import requests
    url, key = common.require_service_credentials("deletes listings")
    with open(args.csv_path, newline="", encoding="utf-8-sig") as f:
        ids = sorted({int(r["id"]) for r in csv.DictReader(f) if (r.get("id") or "").strip()})
    if not ids:
        sys.exit("No ids in the CSV.")

    H = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(f"{url}/rest/v1/businesses", headers=H, timeout=60,
                     params={"select": "*", "id": f"in.({','.join(map(str, ids))})"})
    r.raise_for_status()
    rows = sorted(r.json(), key=lambda x: x["id"])
    missing = set(ids) - {x["id"] for x in rows}
    print(f"{len(rows)} of {len(ids)} listing(s) found{f'; not found (already gone?): {sorted(missing)}' if missing else ''}:")
    for x in rows:
        print(f"  id {x['id']:<5} {x['business_name'][:50]:50} {x.get('address') or ''}")
    if not rows:
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(common.DATA_DIR, f"removed_listings_backup_{stamp}.csv")
    fields = sorted({k for x in rows for k in x})
    with open(backup, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nFull rows backed up -> {backup}")

    if not args.apply:
        print("\nDRY RUN. Nothing deleted. Re-run with --apply to remove these.")
        return

    # Remember them first, so they stay out even if the delete is interrupted.
    deny = os.path.join(common.DATA_DIR, "denylist.csv")
    new = not os.path.exists(deny)
    with open(deny, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["Business Name", "Website"])
        for x in rows:
            w.writerow([x["business_name"], x.get("website") or ""])
    d = requests.delete(f"{url}/rest/v1/businesses", headers={**H, "Prefer": "return=minimal"}, timeout=60,
                        params={"id": f"in.({','.join(str(x['id']) for x in rows)})"})
    d.raise_for_status()
    print(f"\nRemoved {len(rows)} listing(s) and added them to the denylist.")
    print("Next: node generate-business-pages.js   (removes their pages), then commit and push.")


if __name__ == "__main__":
    main()
