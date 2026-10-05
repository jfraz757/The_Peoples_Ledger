#!/usr/bin/env python3
"""
ledger.py  --  The People's Ledger pipeline runner

The ONE entry point for routine work. Each verb runs its steps in order with the same
Python that is running this file, from the repo root, and stops at the first failure
or wherever YOU need to make a decision. Works the same from PowerShell or Git Bash:

    py pipeline\\ledger.py <verb>          (PowerShell)
    python pipeline/ledger.py <verb>      (Git Bash)

This replaced docs/monthly_link_check.sh and docs/quarterly_refresh.sh (October 2026).
Both hardcoded a Python path that no longer existed, so both had been failing; and the
quarterly script uploaded straight after prepare.py, skipping resolve_review, the review
flags and your own review entirely.

Destructive steps stay manual on purpose -- this runner only ever prints them:
  clean_addresses.py / purge_out_of_state.py / dedupe_live.py with --apply,
  reconcile_certifications.py with --apply / --insert-new.
"""

import os
import shutil
import subprocess
import sys

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE_DIR)
import common  # noqa: E402

REPO_ROOT = common.REPO_ROOT
RULE = "=" * 64


def run(script, *args, label=""):
    """Run a pipeline script with the SAME interpreter running ledger.py, from the repo
    root. Stop the whole chain if a step fails."""
    path = os.path.join(PIPELINE_DIR, script)
    if not os.path.exists(path):
        print(f"\n!! {script} not found in {PIPELINE_DIR}. Stopping.")
        sys.exit(1)
    shown = " ".join([script, *args])
    print(f"\n{RULE}\n>> {label or script}\n   ({shown})\n{RULE}")
    res = subprocess.run([sys.executable, path, *args], cwd=REPO_ROOT)
    if res.returncode != 0:
        print(f"\n!! {script} exited with code {res.returncode}. Stopping the chain here.")
        sys.exit(res.returncode)


def generate_pages():
    node = shutil.which("node")
    if not node:
        print("\n!! node not found on PATH -- run `node generate-business-pages.js` yourself.")
        return False
    print(f"\n{RULE}\n>> Regenerate static business pages + sitemap\n   (generate-business-pages.js)\n{RULE}")
    res = subprocess.run([node, "generate-business-pages.js"], cwd=REPO_ROOT)
    if res.returncode != 0:
        print("\n!! generate-business-pages.js failed. Stopping.")
        sys.exit(res.returncode)
    return True


def done(*lines):
    print("\n" + "-" * 64)
    for line in lines:
        print(line)


# --- verbs ------------------------------------------------------------------
def links():
    """Monthly. Re-check every website and update Active / Inactive / No Website."""
    run("maintain.py", label="Link status check (monthly)")
    done("Link check done. If any statuses changed, publish them to the static pages:",
         "        node generate-business-pages.js",
         "        git add businesses/ && git commit -m \"Refresh link status\" && git push")


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def monthly():
    """Unattended monthly run (Task Scheduler, 1st of the month -- see
    install_monthly_links.ps1): link check, regenerate pages, then commit and push
    ONLY businesses/ so the new statuses reach the live site.

    The link check went unrun for two months in 2026 because it depended on someone
    remembering it, and a status change that is never pushed never reaches the site.
    Pushes only from `main`; on any other branch it commits nothing and says so."""
    from datetime import date
    print(f"\n#### Monthly link check {date.today().isoformat()} ####")
    run("maintain.py", label="Link status check (monthly)")
    generate_pages()

    branch = git("branch", "--show-current").stdout.strip()
    if branch != "main":
        done(f"On branch '{branch}', not main -- pages regenerated but NOT committed or pushed.")
        return
    git("add", "-A", "businesses/")
    if git("diff", "--cached", "--quiet", "--", "businesses/").returncode == 0:
        done("No page changes this month. Nothing to commit.")
        return
    # Pathspec commit: only businesses/ goes in, even if other files happen to be staged.
    c = git("commit", "-m", f"Monthly link check {date.today().isoformat()}", "--", "businesses/")
    print(c.stdout.strip() or c.stderr.strip())
    p = git("push", "origin", "main")
    if p.returncode != 0:
        print(f"!! git push failed -- run `git push` by hand.\n{p.stderr.strip()}")
        sys.exit(1)
    done("Committed and pushed. The live site updates in a few minutes.")


def new_cycle():
    """Start a new quarterly cycle: save manual drops, then archive the previous
    cycle's progress and output so the scraper starts fresh."""
    import prepare
    prepare.commit_drops(explicit=False)
    archive_dir, moved = common.archive_files(common.CYCLE_FILES, "cycle")
    if not moved:
        done("Nothing to archive -- data/ already holds no cycle files. Ready to scrape.")
        return
    done(f"Archived {len(moved)} file(s) from the previous cycle ->",
         f"    {os.path.relpath(archive_dir, REPO_ROOT)}",
         *(f"      {m}" for m in moved),
         "",
         "Nothing was deleted. The Maps/page caches and denylist are untouched.",
         "Next: python pipeline/ledger.py scrape")


