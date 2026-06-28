# The People's Ledger — Technical Reference for Claude Sessions

**Purpose:** This document gives Claude full working context before making any changes to The People's Ledger. Read this before writing any code, editing any file, or making any Supabase recommendations. Every architectural decision here is intentional.

---

## 1. Project Overview

The People's Ledger is a free, public, searchable directory of underrepresented businesses in Kentucky. It was built to serve everyday consumers — not procurement officers — filling a gap that supplier diversity programs were never designed to address. The directory currently contains roughly 1,794 verified, deduplicated records.

**Live URL:** thepeoplesledger.net
**GitHub Repo:** github.com/jfraz757/The_Peoples_Ledger
**Branch:** main (auto-deploys to GitHub Pages on push)
**Operated by:** Education to Action LLC, Louisville, KY (Joe Frazier)
**Intentionally open-sourced** as a replicable model for other states

---

## 2. Tech Stack

**Frontend:** Vanilla HTML, CSS, JavaScript — no frameworks, no build step
**Database:** Supabase (PostgreSQL) — accessed via REST API and RPC functions
**Search:** PostgreSQL `pg_trgm` extension — trigram-based fuzzy search
**Hosting:** GitHub Pages
**Font:** Google Fonts (Michroma — display only; Arial for body)
**Favicon lookup:** Google Favicon API

**Supabase credentials (hardcoded in index.html and about.html — intentional):**
```
SUPABASE_URL = "https://ursmecdpgtqckacyhnko.supabase.co"
SUPABASE_KEY = "sb_publishable_A0zmuZVHVPtosZrNdFE4GQ_sITuTrkg"
```

**Python environment (for scripts):** `C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe`
**API keys for scripts:** stored in `.env` file (gitignored — never commit)

---

## 3. File Structure

```
The_Peoples_Ledger/
├── index.html                  # Main directory page (served by GitHub Pages)
├── about.html                  # About page with live business count
├── admin.html                  # Local-only moderation tool (gitignored)
├── CNAME                       # thepeoplesledger.net
├── README.md                   # Project overview + runbook (committed)
├── .gitignore                  # Excludes .env, admin.html, data/
├── generate-business-pages.js  # Builds the static /businesses/ SEO pages
├── businesses/                 # Generated per-business pages + sitemap (committed)
│
├── pipeline/                   # All maintenance scripts (run manually, not deployed)
│   ├── scrape.py               # Web discovery: Google Maps, listicles, social
│   ├── prepare.py              # Filter + dedupe a scrape into one dispositioned file
│   ├── upload_to_supabase.py   # Insert approved rows into the businesses table
│   ├── enrich.py               # Post-upload: fill industry + services via Claude
│   ├── dedupe_live.py          # One-off/repeatable duplicate cleanup on the live table
│   ├── maintain.py             # Link-status check (monthly) + buyblack fix (as needed)
│   ├── reconcile_certifications.py  # Lane 2: certification spreadsheets (to build)
│   └── view_database.py        # Open a data/ CSV in D-Tale
│
├── data/                       # All working files (gitignored, never committed)
│   ├── businesses_scraped.csv          # Raw scraper output
│   ├── businesses_scraped_sources.csv  # Per-row source audit
│   ├── businesses_scraped_checkpoint.csv
│   ├── scraper_progress.json
│   ├── businesses_prepared.csv         # prepare.py output (the file you review)
│   └── cache/                          # Cached HTML, Maps responses, extractions
│
└── docs/
    └── PeoplesLedger_Technical_Reference.md   # This file
```

**admin.html is gitignored** — runs locally only, holds the admin password and the service-role key, never deployed.

**The entire `data/` folder is gitignored.** The live site reads from Supabase, so no working file belongs in the repo. Scripts derive `data/` from their own location, so there are no hardcoded absolute paths anywhere in committed code.

---

## 4. Supabase Database Schema

### Two tables: `businesses` and `submissions`

#### `businesses` — the public-facing directory

