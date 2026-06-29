# The People's Ledger: Order of Operations

The pipeline has two phases. Everything that weeds the data before YOU review it
happens on the CSV, before upload. The live-table cleaners run AFTER upload and
are a safety net, not part of the review prep. Three scripts you may have
expected to run pre-review (`clean_addresses.py`, `purge_out_of_state.py`,
`dedupe_live.py`) cannot, because they operate on uploaded Supabase rows.

Run everything from the repo root in Git Bash.

---

## Phase 1: build a clean review pile (CSV, before you look at anything)

### 1. Gather
```bash
python pipeline/scrape.py                       # main lane (when refreshing)
python pipeline/discover_categories.py          # Lane 1b (when expanding ethnic retail)
python pipeline/discover_categories.py --triage # label + sort the category review file
#   work category_review.csv, then:
python pipeline/discover_categories.py --promote
```

### 2. Disposition and weed
```bash
python pipeline/prepare.py
```
This single step removes most of what you do not want to see. It drops chains,
drops out-of-state rows that HAVE an out-of-state address, drops businesses
already in the live directory (skip-known), drops anything on your denylist,
and merges exact-name duplicates among the keepers. Rows with no address land in
"Needs review" because their state cannot be judged from an empty address field.

### 3. Auto-settle the "Needs review" rows
```bash
python pipeline/resolve_review.py
```
This is the step that has been missing. It visits each Needs-review row's
website and its about/contact pages, reads the address, then promotes Kentucky
businesses to Good to go and drops out-of-state ones. After this, the only rows
left at "Needs review" are the ones no automated check could settle.

Tip: `python pipeline/resolve_review.py --limit 10` does a small test run first;
`--dry-run` reports without writing.

### 4. NOW review by hand
Open `businesses_prepared.csv`. Filter to "Needs review". This pile is now small
and genuine: not chains, not already-live, not denylisted, not out-of-state by
address or by website. Keep the good ones (set Disposition to "Good to go"),
drop the rest.

### 5. Remember your drops so they never come back
```bash
python pipeline/prepare.py --commit-drops
```
Records the rows you just dropped (and the out-of-state ones resolve_review
dropped) to `data/denylist.csv`. Next run, prepare drops them automatically.
Run this BEFORE any future `prepare.py`, since a normal run regenerates the file.

### 6. Upload
```bash
python pipeline/upload_to_supabase.py
```
Uploads only the "Good to go" rows.

---

## Phase 2: live-table cleanup and enrichment (after upload)

These act on Supabase, so they only make sense once rows are live. For a normal
upload, run enrich. The three cleaners are safety nets for whatever slipped past
Phase 1; run them when warranted. If you do run them, run them BEFORE enrich so
you are not paying Claude to enrich rows you are about to delete.

```bash
# safety nets (as needed)
python pipeline/clean_addresses.py            # dry run; --apply to strip stray N/A
python pipeline/purge_out_of_state.py         # dry run; --apply to delete out-of-state
python pipeline/dedupe_live.py                # merge any duplicates that got through

# standard post-upload enrichment
python pipeline/enrich.py                     # fill industry + services via Claude

# publish
node generate-business-pages.js               # rebuild static pages + sitemap
git add -A && git commit -m "..." && git push
```

Monthly, separately:
```bash
python pipeline/maintain.py                   # re-check website link status
python pipeline/maintain.py --buyblack        # resolve buyblack.org URLs (SerpApi), as needed
```

---

## What still reaches your eyes (known gaps)

1. **Listicle-website rows.** When a Needs-review row's "website" is the article
   it was scraped from (smileypete.com, the Tennessee Tribune, Voice of Black
   Cincinnati) rather than the business's own site, `resolve_review.py` reads the
   listicle, finds no single address, and leaves the row for you. The fix is to
   detect those aggregator URLs, search for the business's real site (the way
   `maintain.py --buyblack` does), then run the address extraction on it. NOT yet
   built.

2. **Near-duplicate names.** prepare merges only EXACT name matches and
   skip-known catches live duplicates, so a "Joe's BBQ" vs "Joe's BBQ LLC" pair,
   or the same business under two different websites, can still appear. A fuzzy
   pre-review dedup in prepare would close this. NOT yet built. `dedupe_live.py`
   still catches these after upload.
