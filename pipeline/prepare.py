"""
Kentucky Minority Business - Prepare
====================================
ONE step that replaces both triage_ky_v2.py and clean_ky_businesses.py.

It reads the raw scraper output and produces a SINGLE file,
data/businesses_prepared.csv, with a Disposition column you can filter:

  Good to go    Kentucky business, not a chain, ready to upload. These rows are
                also deduplicated and merged (the old clean step).
  Needs review  Organic or social find with no address. Verify it is Kentucky
                before you promote it. Edit its Disposition to "Good to go" to
                keep it, or "Dropped" to discard it.
  Dropped       A chain/franchise or an out-of-state address. See the Reason
                column. Nothing is deleted; you can override any call.

Workflow:
  1. Run this.
  2. Open businesses_prepared.csv (Supabase staging table, Excel, whatever you
     like) and work the "Needs review" rows: flip the keepers to "Good to go".
  3. Run upload_to_supabase.py. It uploads ONLY the "Good to go" rows.

Usage:
    python prepare.py
"""

import os
import re
import sys
import pandas as pd

# One address resolver, shared with purge_out_of_state.py, so the pre-upload filter
# and the post-upload purge can never disagree about what counts as out-of-state.
# Both live in pipeline/; add that directory to sys.path so this works whether the
# script is run from the repo root or from pipeline/.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from purge_out_of_state import detect_state  # noqa: E402

# Fuzzy name matching for the pre-review dedup. rapidfuzz is installed globally
# (dedupe_live.py uses it); fall back to difflib if it is ever missing.
try:
    from rapidfuzz import fuzz
    def _ratio(a, b):
        return fuzz.token_sort_ratio(a, b)
except Exception:
    import difflib
    def _ratio(a, b):
        sa, sb = " ".join(sorted(a.split())), " ".join(sorted(b.split()))
        return 100 * difflib.SequenceMatcher(None, sa, sb).ratio()

# Portable paths: data/ sits next to the pipeline/ folder this script lives in.
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
DATA_DIR     = os.path.join(REPO_ROOT, "data")

INPUT_FILE   = os.path.join(DATA_DIR, "businesses_scraped.csv")
SOURCES_FILE = os.path.join(DATA_DIR, "businesses_scraped_sources.csv")
OUTPUT_FILE  = os.path.join(DATA_DIR, "businesses_prepared.csv")

# Lane 1b: the category-seeded discovery output (discover_categories.py). Read
# alongside the main scrape when present, so its verified passes flow through the
# same disposition + dedupe + upload path. Absent on installs that never run it.
CATEGORY_INPUT_FILE   = os.path.join(DATA_DIR, "businesses_scraped_categories.csv")
CATEGORY_SOURCES_FILE = os.path.join(DATA_DIR, "businesses_scraped_categories_sources.csv")

# Skip-known: drop rows for businesses already in the live Supabase directory, so
# re-running prepare.py stops resurfacing businesses you already uploaded (e.g.
# Good Brothers Pharmacy coming back with a blank address and a Facebook URL when
# it is already live with the correct data). Matched on normalized business name
# or normalized website. Needs SUPABASE_URL + SUPABASE_KEY in .env (the same
# publishable read key index.html uses). If absent or unreachable, prepare still
# runs and just skips this step.
SKIP_KNOWN = True

# Denylist: businesses you have deliberately rejected during review. Unlike
# skip-known (which suppresses businesses already LIVE), this remembers the ones
# you DROPPED but never uploaded, so they stop reappearing every run. prepare.py
# reads it on every run; `--commit-drops` appends your current Dropped rows to it.
DENYLIST_FILE = os.path.join(DATA_DIR, "denylist.csv")