| Column | Type | Notes |
|---|---|---|
| id | BIGINT | Auto-generated primary key |
| business_name | TEXT | |
| address | TEXT | Full address |
| phone | TEXT | |
| services_products | TEXT | Filled by Claude API where missing |
| website | TEXT | Business website URL |
| minority_type | TEXT | Comma-separated if multiple — e.g. "Black-Owned, Women-Owned" |
| industry | TEXT | One of 23 standardized categories (assigned by Claude API) |
| status | TEXT | Active / Inactive / No Website — reflects link check, not business operating status |
| kentucky_based | TEXT | Yes / No |
| certification_type | TEXT | Comma-separated if multiple — DBE, MBE, WBE, MWBE, SBE, ACDBE, VBE, SDVBE, LGBTBE, Not Certified, Unknown |

**No `status` field for moderation** — unlike CandidateVoice, every record in `businesses` is public. There is no pending/approved gate on this table. All new business submissions go through `submissions` first.

**`minority_type` is a comma-separated string**, not a normalized junction table. This is a known limitation. Do not assume it's a single value — filter logic must handle comma-separated values.

#### `submissions` — community submission queue (never public)

| Column | Type | Notes |
|---|---|---|
| id | BIGINT | Auto-generated primary key |
| submission_type | TEXT | "new" or "update" |
| business_name | TEXT | |
| address | TEXT | For new submissions only |
| phone | TEXT | For new submissions only |
| website | TEXT | For new submissions only |
| services_products | TEXT | For new submissions only |
| minority_type | TEXT | For new submissions only |
| kentucky_based | TEXT | For new submissions only |
| update_notes | TEXT | Free text correction details — for update submissions only |
| submitter_name | TEXT | Required — used for follow-up |
| submitter_email | TEXT | Required — used for follow-up, never displayed publicly |
| status | TEXT | pending / approved / rejected |
| submitted_at | TIMESTAMP | Auto-set on insert |

---

## 5. Supabase RPC Functions (Critical — Search Uses These)

The search system does NOT use direct REST queries against the `businesses` table. It calls two custom PostgreSQL RPC functions:

### `search_businesses`
Called by `fetchRecords()` in index.html. Parameters:
- `search_text` — combined name + service query string
- `area_text` — city/area filter
- `ownership_type` — "All" or a specific minority type
- `industry_filter` — "All" or a specific industry
- `page_limit` — page size (default 24)
- `page_offset` — pagination offset

Returns records with a `total_count` field on each row for pagination.

**Rebuilt June 2026.** The original used whole-string `similarity()`, which diluted short queries against long names so a single word like "chois" scored below threshold and returned nothing until more words were added. The current version:
- Matches `search_text` across `business_name`, `services_products`, AND `industry`, not the name alone.
- Primary match is a normalized substring (`ILIKE`) where both the query and the field are lowercased with all non-alphanumerics stripped, so punctuation no longer blocks matches. This is why "chois" now finds "Choi's Asian Food Market" (the apostrophe between the i and s previously broke the literal match).
- Fuzzy fallback uses `word_similarity()` (term vs the best-matching segment of the field), not whole-string `similarity()`, so short fragments match inside long names.
- Ranks exact substring hits first, then by fuzzy relevance, then alphabetically.

The return column list is flat and must match the `businesses` schema; if a column is added/renamed/removed, update the `RETURNS TABLE` block.

### `suggest_search`
Called when a search returns zero results. Parameter:
- `search_text` — the failed query

Returns the closest business names by `word_similarity` (rebuilt June 2026 to match the new search logic). Powers the "Did You Mean...?" feature.

**Critical:** If the schema of `businesses` changes (column added/renamed/removed), these RPC functions may need to be updated in Supabase. They are not auto-updated by schema changes.

---

## 6. Data Flow

### Public submission flow
```
User fills form (index.html modal — "Add a Business" or "Submit a Correction")
        ↓
POST to /rest/v1/submissions
  submission_type: "new" OR "update"
  status: "pending"
        ↓
Joe opens admin.html locally
        ↓
Admin reads /rest/v1/submissions (all statuses, ordered by submitted_at desc)
        ↓
    [APPROVE]                           [REJECT]
        ↓                                   ↓
  If submission_type = "new":         PATCH submissions
    POST to /rest/v1/businesses         set status = "rejected"
    Insert as live record
    status: "Active"
        ↓
  If submission_type = "update":
    Admin must MANUALLY apply the
    correction in Supabase table editor
    (admin.html only marks it approved —
    it does NOT auto-patch businesses)
        ↓
  PATCH submissions set status = "approved"
```

