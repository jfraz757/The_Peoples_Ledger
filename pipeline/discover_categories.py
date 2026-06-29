"""
discover_categories.py  --  The People's Ledger  (Lane 1b)
===========================================================
Category-seeded discovery. Finds the immigrant- and ethnic-owned retail that the
main scraper misses, because those owners rarely set Google's self-identified
ownership attribute, so the attribute-gated main lane (scrape.py Phase 1) drops
them even when Maps surfaces them.

THE RULE THAT KEEPS THIS HONEST: the category term decides what gets SURFACED,
never what gets TAGGED. Searching "carniceria" finds the store; it does not get
to write minority_type. Ownership is only ever stamped from evidence. Everything
else waits for a human. This is why the lane cannot reintroduce the v2
mislabeling problem the attribute gate was built to stop.

Each surfaced business gets a verification tier:

  Tier A  auto-accept   Google's self-identified ownership attribute is present.
                        Same gate as the main lane. Tagged from the attribute.
  Tier B  website check Run the business website through the SAME Phase 4
                        on-page evidence check scrape.py uses. Accepted only if
                        the page actually states ownership. The category term is
                        the reason we looked, not the proof. Tagged from the page
                        (specific type if found, else "Minority-Owned (general)").
  Tier C  manual review Everything else (no attribute, and no website or a
                        website with no ownership evidence). Written to
                        category_review.csv with a SUGGESTED type for you to
                        confirm or reject. NEVER auto-tagged, NEVER uploaded.

Seed terms are split into two buckets:
  name_evident  term strongly implies an ownership group (carniceria -> Latine).
                A suggested type is offered on the Tier C row (you still confirm).
  ambiguous     term implies an ethnic retail niche but NOT a specific ownership
                group (international grocery, halal market). Suggested type is
                left BLANK on purpose. You decide after verifying.

OUTPUTS (data/, all gitignored working files):
  businesses_scraped_categories.csv          Tier A + Tier B passes, 6-col schema.
                                             prepare.py reads this alongside the
                                             main scrape output.
  businesses_scraped_categories_sources.csv  source audit for those passes.
  category_review.csv                        Tier C rows for you to work by hand.
  category_progress.json                     resume state (separate namespace).

WORKFLOW:
  1. python pipeline/discover_categories.py
       Runs the Maps searches, writes the three files above. No DB writes; this
       only produces CSVs. Re-runs are cheap (cache + resume) and skip anything
       already in the live directory.
  2. Open category_review.csv. For each business you want to keep, set Keep? to
     "yes" and put a confirmed ownership label in Suggested Minority Type (the
     ambiguous-bucket rows arrive blank by design).
  3. python pipeline/discover_categories.py --promote
       Appends your kept, typed Tier C rows into businesses_scraped_categories.csv.
  4. python pipeline/prepare.py   (reads both scrape and category outputs)
  5. python pipeline/upload_to_supabase.py   (uploads only "Good to go" rows)

Cost: SEED_TERMS x CATEGORY_CITIES Maps searches. The script prints the
projected count before spending anything. CATEGORY_CITIES defaults to a focused
pilot set; set it to scrape.STATEWIDE_CITIES for full coverage.

Usage:
    python pipeline/discover_categories.py            # run discovery
    python pipeline/discover_categories.py --promote  # promote kept Tier C rows
"""

import os
import csv
import json
import argparse
import unicodedata

import scrape   # reuse Maps, evidence check, cache, skip-known, dedupe helpers

# ---------------------------------------------------------------------------
# Paths (portable, same convention as the rest of the pipeline)
# ---------------------------------------------------------------------------
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
DATA_DIR     = os.path.join(REPO_ROOT, "data")

OUT_PASSES  = os.path.join(DATA_DIR, "businesses_scraped_categories.csv")
OUT_SOURCES = os.path.join(DATA_DIR, "businesses_scraped_categories_sources.csv")
OUT_REVIEW  = os.path.join(DATA_DIR, "category_review.csv")
PROGRESS    = os.path.join(DATA_DIR, "category_progress.json")

REVIEW_COLUMNS = [
    "Business Name", "Address", "Phone", "Website",
    "Suggested Minority Type", "Bucket", "Category Term", "Source", "Keep?",
]