def scrape():
    """Quarterly. Lane 1 discovery (Google Maps; web lane off by default)."""
    progress = os.path.join(common.DATA_DIR, "scraper_progress.json")
    if os.path.exists(progress):
        print("Note: data/scraper_progress.json exists, so this RESUMES the current cycle.")
        print("      If you meant to start a new quarterly cycle, stop now (Ctrl+C) and run:")
        print("        python pipeline/ledger.py new-cycle")
    run("scrape.py", label="Lane 1 discovery (SerpApi Google Maps)")
    done("Scrape done. Next: python pipeline/ledger.py prep")


def prep():
    """Build a clean review pile, then stop for the human."""
    run("prepare.py",
        label="Disposition + weed (chains, out-of-state, already-live, denylist, near-duplicates)")
    run("resolve_review.py",
        label="Auto-settle Needs-review rows (real website + address lookup)")
    run("flag_review.py",
        label="Flag national brands, likely live duplicates, unverified ownership")
    done("Your review pile is now as small as automation can make it.",
         "",
         "  1. Open  data/businesses_prepared.csv  and filter Disposition = 'Needs review'.",
         "     The 'Review Flag' column says why each row is there.",
         "     Keep the good ones (set 'Good to go'); set the rest to 'Dropped'.",
         "     Dropped rows are remembered automatically -- they will not come back.",
         "  2. Publish:  python pipeline/ledger.py publish")


def publish():
    """Upload the approved rows, enrich them, rebuild the static pages."""
    run("upload_to_supabase.py", label="Upload 'Good to go' rows to Supabase")
    run("enrich.py", "--industries", label="Fill industry categories (Claude)")
    run("enrich.py", "--services", label="Fill missing service descriptions (Claude)")
    generate_pages()
    done("Uploaded, enriched and pages regenerated.",
         "  1. Check it locally:  python -m http.server 8080   (open http://localhost:8080)",
         "  2. git add -A && git commit -m \"Quarterly refresh\" && git push",
         "  3. Update the record count in README.md and the technical reference.")


def certs():
    """Lane 2 dry run: compare the three certifier downloads with the live table."""
    run("reconcile_certifications.py",
        label="Reconcile certifier lists (DRY RUN -- nothing is written)")
    done("Dry run only. Read the BACKFILL / INSERT / REVIEW counts above, then:",
         "        python pipeline/reconcile_certifications.py --apply               # labels only",
         "        python pipeline/reconcile_certifications.py --apply --insert-new  # also add new businesses",
         "Afterwards: python pipeline/enrich.py, then node generate-business-pages.js")


def publish_submissions():
    """After approving submissions in admin.html: enrich them, rebuild pages, commit."""
    run("enrich_submissions.py",
        label="Enrich industry/services for newly-approved submissions")
    if not generate_pages():
        return
    subprocess.run(["git", "add", "businesses/"], cwd=REPO_ROOT)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT).returncode
    if staged == 0:
        done("No page changes to commit.")
        return
    subprocess.run(["git", "commit", "-m", "Publish new submissions"], cwd=REPO_ROOT)
    done("Committed the regenerated pages. Push to put them live:",
         "        git push")


def maintain():
    """Read-only health check of the live table. Nothing here changes data."""
    run("clean_addresses.py", label="Report stray N/A in addresses (DRY RUN)")
    run("purge_out_of_state.py", label="Report out-of-state rows (DRY RUN)")
    run("dedupe_live.py", label="Report duplicate rows (DRY RUN)")
    done("Health check done. Nothing was changed. To apply a fix, run it with --apply:",
         "        python pipeline/clean_addresses.py --apply",
         "        python pipeline/purge_out_of_state.py --apply",
         "        python pipeline/dedupe_live.py --apply",
         "Then: node generate-business-pages.js")


USAGE = """The People's Ledger runner.   python pipeline/ledger.py <verb>

MONTHLY
  links                link-status check of every website
  monthly              links + regenerate pages + commit/push businesses/ (what the
                       scheduled task runs on the 1st -- see install_monthly_links.ps1)

QUARTERLY, in this order
  (download the three certifier files by hand first -- see docs/maintenance_checklist.md)
  new-cycle            archive last cycle's progress + output so the scrape starts fresh
  scrape               Lane 1 discovery (SerpApi Google Maps)
  prep                 prepare + resolve_review + flag_review, then stop for your review
  publish              upload + enrich + regenerate pages (run after you review)
  certs                Lane 2 dry run against the certifier downloads

AS NEEDED
  publish-submissions  after approving in admin.html: enrich, regenerate pages, commit
  maintain             read-only health checks of the live table
"""

VERBS = {
    "links": links,
    "monthly": monthly,
    "new-cycle": new_cycle,
    "scrape": scrape,
    "prep": prep,
    "publish": publish,
    "certs": certs,
    "publish-submissions": publish_submissions,
    "enrich-new": publish_submissions,   # old name, kept so muscle memory still works
    "maintain": maintain,
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in VERBS:
        print(USAGE)
        sys.exit(0 if len(sys.argv) < 2 else 2)
    VERBS[sys.argv[1]]()


if __name__ == "__main__":
    main()