**Update submissions now auto-apply to the `businesses` table (updated June 2026).** When you approve an update submission in admin.html, the admin looks up the business by exact name and PATCHes only the fields that were submitted. Blank fields are ignored. If no exact name match is found, the admin alerts you to apply the correction manually. Any free-text additional notes are shown in the approval alert for your review but do not auto-apply.

### Data pipeline flow

Two intake lanes feed the one `businesses` table. They use different tools on purpose and must not be mixed.

**Lane 1 — Web discovery (quarterly).** Run from the repo root:
```
pipeline/scrape.py    → data/businesses_scraped.csv (raw web discovery)
pipeline/prepare.py   → data/businesses_prepared.csv
                        (filtered, deduped, one Disposition column:
                         Good to go / Needs review / Dropped)
   [review the "Needs review" rows; flip keepers to "Good to go"]
pipeline/upload_to_supabase.py → inserts ONLY "Good to go" rows into businesses
pipeline/enrich.py    → fills industry, then services_products, via Claude
pipeline/maintain.py  → sets status (Active / Inactive / No Website)
generate-business-pages.js → regenerates the static /businesses/ pages
```

**Lane 2 — Certification spreadsheets (as agencies refresh).** The Louisville HRC, KY Transportation, and KY Finance certification lists are CAPTCHA-protected manual downloads and the only source of `certification_type`. They must NOT go through prepare.py, which strips chains and out-of-state records that are legitimate on an authoritative list. Reconcile them against the live table with fuzzy matching, fill certification_type on matches, and insert genuine new businesses. (`reconcile_certifications.py` is not built yet.)

---

## 7. Page-by-Page Reference

### index.html
The entire public-facing product lives here. Features:
- **Search:** Three simultaneous search fields — business name, city/area, service/product — all passed as `search_text` and `area_text` to `search_businesses` RPC
- **Fuzzy search:** pg_trgm handles typos — "Did You Mean...?" suggestions on zero results via `suggest_search` RPC
- **Ownership type filter:** Pills for All, Black-Owned, Women-Owned, Latine-Owned, Asian-Owned, LGBTQ+-Owned, Veteran-Owned, Native American-Owned, Disability-Owned, Muslim-Owned
- **Industry filter:** 23 categories (hardcoded in UI, not dynamically loaded from DB)
- **Business cards:** Favicon, ownership badge, address, phone, services preview, website link, link status indicator
- **Pagination:** 24 records per page, total count from `total_count` field on RPC results
- **Export:** Full filtered results to CSV or JSON — calls `/rest/v1/businesses` directly with current filters, not the RPC
- **Submit modal:** Two-tab form — "Add a Business" (full profile) and "Submit a Correction" (business name + free text notes). Both post to `/rest/v1/submissions`
- **Live business count:** Fetched on page load from `businesses` table using `Prefer: count=exact`
- **Mobile responsive** with hamburger nav drawer

### about.html
Static content page. Pulls live business count from `businesses` table on load (same count fetch as index.html). Contains origin story, stat blocks, and CTA back to directory.

