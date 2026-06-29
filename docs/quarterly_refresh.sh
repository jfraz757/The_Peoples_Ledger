#!/bin/bash
# ============================================================
# The People's Ledger Quarterly Data Refresh Pipeline
# Run once per quarter after manually downloading source files
# Usage: bash quarterly_refresh.sh
# ============================================================

PYTHON="C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe"
REPO="C:/Users/jfraz/The_Peoples_Ledger"

echo ""
echo "============================================================"
echo "  The People's Ledger Quarterly Data Refresh"
echo "  $(date '+%B %Y')"
echo "============================================================"
echo ""
echo "  BEFORE RUNNING THIS SCRIPT, confirm you have:"
echo "  [ ] Downloaded the Louisville HRC CSV from"
echo "      diversitycompliance.com and placed it in the repo root"
echo "  [ ] Attempted KY Transportation Cabinet export (B2GNow)"
echo "  [ ] Attempted KY Finance & Administration Cabinet MWBE .xlsx"
echo "      (Note: minority type fields removed as of 2026 due to"
echo "       anti-DEI legislation. HRC data is the reliable source.)"
echo ""
read -p "  Have you completed the manual downloads? (y/n): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo ""
  echo "  Exiting. Complete manual downloads first, then re-run."
  echo ""
  exit 0
fi

cd "$REPO" || { echo "ERROR: Could not navigate to repo. Check REPO path."; exit 1; }

echo ""
echo "[1/6] Running scraper (Lane 1 web discovery)..."
echo "      Collects new businesses from web sources."
echo "      API cost: ~\$9/1000 records (Anthropic + SerpApi)"
echo ""
"$PYTHON" pipeline/scrape.py
echo ""

echo "[2/6] Preparing (filter + dedupe)..."
echo "      Merges records, removes duplicates, and dispositions each"
echo "      row (Good to go / Needs review / Dropped). Free."
echo ""
"$PYTHON" pipeline/prepare.py
echo ""

echo "[3/6] Uploading to Supabase..."
echo "      Inserts the 'Good to go' rows in batches of 100. Free."
echo ""
"$PYTHON" pipeline/upload_to_supabase.py
echo ""

echo "[4/6] Enriching (industry + services)..."
echo "      Assigns industry category, then fills missing service"
echo "      descriptions, via Claude API."
echo "      API cost: ~\$0.75-\$1.00/1000 records"
echo ""
"$PYTHON" pipeline/enrich.py
echo ""

echo "[5/6] Cleaning addresses..."
echo "      Strips stray 'N/A' tokens so real Kentucky rows are not"
echo "      mis-flagged by the purge. Backs up every change to data/. Free."
echo ""
"$PYTHON" pipeline/clean_addresses.py --apply
echo ""

echo "[6/6] Checking for out-of-state businesses (DRY RUN)..."
echo "      Lane 1 scraping is not yet state-gated, so a fresh scrape can"
echo "      pull in Indiana/Ohio businesses. This reports them and writes"
echo "      data/out_of_state_to_delete_<ts>.csv and"
echo "      data/out_of_state_review_<ts>.csv. NOTHING is deleted here."
echo ""
"$PYTHON" pipeline/purge_out_of_state.py
echo ""

echo "============================================================"
echo "  Quarterly refresh complete. Data is loaded; no deletes yet."
echo ""
echo "  OUT-OF-STATE PURGE is intentionally manual. Review first:"
echo "  [ ] Open the two CSVs from step 6 in data/. Confirm the delete"
echo "      list is truly out-of-state and that nothing flagged"
echo "      kentucky_based='Yes' is a real KY business."
echo "  [ ] Apply the purge:"
echo "        python pipeline/purge_out_of_state.py --apply"
echo "  [ ] If real out-of-state rows landed in the review CSV, delete"
echo "      those exact ids (dry run first, then add --apply):"
echo "        python pipeline/purge_out_of_state.py --delete-from data/out_of_state_review_<ts>.csv"
echo ""
echo "  THEN finish the refresh:"
echo "  [ ] node generate-business-pages.js   (rebuild SEO pages + sitemap)"
echo "  [ ] Spot-check new records in the Supabase dashboard"
echo "  [ ] If new industry categories were added, update the pills in index.html"
echo "  [ ] Update the record count in README.md and the Technical Reference"
echo "  [ ] Test locally (python -m http.server 8080), then:"
echo "        git add -A"
echo "        git commit -m \"Quarterly refresh; remove out-of-state businesses\""
echo "        git push"
echo "============================================================"
echo ""