# Reasons that are auto-assigned by rules every run (so they need not be recorded
# in the denylist; only genuine manual rejections are captured by --commit-drops).
AUTO_DROP_REASONS = {"out-of-state address", "chain/franchise", "already in directory",
                     # "denylisted" belongs here too: those rows were dropped BY the
                     # denylist, so re-recording them as fresh human decisions is
                     # circular. It was harmless (the key dedup stopped real growth)
                     # but it reported "Recorded 31 manual drop(s)" when 2 were new,
                     # and it let website variants of an already-rejected business
                     # accumulate as extra denylist rows. Added July 2026.
                     "denylisted"}

OUTPUT_COLUMNS = [
    "Business Name", "Address", "Phone", "Services / Products",
    "Website", "Minority Type", "Status", "Kentucky Based",
    "Disposition", "Reason", "Source",
]

CHAIN_BLOCKLIST = [
    "walmart", "starbucks", "mcdonald", "qdoba", "buc-ee", "hobby lobby",
    "lowe's", "lowes", "home depot", "jcpenney", "jc penney", "kay jewelers",
    "belk", "bealls", "t.j. maxx", "tj maxx", "planet fitness", "dollar general",
    "office depot", "rural king", "kroger", "target", "fedex", "u-haul", "uhaul",
    "enterprise rent", "great american cookies", "cheddar's", "mellow mushroom",
    "duluth trading", "plato's closet", "clothes mentor", "goodwill",
    "factory connection", "airgas", "ace hardware", "holiday inn", "baymont",
    "wyndham", "by ihg", "neil huffman", "harley-davidson", "mclane",
    "international paper", "jabil", "metalsa", "kruger packaging", "servpro",
    "fastsigns", "signarama", "golden corral", "bath & body works",
    "dwain taylor", "david taylor chrysler", "glockner", "identogo",
    "earthwise pet", "agave & rye", "uptown cheapskate", "keller williams",
    "el toro ip targeting",
]

US_STATES = ("al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il",
             "in","ia","ks","la","me","md","ma","mi","mn","ms","mo","mt","ne",
             "nv","nh","nj","nm","ny","nc","nd","oh","ok","or","pa","ri","sc",
             "sd","tn","tx","ut","vt","va","wa","wv","wi","wy","dc")


# ── classification helpers ────────────────────────────────────────────────────
def addr_state(addr):
    """Classify an address as KY / other-state / unclear / blank.

    July 2026: this now delegates to purge_out_of_state.detect_state() instead of
    using its own rules. The old version only recognised another state from a
    two-letter code immediately followed by a ZIP (", OH 45241"), so every one of
    these fell through to "unclear" and was NOT dropped:

        Los Angeles                      (no state token at all)
        Houston, Texas                   (state spelled out)
        Walnut Hills, Cincinnati, Ohio   (spelled out, mid-string)
        Vancouver, B.C.                  (not a US state)

    Eleven such rows reached "Good to go" in the July 2026 run and would have been
    uploaded, then removed later by purge_out_of_state.py -- after publishing.

    detect_state() already resolves abbreviation-before-ZIP, trailing two-letter
    token, full state name, and a ZIP-prefix fallback, and it deliberately treats a
    NON-KY match found only by a bare spelled-out name as low confidence, so a
    street called "456 Texas St, Louisville" is not mistaken for Texas. Those route
    to review rather than being dropped.

    Sharing one resolver is the durable fix the Change Log calls for ("ideally via a
    shared address-resolver module imported by both prepare.py and
    purge_out_of_state.py so the two never drift apart"). Importing is safe: that
    module's only executable statement is guarded by `if __name__ == "__main__"`.
    """
    a = str(addr or "").strip()
    if not a:
        return "blank"

    abbr, method, confident = detect_state(a)
    if abbr == "KY":
        return "KY"
    if abbr and abbr != "KY" and confident:
        return "other-state"

    # detect_state deliberately reports a NON-KY full-name match as low confidence,
    # because it also backs purge_out_of_state.py, which DELETES live rows -- there,
    # "456 Texas St, Louisville" being read as Texas would destroy a real record.
    #
    # prepare.py runs BEFORE upload, so the cost of being wrong is asymmetric: a
    # wrongly dropped candidate never gets published and is recoverable from the
    # prepared CSV, whereas a wrongly kept one goes live and needs the purge later.
    # That justifies one extra rule here that purge should NOT adopt:
    #
    #   a state name in the FINAL comma segment is a location, not a street name.
    #     "Houston, Texas"                -> tail "texas"      -> Texas, drop
    #     "Walnut Hills, Cincinnati, Ohio"-> tail "ohio"       -> Ohio, drop
    #     "456 Texas St, Louisville"      -> tail "louisville" -> not matched, keep
    #
    # Anything still unresolved ("Los Angeles" with no state token, "Vancouver, B.C."
    # which is not a US state) stays unclear and goes to human review. Never drop on
    # uncertainty.
    if abbr and abbr != "KY" and method == "full_name":
        from purge_out_of_state import FULL_NAME_TO_ABBR
        tail = " " + a.split(",")[-1].strip().lower() + " "
        for name, ab in FULL_NAME_TO_ABBR.items():
            if ab == abbr and re.search(r"\b" + re.escape(name) + r"\b", tail):
                return "other-state"

    return "unclear"