# ---------------------------------------------------------------------------
# Seed terms. (search_term, suggested_type). Suggested type is a HINT for the
# Tier C review row only; it is never auto-applied. Labels match the ownership
# pills in index.html exactly.
# ---------------------------------------------------------------------------
NAME_EVIDENT = [
    ("carniceria",            "Latine-Owned"),
    ("mercado latino",        "Latine-Owned"),
    ("tienda mexicana",       "Latine-Owned"),
    ("supermercado mexicano", "Latine-Owned"),
    ("panaderia mexicana",    "Latine-Owned"),
    ("asian market",          "Asian-Owned"),
    ("asian grocery",         "Asian-Owned"),
    ("oriental market",       "Asian-Owned"),
    ("chinese grocery",       "Asian-Owned"),
    ("korean market",         "Asian-Owned"),
    ("vietnamese market",     "Asian-Owned"),
    ("indian grocery",        "Asian-Owned"),
    ("filipino store",        "Asian-Owned"),
]

# Ambiguous: the niche is clear, the ownership group is NOT. Suggested type stays
# blank so the lane never guesses ownership from a generic term.
AMBIGUOUS = [
    ("international grocery",   ""),
    ("international market",    ""),
    ("halal market",           ""),
    ("halal grocery",          ""),
    ("african market",         ""),
    ("african grocery",        ""),
    ("middle eastern grocery", ""),
    ("ethnic grocery",         ""),
]

SEED_TERMS = (
    [(t, ty, "name_evident") for t, ty in NAME_EVIDENT]
    + [(t, ty, "ambiguous") for t, ty in AMBIGUOUS]
)

# Pilot cities (the metros with the densest immigrant-owned retail). Set this to
# scrape.STATEWIDE_CITIES for a full pass.
CATEGORY_CITIES = [
    "Louisville", "Lexington", "Bowling Green",
    "Owensboro", "Covington", "Florence",
]

GENERAL = "Minority-Owned (general)"


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------
def load_progress() -> dict:
    p = {}
    if os.path.exists(PROGRESS):
        try:
            with open(PROGRESS, "r", encoding="utf-8") as f:
                p = json.load(f)
        except Exception as e:
            print(f"  [Could not read {os.path.basename(PROGRESS)}, starting fresh: {e}]")
            p = {}
    p.setdefault("units_done", [])
    return p


