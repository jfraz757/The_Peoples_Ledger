# The People's Ledger
### Kentucky Underrepresented Business Directory
**Live at [thepeoplesledger.net](https://thepeoplesledger.net)**
### Open-source data infrastructure by Joe Frazier, Education to Action LLC

Money Talks. Spend Where It Counts.

---

## Background

From 2021 to 2023, I served as Founding Executive Director of the Kentucky Chamber Foundation's Center for Diversity, Equity, and Inclusion. In that role, I built Kentucky's first statewide minority-owned business database, MBDKY.com, in partnership with the Kentucky Finance and Revenue Cabinet, the Kentucky Transportation Cabinet, the Louisville Human Relations Commission, and Interapt, a minority-owned tech firm. I coined the phrase "Minority Owned, Kentucky Grown." The platform aggregated certified MBE data from across the Commonwealth into a single searchable directory, the first of its kind.

Supplier diversity programs were built for procurement. To get certified, to get listed, to get found in a government database, a business had to already be inside the system, chasing contracts, navigating paperwork, and meeting thresholds most small businesses never hear about. The programs were not wrong. But they were never designed to talk to everyday people. They were designed to talk to purchasing officers. Most underrepresented businesses in Kentucky were invisible in those systems before the anti-DEI movement started dismantling them. They remain invisible now.

The People's Ledger was built for everyone else.

I am an Applied Sociologist and DEI Strategist, not a software engineer. This codebase was built through a collaborative, iterative process between me and two AI systems, Claude (Anthropic) and Gemini (Google), bouncing drafts back and forth, pressure-testing logic, catching bugs, and refining the pipeline until it worked. That process is documented here because it matters: this is what community-centered, mission-first technology development looks like when the person driving it leads with purpose and uses every available tool to get there.

If the work is too political, it is because it is too honest.

---

## What This Is

A full-stack directory of underrepresented businesses in Kentucky: a live, searchable, public-facing database built on open-source tooling and community data. It is designed to operate independently of government certification systems, which were never built to serve everyday consumers in the first place.

The directory currently contains roughly 1,264 verified, deduplicated business records spanning 23 industry categories and 10 ownership types. It is live, searchable, and exportable by anyone.

---

## Architecture

### Two intake lanes, one database

Businesses enter through two separate lanes that both end at the Supabase `businesses` table. They use different tooling on purpose and are never mixed.

```
Lane 1: Web discovery (quarterly)
  scrape.py  ->  prepare.py  ->  [human review]  ->  upload  ->  enrich  ->  maintain  ->  SEO pages

Lane 2: Certification spreadsheets (as agencies refresh)
  manual download  ->  reconcile_certifications.py  ->  businesses table
```

The live website reads from Supabase at runtime. It is a static site with no build step, so none of the Python tooling or working data is part of what gets deployed.

### Database schema

**businesses table**

| Column | Type | Notes |
|---|---|---|
| id | BIGINT | Auto-generated primary key |
| business_name | TEXT | Business name |
| address | TEXT | Full address |
| phone | TEXT | Phone number |
| services_products | TEXT | Services or products offered (filled by Claude where missing) |
| website | TEXT | Business website URL |
| minority_type | TEXT | Ownership category or categories, comma-separated |
| industry | TEXT | Standardized industry category, one of 23 |
| status | TEXT | Active, Inactive, or No Website (reflects the link check) |
| kentucky_based | TEXT | Yes or No |
| certification_type | TEXT | DBE, MBE, WBE, and similar, comma-separated; from lane 2 |

**submissions table**

| Column | Type | Notes |
|---|---|---|
| id | BIGINT | Auto-generated primary key |
| submission_type | TEXT | "new" or "update" |
| business_name | TEXT | Business name submitted |
| address, phone, website, services_products, minority_type, kentucky_based | TEXT | New business fields |
| update_notes | TEXT | Correction details for existing listings |
| submitter_name | TEXT | Who submitted it |
| submitter_email | TEXT | Contact for follow-up, never displayed publicly |
| status | TEXT | pending, approved, or rejected |
| submitted_at | TIMESTAMP | Auto-set on insert |

---

## Website Features

- **Search** by business name, city or area, and service or product simultaneously
- **Fuzzy search** powered by PostgreSQL pg_trgm, which handles typos and misspellings
- **"Did You Mean...?"** suggestions when a search returns zero results
- **Filter by ownership type**: Black-Owned, Women-Owned, Latine-Owned, Asian-Owned, LGBTQ+-Owned, Veteran-Owned, Native American-Owned, Disability-Owned, and Muslim-Owned
- **Filter by industry** across 23 standardized categories assigned by Claude
- **Business cards** with favicon, ownership type badge, address, phone, services, and link status
- **Export** full or filtered results to CSV or JSON
- **Submit a Business or Correction** through a public form with two tabs: a full profile for new businesses, and a free-text correction for existing listings
- **Mobile responsive**

---

## The Data Pipeline

Everything lives in `pipeline/`. Scripts derive their data folder from their own location and load `.env` from the repo root, so there are no hardcoded absolute paths anywhere in committed code. Run them from the repo root.

### Lane 1: Web discovery

**`scrape.py`** is the web-discovery engine. It runs a SerpApi Google Maps phase that pulls structured name, address, phone, and website with no page fetch, keeping a Maps result only when Google's own self-identified ownership attribute is present and tagging it from that attribute. It then runs targeted organic and social searches across statewide Kentucky cities, fetches candidate pages, and extracts structured fields with Claude (`claude-haiku-4-5`). It caches HTML, Maps responses, and extractions to disk, resumes from a progress file, and skips businesses already in the directory. Output lands in `data/businesses_scraped.csv`.

**`prepare.py`** turns a raw scrape into one reviewable file, `data/businesses_prepared.csv`, with a Disposition column: Good to go, Needs review, or Dropped. It filters out chains and out-of-state addresses, holds address-less names for human review, and deduplicates the keepers using field-level merge logic (most complete address wins, most specific ownership type wins, and so on). This replaces the earlier separate triage and cleaning steps.

**`upload_to_supabase.py`** inserts only the rows you marked Good to go, in batches of 100. It needs a Supabase key with insert rights, the service role key, which is distinct from the read-only publishable key used by the scraper and the live site.

**`enrich.py`** runs two post-upload passes with Claude (`claude-sonnet-4-6`): it assigns an industry category, then writes a brief services description for records that lack one. Industry runs first because the services prompt uses it. Both passes skip records that already have the field, so re-running is safe.

**`maintain.py`** re-checks every website and sets status to Active, Inactive, or No Website. With `--buyblack` it also resolves buyblack.org placeholder URLs to a real site or Instagram using SerpApi, which is gated behind the flag because it costs searches.

**`view_database.py`** opens any `data/` CSV in D-Tale for spot-checks.

### Lane 2: Certification spreadsheets

The Louisville HRC, Kentucky Transportation, and Kentucky Finance certification lists are CAPTCHA-protected manual downloads. They are the only source of the `certification_type` field, and they reach formally certified businesses that the web crawl never surfaces. They must not go through `prepare.py`, which would strip authoritative records as if they were noise. Instead they are reconciled against the live table with fuzzy matching, certification data is filled on matches, and genuinely new businesses are inserted. (`reconcile_certifications.py` is not built yet.)

---

## Setup

**1. Clone the repository**
```
git clone https://github.com/jfraz757/The_Peoples_Ledger.git
cd The_Peoples_Ledger
```

**2. Install dependencies**
```
pip install requests beautifulsoup4 pandas anthropic google-search-results python-dotenv supabase dtale
```

**3. Configure API keys**

Copy `.env.example` to `.env` in the repo root and fill in your keys:
```
SERPAPI_KEY=your_serpapi_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_key
```

- SerpApi: [serpapi.com](https://serpapi.com), 100 free searches per month on the free tier
- Anthropic: [console.anthropic.com](https://console.anthropic.com), pay-as-you-go
- Supabase: [supabase.com](https://supabase.com), free tier supports up to 500MB

**4. Run lane 1, from the repo root**
```
python pipeline/scrape.py
python pipeline/prepare.py
# review data/businesses_prepared.csv, flip keepers to "Good to go"
python pipeline/upload_to_supabase.py
python pipeline/enrich.py
python pipeline/maintain.py
node generate-business-pages.js
```

**5. Open the directory**

Open `index.html` in any browser, or visit the live site.

**6. Review submissions (local only)**

Open `admin.html` locally. It is gitignored and never pushed to GitHub.

---

## Cost Estimate

`scrape.py` extracts with Claude Haiku and pulls a large share of records from the Maps phase with no Claude call at all, so per-run extraction cost is low. `enrich.py` uses Claude Sonnet and runs roughly $0.75 to $1.00 per 1,000 records categorized. SerpApi's free tier of 100 searches per month covers light use; a full statewide scrape is larger and needs a paid plan. The scraper prints its projected search count before spending anything.

---

## Maintenance Schedule

| Task | Command | Frequency |
|---|---|---|
| Add new businesses | `python pipeline/scrape.py` | Quarterly |
| Prepare (filter and dedupe) | `python pipeline/prepare.py` | After each scrape |
| Upload approved rows | `python pipeline/upload_to_supabase.py` | After review |
| Enrich (industry and services) | `python pipeline/enrich.py` | After upload |
| Regenerate SEO pages | `node generate-business-pages.js` | After upload (quarterly) |
| Refresh link statuses | `python pipeline/maintain.py` | Monthly |
| Fix buyblack URLs | `python pipeline/maintain.py --buyblack` | As needed |
| Reconcile certification lists | `python pipeline/reconcile_certifications.py` | As agencies refresh |

---

## Security

API keys are loaded from a local `.env` file excluded from version control through `.gitignore`. Never commit your `.env`. The entire `data/` folder is gitignored as well, so no scraped records or caches reach the repo.

The Supabase `businesses` table has Row Level Security enabled. Public read access is granted through explicit RLS policies. Bulk uploads and admin inserts use the service role key, which is kept out of the repo. The publishable key is intentionally present in `index.html`, which is the correct and documented model for browser-facing Supabase applications: a read-only publishable key paired with RLS policies. The admin panel, `admin.html`, is excluded from version control entirely and exists only on local machines.

---

## Known Limitations

- Ownership detection in `scrape.py` is source-dependent. Google Maps results are kept only when Google's self-identified ownership attribute is present and are tagged from it; organic and social results are tagged from ownership language in the page text. Badges and images are not read.
- The national certification directories (NMSDC, WBENC, NGLCC) are membership-gated paid databases aimed at procurement, not a consumer audience, and scraping them is against their terms. They are intentionally out of scope. Free directories that render through JavaScript are handled by locating their data endpoint rather than by adding a headless browser.
- The database reflects what is publicly visible on the open web at the time of each run. It is not a substitute for certified MBE data and should not be used for procurement compliance without independent verification.
- `minority_type` and `certification_type` store multiple values as comma-separated strings. A future version would normalize these into junction tables for more reliable filtering.

---

## How This Was Built

I want to be transparent about the development process, because it reflects a broader point about who gets to build technology and how.

I started with a baseline script from Gemini and brought it to Claude for structural improvements. Claude rewrote the architecture, added the SerpApi and Anthropic API integrations, expanded the search query list, built in checkpoint saving, and added .env-based key protection. I then brought Gemini's later suggestions back to Claude for review. Some were worth keeping, like the directory harvesting function and the deep link crawler, and Claude integrated both while flagging where Gemini's version had quietly downgraded the script by dropping Claude extraction, cutting the query list, and reverting to free Google scraping that gets rate-limited.

The database migration, cleaning pipeline, Supabase integration, full directory website, admin panel, and all search and submission infrastructure were built iteratively with Claude across subsequent sessions. A later reorganization consolidated the pipeline into the structure documented above. The result reflects the best contributions from both systems, stress-tested and integrated by someone who knew what the data needed to do in the real world.

That is the model I bring to all of my work: gather the best available input, evaluate it honestly, integrate what serves the mission, and name what does not.

---

## Why This Matters

Corporations spend billions influencing the politics that shape your community, funding candidates, backing litigation, and lobbying against the programs designed to create equity in the marketplace. Your spending is just as political, whether you think of it that way or not. Every dollar you spend is a vote for what kind of economy you want to live in. The only question is whether you cast it deliberately.

Supplier diversity programs were built for procurement, not people. The businesses that did not qualify, did not apply, or simply were not trying to do business with the state were invisible in those systems. This directory exists to make them visible, to consumers, neighbors, and communities looking to put their dollars where their values are.

I built MBDKY.com because the data did not exist in one place. I built The People's Ledger because the audience that needed it most was never the target of what came before. The through-line is the same conviction I have carried from Charlottesville to Louisville: the infrastructure for equity should not depend on political will at the state level to survive. When institutions pull back, the community builds forward.

This tool is open-sourced intentionally, so that other states, other organizers, and other practitioners can adapt it for their own contexts. The code is the easy part to replicate. The harder thing to replicate is knowing why it needs to exist in the first place.

Money talks. Spend where it counts.

The most important step is always the next one.

---

## Contributing

Pull requests are welcome. If you adapt this for another state, add support for JavaScript-rendered directories, or build on the submission and admin infrastructure, please open a PR and document what you changed and why. Community-maintained is the only kind of infrastructure that outlasts any one administration.

---

## Credits and Acknowledgements

**AI Systems**
- [Claude (Anthropic)](https://anthropic.com): architecture, SerpApi integration, Claude extraction pipeline, database migration, website build, admin panel, search infrastructure, the pipeline reorganization, and iterative debugging throughout
- [Gemini (Google)](https://gemini.google.com): contributed the directory harvesting concept and deep link crawling approach during early iterative development

**APIs and Data Services**
- [SerpApi](https://serpapi.com): Google search interface for business discovery
- [Supabase](https://supabase.com): PostgreSQL database, REST API, and Row Level Security
- [Anthropic Claude API](https://console.anthropic.com): structured field extraction and industry categorization

**Python Libraries**
- [BeautifulSoup4](https://pypi.org/project/beautifulsoup4/): HTML parsing
- [pandas](https://pandas.pydata.org): data structuring and CSV processing
- [python-dotenv](https://pypi.org/project/python-dotenv/): API key management
- [D-Tale](https://github.com/man-group/dtale): interactive data exploration
- [requests](https://pypi.org/project/requests/): HTTP client

**Frontend**
- [Google Fonts, Michroma](https://fonts.google.com/specimen/Michroma): display typography
- [Google Favicon Service](https://www.google.com/s2/favicons): business favicons
- [PostgreSQL pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html): fuzzy search and trigram similarity

---

## Author

**Joe Frazier**
Applied Sociologist, DEI Strategist, Founder, Education to Action LLC
Louisville, KY | [educationtoaction.net](https://educationtoaction.net) | [linkedin.com/in/jfraz1](https://linkedin.com/in/jfraz1)

**The People's Ledger** is live at [thepeoplesledger.net](https://thepeoplesledger.net)
Open source at [github.com/jfraz757/The_Peoples_Ledger](https://github.com/jfraz757/The_Peoples_Ledger)

Built collaboratively with Claude (Anthropic) and Gemini (Google), 2026.
