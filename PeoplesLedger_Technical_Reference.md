# The People's Ledger — Technical Reference for Claude Sessions

**Purpose:** This document gives Claude full working context before making any changes to The People's Ledger. Read this before writing any code, editing any file, or making any Supabase recommendations. Every architectural decision here is intentional.

---

## 1. Project Overview

The People's Ledger is a free, public, searchable directory of underrepresented businesses in Kentucky. It was built to serve everyday consumers — not procurement officers — filling a gap that supplier diversity programs were never designed to address. The directory currently contains 1,191 verified, deduplicated records.

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
├── index.html                      # Main directory page — all user-facing features
├── about.html                      # About page with origin story and live business count
├── CNAME                           # thepeoplesledger.net
├── README.md                       # Comprehensive project documentation (committed)
├── env.example                     # Template for .env keys
├── .gitignore                      # Excludes .env, admin.html, CSV files
│
├── ky_minority_business_scraper.py # Core data collection engine
├── clean_ky_businesses.py          # Duplicate merger and data cleaner
├── upload_to_supabase.py           # CSV → Supabase bulk loader
├── categorize_industries.py        # Claude API industry classifier
├── fill_missing_services.py        # Claude API services gap filler
├── check_link_status.py            # Monthly website URL status checker
├── fix_buyblack_urls.py            # Replaces buyblack.org placeholder URLs
├── view_database.py                # Opens CSV in D-Tale for local exploration
├── data_gather_.ipynb              # Jupyter notebook for data exploration and prep
│
├── ky_minority_businesses.csv      # Raw collected data (gitignored)
├── ky_minority_businesses_cleaned.csv  # Cleaned deduplicated data (gitignored)
└── checkpoint_ky_minority_businesses.csv  # Scraper progress checkpoint (gitignored)
```

**admin.html is NOT in the repo.** Gitignored, runs locally only, never deployed. Contains hardcoded admin password.

**CSV files are gitignored** — they are working files, not deployment artifacts.

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

Returns records with a `total_count` field on each row for pagination. Uses `pg_trgm` for fuzzy matching.

### `suggest_search`
Called when a search returns zero results. Parameter:
- `search_text` — the failed query

Returns suggested alternative terms based on trigram similarity to existing business names and services. Powers the "Did You Mean...?" feature.

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

### Data pipeline flow (scraper → live)
```
ky_minority_business_scraper.py   → ky_minority_businesses.csv (raw)
clean_ky_businesses.py            → ky_minority_businesses_cleaned.csv
upload_to_supabase.py             → businesses table (Supabase)
categorize_industries.py          → fills industry column via Claude API
fill_missing_services.py          → fills services_products via Claude API
check_link_status.py              → updates status column (monthly)
fix_buyblack_urls.py              → fixes placeholder URLs (as needed)
```

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

---

## 10. Python Scripts Reference

All scripts use `.env` for credentials. Run from the project root. Python path: `C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe`

| Script | Purpose | Frequency | API Cost |
|---|---|---|---|
| `ky_minority_business_scraper.py` | Scrapes new businesses from web | Quarterly | ~$9/1000 records (Anthropic) + SerpApi |
| `clean_ky_businesses.py` | Deduplicates and merges CSV records | After each scraper run | Free |
| `upload_to_supabase.py` | Loads cleaned CSV to Supabase in batches of 100 | After cleaning | Free |
| `categorize_industries.py` | Assigns industry category via Claude API | After upload | ~$0.75-1.00/1000 records |
| `fill_missing_services.py` | Generates service descriptions via Claude API | After upload | Low |
| `check_link_status.py` | Re-checks all website URLs, updates status field | Monthly | Free |
| `fix_buyblack_urls.py` | Replaces buyblack.org placeholder URLs with real ones | As needed | SerpApi |
| `view_database.py` | Opens CSV in D-Tale browser explorer | As needed | Free |
| `data_gather_.ipynb` | Jupyter notebook for data prep and exploration | As needed | Free |

**Claude API model used in scripts:** `claude-sonnet-4-6`

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

**Sitemap:** `/businesses/sitemap.xml` lists every business page plus `index.html` and `about.html`. Submitted to Google Search Console June 24, 2026 — 1,266 pages discovered, Status: Success.
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

| Task | Script | Frequency |
|---|---|---|
| Add new businesses | `ky_minority_business_scraper.py` | Quarterly |
| Clean and deduplicate | `clean_ky_businesses.py` | After each scraper run |
| Upload to Supabase | `upload_to_supabase.py` | After cleaning |
| Categorize new records | `categorize_industries.py` | After upload |
| Fill missing services | `fill_missing_services.py` | After upload |
| Regenerate SEO pages | `generate-business-pages.js` | After upload (quarterly) |
| Refresh link statuses | `check_link_status.py` | Monthly |
| Fix directory placeholders | `fix_buyblack_urls.py` | As needed |

---

## 14. Known Limitations

- `minority_type` and `certification_type` are comma-separated strings, not normalized. Future refactor would use junction tables.
- Minority type detection in the scraper depends on ownership language appearing in page text — badges and images are not read.
- Several national directories (NMSDC, WBENC, NGLCC) use JavaScript rendering and can't be scraped with standard HTTP requests. Selenium/Playwright would be needed.
- This is not a substitute for certified MBE data for procurement compliance.
- The KY Transportation Cabinet and KY Finance & Administration Cabinet no longer include minority type fields in exports. The Louisville HRC database still does — it includes both `Ethnicity` and `Certification Type` columns and should be downloaded and merged on each quarterly data refresh.

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
git add PeoplesLedger_Technical_Reference.md
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
- [ ] Does a Python script need to be updated? Test with a small batch before running on the full 1,264 records
