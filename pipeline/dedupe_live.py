#!/usr/bin/env python3
"""
dedupe_live.py - One-time (and repeatable) duplicate cleanup for the live
businesses table in The People's Ledger.

Place in: pipeline/dedupe_live.py
Run from: repo root

What it does
------------
Groups businesses by normalized name -- prepare.py's _dedup_name, which also drops
punctuation and a trailing corporate suffix (LLC, Inc, Co, Company...). For every
cluster of 2 or more rows it:

  1. Keeps the LOWEST id as the survivor (id stays stable, so generated
     business pages and indexed URLs keyed on id do not break).
  2. Backfills any blank, null, or "N/A" field on the survivor from its twins,
     so you keep the best available data rather than whichever row sorted first.
     For minority_type and certification_type (comma-separated), it unions the
     distinct values across the cluster.
  3. Decides whether the cluster is a TRIVIAL duplicate (same business, cosmetic
     address differences, or one copy missing the street address) or a REAL
     CONFLICT (two genuinely different street addresses, e.g. The Logo Warehouse).
       - Trivial  -> auto-merge: survivor patched, twins deleted.
       - Conflict -> two real locations; written to data/dedupe_review.csv and left alone.

Safety
------
  - DRY RUN by default. Nothing is written to Supabase. It produces:
        data/dedupe_plan.csv     (every auto-merge it WOULD do)
        data/dedupe_review.csv   (conflicts for you to resolve by hand)
    and prints a summary.
  - Pass --apply to actually patch survivors and delete twins. The review
    clusters are NEVER auto-applied, with or without --apply.
  - Pass --selftest to validate the address logic against known examples and
    exit. No network, no database. Run this first.

Requires the SERVICE ROLE key (deletes bypass RLS). Add to .env at repo root:
    SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=<service role key>

After an --apply run, regenerate the static pages:
    node generate-business-pages.js
"""

import sys
import csv
import re
import argparse
from pathlib import Path
from collections import defaultdict

import requests
from rapidfuzz import fuzz

# --- paths ------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

sys.path.insert(0, str(SCRIPT_DIR))
import common  # noqa: E402
# The SAME name key prepare.py and scrape.py use, so the live cleanup agrees with the
# pre-upload dedup about what counts as one business. This used to group on
# lower(trim(name)) only, so "Braxton Brewing Co." and "Braxton Brewing Company" -- one
# business to prepare.py -- were two different businesses here and never merged.
from prepare import _dedup_name  # noqa: E402

# Fields backfilled on the survivor when blank. website is handled separately
# (it gets upgraded, not just filled), and the two comma-separated fields are
# unioned.
FILL_FIELDS = [
    "address", "phone", "services_products",
    "industry", "kentucky_based", "status",
]
UNION_FIELDS = ["minority_type", "certification_type"]

ADDRESS_ABBR = {
    "street": "st", "road": "rd", "avenue": "ave", "drive": "dr",
    "boulevard": "blvd", "suite": "ste", "court": "ct", "lane": "ln",
    "place": "pl", "parkway": "pkwy", "highway": "hwy", "circle": "cir",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "apartment": "apt", "building": "bldg", "floor": "fl",
}

BLANK_VALUES = {"", "n/a", "na", "none", "null", "unknown"}


# --- value helpers ----------------------------------------------------------
def is_blank(value):
    if value is None:
        return True
    return str(value).strip().lower() in BLANK_VALUES


def normalize_address(addr):
    """Lowercase, drop punctuation, expand-then-standardize abbreviations,
    strip N/A tokens and the pandas '.0' float artifact, collapse whitespace."""
    if addr is None:
        return ""
    s = str(addr).lower()
    s = re.sub(r"\bn/?a\b", " ", s)          # drop N/A tokens
    s = re.sub(r"(\d)\.0\b", r"\1", s)        # 47111.0 -> 47111
    s = re.sub(r"[^a-z0-9\s]", " ", s)        # drop punctuation
    tokens = [ADDRESS_ABBR.get(t, t) for t in s.split()]
    return " ".join(tokens).strip()


def street_number(norm_addr):
    """First standalone number that looks like a house/box number, or None.
    Used to tell a real street-address conflict from a formatting difference.

    The ZIP code is removed first. Without that, a city-only address such as
    "Louisville, KY 40211" yielded 40211 as its "street number", so it 'conflicted'
    with "670 S 39th St, Louisville, KY 40211" -- the same business -- and the pair
    was sent to manual review instead of merging (Skips Defense Solutions, Thompson
    Catering, October 2026)."""
    s = re.sub(r"\b\d{5}(\s\d{4})?\s*$", "", norm_addr).strip()   # trailing ZIP / ZIP+4
    m = re.search(r"\b(\d{1,6})\b", s)
    return m.group(1) if m else None


