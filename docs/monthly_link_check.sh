#!/bin/bash
# ============================================================
# The People's Ledger — Monthly Link Status Check
# Run this once a month from the repo root
# Usage: bash monthly_link_check.sh
# ============================================================

PYTHON="C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe"
REPO="C:/Users/jfraz/The_Peoples_Ledger"

echo ""
echo "============================================================"
echo "  The People's Ledger — Monthly Maintenance"
echo "  $(date '+%B %Y')"
echo "============================================================"
echo ""

# Confirm we're in the right directory
cd "$REPO" || { echo "ERROR: Could not navigate to repo. Check REPO path."; exit 1; }

echo "[1/1] Running link status check..."
echo "      This re-checks all business website URLs and updates"
echo "      the status field (Active / Inactive / No Website)."
echo ""
"$PYTHON" check_link_status.py

echo ""
echo "============================================================"
echo "  Monthly check complete."
echo "  Review any errors above before closing."
echo "============================================================"
echo ""
