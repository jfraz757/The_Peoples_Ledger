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
SOURCES_FILE = os.path.join(REPO_ROOT, "data", "businesses_scraped_sources.csv")

# Flags serious enough that the row must not upload without a human looking at it.
# These MOVE the row to "Needs review"; everything else is advisory only.
#
# July 2026: this script originally never touched Disposition, on the reasoning that
# flags have false positives ("Hancock County Department of Veterans" matches
# "Henderson County..." at 89% and is a different organisation). That was wrong.
# "Needs review" is not a rejection, it means a human should look -- exactly what a
# high-priority flag means. Leaving them as "Good to go" put the warning in a column
# the reviewer was not filtering on, so 18 rows including a known live duplicate
# passed a careful review pass untouched and would have uploaded.
PROMOTE_TO_REVIEW = ("national brand", "OUT OF STATE", "no Kentucky signal",
                     "ALREADY LIVE", "UNVERIFIED OWNERSHIP")

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


# "Minority-Owned (general)" is not a category. It is what the extractor emits when it
# found ownership language somewhere on a page but could not attribute it to a specific
# group -- which is exactly what happens when the page is ABOUT something else and the
# business is merely mentioned on it.
#
# Measured on the July 2026 run: all 196 such rows came from the organic lane, none from
# google_maps (which is gated on Google's own self-identified ownership attribute). They
# cluster on pages with no ownership dimension at all:
#
#     93  somersetpulaskichamber.com/ribbon-cuttings/   every business that opened
#     19  itsbuzzing.com/buy-local/.../farmers-markets  farmers markets
#     14  thecovky.gov/experiencing-covington/          a tourism page
#     11  owensborotimes.com/.../chamber-celebration    where R.W. Baird came from
#
# R.W. Baird is a ~$400B national investment firm. 610 Magnolia is Edward Lee's
# restaurant -- he is Korean-American -- tagged Black-Owned off a Lee Initiative article
# about a charity that FUNDS Black-owned restaurants.
#
# This is also why the live directory holds 283 unfilterable "Minority-Owned (general)"
# rows. That was recorded as a taxonomy gap; it is really an evidence gap. Those rows may
# never have had ownership evidence.
#
# An earlier attempt at this rule matched URL text for ownership words. It flagged 172 of
# 333 organic rows (52%) and was wrong often: vobzone.com is a Veteran Owned Business
# directory, aviatraaccelerators.org is a women's business accelerator, and neither URL
# says so. The tag value itself is the reliable signal, not the URL.
GENERIC_TAG = "minority-owned (general)"


def flag_row(row, name_counts, live_keys=None, live_choices=None, found_via=""):
    """Return a list of reasons this row deserves a second look. Empty = looks fine."""
    flags = []
    name = str(row.get("Business Name") or "")
    addr = str(row.get("Address") or "").strip()

    # Ownership tag provenance. Only meaningful for the organic lane: google_maps rows
    # are gated on Google's own self-identified ownership attribute, so their tag does
    # not come from page text at all.
    src = str(row.get("Source") or "").strip()
    mtype = str(row.get("Minority Type") or "").strip().lower()
    if GENERIC_TAG in mtype and "organic" in src:
        page = re.sub(r"^https?://(www\.)?", "", found_via)[:64] if found_via else "unknown page"
        flags.append(f"UNVERIFIED OWNERSHIP: tagged only 'general' from page text -> {page}")

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

    # "Found Via" is the page the scraper actually read. For an organic row it IS the
    # evidence behind the ownership tag, so it belongs in the review file rather than
    # buried in a separate audit log nobody opens.
    via = {}
    if os.path.exists(SOURCES_FILE):
        s = pd.read_csv(SOURCES_FILE, encoding="utf-8-sig").fillna("")
        if "Found Via" in s.columns:
            for _, r in s.iterrows():
                nm = str(r.get("Business Name") or "").strip()
                if nm and str(r.get("Found Via") or "").strip():
                    via.setdefault(nm, str(r["Found Via"]).strip())
        print(f"Source audit loaded: {len(via)} 'Found Via' URLs.")
    df["Found Via"] = [via.get(str(n).strip(), "") for n in df["Business Name"]]

    # "Needs review" rows are flagged too. That is where Blak Coffee sat -- an
    # already-live business in the manual pile, which is exactly the review time
    # this script exists to save.
    flags = []
    for _, row in df.iterrows():
        flags.append("; ".join(flag_row(row, name_counts, live_keys, live_choices,
                                        row.get("Found Via", "")))
                     if row["Disposition"] in ("Good to go", "Needs review") else "")
    df["Review Flag"] = flags

    # Move serious flags into the workflow the reviewer actually uses. A "Good to go"
    # row with a warning buried in a column is a row that ships unreviewed.
    promoted_mask = (df["Disposition"] == "Good to go") & \
                    df["Review Flag"].str.contains("|".join(PROMOTE_TO_REVIEW), na=False)
    n_promoted = int(promoted_mask.sum())
    if n_promoted and not report_only:
        df.loc[promoted_mask, "Disposition"] = "Needs review"
        df.loc[promoted_mask, "Reason"] = "flagged: " + df.loc[promoted_mask, "Review Flag"].str[:70]

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
        print(f"\nWould move {int(promoted_mask.sum())} row(s) to 'Needs review'.")
        print("--report: nothing written.")
        return

    df.to_csv(PREP_FILE, index=False, encoding="utf-8-sig")
    print(f"\nWrote 'Review Flag' + 'Found Via' -> {os.path.basename(PREP_FILE)}")
    if n_promoted:
        print(f"Moved {n_promoted} flagged row(s) from 'Good to go' to 'Needs review' so they "
              f"cannot upload unexamined.")
    print("Filter Disposition = 'Needs review'. Everything needing a decision is there.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Flag Good-to-go rows that deserve a second look.")
    ap.add_argument("--report", action="store_true", help="Print findings without writing.")
    main(report_only=ap.parse_args().report)
