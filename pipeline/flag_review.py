"""
Annotate businesses_prepared.csv with a "Review Flag" column so the things worth a second
look surface instead of hiding among hundreds of rows.

    python pipeline/flag_review.py              # annotate in place
    python pipeline/flag_review.py --report     # print the flagged rows, write nothing

Run it AFTER prepare.py and resolve_review.py, right before your manual review pass.

WHY THIS EXISTS
The July 2026 run produced 941 "Good to go" rows. Reviewing that many by eye means the
handful of genuine problems are invisible: `1st Franklin Financial`, a national consumer
finance chain, sat in the middle of the list looking exactly like a local business.

WHAT IT DOES NOT DO
It never changes Disposition. Everything here is a prompt for a human decision, not a
verdict, because every rule below has real false positives:

  * "Tax Service" catches Jackson Hewitt (a national franchise) AND Bosch Tax Services
    (a local Kentucky firm). The keyword cannot tell them apart.
  * A repeated name usually means a local business with two locations (Cora's Cakery has
    three) and only sometimes means a franchise.
  * A franchise LOCATION can be genuinely minority-owned. A Black-owned Liberty Tax
    franchisee is a real minority-owned small business by any reasonable reading. Whether
    that belongs in a consumer directory of underrepresented businesses is a values call
    for the operator, not something a keyword list should decide silently. This is exactly
    why these names are flagged here rather than added to prepare.py's CHAIN_BLOCKLIST,
    which drops rows automatically.

NOTE: prepare.py rebuilds businesses_prepared.csv from OUTPUT_COLUMNS and will discard the
"Review Flag" column. That is fine -- just re-run this script after any prepare.py run.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter

import pandas as pd
from rapidfuzz import fuzz, process

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PIPELINE_DIR)
PREP_FILE = os.path.join(REPO_ROOT, "data", "businesses_prepared.csv")

# Needed for the live-duplicate check. Read-only: this script never writes to Supabase.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass

sys.path.insert(0, PIPELINE_DIR)
from prepare import addr_state  # noqa: E402  (shared resolver -- see prepare.addr_state)

# National brands confirmed present in the July 2026 run. These are franchise or
# multi-state operators, NOT local businesses that happen to share a word. Keep this
# list literal and specific; a loose pattern here would flag half the directory.
NATIONAL_BRANDS = [
    "1st franklin financial", "liberty tax", "jackson hewitt", "beltone",
    "shred nations", "h&r block", "state farm", "allstate", "edward jones",
    "re/max", "remax", "century 21", "keller williams", "anytime fitness",
    "snap fitness", "great clips", "supercuts", "servpro", "matco tools",
    "cruise planners", "expedia cruises",
]


def norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).strip()


def fetch_live_names():
    """Live business names, or None if the directory cannot be read.

    prepare.py's skip-known already drops EXACT matches. This catches the near
    misses it cannot, which are the ones that waste review time: the live row is
    "Blak Koffee" and the scrape found "Blak Coffee" -- one letter, 91% similar,
    and the scraped website was a do502.com listicle rather than blakkoffee.com,
    so neither the name key nor the site key matched.

    These are FLAGGED, never dropped, and the reason is in the data: at a cutoff
    low enough to catch Blak (91), you also catch "Hancock County Department of
    Veterans" vs "Henderson County Department of Veterans" at 89 -- different
    counties, genuinely different organizations. No threshold separates them, so
    a human has to look.
    """
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        return None
    names, offset = [], 0
    try:
        while True:
            req = urllib.request.Request(
                f"{url}/rest/v1/businesses?select=business_name&order=id.asc"
                f"&limit=1000&offset={offset}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"})
            page = json.loads(urllib.request.urlopen(req, timeout=30).read())
            names += [b["business_name"] for b in page if b.get("business_name")]
            if len(page) < 1000:
                break
            offset += 1000
    except Exception as e:
        print(f"  [live-duplicate check off: {e}]")
        return None
    return names


def flag_row(row, name_counts, live_keys=None, live_choices=None):
    """Return a list of reasons this row deserves a second look. Empty = looks fine."""
    flags = []
    name = str(row.get("Business Name") or "")
    addr = str(row.get("Address") or "").strip()

    # Near-duplicate of something already live. 88 is deliberately below the 93 that
    # prepare.py's own analysis used, because Blak Coffee/Koffee sits at 91 -- the
    # cost of a false flag is a glance, the cost of a miss is a duplicate published.
    if live_choices:
        m = process.extractOne(norm(name), live_choices,
                               scorer=fuzz.token_sort_ratio, score_cutoff=88)
        if m and norm(name) != m[0]:
            flags.append(f"ALREADY LIVE? ~ '{live_keys[m[0]]}' ({m[1]:.0f}%)")

    hit = next((b for b in NATIONAL_BRANDS if b in norm(name)), None)
    if hit:
        flags.append(f"national brand? ({hit})")

    n = name_counts.get(norm(name), 0)
    if n > 1:
        flags.append(f"{n} locations - chain or multi-site?")

    if not addr:
        flags.append("no address")
    else:
        state = addr_state(addr)
        if state == "other-state":
            flags.append("OUT OF STATE")
        elif state == "unclear":
            # Survived prepare.py's filter but still has no Kentucky signal. The July run
            # left 3 of these: "Los Angeles", "Vancouver, B.C.", "Washington, D.C." --
            # unresolvable from the address text, so a human has to look.
            flags.append("no Kentucky signal in address")

    if not str(row.get("Website") or "").strip():
        flags.append("no website")

    return flags


def main(report_only=False):
    if not os.path.exists(PREP_FILE):
        sys.exit(f"Not found: {PREP_FILE}. Run prepare.py first.")

    df = pd.read_csv(PREP_FILE, encoding="utf-8-sig").fillna("")
    good = df[df["Disposition"] == "Good to go"]
    # Count over "Good to go" only: a name repeated across Dropped rows says nothing about
    # whether the surviving row is a chain.
    name_counts = Counter(norm(n) for n in good["Business Name"])

    live = fetch_live_names()
    live_keys = {norm(n): n for n in live} if live else None
    live_choices = list(live_keys.keys()) if live_keys else None
    if live_choices:
        print(f"Live directory loaded: {len(live_choices)} names for duplicate check.")

    # "Needs review" rows are flagged too. That is where Blak Coffee sat -- an
    # already-live business in the manual pile, which is exactly the review time
    # this script exists to save.
    flags = []
    for _, row in df.iterrows():
        flags.append("; ".join(flag_row(row, name_counts, live_keys, live_choices))
                     if row["Disposition"] in ("Good to go", "Needs review") else "")
    df["Review Flag"] = flags

    flagged = df[df["Review Flag"] != ""]
    print(f"Good to go rows : {len(good)}")
    print(f"Flagged for review: {len(flagged)}  ({100*len(flagged)/max(len(good),1):.0f}%)")
    print()

    tally = Counter()
    for f in flagged["Review Flag"]:
        for part in f.split("; "):
            tally[re.sub(r"\(.*?\)", "()", re.sub(r"^\d+ locations", "N locations", part))] += 1
    print("By flag:")
    for k, v in tally.most_common():
        print(f"  {v:>4}  {k}")

    # "no Kentucky signal" belongs here: those rows survived prepare.py's out-of-state
    # filter only because their address could not be resolved at all, not because they
    # looked like Kentucky. They are the likeliest bad publishes in the whole set.
    priority = flagged[flagged["Review Flag"].str.contains(
        "national brand|OUT OF STATE|no Kentucky signal|ALREADY LIVE", na=False)]
    if len(priority):
        print(f"\nHighest priority ({len(priority)}):")
        for _, r in priority.iterrows():
            print(f"  {str(r['Business Name'])[:38]:40} | {str(r['Address'])[:26]:28} | {r['Review Flag']}")

    if report_only:
        print("\n--report: nothing written.")
        return

    df.to_csv(PREP_FILE, index=False, encoding="utf-8-sig")
    print(f"\nWrote 'Review Flag' column -> {os.path.basename(PREP_FILE)}")
    print("Sort or filter on it for your review pass. Disposition was not changed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Flag Good-to-go rows that deserve a second look.")
    ap.add_argument("--report", action="store_true", help="Print findings without writing.")
    main(report_only=ap.parse_args().report)