def zip_code(addr):
    """The 5-digit ZIP at the end of an address, or None."""
    m = re.search(r"\b(\d{5})(?:\s\d{4})?\s*$", normalize_address(addr))
    return m.group(1) if m else None


def normalize_phone(p):
    """Reduce a phone field to its first 10-digit number so formatting and a
    trailing fax number do not block a match. '(817) 498-0388 Fax: ...' and
    '817-498-0388' both become '8174980388'."""
    if is_blank(p):
        return ""
    digits = re.sub(r"\D", "", str(p))
    if len(digits) >= 11 and digits[0] == "1":
        digits = digits[1:]
    return digits[:10] if len(digits) >= 10 else digits


def address_quality(addr):
    """Rank an address for survivor selection:
    3 = real street address, 2 = PO box, 1 = city only, 0 = blank/N/A.
    A bare ZIP must not count as a street number, so a real street address is
    detected as a leading house number followed by a word, or a street-type
    token, not just any digit run."""
    if addr is None:
        return 0
    raw = str(addr).lower()
    norm = normalize_address(addr)
    if not norm:
        return 0
    if re.search(r"\bp\.?\s*o\.?\s*box\b", raw):
        return 2
    if re.match(r"^\d{1,6}\s+[a-z]", norm):
        return 3
    if re.search(r"\b(st|rd|ave|dr|blvd|ln|ct|pl|pkwy|hwy|cir|way|pike|plaza|"
                 r"broadway|trl|terr?)\b", norm):
        return 3
    return 1


def website_quality(w):
    """2 = real site, 1 = buyblack.org or facebook placeholder, 0 = blank."""
    if is_blank(w):
        return 0
    wl = str(w).lower()
    if "buyblack.org" in wl or "facebook.com" in wl:
        return 1
    return 2


def addresses_conflict(a, b):
    """True only when both addresses carry a real, differing street number and
    the strings are not a close fuzzy match. A city-only or N/A twin is treated
    as a less-complete duplicate, not a conflict."""
    na, nb = normalize_address(a), normalize_address(b)
    if not na or not nb:
        return False
    if na == nb:
        return False
    sa, sb = street_number(na), street_number(nb)
    if sa is None or sb is None:
        return False               # one side has no street number -> not a conflict
    if sa == sb:
        return False               # same building, formatting differs
    # both have street numbers and they differ; allow a close fuzzy match through
    return fuzz.token_sort_ratio(na, nb) < 88


# Generic ownership tags. When a merged cluster also carries a specific type, these are
# dropped rather than unioned in: "Black-Owned, Women-Owned, Minority-Owned (general)"
# says no more than "Black-Owned, Women-Owned", and the generic tag is the unverified
# page-text label (Section 6b). Same rule as prepare.best_minority_type.
GENERIC_OWNERSHIP = {"minority-owned (general)", "minority-owned"}


def union_csv(values, drop_generic=False):
    """Union comma-separated tokens across rows, preserving first-seen order."""
    seen, out = set(), []
    for v in values:
        if is_blank(v):
            continue
        for tok in str(v).split(","):
            tok = tok.strip()
            key = tok.lower()
            if tok and key not in seen:
                seen.add(key)
                out.append(tok)
    if drop_generic and any(t.lower() not in GENERIC_OWNERSHIP for t in out):
        out = [t for t in out if t.lower() not in GENERIC_OWNERSHIP]
    return ", ".join(out)


# --- supabase ---------------------------------------------------------------
def fetch_all(url, key):
    return common.fetch_all("*", key=key)


def patch_survivor(url, key, sid, fields):
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json", "Prefer": "return=minimal",
    }
    r = requests.patch(f"{url}/rest/v1/businesses",
                       headers=headers, params={"id": f"eq.{sid}"},
                       json=fields, timeout=60)
    r.raise_for_status()


def delete_row(url, key, rid):
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Prefer": "return=minimal"}
    r = requests.delete(f"{url}/rest/v1/businesses",
                        headers=headers, params={"id": f"eq.{rid}"}, timeout=60)
    r.raise_for_status()


