#!/usr/bin/env python3
"""
clean_addresses.py  --  The People's Ledger

Remove "N/A" tokens from the `address` column of the live `businesses` table.
Many rows carry a real Kentucky address plus a trailing "N/A" (e.g.
"Louisville, KY, N/A"), which was pushing them into the purge review pile.

After stripping "N/A" the script tidies the leftovers: drops empty comma
segments, collapses double spaces, strips stray leading/trailing commas. If a
cell was nothing but "N/A", the address is set to NULL (so purge keeps it as
blank).

Conventions match pipeline/purge_out_of_state.py and dedupe_live.py:
  - Run from the repo root. Portable paths; loads .env from the repo root.
  - Needs the SERVICE-ROLE key (UPDATE rights), not the publishable key.
  - --dry-run is the DEFAULT. Nothing changes unless you pass --apply.

Usage:
    python pipeline/clean_addresses.py --selftest   # validate cleaner, no network
    python pipeline/clean_addresses.py              # dry-run: report + backup CSV
    python pipeline/clean_addresses.py --apply       # write the changes

Run this BEFORE re-running purge_out_of_state.py.
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

# A delimited "N/A" (or "n/a", "N / A"), not preceded/followed by an
# alphanumeric, so we never gut a real word. "NA" with no slash is left alone.
_NA_RE = re.compile(r"(?<![A-Za-z0-9])[Nn]\s*/\s*[Aa](?![A-Za-z0-9])")


def clean_address(addr):
    """Return (new_value_or_None, changed_bool)."""
    if addr is None:
        return None, False
    original = str(addr)
    segments = []
    for seg in original.split(","):
        seg = _NA_RE.sub(" ", seg)            # drop N/A tokens in this segment
        seg = re.sub(r"\s+", " ", seg).strip()  # collapse whitespace
        if seg:
            segments.append(seg)
    result = ", ".join(segments).strip().strip(",").strip()
    if not result:
        # cell was empty or only N/A -> NULL
        return None, (original.strip() != "")
    return result, (result != original)


# ----------------------------------------------------------------------------
# Self-test (no network)
# ----------------------------------------------------------------------------
def selftest():
    cases = [
        ("Louisville, KY, N/A", "Louisville, KY"),
        ("N/A, Louisville, KY 40202", "Louisville, KY 40202"),
        ("123 Main St, Louisville, KY 40202 N/A", "123 Main St, Louisville, KY 40202"),
        ("N/A", None),
        ("n/a", None),
        ("N / A, Frankfort, Kentucky 40601", "Frankfort, Kentucky 40601"),
        ("Lexington, KY, N/A, N/A", "Lexington, KY"),
        ("789 Spring St, New Albany, IN 47150", "789 Spring St, New Albany, IN 47150"),  # unchanged
        ("", None),
        (None, None),
        ("Anna's Bakery, Owensboro, KY", "Anna's Bakery, Owensboro, KY"),  # 'Anna' not touched
        ("123 Nashville Rd, Bowling Green, KY 42101", "123 Nashville Rd, Bowling Green, KY 42101"),
    ]
    passed = failed = 0
    for addr, expected in cases:
        got, changed = clean_address(addr)
        ok = got == expected
        passed += ok
        failed += (not ok)
        flag = "ok " if ok else "FAIL"
        print(f"  [{flag}] {addr!r:<55} -> {got!r:<45} changed={changed}")
    print(f"\n  {passed} passed, {failed} failed")
    return failed == 0


# ----------------------------------------------------------------------------
# .env / credentials (same as purge_out_of_state.py)
# ----------------------------------------------------------------------------
def load_env():
    env_path = REPO_ROOT / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_credentials():
    load_env()
    url = os.environ.get("SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_KEY")
           or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("SUPABASE_SECRET_KEY"))
    if not url or not key:
        sys.exit(
            "Missing credentials. Add to .env at the repo root:\n"
            "  SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co\n"
            "  SUPABASE_SERVICE_KEY=<your service-role key>\n"
            "(The publishable/anon key cannot UPDATE.)"
        )
    if "ursmecdpgtqckacyhnko" not in url:
        sys.exit(f"Refusing to run: SUPABASE_URL is not the People's Ledger project.\n  got: {url}")
    return url.rstrip("/"), key


def fetch_all(url, key):
    import requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset, page = [], 0, 1000
    while True:
        r = requests.get(
            f"{url}/rest/v1/businesses",
            headers=headers,
            params={"select": "id,business_name,address", "order": "id.asc",
                    "limit": page, "offset": offset},
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


def patch_address(url, key, biz_id, new_value):
    import requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    r = requests.patch(
        f"{url}/rest/v1/businesses",
        headers=headers,
        params={"id": f"eq.{biz_id}"},
        json={"address": new_value},
        timeout=60,
    )
    r.raise_for_status()


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def main():
    ap = argparse.ArgumentParser(description="Strip N/A from business addresses.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually write changes. Default is a dry run.")
    ap.add_argument("--selftest", action="store_true",
                    help="Validate the cleaner against known cases. No network.")
    args = ap.parse_args()

    if args.selftest:
        print("Running address-cleaner self-test:\n")
        sys.exit(0 if selftest() else 1)

    url, key = get_credentials()
    print("Fetching all businesses...")
    rows = fetch_all(url, key)
    print(f"  fetched {len(rows)} records\n")

    changes = []
    for row in rows:
        new_val, changed = clean_address(row.get("address"))
        if changed:
            changes.append({
                "id": row.get("id"),
                "business_name": row.get("business_name"),
                "old_address": row.get("address"),
                "new_address": "" if new_val is None else new_val,
                "set_null": new_val is None,
            })

    print(f"Rows with an N/A to clean: {len(changes)}")
    set_null = sum(1 for c in changes if c["set_null"])
    if set_null:
        print(f"  of those, {set_null} become NULL (cell was only N/A)\n")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DATA_DIR / f"address_na_cleanup_{ts}.csv"
    write_csv(backup, changes,
              ["id", "business_name", "old_address", "new_address", "set_null"])
    print(f"Wrote before/after backup -> {backup}\n")

    if not args.apply:
        print("DRY RUN. Nothing changed. Review the CSV, then re-run with --apply.")
        return

    if not changes:
        print("Nothing to change.")
        return

    print(f"Updating {len(changes)} addresses...")
    for i, c in enumerate(changes, 1):
        new_val, _ = clean_address(c["old_address"])
        patch_address(url, key, c["id"], new_val)
        if i % 50 == 0 or i == len(changes):
            print(f"  updated {i}/{len(changes)}")
    print("\nDone. Now re-run the purge:")
    print("  python pipeline/purge_out_of_state.py            # dry run")
    print("  python pipeline/purge_out_of_state.py --apply     # delete")


if __name__ == "__main__":
    main()
