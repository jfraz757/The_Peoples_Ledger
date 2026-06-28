#!/usr/bin/env python3
"""
purge_out_of_state.py  --  The People's Ledger

Remove businesses from the live `businesses` table whose address resolves to a
US state OTHER than Kentucky. Blank/NULL addresses are KEPT. Addresses present
but whose state cannot be confidently determined are KEPT and written to a
review CSV (we never delete on uncertainty).

Conventions (match pipeline/dedupe_live.py):
  - Run from the repo root.
  - Loads .env from the repo root; derives data/ from this file's location.
  - Needs the SERVICE-ROLE key (DELETE rights), not the publishable key.
  - --dry-run is the DEFAULT. Nothing is deleted unless you pass --apply.

Usage:
    python pipeline/purge_out_of_state.py --selftest      # validate the parser, no network
    python pipeline/purge_out_of_state.py                 # dry-run: report + write CSVs
    python pipeline/purge_out_of_state.py --apply         # actually delete (backs up first)

Deleting a reviewed list (rows the purge KEPT but you've judged out-of-state):
    python pipeline/purge_out_of_state.py --delete-from data/out_of_state_review_<ts>.csv
                                                          # dry-run: full backup + warnings
    python pipeline/purge_out_of_state.py --delete-from data/out_of_state_review_<ts>.csv --apply
                                                          # actually delete those exact IDs

CSVs written to data/:
    out_of_state_to_delete_<ts>.csv   # the deletion candidates (also the backup)
    out_of_state_review_<ts>.csv      # address present but state undetermined (KEPT)
    review_delete_backup_<ts>.csv     # full rows backed up before a --delete-from run
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# Portable paths (no hardcoded absolute paths anywhere)
# ----------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent          # pipeline/ lives under the repo root
DATA_DIR = REPO_ROOT / "data"

# ----------------------------------------------------------------------------
# State reference data
# ----------------------------------------------------------------------------
STATE_ABBRS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

# Full state names -> abbreviation. "Washington" is deliberately omitted from
# full-name matching because it collides with the city, DC, and the surname;
# real WA/DC addresses carry a ZIP and resolve via the ZIP path instead.
FULL_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY",
}

# First-3-digits-of-ZIP -> state, as contiguous ranges. Used only as a fallback
# when no textual state token is found. KY is 400-427; Indiana (Louisville's
# neighbor, e.g. 47111 / 47150 New Albany) is 460-479, so the river is a clean
# split. Inclusive ranges.
ZIP3_RANGES = [
    (6, 9, "PR"), (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"),
    (39, 49, "ME"), (50, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"),
    (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"), (200, 205, "DC"),
    (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"), (270, 289, "NC"),
    (290, 299, "SC"), (300, 319, "GA"), (320, 349, "FL"), (350, 369, "AL"),
    (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"), (400, 427, "KY"),
    (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"), (500, 528, "IA"),
    (530, 549, "WI"), (550, 567, "MN"), (570, 577, "SD"), (580, 588, "ND"),
    (590, 599, "MT"), (600, 629, "IL"), (630, 658, "MO"), (660, 679, "KS"),
    (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"), (730, 749, "OK"),
    (750, 799, "TX"), (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"),
    (840, 847, "UT"), (850, 865, "AZ"), (870, 884, "NM"), (889, 898, "NV"),
    (900, 961, "CA"), (967, 968, "HI"), (970, 979, "OR"), (980, 994, "WA"),
    (995, 999, "AK"),
]

# state abbreviation immediately before a ZIP, e.g. "Louisville, KY 40202".
# 4-5 digits: 4 covers Northeast ZIPs whose leading zero was stripped upstream
# ("Randolph, MA, 2368" was really 02368).
_ABBR_ZIP_RE = re.compile(r"\b([A-Za-z]{2})\b[\s,\.]+\d{4,5}")
# any 5-digit ZIP (tolerates the "47111.0" float artifact and ZIP+4)
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?(?:\.0+)?\b")
# trailing country phrase, stripped before state detection. "United States"
# is valid address text, so we ignore it for parsing but leave it in the DB.
_COUNTRY_RE = re.compile(
    r"[\s,]*\b(?:united states of america|united states|u\.?\s?s\.?\s?a\.?|usa)\.?\s*$",
    re.IGNORECASE,
)


def _strip_country(text):
    prev = None
    while prev != text:
        prev = text
        text = _COUNTRY_RE.sub("", text).strip().strip(",").strip()
    return text


def _zip3_to_state(zip5: str):
    try:
        z3 = int(zip5[:3])
    except (ValueError, TypeError):
        return None
    for low, high, st in ZIP3_RANGES:
        if low <= z3 <= high:
            return st
    return None


def detect_state(address):
    """
    Return (state_abbr_or_None, method, confident_bool).

    method is one of: abbr_zip, trailing_token, full_name, zip_prefix, none.
    confident_bool is True when we trust the result enough to act on it (delete
    or keep). A non-KY result with confident=False is routed to manual review.
    """
    if address is None:
        return None, "none", False
    text = _strip_country(str(address).strip())
    if not text:
        return None, "none", False

    # 1. State abbreviation directly before a ZIP (most reliable; structured
    #    addresses look like "City, ST 40202"). Take the LAST such match.
    last = None
    for m in _ABBR_ZIP_RE.finditer(text):
        cand = m.group(1).upper()
        if cand in STATE_ABBRS:
            last = cand
    if last:
        return last, "abbr_zip", True

    # 2. Trailing standalone 2-letter state token in the final comma segment
    #    ("New Albany, IN", "Maryland Heights, MO"). A concrete state code is
    #    stronger evidence than a spelled-out name, so this runs before names.
    tail = text.split(",")[-1].strip()
    for tok in reversed(re.findall(r"\b([A-Za-z]{2})\b", tail)):
        if tok.upper() in STATE_ABBRS:
            return tok.upper(), "trailing_token", True

    # 3. Full state name anywhere. KY by name is safe to keep. A NON-KY result
    #    found only by a bare name (no ZIP, no code) is low-confidence to avoid
    #    a state-name-as-street-name false positive ("456 Texas St, Louisville")
    #    -- those route to review, not delete. Longest match wins ("new york"
    #    over "york").
    low = " " + text.lower() + " "
    best = None
    for name, abbr in FULL_NAME_TO_ABBR.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            if best is None or len(name) > len(best[0]):
                best = (name, abbr)
    if best:
        abbr = best[1]
        return abbr, "full_name", (abbr == "KY")

    # 4. ZIP-prefix inference (fallback). Reliable for KY-vs-not.
    zips = _ZIP_RE.findall(text)
    if zips:
        st = _zip3_to_state(zips[-1])
        if st:
            return st, "zip_prefix", True

    return None, "none", False


def classify(address):
    """Return one of: keep_blank, keep_ky, delete, review (+ state, method)."""
    if address is None or not str(address).strip():
        return "keep_blank", None, "none"
    state, method, confident = detect_state(address)
    if state is None:
        return "review", None, method
    if state == "KY":
        return "keep_ky", "KY", method
    # non-KY
    if confident:
        return "delete", state, method
    return "review", state, method  # non-KY but low confidence -> human looks


# ----------------------------------------------------------------------------
# Self-test (no network required)
# ----------------------------------------------------------------------------
def selftest():
    cases = [
        ("123 Main St, Louisville, KY 40202", "keep_ky"),
        ("500 W Jefferson St, Louisville, Kentucky 40202", "keep_ky"),
        ("Louisville, KY", "keep_ky"),
        ("Frankfort, Kentucky", "keep_ky"),
        ("789 Spring St, New Albany, IN 47150", "delete"),
        ("47111.0 area, Jeffersonville, IN 47111.0", "delete"),  # float ZIP artifact
        ("New Albany, IN", "delete"),
        ("100 Vine St, Cincinnati, OH 45202", "delete"),
        ("1 Infinite Loop, Cupertino, CA 95014", "delete"),
        ("123 Kentucky Ave, Indianapolis, IN 46204", "delete"),  # KY is a street name here
        ("", "keep_blank"),
        (None, "keep_blank"),
        ("   ", "keep_blank"),
        ("Lexington", "review"),          # city only, no state signal
        ("PO Box 1234", "review"),         # no state, no usable ZIP
        ("Suite 200, Some Plaza", "review"),
        ("Nashville, Tennessee", "review"),  # full-name-only non-KY -> review (safe)
        ("Bowling Green, KY 42101", "keep_ky"),
        ("123 Main St 40203", "keep_ky"),  # ZIP-only, KY range
        ("456 Oak Rd 60601", "delete"),    # ZIP-only, Chicago IL range
        # --- regressions from the 20260628 review CSV ---
        ("Mount Sterling, KY, United States", "keep_ky"),   # country suffix
        ("KY, United States", "keep_ky"),
        ("Cynthiana, KY, United States", "keep_ky"),
        ("Randolph, MA, 2368", "delete"),                   # leading-zero ZIP
        ("Hackensack, NJ, 7601", "delete"),
        ("PO Box 101, Durham, NH, 3824", "delete"),
        ("Maryland Heights, MO", "delete"),                 # trailing code beats name
        ("Wyoming", "review"),                              # bare non-KY name
        ("Phoenix, Arizona", "review"),
        ("Albuquerque, New Mexico", "review"),
    ]
    passed = failed = 0
    for addr, expected in cases:
        got, _, _ = classify(addr)
        ok = got == expected
        passed += ok
        failed += (not ok)
        flag = "ok " if ok else "FAIL"
        st, method, conf = detect_state(addr)
        print(f"  [{flag}] expected={expected:<10} got={got:<10} "
              f"state={str(st):<4} method={method:<14} {addr!r}")
    print(f"\n  {passed} passed, {failed} failed")
    return failed == 0


# ----------------------------------------------------------------------------
# .env loading (no hard dependency on python-dotenv)
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
    # Accept the common names for the service-role key. This MUST be the
    # service-role key (DELETE rights), not the publishable read key.
    key = (os.environ.get("SUPABASE_SERVICE_KEY")
           or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("SUPABASE_SECRET_KEY"))
    if not url or not key:
        sys.exit(
            "Missing credentials. Add to .env at the repo root:\n"
            "  SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co\n"
            "  SUPABASE_SERVICE_KEY=<your service-role key>\n"
            "(The publishable/anon key cannot DELETE.)"
        )
    if "ursmecdpgtqckacyhnko" not in url:
        sys.exit(f"Refusing to run: SUPABASE_URL is not the People's Ledger project.\n  got: {url}")
    return url.rstrip("/"), key


# ----------------------------------------------------------------------------
# Supabase I/O
# ----------------------------------------------------------------------------
def fetch_all(url, key):
    import requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset, page = [], 0, 1000
    while True:
        r = requests.get(
            f"{url}/rest/v1/businesses",
            headers=headers,
            params={
                "select": "id,business_name,address,kentucky_based",
                "order": "id.asc",
                "limit": page,
                "offset": offset,
            },
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


def fetch_by_ids(url, key, ids):
    """Fetch FULL rows (select=*) for the given ids, for a complete backup."""
    import requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    found = []
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        id_list = ",".join(str(x) for x in chunk)
        r = requests.get(
            f"{url}/rest/v1/businesses",
            headers=headers,
            params={"select": "*", "id": f"in.({id_list})"},
            timeout=60,
        )
        r.raise_for_status()
        found.extend(r.json())
    return found


def read_ids_from_csv(path):
    """Pull the `id` column out of a review CSV. Returns a de-duped list of ids
    in file order. Errors out clearly if the column is missing."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"CSV not found: {p}")
    ids, seen = [], set()
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "id" not in reader.fieldnames:
            sys.exit(f"CSV has no 'id' column. Columns: {reader.fieldnames}")
        for row in reader:
            raw = (row.get("id") or "").strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            ids.append(raw)
    return ids