# --- core -------------------------------------------------------------------
def build_plan(rows):
    """Return (auto_merges, conflicts). auto_merges is a list of dicts with the
    survivor id, the patch to apply, and the loser ids to delete."""
    clusters = defaultdict(list)
    for row in rows:
        key = _dedup_name(row.get("business_name", ""))
        if key:
            clusters[key].append(row)

    auto_merges, conflicts = [], []

    for key, group in clusters.items():
        if len(group) < 2:
            continue

        # Survivor = best data quality (street address, then real website),
        # lowest id breaks ties. This can differ from lowest id, so the row
        # with the better address wins over a PO box or city-only twin.
        survivor = max(group, key=lambda r: (
            address_quality(r.get("address")),
            website_quality(r.get("website")),
            -r["id"],
        ))
        losers = [r for r in group if r["id"] != survivor["id"]]

        # A loser is a real conflict only if its address conflicts with the
        # survivor AND it does not share the survivor's phone. Same name plus
        # same phone is a duplicate even when the street addresses differ.
        # ...unless the two ZIP codes differ: then they are two locations of one business
        # that share a central phone number (V.C. Veterans Contracting, Lexington 40509
        # and Richmond 40475), and merging would delete a real listing.
        sp = normalize_phone(survivor.get("phone"))
        sz = zip_code(survivor.get("address"))
        incompatible = []
        for r in losers:
            if addresses_conflict(survivor.get("address"), r.get("address")):
                rp = normalize_phone(r.get("phone"))
                rz = zip_code(r.get("address"))
                same_phone = sp and rp and sp == rp
                different_zip = sz and rz and sz != rz
                if not same_phone or different_zip:
                    incompatible.append(r)

        if incompatible:
            for r in group:
                conflicts.append({
                    "name_key": key, "id": r["id"],
                    "business_name": r.get("business_name", ""),
                    "address": r.get("address", ""),
                    "industry": r.get("industry", ""),
                    "phone": r.get("phone", ""),
                    "website": r.get("website", ""),
                })
            continue

        # build the survivor patch: backfill blanks, union the csv fields
        patch = {}
        for f in FILL_FIELDS:
            if is_blank(survivor.get(f)):
                for r in losers:
                    if not is_blank(r.get(f)):
                        patch[f] = r.get(f)
                        break

        # website: upgrade, do not just fill. Across the cluster pick the
        # highest-quality site (real business site > buyblack/social > blank).
        # This replaces a survivor's placeholder link with the real site from
        # the row it absorbs.
        best_site = max(group, key=lambda r: (website_quality(r.get("website")),
                                              -r["id"]))
        if (website_quality(best_site.get("website"))
                > website_quality(survivor.get("website"))):
            patch["website"] = best_site.get("website")

        for f in UNION_FIELDS:
            merged = union_csv([survivor.get(f)] + [r.get(f) for r in losers],
                               drop_generic=(f == "minority_type"))
            if merged and merged.lower() != str(survivor.get(f) or "").strip().lower():
                patch[f] = merged

        auto_merges.append({
            "name_key": key,
            "survivor_id": survivor["id"],
            "survivor_name": survivor.get("business_name", ""),
            "survivor_address": survivor.get("address", ""),
            "loser_ids": [r["id"] for r in losers],
            "patch": patch,
        })

    return auto_merges, conflicts


def write_plan_csvs(auto_merges, conflicts):
    DATA_DIR.mkdir(exist_ok=True)
    plan_path = DATA_DIR / "dedupe_plan.csv"
    review_path = DATA_DIR / "dedupe_review.csv"

    with plan_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["survivor_id", "survivor_name", "survivor_address",
                    "delete_ids", "fields_backfilled"])
        for m in auto_merges:
            w.writerow([m["survivor_id"], m["survivor_name"],
                        m.get("survivor_address", ""),
                        " ".join(str(i) for i in m["loser_ids"]),
                        "; ".join(f"{k}={v}" for k, v in m["patch"].items())])

    with review_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name_key", "id", "business_name",
                                          "address", "industry", "phone",
                                          "website"])
        w.writeheader()
        for c in conflicts:
            w.writerow(c)

    return plan_path, review_path


def apply_plan(url, key, auto_merges):
    patched = deleted = 0
    for m in auto_merges:
        if m["patch"]:
            patch_survivor(url, key, m["survivor_id"], m["patch"])
            patched += 1
        for rid in m["loser_ids"]:
            delete_row(url, key, rid)
            deleted += 1
    return patched, deleted


