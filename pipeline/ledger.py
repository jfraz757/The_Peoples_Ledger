#!/usr/bin/env python3
"""
ledger.py  --  The People's Ledger pipeline runner

One command per phase, so you don't have to remember the order or which of the
scripts to run. The individual scripts still exist for the occasional jobs
(scraping, discovery, applying a destructive cleaner). This just chains the
routine ones and stops where YOU need to make a decision.

  python pipeline/ledger.py prep      # prepare + resolve_review, then STOP for your review
  python pipeline/ledger.py publish   # upload + enrich  (run AFTER you review)
  python pipeline/ledger.py maintain  # live-table health checks (read-only, dry run)

Deliberately NOT chained here, because they are expensive or destructive and
should be run on purpose:
  - scrape.py / discover_categories.py        (discovery; costs SerpApi + Claude)
  - prepare.py --commit-drops                 (records your manual drops)
  - clean_addresses.py / purge_out_of_state.py / dedupe_live.py with --apply
  - maintain.py (link status; slow, writes status, run monthly)

See order_of_operations.md for the full map.
"""

import os
import sys
import subprocess

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)


def run(script, *args, label=""):
    """Run a pipeline script with the SAME interpreter running ledger.py, from
    the repo root. Stop the whole chain if a step fails."""
    path = os.path.join(PIPELINE_DIR, script)
    if not os.path.exists(path):
        print(f"\n!! {script} not found in {PIPELINE_DIR}. Stopping.")
        sys.exit(1)
    shown = " ".join([script, *args])
    print(f"\n{'='*64}\n>> {label or script}\n   ({shown})\n{'='*64}")
    res = subprocess.run([sys.executable, path, *args], cwd=REPO_ROOT)
    if res.returncode != 0:
        print(f"\n!! {script} exited with code {res.returncode}. Stopping the chain here.")
        sys.exit(res.returncode)


def prep():
    """Build a clean review pile, then stop for the human."""
    run("prepare.py",
        label="Disposition + weed (chains, out-of-state by address, already-live, denylist, exact-name dupes)")
    run("resolve_review.py",
        label="Auto-settle Needs-review rows by reading each website + about/contact pages")
    print("\n" + "-"*64)
    print("Phase 1 complete. Your review pile is now as small as automation can make it.")
    print("\n  1. Open  data/businesses_prepared.csv  and filter Disposition = 'Needs review'.")
    print("     Keep the good ones (set 'Good to go'); drop the rest.")
    print("  2. Remember your drops so they never return:")
    print("        python pipeline/prepare.py --commit-drops")
    print("  3. Publish:")
    print("        python pipeline/ledger.py publish")


def publish():
    """Upload the approved rows and enrich them. Run this AFTER you review."""
    run("upload_to_supabase.py",
        label="Upload 'Good to go' rows to Supabase")
    run("enrich.py", "--industries",
        label="Fill industry categories (Claude)")
    run("enrich.py", "--services",
        label="Fill missing service descriptions (Claude)")
    print("\n" + "-"*64)
    print("Uploaded and enriched. To publish the site:")
    print("        node generate-business-pages.js")
    print("        git add -A && git commit -m \"Add businesses\" && git push")
    print("Then update the record count in README.md and the technical reference.")


def maintain():
    """Read-only health check of the live table. Nothing here changes data."""
    run("clean_addresses.py",
        label="Report stray N/A in addresses (DRY RUN)")
    run("purge_out_of_state.py",
        label="Report out-of-state rows (DRY RUN)")
    run("dedupe_live.py",
        label="Report duplicate rows (DRY RUN)")
    print("\n" + "-"*64)
    print("Health check done. Nothing was changed. To apply a fix, run it with --apply:")
    print("        python pipeline/clean_addresses.py --apply")
    print("        python pipeline/purge_out_of_state.py --apply")
    print("        python pipeline/dedupe_live.py --apply")
    print("\nLink status is separate (slow, monthly):")
    print("        python pipeline/maintain.py")


USAGE = """The People's Ledger runner. Usage:

  python pipeline/ledger.py prep       prepare + resolve_review, then stop for your review
  python pipeline/ledger.py publish    upload + enrich (run after you review)
  python pipeline/ledger.py maintain   live-table health checks (read-only, dry run)
"""

VERBS = {"prep": prep, "publish": publish, "maintain": maintain}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in VERBS:
        print(USAGE)
        sys.exit(0 if len(sys.argv) < 2 else 2)
    VERBS[sys.argv[1]]()


if __name__ == "__main__":
    main()
