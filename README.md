# The People's Ledger

A public directory of underrepresented-owned businesses in Kentucky.
Tagline: Money Talks. Spend Where It Counts.

The live site is static (GitHub Pages) and reads all business data from Supabase
at runtime. None of the data files or Python scripts are part of the deployed
site; they are the tooling that maintains the Supabase `businesses` table.

## Repo layout

```
/                         <- the website (served by GitHub Pages, do not move)
  index.html              public directory
  admin.html              local-only moderation tool (gitignored, holds the service key)
  about.html
  CNAME
  generate-business-pages.js   builds the static /businesses/ profile pages
  businesses/             generated profile pages + sitemap

pipeline/                 <- all maintenance scripts (run manually, not deployed)
  scrape.py               web discovery: Google Maps, listicles, social
  prepare.py              filter + dedupe a scrape into one dispositioned file
  upload_to_supabase.py   insert the approved rows into the businesses table
  enrich.py               post-upload: fill industry + services via Claude
  maintain.py             link-status check (monthly) + buyblack URL fix (as needed)
  reconcile_certifications.py  (lane two, certification spreadsheets) - to build
  view_database.py        quick local view of a data/ CSV in D-Tale

data/                     <- all working files (gitignored, never committed)
  businesses_scraped.csv          raw scraper output
  businesses_scraped_sources.csv  per-row source audit
  businesses_scraped_checkpoint.csv
  scraper_progress.json
  cache/                          cached HTML + Maps responses + extractions
  businesses_prepared.csv         prepare.py output (the one file you review)

docs/
  PeoplesLedger_Technical_Reference.md   the deep reference
```

## Two ways businesses get in

The directory has two separate intake lanes that both end at the `businesses`
table. They use different tools on purpose and must not be mixed.

### Lane 1 - Web discovery (quarterly)

Finds consumer-facing businesses by crawling the web. Run from `pipeline/`:

1. `python scrape.py` -> writes data/businesses_scraped.csv
2. `python prepare.py` -> writes data/businesses_prepared.csv with a
   **Disposition** column: Good to go, Needs review, or Dropped
3. Open businesses_prepared.csv. Work the **Needs review** rows; change any real
   Kentucky business to `Good to go`, change the rest to `Dropped`. The Dropped
   rows (chains, out-of-state) are there for audit; override any you disagree with.
4. `python pipeline/upload_to_supabase.py` -> inserts ONLY the `Good to go` rows
5. Post-upload enrichment: `python pipeline/enrich.py` (fills industry, then
   services), then `python pipeline/maintain.py` (sets link status)
6. Regenerate static pages: `node generate-business-pages.js`

### Lane 2 - Certification spreadsheets (as agencies refresh)

The Louisville HRC, KY Transportation DBE, and Finance certification lists are
CAPTCHA-protected manual downloads. They are the ONLY source of the
`certification_type` column and reach businesses the web crawl never finds.
These must NOT go through prepare.py (it would strip authoritative records).
Reconcile them against the live table with fuzzy matching, fill
certification_type on matches, and insert genuine new businesses.
(`reconcile_certifications.py` is not built yet.)

## Keys

- Scraper uses a read-only publishable Supabase key (the same one in index.html).
- upload_to_supabase.py needs a key with INSERT rights (the service role key).
  Keep them straight; they are different keys.

## Notes

- `minority_type` is a comma-separated string, not a normalized table.
- The Supabase search RPC functions (`search_businesses`, `suggest_search`) are
  not auto-updated by schema changes. If you add or rename a column, update them.
- Cloudflare DNS must stay DNS-only (gray cloud) or Let's Encrypt renewals fail.
