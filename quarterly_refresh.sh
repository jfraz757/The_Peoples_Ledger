#!/bin/bash
# ============================================================
# The People's Ledger — Quarterly Data Refresh Pipeline
# Run once per quarter after manually downloading source files
# Usage: bash quarterly_refresh.sh
# ============================================================

PYTHON="C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe"
REPO="C:/Users/jfraz/The_Peoples_Ledger"

echo ""
echo "============================================================"
echo "  The People's Ledger — Quarterly Data Refresh"
echo "  $(date '+%B %Y')"
echo "============================================================"
echo ""
echo "  BEFORE RUNNING THIS SCRIPT — confirm you have:"
echo "  [ ] Downloaded the Louisville HRC CSV from"
echo "      diversitycompliance.com and placed it in the repo root"
echo "  [ ] Attempted KY Transportation Cabinet export (B2GNow)"
echo "  [ ] Attempted KY Finance & Administration Cabinet MWBE .xlsx"
echo "      (Note: minority type fields removed as of 2026 due to"
echo "       anti-DEI legislation — HRC data is the reliable source)"
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
echo "[1/5] Running scraper..."
echo "      Collects new businesses from web sources."
echo "      API cost: ~\$9/1000 records (Anthropic + SerpApi)"
echo ""
"$PYTHON" ky_minority_business_scraper.py
echo ""

echo "[2/5] Cleaning and deduplicating..."
echo "      Merges records, removes duplicates. Free."
echo ""
"$PYTHON" clean_ky_businesses.py
echo ""

echo "[3/5] Uploading to Supabase..."
echo "      Loads ky_minority_businesses_cleaned.csv in batches of 100. Free."
echo ""
"$PYTHON" upload_to_supabase.py
echo ""

echo "[4/5] Categorizing industries..."
echo "      Assigns industry category via Claude API."
echo "      API cost: ~\$0.75-\$1.00/1000 records"
echo ""
"$PYTHON" categorize_industries.py
echo ""

echo "[5/5] Filling missing services..."
echo "      Generates service descriptions for records missing them. Low cost."
echo ""
"$PYTHON" fill_missing_services.py
echo ""

echo "============================================================"
echo "  Quarterly refresh complete."
echo ""
echo "  AFTER RUNNING — check the following:"
echo "  [ ] Review any errors printed above"
echo "  [ ] Spot-check new records in Supabase dashboard"
echo "  [ ] If any new industry categories were added, update"
echo "      the hardcoded industry pills in index.html"
echo "  [ ] Run monthly_link_check.sh if not done this month"
echo "  [ ] Update the record count in README.md if it changed"
echo "============================================================"
echo ""