# --- selftest ---------------------------------------------------------------
def selftest():
    """Validate conflict detection against real pairs from the 5a output."""
    cases = [
        # (addr_a, addr_b, expect_conflict, label)
        ("224 Carter Avenue, Louisville, KY, 40229", "N/A, Louisville, KY, N/A",
         False, "street vs N/A (same business)"),
        ("8703 High Jackson Road, Charlestown, IN, 47111",
         "8703 High Jackson Road, Charlestown, IN, 47111.0",
         False, "trailing .0 float artifact"),
        ("2607 Robin Road West, New Albany, IN, 47150",
         "2607 W Robin Rd, New Albany, IN, 47150",
         False, "abbreviation + word order"),
        ("642 S. 4th Street, Suite 400, Louisville, KY, 40202",
         "642 S. 4th Street, Suite #400, Louisville, KY, 40202",
         False, "suite punctuation"),
        ("9850 Von Allmen Court, Suite 201, Louisville, KY, 40241",
         "10308 Arbor Oak Drive, Louisville, KY, 40229",
         True, "Logo Warehouse: two real addresses"),
        ("9400 Bunsen Parkway, Suite 150, Louisville, KY, 40220",
         "5220 Oakland Avenue, St. Louis, MO, 63110",
         True, "Civil Design: different state"),
        ("PO Box 1858, Bridgeview, IL, 60455",
         "3950 W. 155th Street, Markham, IL, 60428",
         True, "Royal Crane: PO box vs street, different city"),
        ("Louisville, KY", "Louisville, Kentucky",
         False, "city only, both copies"),
        ("670 S. 39th St., Louisville, KY, 40211", "Louisville, KY 40211",
         False, "ZIP is not a street number (Skips Defense)"),
        ("Winchester, KY 40391", "121 Hud Road, Winchester, Kentucky 40391",
         False, "ZIP is not a street number (Thompson Catering)"),
        ("1307 E Broadway St, Campbellsville, KY 42718", "801 S Main St, Nicholasville, KY 40356",
         True, "Fiesta Mexico: two real locations"),
    ]
    ok = True
    for a, b, expect, label in cases:
        got = addresses_conflict(a, b)
        flag = "PASS" if got == expect else "FAIL"
        if got != expect:
            ok = False
        print(f"  [{flag}] {label}: conflict={got} (expected {expect})")

    print("\n  Phone normalization:")
    phone_cases = [
        ("(817) 498-0388 Fax: (817) 281-1867", "817-498-0388", True, "fax tail vs dashes"),
        ("+1 859-247-9056", "859-247-9056", True, "leading +1"),
        ("(270) 304-9301", "270-304-9301", True, "parens vs dashes"),
        ("(502) 708-0634", "(502) 999-0000", False, "different numbers"),
    ]
    for a, b, expect, label in phone_cases:
        match = (normalize_phone(a) != "" and normalize_phone(a) == normalize_phone(b))
        flag = "PASS" if match == expect else "FAIL"
        if match != expect:
            ok = False
        print(f"  [{flag}] {label}: match={match} (expected {expect})")

    print("\nSelftest:", "all passed" if ok else "FAILURES ABOVE")
    return ok


# --- main -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Dedupe the live businesses table.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually patch survivors and delete twins. "
                         "Without this flag the script only writes the plan CSVs.")
    ap.add_argument("--yes", action="store_true",
                    help="With --apply: skip the confirmation question.")
    ap.add_argument("--selftest", action="store_true",
                    help="Validate address logic against known examples and exit.")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    url, key = common.require_service_credentials("merges and deletes rows")

    print("Fetching businesses...")
    rows = fetch_all(url, key)
    print(f"  {len(rows)} rows fetched.")

    auto_merges, conflicts = build_plan(rows)
    plan_path, review_path = write_plan_csvs(auto_merges, conflicts)

    rows_removed = sum(len(m["loser_ids"]) for m in auto_merges)
    conflict_clusters = len({c["name_key"] for c in conflicts})

    print("\n--- SUMMARY ---")
    print(f"  Auto-merge clusters : {len(auto_merges)}")
    print(f"  Rows to delete      : {rows_removed}")
    print(f"  Kept separate       : {conflict_clusters} name(s), {len(conflicts)} rows -- "
          f"same name, different locations (listed in dedupe_review.csv; nothing to do)")
    print(f"\n  Plan written   : {plan_path}")
    print(f"  Review written : {review_path}")

    if not args.apply:
        print("\nDRY RUN. Nothing was changed. Review the two CSVs above, then "
              "re-run with --apply to perform the auto-merges.")
        return

    if not auto_merges:
        print("\nNothing to merge.")
        return
    print("\n--apply set. Performing auto-merges...")
    if not args.yes:
        confirm = input(f"This will patch {len(auto_merges)} survivors and delete "
                        f"{rows_removed} rows. Proceed? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print(f"Aborted (got {confirm!r}). No changes made. "
                  f"Re-run with --apply --yes to skip this question.")
            return

    patched, deleted = apply_plan(url, key, auto_merges)
    print(f"\nDone. Survivors patched: {patched}. Rows deleted: {deleted}.")
    print("Same-name businesses at different locations were left untouched, as intended.")
    print("\nNext: regenerate the static pages with "
          "`node generate-business-pages.js`, then commit and push.")


if __name__ == "__main__":
    main()
