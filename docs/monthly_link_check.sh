#!/bin/bash
# ============================================================
# The People's Ledger Monthly Link Status Check
# Run once a month from the repo root
# Usage: bash monthly_link_check.sh
# ============================================================

PYTHON="C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe"
REPO="C:/Users/jfraz/The_Peoples_Ledger"

echo ""
echo "============================================================"
echo "  The People's Ledger Monthly Maintenance"
echo "  $(date '+%B %Y')"
echo "============================================================"
echo ""

# Confirm we're in the right directory
cd "$REPO" || { echo "ERROR: Could not navigate to repo. Check REPO path."; exit 1; }

echo "[1/1] Running link status check..."
echo "      Re-checks all business website URLs and updates the"
echo "      status field (Active / Inactive / No Website)."
echo "      Buyblack URL fixes are NOT run here (they cost SerpApi)."
echo "      Run 'python pipeline/maintain.py --buyblack' only as needed."
echo ""
"$PYTHON" pipeline/maintain.py

echo ""
echo "============================================================"
echo "  Monthly check complete."
echo "  Review any errors above before closing."
echo "============================================================"
echo ""
