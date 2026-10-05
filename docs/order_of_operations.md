# The People's Ledger: Order of Operations

The pipeline has two phases. Everything that weeds the data before YOU review it
happens on the CSV, before upload. The live-table cleaners run AFTER upload and
are a safety net, not part of the review prep. Three scripts you may have
expected to run pre-review (`clean_addresses.py`, `purge_out_of_state.py`,
`dedupe_live.py`) cannot, because they operate on uploaded Supabase rows.

Run everything from the repo root through `pipeline/ledger.py`. Each verb below is
listed with the scripts it runs, so you can still run a step on its own.

---

## Phase 1: build a clean review pile (CSV, before you look at anything)

### 0. Start the cycle
```bash
python pipeline/ledger.py new-cycle
```
Records the manual drops from last cycle's `businesses_prepared.csv`, then moves
`scraper_progress.json` and the old scrape/prepare files into
`data/archive/<timestamp>_cycle/`. Without this the scraper resumes the FINISHED cycle:
every search is already marked done, so it spends nothing and finds nothing.

### 1. Gather
```bash
python pipeline/ledger.py scrape                 # scrape.py -- main lane, Google Maps
python pipeline/discover_categories.py           # Lane 1b (when expanding ethnic retail)
python pipeline/discover_categories.py --triage  # label + sort the category review file
#   work category_review.csv, then:
python pipeline/discover_categories.py --promote
```
`scrape.py` drops chains, out-of-state addresses and businesses already in the
directory at intake, using `prepare.py`'s own functions, so they never reach the CSV.

### 2. Disposition, auto-settle, flag
```bash
python pipeline/ledger.py prep    # prepare.py -> resolve_review.py -> flag_review.py
```
- `prepare.py` drops chains, out-of-state rows, already-live businesses (skip-known) and
  anything on your denylist, then merges near-duplicates (exact name, and fuzzy names
  that share a phone or website). Rows with no address go to "Needs review".
- `resolve_review.py` settles the no-address rows: it looks up each business's real
  website (even when the listed link is a listicle or aggregator), reads the address,
  promotes Kentucky businesses and drops out-of-state ones.
- `flag_review.py` adds a "Review Flag" column and moves anything serious (national
  brand, likely duplicate of a live row, unverified ownership, no Kentucky signal) into
  "Needs review" so it cannot upload unexamined.

### 3. Review by hand
Open `data/businesses_prepared.csv`, filter to "Needs review". Keep the good ones (set
Disposition to "Good to go"), set the rest to "Dropped". Dropped rows are recorded to
`data/denylist.csv` automatically -- by the next `prepare.py`, by the upload, or by
`new-cycle`, whichever comes first -- so they never come back.

### 4. Publish
```bash
python pipeline/ledger.py publish   # upload_to_supabase.py -> enrich.py -> generate-business-pages.js
```
Uploads only the "Good to go" rows, archives the consumed files, fills industry and
services with Claude, and regenerates the static pages.

---

## Phase 2: live-table cleanup (after upload)

These act on Supabase, so they only make sense once rows are live. They are safety
nets for whatever slipped past Phase 1. The runner only ever runs them as dry runs.

```bash
python pipeline/ledger.py maintain   # clean_addresses / purge_out_of_state / dedupe_live, all DRY RUN

# apply the ones you agree with, individually:
python pipeline/clean_addresses.py --apply
python pipeline/purge_out_of_state.py --apply
python pipeline/dedupe_live.py --apply

# then publish
node generate-business-pages.js
git add -A && git commit -m "..." && git push
```

Lane 2 (certifications) runs against the live table too:
```bash
python pipeline/ledger.py certs                                   # dry run
python pipeline/reconcile_certifications.py --apply               # backfill labels
python pipeline/reconcile_certifications.py --apply --insert-new  # also add businesses
```

Monthly, separately:
```bash
python pipeline/ledger.py links                # maintain.py: re-check website link status
python pipeline/maintain.py --buyblack         # resolve buyblack.org URLs (SerpApi), as needed
```