def save_progress(p: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(PROGRESS, "w", encoding="utf-8") as f:
            json.dump(p, f, indent=0)
    except Exception as e:
        print(f"  [progress save error] {e}")


# ---------------------------------------------------------------------------
# Existing-file helpers (so re-runs do not duplicate rows)
# ---------------------------------------------------------------------------
def _read_name_website(path: str) -> list[tuple[str, str]]:
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                out.append((row.get("Business Name", ""), row.get("Website", "")))
    except Exception:
        pass
    return out


def load_existing_passes() -> list[dict]:
    """Existing Tier A/B rows, as scrape-style dicts (6 fields + _source)."""
    if not os.path.exists(OUT_PASSES):
        return []
    rows = []
    try:
        with open(OUT_PASSES, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rows.append({
                    "business_name": r.get("Business Name", ""),
                    "address":       r.get("Address", ""),
                    "phone":         r.get("Phone", ""),
                    "services":      r.get("Services / Products", ""),
                    "website":       r.get("Website", ""),
                    "minority_type": r.get("Minority Type", ""),
                    "_source":       "category",
                })
    except Exception:
        pass
    return rows


def load_existing_reviews() -> list[dict]:
    if not os.path.exists(OUT_REVIEW):
        return []
    rows = []
    try:
        with open(OUT_REVIEW, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rows.append({k: r.get(k, "") for k in REVIEW_COLUMNS})
    except Exception:
        pass
    return rows


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def write_passes(passes: list[dict]):
    """Tier A/B passes -> 6-col CSV (reusing scrape.save_csv) + a sources audit."""
    if not passes:
        return
    scrape.save_csv(passes, OUT_PASSES)   # dedups by name+website, 6-col schema
    src_rows = []
    seen = set()
    for b in passes:
        key = (b.get("business_name", ""), b.get("website", ""))
        if key in seen:
            continue
        seen.add(key)
        src_rows.append({
            "Business Name": b.get("business_name", ""),
            "Website":       b.get("website", ""),
            "Source":        b.get("_source", "category"),
        })
    with open(OUT_SOURCES, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["Business Name", "Website", "Source"])
        w.writeheader()
        w.writerows(src_rows)


def write_reviews(reviews: list[dict]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_REVIEW, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        w.writeheader()
        for r in reviews:
            w.writerow({k: r.get(k, "") for k in REVIEW_COLUMNS})


def make_review_row(cand: dict, suggested: str, bucket: str, term: str) -> dict:
    return {
        "Business Name":           cand.get("business_name", ""),
        "Address":                 cand.get("address", ""),
        "Phone":                   cand.get("phone", ""),
        "Website":                 cand.get("website", ""),
        "Suggested Minority Type": suggested,   # blank for ambiguous bucket
        "Bucket":                  bucket,
        "Category Term":           term,
        "Source":                  "category_maps_lead",
        "Keep?":                   "",
    }


def specific_type_from(found: list[dict]) -> str:
    """First specific (non-general) ownership type the evidence check returned."""
    for f in found:
        mt = (f.get("minority_type") or "").strip()
        if mt and GENERAL not in mt:
            return mt
    return GENERAL


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def run_discovery():
    if not scrape.SERPAPI_KEY or not scrape.ANTHROPIC_API_KEY:
        print("ERROR: SERPAPI_KEY and ANTHROPIC_API_KEY must be set in .env.")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    scrape.ensure_cache_dirs()
    p = load_progress()
    units_done = set(p["units_done"])

    # Carry forward anything already written so we never duplicate.
    passes  = load_existing_passes()
    reviews = load_existing_reviews()
    seen = set()
    for b in passes:
        seen.add(scrape.business_key(b["business_name"], b.get("website", "")))
    for r in reviews:
        seen.add(scrape.business_key(r["Business Name"], r.get("Website", "")))

    # Skip-known: do not re-surface businesses already in the live directory.
    known_keys, known_hosts, known_urls = set(), set(), set()
    if scrape.SKIP_KNOWN_BUSINESSES and scrape.SUPABASE_URL and scrape.SUPABASE_KEY:
        print("\n=== Loading existing directory from Supabase (skip-known) ===")
        known_keys, known_hosts, known_urls, _ = scrape.fetch_known_businesses()

    total_units = len(SEED_TERMS) * len(CATEGORY_CITIES)
    print("\n=== Projected SerpApi searches (category lane) ===")
    print(f"  Terms: {len(SEED_TERMS)}  x  Cities: {len(CATEGORY_CITIES)}  =  {total_units}")
    if units_done:
        print(f"  Resuming: {len(units_done)} already done, will be skipped.")
    print(f"  (Tier B website checks fetch pages + call Claude; those are not SerpApi.)\n")

    def flush():
        write_passes(passes)
        write_reviews(reviews)
        p["units_done"] = sorted(units_done)
        save_progress(p)

    added_a, added_b, added_c = 0, 0, 0

    try:
        for term, suggested, bucket in SEED_TERMS:
            for city in CATEGORY_CITIES:
                unit = f"{term}|{city}"
                if unit in units_done:
                    continue
                # minority_type passed here only becomes _lead_hint inside
                # get_maps_businesses; it is NOT used to tag anything.
                results = scrape.get_maps_businesses(term, suggested or "", city)
                for cand in results:
                    name = (cand.get("business_name") or "").strip()
                    site = (cand.get("website") or "").strip()
                    if not name:
                        continue
                    key = scrape.business_key(name, site)
                    if key in seen or key in known_keys:
                        continue
                    host = scrape.get_host(site) if site else ""
                    if site and not scrape.is_social(site) and host in known_hosts:
                        seen.add(key)
                        continue

                    # Tier A: Google's own ownership attribute matched.
                    if cand.get("_confirmed"):
                        passes.append({
                            "business_name": name,
                            "address":       cand.get("address", ""),
                            "phone":         cand.get("phone", ""),
                            "services":      "",
                            "website":       site,
                            "minority_type": cand.get("minority_type", ""),
                            "_source":       "category_maps",
                        })
                        seen.add(key)
                        added_a += 1
                        print(f"    [A] {name}  ({cand.get('minority_type','')})")
                        continue

                    # Tier B: usable website -> run the on-page evidence check.
                    usable = (site and not scrape.is_social(site)
                              and not scrape.should_skip(site))
                    if usable:
                        found = scrape.process_url(site)   # fetch + signal + extract
                        if found:
                            passes.append({
                                "business_name": name,
                                "address":       cand.get("address", ""),
                                "phone":         cand.get("phone", ""),
                                "services":      "",
                                "website":       site,
                                "minority_type": specific_type_from(found),
                                "_source":       "category_web",
                            })
                            seen.add(key)
                            added_b += 1
                            print(f"    [B] {name}  (website evidence)")
                            continue

                    # Tier C: no attribute, and no website or no on-page evidence.
                    reviews.append(make_review_row(cand, suggested, bucket, term))
                    seen.add(key)
                    added_c += 1

                units_done.add(unit)
                flush()
    except scrape.SerpApiHalt as e:
        flush()
        print(f"\n!!! Halted: {e}")
        print(f"SerpApi searches used this run: {scrape._SEARCH_COUNT}")
        print("Progress saved. Re-run the same command to resume where it stopped.")
        return

    flush()
    print("\n=== Category discovery complete ===")
    print(f"  Tier A (attribute-confirmed): {added_a}")
    print(f"  Tier B (website-verified):    {added_b}")
    print(f"  Tier C (manual review):       {added_c}")
    print(f"  SerpApi searches used this run: {scrape._SEARCH_COUNT}")
    print(f"\n  Verified passes -> {os.path.basename(OUT_PASSES)} "
          f"({len(passes)} total)")
    print(f"  Review queue    -> {os.path.basename(OUT_REVIEW)} "
          f"({len(reviews)} total)")
    print("\n  Next: work category_review.csv (set Keep? = yes and confirm a type),")
    print("        then: python pipeline/discover_categories.py --promote")


# ---------------------------------------------------------------------------
# Promote: move confirmed Tier C rows into the verified passes file
# ---------------------------------------------------------------------------
def promote():
    if not os.path.exists(OUT_REVIEW):
        print(f"No review file at {OUT_REVIEW}. Run discovery first.")
        return

    reviews = load_existing_reviews()
    passes  = load_existing_passes()
    existing = {scrape.business_key(b["business_name"], b.get("website", "")) for b in passes}

    keep_vals = {"y", "yes", "true", "1", "keep"}
    promoted, skipped_no_type, skipped_dupe, skipped_nontarget = 0, 0, 0, 0
    kept_back = []   # rows we leave in the review file (not kept, or kept-but-untyped)

    for r in reviews:
        decision = (r.get("Keep?") or "").strip().lower()
        if decision not in keep_vals:
            kept_back.append(r)
            continue
        name = r.get("Business Name", "")
        # Safety net: never promote an obvious chain or American/European butcher,
        # even if it was marked yes. These are reliably not targets.
        if _looks_chain(name) or _looks_american_butcher(name):
            print(f"  [skip: not a target] {name} -- chain/butcher, not promoted")
            skipped_nontarget += 1
            continue
        mtype = (r.get("Suggested Minority Type") or "").strip()
        if not mtype:
            print(f"  [skip: no type] {name} "
                  f"-- set Suggested Minority Type before promoting")
            skipped_no_type += 1
            kept_back.append(r)
            continue
        key = scrape.business_key(name, r.get("Website", ""))
        if key in existing:
            skipped_dupe += 1
            continue
        passes.append({
            "business_name": name,
            "address":       r.get("Address", ""),
            "phone":         r.get("Phone", ""),
            "services":      "",
            "website":       r.get("Website", ""),
            "minority_type": mtype,
            "_source":       "category_manual",
        })
        existing.add(key)
        promoted += 1

    write_passes(passes)
    write_reviews(kept_back)   # promoted rows drop out of the review queue

    print("\n=== Promote complete ===")
    print(f"  Promoted to {os.path.basename(OUT_PASSES)}: {promoted}")
    if skipped_nontarget:
        print(f"  Skipped (chain/butcher, not a target): {skipped_nontarget}")
    if skipped_no_type:
        print(f"  Left in review (Keep? set but no type): {skipped_no_type}")
    if skipped_dupe:
        print(f"  Skipped (already in passes): {skipped_dupe}")
    print(f"  Remaining in review queue: {len(kept_back)}")
    print("\n  Next: python pipeline/prepare.py  then  python pipeline/upload_to_supabase.py")


# ---------------------------------------------------------------------------
# Triage: label + sort category_review.csv so the manual pass is fast.
#
# This does NOT tag ownership and does NOT touch Keep?. It only surfaces whether
# the business NAME corroborates the category (a far stronger signal than the
# search term alone), flags likely chains, notes Kentucky, and sorts so the
# strongest rows group at the top. The category term decided what was surfaced;
# the human still decides what is kept.
# ---------------------------------------------------------------------------
LATINE_TOKENS = [
    "carniceria", "mercado", "panaderia", "taqueria", "tienda", "supermercado",
    "abarrotes", "tortilleria", "latino", "latina", "mexican", "mexicana",
    "hispano", "michoacan", "jalisco", "guerrero", "oaxaca", "puebla", "azteca",
    "guadalajara", "el ", "la ", "los ", "las ", "mi ", "taqueria", "fonda",
    "salvador", "guatemal", "honduren", "cubano", "boricua", "sabor",
]
ASIAN_TOKENS = [
    "asian", "oriental", "china", "chinese", "korea", "korean", "viet", "saigon",
    "pho ", "india", "indian", "desi", "patel", "filipino", "manila", "tokyo",
    "seoul", "bangkok", "thai", "japan", "japanese", "hmart", "h mart", "mekong",
    "kimchi", "sari sari", "nepal", "himalaya", "pakistan", "bangladesh", "lao",
    "cambod", "burmese", "myanmar", "halal", "spices", "masala", "curry",
]
CHAIN_HINTS = [
    "walmart", "kroger", "target", "meijer", "aldi", "costco", "sam's", "save a lot",
    "save-a-lot", "dollar general", "family dollar", "food lion", "publix",
    "whole foods", "trader joe", "gordon food", "restaurant depot", "the fresh market",
    "speedway", "circle k", "7-eleven", "shell", "marathon", "amazon",
]

# Generic American/European butcher and meat-market vocabulary. Maps returns these
# for "carniceria" (which means butcher shop), so they flood in around Cincinnati
# and Covington. Deliberately conservative: NO "deli", "farm", "ranch", "market",
# or "seafood", all of which overlap with real ethnic stores (Gutierrez Deli,
# Mi Ranchito, Janta Farmers Market, Seafood City). A row is dropped only when one
# of these appears AND the name carries no ethnic token at all (see the override).
BUTCHER_TOKENS = [
    "butcher", "butchery", "meat market", "meat & produce", "meat and produce",
    " meats", "meat co", "meat company", "smokehouse", "steer", "cattle",
    "poultry", "stockyard", "provisions", "packing",
]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _name_corroborates(name: str, suggested_type: str) -> bool:
    n = " " + _norm(name) + " "
    if "Latine" in suggested_type:
        toks = LATINE_TOKENS
    elif "Asian" in suggested_type:
        toks = ASIAN_TOKENS
    else:
        toks = LATINE_TOKENS + ASIAN_TOKENS   # ambiguous bucket: any ethnic signal
    return any(_norm(t) in n for t in toks)


def _looks_chain(name: str) -> bool:
    n = _norm(name)
    return any(_norm(c) in n for c in CHAIN_HINTS)


def _has_ethnic_token(name: str) -> bool:
    """Any Latine OR Asian name token. Used to PROTECT real ethnic businesses
    from the butcher filter (e.g. 'Carniceria Butcher Shop', 'Mi Casa Butcher')."""
    n = " " + _norm(name) + " "
    return any(_norm(t) in n for t in LATINE_TOKENS + ASIAN_TOKENS)


def _looks_american_butcher(name: str) -> bool:
    """Generic butcher/meat vocabulary with NO ethnic token. Catches the American
    and German meat markets Maps returns for 'carniceria'."""
    n = " " + _norm(name) + " "
    if _has_ethnic_token(name):
        return False
    return any(_norm(t) in n for t in BUTCHER_TOKENS)


def _has_ky(addr: str) -> bool:
    import re
    return bool(re.search(r",?\s*ky\b|kentucky", _norm(addr)))


def triage():
    if not os.path.exists(OUT_REVIEW):
        print(f"No review file at {OUT_REVIEW}. Run discovery first.")
        return

    rows = load_existing_reviews()   # preserves Keep? and Suggested Minority Type
    rank = {"Strong": 0, "Review": 1, "Ambiguous": 2, "Drop?": 3}
    out = []
    counts = {"Strong": 0, "Review": 0, "Ambiguous": 0, "Drop?": 0}
    kept_but_dropped = []   # Keep?=yes rows the filter now flags Drop?

    for r in rows:
        name   = r.get("Business Name", "")
        stype  = r.get("Suggested Minority Type", "")
        bucket = r.get("Bucket", "")
        corrob = _name_corroborates(name, stype)
        chain  = _looks_chain(name)
        butcher = _looks_american_butcher(name)
        ky     = _has_ky(r.get("Address", ""))

        if chain or butcher:
            label = "Drop?"
        elif bucket == "ambiguous":
            label = "Ambiguous"
        elif corrob and ky:
            label = "Strong"
        else:
            label = "Review"
        counts[label] += 1

        if label == "Drop?" and (r.get("Keep?") or "").strip().lower() in ("y", "yes", "true", "1", "keep"):
            kept_but_dropped.append(name)

        row = dict(r)
        row["Triage"]            = label
        row["Name Corroborates"] = "yes" if corrob else "no"
        row["KY"]                = "yes" if ky else "no"
        out.append(row)

    out.sort(key=lambda x: (rank.get(x["Triage"], 9), x.get("Category Term", ""), x.get("Business Name", "")))

    fieldnames = (REVIEW_COLUMNS[:-1]            # everything except Keep?
                  + ["Triage", "Name Corroborates", "KY", "Keep?"])
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_REVIEW, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)

    print("\n=== Triage complete (category_review.csv labeled + sorted) ===")
    print(f"  Strong    (name + term agree, KY): {counts['Strong']}   "
          f"-> skim, then set Keep? = yes")
    print(f"  Review    (name does not corroborate): {counts['Review']}   "
          f"-> eyeball; most are drops")
    print(f"  Ambiguous (blank type by design): {counts['Ambiguous']}   "
          f"-> verify ownership, fill the type")
    print(f"  Drop?     (chain or American/European butcher): {counts['Drop?']}   "
          f"-> almost certainly not a target")
    print("\n  Rows are sorted Strong -> Review -> Ambiguous -> Drop?, then by term.")
    print("  Even Strong rows deserve a skim: corroboration is a strong lean, not proof.")

    if kept_but_dropped:
        print(f"\n  !! {len(kept_but_dropped)} row(s) you marked Keep?=yes are now flagged Drop? "
              f"(butcher/chain, not a target):")
        for nm in kept_but_dropped[:30]:
            print(f"       {nm}")
        if len(kept_but_dropped) > 30:
            print(f"       ... and {len(kept_but_dropped) - 30} more")
        print("     Filter Triage = Drop? and clear the Keep? on those before --promote.")

    print("\n  When done: python pipeline/discover_categories.py --promote")


def main():
    ap = argparse.ArgumentParser(description="Category-seeded discovery lane for The People's Ledger.")
    ap.add_argument("--promote", action="store_true",
                    help="Append confirmed Tier C rows (Keep?=yes + a type) into the passes file.")
    ap.add_argument("--triage", action="store_true",
                    help="Label and sort category_review.csv to make the manual pass fast. Non-destructive to Keep?.")
    args = ap.parse_args()
    if args.promote:
        promote()
    elif args.triage:
        triage()
    else:
        run_discovery()


if __name__ == "__main__":
    main()