### admin.html (LOCAL ONLY — NOT IN REPO)
- Password-protected (hardcoded password — reason it's gitignored)
- Loads ALL submissions on login (no status filter — shows pending, approved, rejected together)
- Filter buttons: All / Pending / Approved / Rejected (client-side filter of loaded data)
- Left panel: submission list. Right panel: submission detail view
- Approve button behavior:
  - `submission_type = "new"` → POSTs to `/rest/v1/businesses`, then PATCHes submission to approved
  - `submission_type = "update"` → Only PATCHes submission to approved; does NOT auto-apply changes. Manual edit in Supabase required.
- Reject button: PATCHes submission to rejected only
- No verify toggle, no stats tab (simpler than CandidateVoice admin)

---

## 8. Design System

Completely different from CandidateVoice — dark theme.

- **Background:** `#1a1a1a` (body), `#111111` (hero/sections)
- **Header background:** `rgba(30, 30, 30, 0.92)` — semi-transparent sticky
- **Primary accent:** `#FFD700` (gold) — used for all headings, borders, CTAs, highlights
- **Body text:** `rgba(255,255,255,0.78)` — slightly transparent white
- **Muted text:** `rgba(255,255,255,0.45)`
- **Font (display/headings):** Michroma (Google Fonts) — letter-spacing heavy, uppercase
- **Font (body):** Arial, Helvetica, sans-serif
- **Borders:** `rgba(255, 215, 0, 0.2)` — subtle gold borders
- **Nav links:** Gold border, gold text, pill-shaped. Hover/active: gold background, dark text
- **Cards:** Dark background `rgba(30,30,30,0.85)`, gold left border accent
- **CTA buttons:** Transparent with gold border; hover fills gold

**Do not mix CandidateVoice's blue/orange design system into this project.** They are completely separate visual identities.

---

## 9. Search Architecture — Key Details

The search is powered by two PostgreSQL RPC functions, not direct table queries. This means:

1. **Never replace `fetchRecords()` with a direct REST query to `businesses`** — you'll lose fuzzy search, the `total_count` field for pagination, and ownership type filtering logic that's encoded in the function.

2. **Export is a direct REST query** — `exportAll()` calls `/rest/v1/businesses` directly because it needs all matching records, not a paginated set. This is intentional and correct.

3. **Industry pills are hardcoded** in the UI (unlike CandidateVoice where they're dynamic). The 23 categories are fixed — if you add a new category to the DB, add it to the HTML pill list too.

4. **Ownership type filtering** uses partial string matching (`ilike '%value%'`) inside the RPC function because `minority_type` is a comma-separated string. A business tagged "Black-Owned, Women-Owned" should show up under either filter.

5. **Punctuation is normalized in search (June 2026).** Both the query and the searched fields are lowercased with non-alphanumerics stripped before substring matching, so apostrophes, ampersands, and periods do not block hits. Category and free-text search are different things: putting a grocery in the Food and Beverage *category* makes the filter work, but the free-text word "groceries" only matches if the word appears in the name, services, or industry text. Keep `services_products` descriptions leading with the plain searchable business type for this reason.

---

## 10. Python Scripts Reference

All scripts load `.env` from the repo root and derive `data/` from their own location, so run them from the repo root. No hardcoded absolute paths. Python: `C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe`

| Script | Purpose | Frequency | API cost |
|---|---|---|---|
| `pipeline/scrape.py` | Web discovery (Maps, listicles, social) | Quarterly | SerpApi + Haiku (low) |
| `pipeline/prepare.py` | Filter geography and chains, dedupe, write the dispositioned file | After each scrape | Free |
| `pipeline/upload_to_supabase.py` | Insert "Good to go" rows in batches of 100 | After review | Free |
| `pipeline/enrich.py` | Repair/fill industry then services via Claude. Now repairs existing bad data, not just nulls. Flags: `--industries` (fill null + fix off-list labels), `--services` (fill null + expand thin text), `--reclassify "Bucket,Bucket"` (re-evaluate a bucket, move only on change), `--reenrich-services "Bucket"` (rewrite a bucket's services regardless of length), `--reenrich-groceries` (per-row classifier over Food and Beverage; rewrites only grocery/market businesses so they are findable by "groceries"), `--dry-run`, `--limit N` | After upload | ~$0.75-1.00/1000 |
| `pipeline/dedupe_live.py` | Merge duplicate rows in the live table. Groups by normalized name; survivor keeps the best address and the real business website (not a buyblack.org placeholder); same-name + same-phone rows merge even when addresses differ; genuine address conflicts go to a review CSV. `--selftest`, `--dry-run` (default), `--apply`. Needs the service-role key. | As needed | Free |
| `pipeline/maintain.py` | Link-status check; `--buyblack` also resolves buyblack.org URLs | Monthly / as needed | Free / SerpApi |
| `pipeline/reconcile_certifications.py` | Lane 2 certification merge (to build) | As agencies refresh | Free |
| `pipeline/view_database.py` | Open a `data/` CSV in D-Tale | As needed | Free |

**Consolidation note:** `prepare.py` replaces the old `triage` + `clean_ky_businesses.py`; `enrich.py` replaces `categorize_industries.py` + `fill_missing_services.py`; `maintain.py` replaces `check_link_status.py` + `fix_buyblack_urls.py`.

**Claude models:** `scrape.py` extracts with `claude-haiku-4-5`; `enrich.py` uses `claude-sonnet-4-6`.

**Upload key:** `upload_to_supabase.py` needs a key with INSERT rights (service role), distinct from the read-only publishable key used by `scrape.py` and the live site.

**Scraper data sources (all require manual download — CAPTCHA-protected):**
- Kentucky Transportation Cabinet (B2GNow portal)
- Kentucky Finance and Administration Cabinet (MWBE certification listings — `.xlsx`, converted to CSV)
- City of Louisville Human Relations Commission (diversitycompliance.com)

**Note from June 2026 data run:** The KY Transportation Cabinet and KY Finance & Administration Cabinet no longer include minority type identification in their exports — a result of anti-DEI legislative pressure. However, the **Louisville HRC database still includes both `Ethnicity` and `Certification Type` fields** in its CSV export, confirmed June 2026. A merge of HRC data (277 records, 208 unique companies) against the existing Supabase database was completed in June 2026, adding `certification_type` and `business_category` data where matches were found. The HRC export remains a reliable source for Louisville-area businesses with government certification data.

---

## 11. RLS Policies

RLS is enabled on the `businesses` table. Per the README, public read, insert, and update access is granted. The `submissions` table also has RLS. If you hit permission errors:
- Public users reading `businesses` → SELECT policy required
- Form submissions posting to `submissions` → INSERT policy required
- Admin approving submissions and inserting into `businesses` → INSERT policy on `businesses` required
- Admin marking submissions approved/rejected → UPDATE policy on `submissions` required

---

## 12. Business SEO Pages

Static per-business HTML pages are generated by `generate-business-pages.js` and live in the `/businesses/` directory. These pages are committed to the repo and deployed via GitHub Pages.

**Script location:** `generate-business-pages.js` (repo root)
**Output directory:** `/businesses/` (committed to repo)
**Run command:**
```bash
node generate-business-pages.js
```

**What the script does:**
1. Queries Supabase for all businesses (paginated — handles 1,000+ records correctly)
2. Writes a static HTML file to `/businesses/{slug}.html` for each business
3. Writes `/businesses/sitemap.xml` listing every business page plus `index.html` and `about.html`

**Slugify logic:** business names are lowercased, non-alphanumeric characters replaced with hyphens, leading/trailing hyphens stripped. Example: "Joe's Plumbing LLC" → `joes-plumbing-llc`.

**Each business page includes:**
- Business name, favicon (Google Favicon API if website exists), status badge (Active/Inactive/No Website), industry
- Ownership tags (minority_type, comma-separated) in gold
- Certification tags (certification_type, comma-separated) in purple — Unknown and Not Certified filtered out of display
- Contact section: address (links to Google Maps), phone (tap-to-call link), website, Kentucky-based flag
- Services & Products description where available
- CTA linking back to `index.html?search=BusinessName` and to the full directory
- Canonical URL and Open Graph meta tags for SEO

**Sitemap:** `/businesses/sitemap.xml` lists every business page plus `index.html` and `about.html`. First submitted to Google Search Console June 24, 2026 (1,266 pages discovered, Status: Success). Regenerated June 2026 after dedupe and enrichment; the table and sitemap now hold 1,794 business pages.
Submit sitemap at: `https://search.google.com/search-console`
Sitemap URL: `https://thepeoplesledger.net/businesses/sitemap.xml`

**Maintenance frequency:** Regenerate after each quarterly data refresh (after new records are uploaded and categorized). This is the final step in the quarterly pipeline.

**After running:**
```bash
git add businesses/
git commit -m "Regenerate business SEO pages"
git push
```

**Node version note:** Script uses native `fetch` (Node 18+). If running an older Node version, install `node-fetch` and require it at the top of the script.

---

## 13. Maintenance Schedule

| Task | Command | Frequency |
|---|---|---|
| Add new businesses | `python pipeline/scrape.py` | Quarterly |
| Prepare (filter + dedupe) | `python pipeline/prepare.py` | After each scrape |
| Upload approved rows | `python pipeline/upload_to_supabase.py` | After review |
| Enrich (industry + services) | `python pipeline/enrich.py` | After upload |
| Dedupe the live table | `python pipeline/dedupe_live.py` (dry-run first, then `--apply`) | As needed |
| Regenerate SEO pages | `node generate-business-pages.js` | After upload (quarterly) |
| Refresh link statuses | `python pipeline/maintain.py` | Monthly |
| Fix buyblack URLs | `python pipeline/maintain.py --buyblack` | As needed |
| Reconcile certification lists | `python pipeline/reconcile_certifications.py` (to build) | As agencies refresh |

---

## 14. Known Limitations

- `minority_type` and `certification_type` are comma-separated strings, not normalized. Future refactor would use junction tables.
- Ownership detection in `scrape.py` works by source: Google Maps results are kept only when Google's own self-identified ownership attribute is present and are tagged from that attribute; organic and social results are tagged from ownership language in the page text. Badges and images are not read. This is the fix for the early bug where chains and same-name businesses were mislabeled from the search query.
- The national certification directories (NMSDC, WBENC, NGLCC) are membership-gated paid databases aimed at B2B procurement, not a consumer directory, and scraping them is ToS-risky. Selenium was evaluated and deliberately not added. Free JS-rendered directories are instead handled by finding their JSON endpoint and adding it to `DIRECTORY_API_ENDPOINTS`.
- This is not a substitute for certified MBE data for procurement compliance.
- The KY Transportation Cabinet and KY Finance & Administration Cabinet no longer include minority type fields in exports. The Louisville HRC database still does — it includes both `Ethnicity` and `Certification Type` columns and should be downloaded and merged on each quarterly data refresh (lane 2).

---

## 15. Key Differences from CandidateVoice

Claude works on both projects. These are easy to confuse — keep them straight:

| | CandidateVoice | The People's Ledger |
|---|---|---|
| Supabase project | lawteswyjpkovzagnshn | ursmecdpgtqckacyhnko |
| Public table | `reviews` | `businesses` |
| Submission table | `submissions` | `submissions` |
| Search method | Direct REST query | RPC functions (`search_businesses`) |
| Moderation | Auto-applies on approve | New records auto-insert; updates require manual DB edit |
| Design | Light, blue/orange | Dark, black/gold |
| Font | Inter | Michroma (display), Arial (body) |
| Industry pills | Dynamic from DB | Hardcoded in HTML |
| Score system | Yes (PostgreSQL trigger) | No |
| Python scripts | No | Yes — full data pipeline |
| SEO pages | `/employers/` via `generate-employer-pages.js` | `/businesses/` via `generate-business-pages.js` |

---

## 17. Keeping This Document Current

This document is only useful if it reflects what actually happened. After any session where something significant is built, broken, fixed, or decided, update this file and commit it.

**Triggers for updating this document:**
- A new feature, script, or page type is added
- A bug is found and fixed
- A known-bad approach is attempted and reverted
- An architectural decision is made (RLS changes, schema changes, new tables)
- A new file is added to the repo

**Commit it like any other file:**
```bash
git add docs/PeoplesLedger_Technical_Reference.md
git commit -m "Update technical reference"
git push
```

The version on GitHub is the source of truth. If your local copy and the repo diverge, the repo wins.

**Before pushing any update to the live site**, always test locally first: run `python -m http.server 8080` from the repo root, open `http://localhost:8080/[changed-file].html`, verify visually at desktop and mobile width, and check browser console (F12) for errors. No exceptions.

---

## 18. Checklist Before Making Changes

- [ ] Am I in the right project? Confirm Supabase URL starts with `ursmecdpgtqckacyhnko`
- [ ] Does this change affect the search RPC? If so, update the function in Supabase, not just the HTML
- [ ] Does the change add a new industry? Add it to both the DB categorization script AND the HTML filter pills
- [ ] Does the change affect the `businesses` schema? Update RPC functions too
- [ ] Does the change affect the submission flow? Test both "Add a Business" and "Submit a Correction" tabs
- [ ] Does the change affect the admin approval flow? Remember that update submissions require manual DB edits
- [ ] Is admin.html being pushed to GitHub? (It should never be — it's gitignored)
- [ ] Is `.env` being pushed to GitHub? (It should never be — it's gitignored)
- [ ] Does a Python script need to be updated? Test with a small batch before running on the full ~1,794 records

---

## 19. The Scraper (pipeline/scrape.py)

The web-discovery engine for lane 1. It writes to `data/`, and its output is consumed by `pipeline/prepare.py` (which merges the old triage and clean steps). The CSV is a 6-column schema; `prepare.py` adds the Status, Kentucky Based, and Disposition columns downstream.

### What it does

- Runs a SerpApi Google Maps phase that pulls structured business name, address, phone, and website with no page fetch and no Claude call. Ownership type comes from Google's self-identified ownership attribute on the result, never from the search query (see caveats).
- Switches extraction from Sonnet to Haiku (`claude-haiku-4-5`). Same JSON extraction job, much lower cost per record.
- Stops truncating long pages at 6000 characters. Pages that are long and carry multiple ownership signals are treated as roundups, chunked, and extracted in full.
- Keeps internal directory profile links (for example `/directory/business/123`), not only outbound links. Deep-link discovery now reads the full page including nav, header, and footer, where About and Contact links usually live.
- Adds an optional config slot for JSON or XHR directory endpoints (`DIRECTORY_API_ENDPOINTS`) so the JS-rendered directories that return nothing to plain requests can be read directly once their data endpoint is found in browser devtools. Empty by default, with a worked example in comments.
- Fixes the domain skip logic to parse the host and match exact or suffix domains, so valid sites are no longer dropped by accidental substring matches.
- Caches fetched HTML and Claude extractions on disk, so re-runs skip the fetch and skip re-paying for unchanged pages.

### Statewide coverage

`STATEWIDE_CITIES` spans every region of Kentucky (Louisville, Lexington, Bowling Green, Owensboro, Covington, Florence, Georgetown, Richmond, Elizabethtown, Nicholasville, Hopkinsville, Frankfort, Paducah, Henderson, Ashland, Murray, Somerset, Madisonville, London, Pikeville, Danville, and Winchester). Both the Maps and organic phases run across this list. Trim the list to cut cost, extend it for finer coverage.

### Instagram and Facebook

Those two domains are intentionally not skipped. Social pages are read through their og: meta tags, which carry the business name and bio even on a partial fetch. With `INCLUDE_SOCIAL_SEARCHES` on, the script also runs `site:instagram.com` and `site:facebook.com` searches per ownership term to surface profiles directly. Social fetches are less reliable than the site search path because the platforms increasingly block logged-out requests, so treat the site search results as the primary social channel.

### SerpApi budget

A full fresh statewide run is roughly 690 SerpApi searches (about 330 Maps, 330 organic, 30 social). The free tier is 100 per month, so a full statewide run needs a paid plan. The script prints the projected count at startup before any search runs. Directory page fetches do not use SerpApi.

### Resume behavior

`data/scraper_progress.json` records exactly which Maps searches, directory harvests, organic searches, and URL scans have completed. If any phase fails, fix the problem and re-run. The script skips finished work, including already-paid SerpApi searches, and resumes where it stopped. Business rows are always written to the checkpoint CSV before a unit of work is marked done, so a crash never loses data it claimed to finish. To force a full fresh run, delete `data/scraper_progress.json`.

### Skipping businesses already in the directory

With `SKIP_KNOWN_BUSINESSES` on (the default), the scraper reads the existing `businesses` table from Supabase at startup and uses it to avoid re-work. It skips scanning any URL whose domain is already a known business website, which saves both the page fetch and the Claude extraction for that page. It also drops exact name plus website matches from the output, so the v2 CSV is a clean list of new candidates rather than a re-run of what you already have. Social hosts are never blanket-skipped, only the exact known profile URL, so a new Instagram business on the same platform still gets scanned. Fuzzy near-duplicate matching is deliberately left to `prepare.py`, which already handles it with rapidfuzz, so the scraper never risks dropping a genuinely new business on a name coincidence.

This needs read access to Supabase. Add to `.env`:

```
SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co
SUPABASE_KEY=<the publishable key already used in index.html>
```

The publishable key allows public reads under the existing RLS SELECT policy, so this is the same access level the live site already uses. If the keys are absent, the scraper prints a notice and proceeds without the optimization. The run ends with a count of how many URLs were skipped as already in the directory.

### Output files

| File | Purpose |
|---|---|
| `data/businesses_scraped.csv` | Main result, 6-column schema, consumed by prepare.py |
| `data/businesses_scraped_sources.csv` | Audit-only log of where each row came from (google_maps, organic, social, directory_api). Does not enter the pipeline. |
| `data/businesses_scraped_checkpoint.csv` | Rolling save during the run |
| `data/scraper_progress.json` | Phase and step resume state |
| `data/cache/` | Cached HTML, Maps responses, and extractions |

### SerpApi limits and clean halting

SerpApi calls distinguish a real failure from a genuine empty result. A quota-exhausted, auth, or persistent rate-limit error halts the run cleanly and does not mark the failed query done, so re-running resumes exactly where it stopped. A genuine no-results response is treated as empty and the run continues. `MAX_SEARCHES_PER_RUN` (default 1500) is a hard per-run ceiling that makes a runaway loop impossible; it is per-invocation, separate from the monthly plan limit. A full statewide pass is about 690 searches. Plan note: the per-hour throughput matters as much as the monthly total. On plans at or below 200 searches/hour, the default 3 to 5 second pacing exceeds the hourly cap and will trigger rate limits, so either raise the delay or run in smaller batches; plans at 1000/hour and above clear the default pace comfortably.

### Caveats to check before uploading

- Maps results are kept only when Google's own self-identified ownership attribute is present in the result, and they are tagged from that attribute, not from the search query. This is the fix for the v2 first-run problem where chains and popular nearby businesses (QDOBA, Walmart, anything with "Black" in the name) were being mislabeled. Coverage depends on owners having set the attribute, so if a run keeps very few Maps businesses, attribute coverage is thin and you can set MAPS_VERIFY_LEADS_VIA_WEBSITE to True, which sends attribute-less Maps results with a website into the Phase 4 evidence check rather than trusting them. Raw Maps responses are cached under data/cache/maps so re-runs and detection tweaks cost no SerpApi searches.
- The Google Maps response shape on SerpApi shifts occasionally. If `local_results` comes back empty, verify the engine parameters against current SerpApi docs.
- Haiku is the extraction model. If ownership-type judgment proves unreliable on some sources, route only those to Sonnet.
---

## 20. Change Log

### June 2026 — Search rebuild, live dedupe, categorization cleanup

Three issues were reported: short/punctuated searches returned nothing, the table had duplicates, and categories were inconsistent so groceries were unfindable. All three were resolved.

**Search.** Rebuilt `search_businesses` and `suggest_search` (see Section 5). Root cause was whole-string `similarity()` diluting short queries plus literal substring matching that an apostrophe could break ("chois" vs "Choi's"). Fix normalizes punctuation on both sides, matches across name + services + industry, and uses `word_similarity()` for fuzzy fallback. These live in Supabase, not the HTML.

**Duplicates.** Built `pipeline/dedupe_live.py`. The live table had ~76 exact-name duplicate clusters, largely from the two intake lanes (web scrape vs certification reconcile) inserting the same business with differently formatted names and addresses. Removed 77 rows; survivors keep the best address and the real business website over directory placeholders. Row count went from a post-merge 1,871 down to 1,794. (The 1,264 in older docs predated the last merge.)

**Categorization.** The strays ("Construction", "Supplier", "Services", etc.) came from the certification/scrape lane, not enrich.py, whose prompt was already pinned to the 23 categories. Root problem: both enrich passes only touched NULL rows, so existing bad/thin/off-list values were never repaired. Fixed off-list labels with deterministic SQL renames, then reclassified the catch-all buckets (Retail and E-Commerce, Other Professional Services) — 127 of 328 rows moved, every grocery/market correctly landing in Food and Beverage. A handful of model misses were hand-corrected (Hopkinsville Black Market, MELANnaire Marketplace, El Papeleo). Then `--reenrich-groceries` rewrote grocery/market descriptions to lead with the searchable type, since fixing the category alone does not make the free-text word "groceries" match.

**Known follow-ups (not blocking):**
- Some certification-lane rows still carry raw NAICS-code text in `services_products` (e.g. First Choice Commercial Services). `--reenrich-services` pointed at those rows would clean them.
- A few `.0` ZIP artifacts (e.g. "47111.0") came from a ZIP-read-as-float in prepare.py or upload; worth fixing at the source so new rows stop arriving that way.
- `scrape.py` mismapped a column on at least one run (an ownership descriptor landed in an address field); watch for it.
- A phone-based near-duplicate scan could catch dupes that exact-name grouping misses (different punctuation in the name).