def delete_ids(url, key, ids):
    import requests
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Prefer": "return=minimal",
    }
    deleted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        id_list = ",".join(str(x) for x in chunk)
        r = requests.delete(
            f"{url}/rest/v1/businesses",
            headers=headers,
            params={"id": f"in.({id_list})"},
            timeout=60,
        )
        r.raise_for_status()
        deleted += len(chunk)
        print(f"  deleted {deleted}/{len(ids)}")
    return deleted


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


# ----------------------------------------------------------------------------
# Delete a reviewed list of ids
# ----------------------------------------------------------------------------
def delete_from_review(csv_path, apply):
    url, key = get_credentials()
    ids = read_ids_from_csv(csv_path)
    if not ids:
        print("No ids found in the CSV. Nothing to do.")
        return
    print(f"Read {len(ids)} unique id(s) from {csv_path}\n")

    print("Fetching full rows for backup...")
    rows = fetch_by_ids(url, key, ids)
    found_ids = {str(r.get("id")) for r in rows}
    missing = [i for i in ids if i not in found_ids]
    print(f"  matched {len(rows)} of {len(ids)} in the database")
    if missing:
        print(f"  {len(missing)} id(s) not found (already deleted?): "
              f"{', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")
    print()

    # Safety re-check: re-classify each address with the CURRENT parser. The
    # whole point of review was the parser mis-flagging real KY rows, so flag
    # anything that now resolves to Kentucky (or blank) BEFORE we delete it.
    resurfaced = []
    for r in rows:
        verdict, state, method = classify(r.get("address"))
        if verdict in ("keep_ky", "keep_blank"):
            resurfaced.append((r.get("id"), r.get("business_name"),
                               r.get("address"), verdict))
    if resurfaced:
        print(f"  !! WARNING: {len(resurfaced)} of these now resolve to "
              f"KEEP under the current parser:")
        for rid, name, addr, verdict in resurfaced[:25]:
            print(f"       [{verdict}] id={rid}  {name!r}  {addr!r}")
        if len(resurfaced) > 25:
            print(f"       ... and {len(resurfaced) - 25} more")
        print("     These are the exact rows the purge would KEEP. If any are real\n"
              "     Kentucky businesses, pull them out of the CSV before --apply.\n")

    # Full backup of everything we're about to touch (select=* union of keys).
    if rows:
        fields = []
        for r in rows:
            for k in r.keys():
                if k not in fields:
                    fields.append(k)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = DATA_DIR / f"review_delete_backup_{ts}.csv"
        write_csv(backup, rows, fields)
        print(f"Backed up {len(rows)} full row(s) -> {backup}\n")

    if not apply:
        print(f"DRY RUN. Nothing deleted. {len(rows)} row(s) would be removed.")
        print("Re-run with --apply once the warnings above (if any) look fine.")
        return

    del_ids = [r.get("id") for r in rows]
    if not del_ids:
        print("Nothing in the database to delete.")
        return
    print(f"Deleting {len(del_ids)} record(s)...")
    n = delete_ids(url, key, del_ids)
    print(f"\nDone. Deleted {n} reviewed record(s).")
    print("Next: regenerate SEO pages so the static pages and sitemap match the DB:")
    print("  node generate-business-pages.js && git add businesses/ && "
          "git commit -m 'Remove reviewed out-of-state businesses' && git push")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Purge non-Kentucky businesses by address.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Default is a dry run (no changes).")
    ap.add_argument("--selftest", action="store_true",
                    help="Validate the address parser against known cases. No network.")
    ap.add_argument("--delete-from", metavar="CSV", default=None,
                    help="Delete the exact rows whose ids are listed in this CSV "
                         "(e.g. a review CSV you've eyeballed). Dry-run unless --apply.")
    args = ap.parse_args()

    if args.selftest:
        print("Running address-parser self-test:\n")
        sys.exit(0 if selftest() else 1)

    if args.delete_from:
        delete_from_review(args.delete_from, args.apply)
        return

    url, key = get_credentials()
    print("Fetching all businesses...")
    rows = fetch_all(url, key)
    print(f"  fetched {len(rows)} records\n")

    to_delete, review = [], []
    counts = {"keep_blank": 0, "keep_ky": 0, "delete": 0, "review": 0}
    flag_mismatch = 0

    for row in rows:
        verdict, state, method = classify(row.get("address"))
        counts[verdict] += 1
        kb = (row.get("kentucky_based") or "").strip()
        enriched = {
            "id": row.get("id"),
            "business_name": row.get("business_name"),
            "address": row.get("address"),
            "detected_state": state or "",
            "method": method,
            "kentucky_based": kb,
        }
        if verdict == "delete":
            to_delete.append(enriched)
            if kb.lower() == "yes":
                flag_mismatch += 1
        elif verdict == "review":
            review.append(enriched)

    print("Summary:")
    print(f"  keep (Kentucky)        : {counts['keep_ky']}")
    print(f"  keep (blank/null)      : {counts['keep_blank']}")
    print(f"  keep (needs review)    : {counts['review']}")
    print(f"  DELETE (out of state)  : {counts['delete']}")
    if flag_mismatch:
        print(f"  !! {flag_mismatch} of the delete candidates are flagged "
              f"kentucky_based='Yes' -- worth eyeballing before --apply")
    print()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fields = ["id", "business_name", "address", "detected_state", "method", "kentucky_based"]
    del_path = DATA_DIR / f"out_of_state_to_delete_{ts}.csv"
    rev_path = DATA_DIR / f"out_of_state_review_{ts}.csv"
    write_csv(del_path, to_delete, fields)
    write_csv(rev_path, review, fields)
    print(f"Wrote delete candidates -> {del_path}")
    print(f"Wrote review list       -> {rev_path}\n")

    if not args.apply:
        print("DRY RUN. Nothing deleted. Review the CSVs above, then re-run with --apply.")
        return

    if not to_delete:
        print("Nothing to delete.")
        return

    print(f"Deleting {len(to_delete)} records... (backup saved to {del_path})")
    ids = [r["id"] for r in to_delete]
    n = delete_ids(url, key, ids)
    print(f"\nDone. Deleted {n} out-of-state records.")
    print("Next: regenerate SEO pages so the static pages and sitemap match the DB:")
    print("  node generate-business-pages.js && git add businesses/ && "
          "git commit -m 'Remove out-of-state businesses' && git push")


if __name__ == "__main__":
    main()