def is_chain(name):
    n = str(name or "").lower()
    return any(c in n for c in CHAIN_BLOCKLIST)


# ── skip-known helpers (drop businesses already in the live directory) ─────────
def _norm_name(name):
    n = str(name or "").lower().strip()
    # "&" and "and" are the same word to a reader, but stripping punctuation turns
    # "A & B" into "a  b" while "A and B" stays "a and b" -- two different keys for
    # one business. Normalize before the punctuation strip, not after.
    # (July 2026: this is why "SKS Accounting & Consulting Firm, Inc." did not match
    # a scraped "SKS Accounting and Consulting Firm".)
    n = n.replace("&", " and ")
    # Hyphens and slashes SEPARATE words; every other mark is noise inside one.
    # Order matters: deleting a hyphen glues words together ("Late-Nite" -> "latenite",
    # which then fails to match a live "Late Nite"), while turning a period into a
    # space would break "l.l.c." -> "llc" that the suffix strip in _dedup_name needs.
    n = re.sub(r"[-/]", " ", n)
    n = re.sub(r"[^\w\s]", "", n)      # drop remaining punctuation
    n = re.sub(r"\s+", " ", n)         # collapse whitespace
    return n


def _norm_site(url):
    u = str(url or "").lower().strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def _load_env():
    """Populate os.environ from REPO_ROOT/.env without requiring python-dotenv."""
    path = os.path.join(REPO_ROOT, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception as e:
        print(f"  [could not read .env: {e}]")


def fetch_live_identity():
    """Return (live_names, live_sites) as sets of normalized keys for every row in
    the live businesses table, or (None, None) if it cannot be fetched."""
    _load_env()
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        print("  [skip-known off: SUPABASE_URL/SUPABASE_KEY not in .env]")
        return None, None
    try:
        import requests
    except Exception:
        print("  [skip-known off: requests not installed]")
        return None, None

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    names, sites = set(), set()
    offset, page = 0, 1000
    try:
        while True:
            r = requests.get(
                f"{url}/rest/v1/businesses",
                headers=headers,
                params={"select": "business_name,website", "limit": page, "offset": offset},
                timeout=60,
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for row in batch:
                # _dedup_name, not _norm_name: the scraped side is compared with the
                # same key, so "Braxton Brewing Co." matches a live "Braxton Brewing
                # Company" and does not upload as a duplicate.
                nm = _dedup_name(row.get("business_name"))
                st = _norm_site(row.get("website"))
                if nm:
                    names.add(nm)
                if st:
                    sites.add(st)
            if len(batch) < page:
                break
            offset += page
    except Exception as e:
        print(f"  [skip-known off: could not read live directory: {e}]")
        return None, None
    print(f"  Loaded {len(names)} live business names for skip-known.")
    return names, sites


def load_denylist():
    """Return (deny_names, deny_sites) as sets of normalized keys, or empty sets."""
    if not os.path.exists(DENYLIST_FILE):
        return set(), set()
    try:
        d = pd.read_csv(DENYLIST_FILE, encoding="utf-8-sig").fillna("")
    except Exception as e:
        print(f"  [could not read denylist: {e}]")
        return set(), set()
    names = {_norm_name(n) for n in d.get("Business Name", []) if _norm_name(n)}
    sites = {_norm_site(w) for w in d.get("Website", []) if _norm_site(w)}
    return names, sites


def commit_drops(explicit=True):
    """Record your manual rejections so they never come back. Reads the CURRENT
    businesses_prepared.csv, takes every Dropped row that is NOT an auto-drop
    (chain, out-of-state, already-in-directory), and appends its identity to
    denylist.csv.

    July 2026: main() now calls this automatically before it regenerates the file,
    so manual drops can no longer be lost. Previously this only ran when you
    remembered `--commit-drops`, and it had to run BEFORE the next prepare.py --
    a normal run overwrites businesses_prepared.csv, taking every unrecorded
    rejection with it. One forgotten flag silently threw away a whole review pass
    and the same businesses came back on the next run.

    `explicit=False` is the automatic call: it stays quiet when there is nothing to
    record, so a routine run does not print noise about work that did not happen.
    """
    if not os.path.exists(OUTPUT_FILE):
        if explicit:
            print(f"No {os.path.basename(OUTPUT_FILE)} yet. Run prepare.py first, then drop rows.")
        return
    df = pd.read_csv(OUTPUT_FILE, encoding="utf-8-sig").fillna("")
    dropped = df[df["Disposition"] == "Dropped"].copy()
    manual = dropped[~dropped["Reason"].isin(AUTO_DROP_REASONS)]
    if not len(manual):
        if explicit:
            print("No manual drops to record (only auto-drops present). Nothing added.")
        return

    new = manual[["Business Name", "Website"]].copy()
    if os.path.exists(DENYLIST_FILE):
        existing = pd.read_csv(DENYLIST_FILE, encoding="utf-8-sig").fillna("")
        combined = pd.concat([existing[["Business Name", "Website"]], new], ignore_index=True)
    else:
        combined = new
    before = 0 if not os.path.exists(DENYLIST_FILE) else len(pd.read_csv(DENYLIST_FILE, encoding="utf-8-sig"))
    combined["_k"] = combined["Business Name"].apply(_norm_name) + "|" + combined["Website"].apply(_norm_site)
    combined = combined.drop_duplicates("_k").drop(columns="_k")
    combined.to_csv(DENYLIST_FILE, index=False, encoding="utf-8-sig")
    added = len(combined) - before
    print(f"Recorded {len(manual)} manual drop(s); denylist now holds {len(combined)} "
          f"business(es) ({added} new).")
    print(f"  -> {DENYLIST_FILE}")
    print("  These will be auto-dropped on every future prepare.py run.")


def classify(row):
    """Return (disposition, reason)."""
    if is_chain(row["Business Name"]):
        return "Dropped", "chain/franchise"
    if row["_state"] == "other-state":
        return "Dropped", "out-of-state address"
    if row["_state"] == "blank" and row["Source"] in ("organic", "social", "unknown",
                                                       "category_maps", "category_web",
                                                       "category_manual"):
        return "Needs review", "no address, verify Kentucky"
    return "Good to go", ""


# ── merge helpers (from the old clean step) ───────────────────────────────────
def most_complete(vals):
    vals = [v for v in vals if pd.notna(v) and str(v).strip() not in ("", "nan", "N/A")]
    return max(vals, key=len) if vals else ""


def best_minority_type(types):
    types = [t for t in types if pd.notna(t) and str(t).strip()]
    if not types:
        return ""
    specific = [t for t in types if "Minority-Owned (general)" not in t]
    pool = specific if specific else types
    return max(pool, key=len)


def best_ky(vals):
    vals = [v for v in vals if pd.notna(v) and str(v).strip()]
    return "Yes" if "Yes" in vals else (vals[0] if vals else "")


def _street_number(addr):
    m = re.match(r"\s*(\d+)", str(addr or ""))
    return m.group(1) if m else ""


def _dedup_name(name):
    """Normalized name with a trailing corporate suffix removed, so 'Joe's BBQ'
    and 'Joe's BBQ LLC' share a key. _norm_name has already lowercased and
    stripped punctuation (l.l.c. -> llc).

    July 2026: added company|pllc|plc|lp to the suffix list, and this key is now
    ALSO what skip-known compares against the live directory. It used to compare
    with _norm_name (no suffix stripping), so 'Braxton Brewing Co.' did not match
    a live 'Braxton Brewing Company' and would upload as a duplicate. Measured on
    the July run: 4 of 916 'Good to go' rows were live duplicates that skip-known
    missed for exactly this reason. `company` was the specific miss -- the old
    pattern had `co` but a trailing 'company' does not match `\\bco\\s*$`.
    """
    n = _norm_name(name)
    n = re.sub(r"\b(llc|inc|incorporated|corp|corporation|company|co|ltd|llp|pllc|plc|lp)\s*$",
               "", n).strip()
    return n


def _norm_phone(p):
    d = re.sub(r"\D", "", str(p or ""))
    if len(d) >= 11 and d[0] == "1":
        d = d[1:]
    return d[:10] if len(d) >= 10 else ""


def _addr_compatible(a, b):
    """Could these two addresses be the same place? Same rule dedupe_live uses:
    two different street numbers whose text is not a close fuzzy match means
    different locations. A blank on either side is compatible (unknown)."""
    na, nb = str(a or "").lower().strip(), str(b or "").lower().strip()
    if not na or not nb:
        return True
    sa, sb = _street_number(na), _street_number(nb)
    if sa and sb and sa != sb:
        return _ratio(na, nb) >= 88
    return True


def _best_website(vals):
    vals = [str(v).strip() for v in vals if str(v).strip()]
    if not vals:
        return ""
    bad = ("facebook.com", "instagram.com", "buyblack.org")
    real = [v for v in vals if not any(b in v.lower() for b in bad)]
    return (real or vals)[0]


class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def dedupe_rows(sub):
    """Collapse near-duplicate rows (Good to go + Needs review) BEFORE review.

    Pass 1: same normalized name. Merge the group only if it is a single
    location (if two rows carry different, non-close street numbers, the name is
    a multi-location collision, so leave the group untouched).
    Pass 2: fuzzy-close names (>=88) that ALSO share a phone or website and have
    compatible addresses, to catch 'Joe's BBQ' vs 'Joe's BBQ LLC'.

    A merged cluster keeps the most complete fields and is 'Good to go' if any
    member was."""
    from collections import defaultdict
    sub = sub.reset_index(drop=True)
    n = len(sub)
    if n <= 1:
        return sub[OUTPUT_COLUMNS] if n else sub

    names  = [_dedup_name(x) for x in sub["Business Name"]]
    addrs  = list(sub["Address"])
    phones = [_norm_phone(x) for x in sub["Phone"]]
    sites  = [_norm_site(x) for x in sub["Website"]]
    dsu = _DSU(n)

    # Pass 1: same name, single location.
    by_name = defaultdict(list)
    for i, nm in enumerate(names):
        if nm:
            by_name[nm].append(i)
    for idxs in by_name.values():
        if len(idxs) < 2:
            continue
        addressed = [i for i in idxs if str(addrs[i]).strip()]
        conflict = False
        for a in range(len(addressed)):
            for b in range(a + 1, len(addressed)):
                if not _addr_compatible(addrs[addressed[a]], addrs[addressed[b]]):
                    conflict = True
                    break
            if conflict:
                break
        if conflict:
            continue   # multi-location same-name; do not merge
        for k in range(1, len(idxs)):
            dsu.union(idxs[0], idxs[k])

    # Pass 2: shared phone/website + fuzzy name + compatible address.
    by_sig = defaultdict(list)
    for i in range(n):
        if phones[i]:
            by_sig[("p", phones[i])].append(i)
        if sites[i]:
            by_sig[("w", sites[i])].append(i)
    for idxs in by_sig.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                if dsu.find(i) == dsu.find(j):
                    continue
                if _ratio(names[i], names[j]) >= 88 and _addr_compatible(addrs[i], addrs[j]):
                    dsu.union(i, j)

    clusters = defaultdict(list)
    for i in range(n):
        clusters[dsu.find(i)].append(i)

    rows = []
    for members in clusters.values():
        g = sub.iloc[members]
        is_good = (g["Disposition"] == "Good to go").any()
        rows.append({
            "Business Name":       max((str(x) for x in g["Business Name"]), key=len),
            "Address":             most_complete(g["Address"].tolist()),
            "Phone":               next((p for p in g["Phone"] if str(p).strip()), ""),
            "Services / Products": most_complete(g["Services / Products"].tolist()),
            "Website":             _best_website(g["Website"].tolist()),
            "Minority Type":       best_minority_type(g["Minority Type"].tolist()),
            "Status":              "",
            "Kentucky Based":      best_ky(g["Kentucky Based"].tolist()),
            "Disposition":         "Good to go" if is_good else "Needs review",
            "Reason":              "" if is_good else "no address, verify Kentucky",
            "Source":              ", ".join(sorted(set(s for s in g["Source"] if str(s).strip()))),
        })
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def main(remember_drops=True):
    # Harvest manual rejections from the PREVIOUS businesses_prepared.csv before this
    # run overwrites it. Without this, a review pass whose drops were never committed
    # is silently discarded and those businesses reappear in the next review pile.
    # Auto-drops (chain / out-of-state / already-live) are excluded -- the rules
    # re-derive those every run, so only genuine human rejections are recorded.
    if remember_drops:
        commit_drops(explicit=False)

    print(f"Loading: {INPUT_FILE}")
    df = pd.read_csv(INPUT_FILE, encoding="utf-8-sig").fillna("")
    print(f"Rows in: {len(df)}")

    # Lane 1b: fold in the category-seeded discovery output when it exists.
    if os.path.exists(CATEGORY_INPUT_FILE):
        cat = pd.read_csv(CATEGORY_INPUT_FILE, encoding="utf-8-sig").fillna("")
        print(f"Category lane rows: {len(cat)} (from {os.path.basename(CATEGORY_INPUT_FILE)})")
        df = pd.concat([df, cat], ignore_index=True)

    # Build the source map from both audit files (main scrape + category lane).
    source_frames = []
    for sf in (SOURCES_FILE, CATEGORY_SOURCES_FILE):
        if os.path.exists(sf):
            s = pd.read_csv(sf, encoding="utf-8-sig").fillna("")
            if {"Business Name", "Website", "Source"}.issubset(s.columns):
                source_frames.append(s[["Business Name", "Website", "Source"]])

    if source_frames:
        src = pd.concat(source_frames, ignore_index=True)
        src["Source"] = src["Source"].replace("", "google_maps")
        src = src.drop_duplicates(["Business Name", "Website"])
        df = df.merge(src, on=["Business Name", "Website"], how="left")
        df["Source"] = df["Source"].fillna("google_maps")
    else:
        df["Source"] = "unknown"

    df["_state"] = df["Address"].apply(addr_state)
    df[["Disposition", "Reason"]] = df.apply(lambda r: pd.Series(classify(r)), axis=1)
    # Pre-fill Kentucky Based from the address; Status is left for review.
    df["Kentucky Based"] = df["_state"].apply(lambda s: "Yes" if s == "KY" else "")
    df["Status"] = ""

    # Skip-known: drop anything already in the live directory so re-runs stop
    # resurfacing businesses you already uploaded. Name OR website match.
    if SKIP_KNOWN:
        live_names, live_sites = fetch_live_identity()
        if live_names is not None:
            def _already_live(r):
                # Must use the SAME key builder as fetch_live_identity() above.
                name_hit = _dedup_name(r["Business Name"]) in live_names
                site = _norm_site(r["Website"])
                site_hit = bool(site) and site in live_sites
                return bool(name_hit or site_hit)
            mask = df.apply(_already_live, axis=1).astype(bool)
            df.loc[mask, "Disposition"] = "Dropped"
            df.loc[mask, "Reason"] = "already in directory"
            print(f"  Skip-known dropped {int(mask.sum())} row(s) already in the live directory.")

    # Denylist: suppress businesses you deliberately rejected in past runs.
    deny_names, deny_sites = load_denylist()
    if deny_names or deny_sites:
        def _denied(r):
            site = _norm_site(r["Website"])
            return bool(_norm_name(r["Business Name"]) in deny_names
                        or (site and site in deny_sites))
        dmask = df.apply(_denied, axis=1).astype(bool)
        df.loc[dmask, "Disposition"] = "Dropped"
        df.loc[dmask, "Reason"] = "denylisted"
        print(f"  Denylist dropped {int(dmask.sum())} previously-rejected row(s).")

    dropped_rows = df[df["Disposition"] == "Dropped"].copy()
    keep_rows    = df[df["Disposition"] != "Dropped"].copy()

    # Collapse near-duplicates (exact-name twins, cross-disposition twins, and
    # fuzzy names sharing a phone/website) BEFORE you review them.
    before_n = len(keep_rows)
    if before_n:
        merged = dedupe_rows(keep_rows)
    else:
        merged = pd.DataFrame(columns=OUTPUT_COLUMNS)
    collapsed = before_n - len(merged)
    if collapsed > 0:
        print(f"  Deduped {collapsed} near-duplicate row(s) before review.")

    other = dropped_rows[OUTPUT_COLUMNS]
    merged = merged[OUTPUT_COLUMNS]
    out = pd.concat([merged, other], ignore_index=True)
    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print(f"\nWrote {len(out)} rows -> {os.path.basename(OUTPUT_FILE)}")
    print("\nBy disposition:")
    print(out["Disposition"].value_counts().to_string())
    dropped = out[out["Disposition"] == "Dropped"]
    if len(dropped):
        print("\nDropped by reason:")
        print(dropped["Reason"].value_counts().to_string())
    print("\nNext: review the 'Needs review' rows, flip keepers to 'Good to go', "
          "then run upload_to_supabase.py.")
    print("Rows you flip to 'Dropped' are remembered automatically the next time "
          "prepare.py runs -- no --commit-drops needed.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Prepare scraped businesses for upload.")
    ap.add_argument("--commit-drops", action="store_true",
                    help="Record the manual drops in the current businesses_prepared.csv to "
                         "denylist.csv, without regenerating anything. A normal run now does "
                         "this automatically, so this is only needed to record drops without "
                         "rebuilding the file.")
    ap.add_argument("--no-remember-drops", action="store_true",
                    help="Do NOT auto-record manual drops from the previous "
                         "businesses_prepared.csv before regenerating. Use this only when you "
                         "want to discard a review pass, e.g. you dropped rows by mistake.")
    args = ap.parse_args()
    if args.commit_drops:
        commit_drops()
    else:
        main(remember_drops=not args.no_remember_drops)
