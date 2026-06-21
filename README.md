# The People's Ledger
### Kentucky Minority Business Directory
**Live at [thepeoplesledger.net](https://thepeoplesledger.net)**
### Open-source data infrastructure by Joe Frazier / Education to Action LLC

---

## Background

From 2021 to 2023, I served as Founding Executive Director of the Kentucky Chamber Foundation's Center for Diversity, Equity, and Inclusion. In that role, I built Kentucky's first statewide minority-owned business database -- MBDKY.com -- in partnership with the Kentucky Finance and Revenue Cabinet, the Kentucky Transportation Cabinet, the Louisville Human Relations Commission, and Interapt, a minority-owned tech firm. I coined the phrase "Minority Owned, Kentucky Grown." The platform aggregated certified MBE data from across the Commonwealth into a single searchable directory, the first of its kind.

That infrastructure no longer exists. MBDKY.com is offline. The state agencies that once published minority business certification data with ownership type included have removed that field from public-facing records -- a casualty of a racist political movement that chose erasure over equity.

The data didn't disappear. We rebuilt it.

I'm an Applied Sociologist and DEI Strategist, not a software engineer. This codebase was built through a collaborative, iterative process between me and two AI systems -- Claude (Anthropic) and Gemini (Google) -- bouncing drafts back and forth, pressure-testing logic, catching bugs, and refining the pipeline until it worked. That process is documented here because it matters: this is what community-centered, mission-first technology development looks like when the person driving it leads with purpose and uses every available tool to get there.

If the work is too political, it is because it is too honest.

---

## What This Is

A full-stack minority business directory for the Commonwealth of Kentucky -- a live, searchable, public-facing database of minority-owned businesses built on open-source tooling and community data. It is designed to operate independently of state government data sources, which are no longer reliable for this purpose.

The directory currently contains **1,191 verified, deduplicated business records** spanning 23 industry categories and 10 minority ownership types. It is live, searchable, and exportable by anyone.

---

## Current State of the Project

As of June 2026, this project has moved through four distinct phases:

**Phase 1 -- Data Collection**
The scraper collected raw business records across all minority ownership categories statewide using SerpApi for search discovery and Claude API for AI-powered structured field extraction. Directory harvesting and deep link crawling expanded the record set beyond what search alone would produce.

**Phase 2 -- Data Cleaning**
A dedicated cleaning script merged 48 duplicate entries using field-level logic: most complete address wins, most specific minority type wins, Active status preferred over Inactive, Kentucky-based preferred where conflicting. 1,240 raw records became 1,191 clean, deduplicated entries.

**Phase 3 -- Database Migration**
The cleaned dataset was migrated from a flat CSV into a live Supabase (PostgreSQL) database -- a hosted relational backend with a REST API. The CSV served its purpose. The database is now home base.

**Phase 4 -- Public-Facing Directory**
A full website was built on top of the Supabase backend: search by name, city, and service; filter by ownership type and industry; fuzzy typo-tolerant search powered by PostgreSQL trigram matching; "Did You Mean...?" suggestions on zero-result searches; business favicons; export to CSV and JSON; and a public submission form for new businesses and corrections.

---

## Architecture

### Full Stack

```
Web Scraper
    |
    v
Raw CSV (checkpoint + final output)
    |
    v
Cleaning Script
    |
    v
Cleaned CSV
    |
    v
Supabase Upload Script
    |
    v
Live Supabase PostgreSQL Database
    |
    v
Public Directory Website (index.html)
    |
    v
Admin Panel (admin.html -- local only, never pushed to GitHub)
```

### Database Schema

**businesses table**

| Column | Type | Notes |
|---|---|---|
| id | BIGINT | Auto-generated primary key |
| business_name | TEXT | Business name |
| address | TEXT | Full address |
| phone | TEXT | Phone number |
| services_products | TEXT | Services or products offered |
| website | TEXT | Business website URL |
| minority_type | TEXT | Ownership category or categories |
| industry | TEXT | Standardized industry category (23 categories) |
| status | TEXT | Link Active, Link Inactive, or No Website |
| kentucky_based | TEXT | Yes or No |

**submissions table**

| Column | Type | Notes |
|---|---|---|
| id | BIGINT | Auto-generated primary key |
| submission_type | TEXT | "new" or "update" |
| business_name | TEXT | Business name submitted |
| address, phone, website, services_products, minority_type, kentucky_based | TEXT | New business fields |
| update_notes | TEXT | Correction details for existing listings |
| submitter_name | TEXT | Who submitted it |
| submitter_email | TEXT | Contact for follow-up |
| status | TEXT | pending, approved, or rejected |
| submitted_at | TIMESTAMP | Auto-set on insert |

---

## Website Features

- **Search** by business name, city or area, and service or product simultaneously
- **Fuzzy search** powered by PostgreSQL pg_trgm -- handles typos and misspellings
- **"Did You Mean...?"** suggestions when a search returns zero results
- **Filter by ownership type** -- Black-Owned, Women-Owned, Latine-Owned, Asian-Owned, LGBTQ+-Owned, Veteran-Owned, Native American-Owned, Disability-Owned, Muslim-Owned
- **Filter by industry** -- 23 standardized categories assigned by Claude API
- **Business cards** with favicon, ownership type badge, address, phone, services, and link status
- **Export** full results to CSV or JSON -- filtered results export too, not just the full database
- **Submit a Business or Correction** -- public form with two tabs: full profile for new businesses, free-text correction for existing listings
- **Mobile responsive**

---

## Scripts

### `ky_minority_business_scraper.py`
The core data collection engine. Runs in three phases: directory harvesting (visiting known MBE directories and extracting individual business links), SerpApi search discovery (29 targeted queries across all ownership categories), and Claude-powered AI extraction (structured field parsing from live web pages, with deep link crawling to About and Contact subpages when homepages lack ownership language).

### `clean_ky_businesses.py`
One-time and post-scraper data cleaning. Merges duplicate records using field-level merge logic and outputs a deduplicated CSV ready for upload.

**Merge logic:** Address -- longest non-null wins. Phone -- first non-null wins. Services -- longest non-null wins. Website -- first non-null wins. Minority Type -- most specific wins; "Minority-Owned (general)" is deprioritized when a more specific category exists. Status -- Active over Inactive over null. Kentucky Based -- Yes over No over null.

### `upload_to_supabase.py`
Loads cleaned CSV into the Supabase businesses table in batches of 100 records. Credentials loaded from .env. Run once after cleaning, or again after a new scraper collection to push fresh records.

### `categorize_industries.py`
Uses Claude API to assign a standardized industry category to each business based on name and services field. Processes only records with null industry values, so interrupted runs resume cleanly.

**23 categories:** Accounting and Finance, Architecture and Engineering, Arts and Entertainment, Business Consulting, Construction and Remodeling, Education and Training, Food and Beverage, Government and Public Services, Health and Wellness, Human Resources and DEI, Information Technology, Insurance and Risk Management, Legal Services, Logistics and Transportation, Manufacturing and Industrial, Marketing and Communications, Media and Publishing, Non-Profit and Social Services, Real Estate and Property Management, Retail and E-Commerce, Staffing and Recruiting, Travel and Hospitality, Other Professional Services.

### `fill_missing_services.py`
Uses Claude API to generate brief service descriptions for records with null services_products fields. Uses business name, industry, and address as context. Does not fabricate -- keeps descriptions accurate to what the name and category can reasonably support.

### `check_link_status.py`
Re-checks every business website URL and updates the status field in Supabase. Run monthly to keep link statuses current. No API cost -- pure HTTP requests.

### `fix_buyblack_urls.py`
Identifies records where the website URL points to buyblack.org root (a directory placeholder) and searches for the business's real website or Instagram using SerpApi. Updates Supabase with the real URL where found.

### `view_database.py`
Opens the database CSV in D-Tale for interactive browser-based exploration. Useful for data quality spot-checks before upload.

---

## How the Scraper Works

**Phase 1 -- Directory Harvesting**
Visits known minority business directories and extracts individual business links rather than treating directory pages as single units. Each listing is queued as its own data source. This approach -- first introduced by Gemini during the iterative build -- is more thorough than page-level scraping.

**Phase 2 -- Search Engine Discovery**
Uses SerpApi to run 29 targeted Google search queries covering all major minority ownership categories across Louisville, Lexington, and statewide Kentucky. Quoted phrase searches ensure precision rather than loose relevance matching. SerpApi eliminates the rate limiting and IP blocking that killed earlier free-tier versions of this script.

**Phase 3 -- AI-Powered Extraction**
Each URL is visited directly. HTML is stripped of navigation and boilerplate, then sent to Claude (Anthropic) for structured field extraction. Claude reads each page the way a researcher would -- pulling business name, contact information, services, and minority ownership type from context rather than rigid pattern matching. If a homepage lacks ownership language, the script automatically checks About, Story, Mission, and Contact subpages before moving on. Deep link crawling was first introduced by Gemini and refined into the final pipeline.

Results are deduplicated, sorted alphabetically, and saved to CSV. Progress checkpoints every 5 businesses.

---

## How This Was Built

I want to be transparent about the development process because it reflects a broader point about who gets to build technology and how.

I started with a baseline script from Gemini. I brought it to Claude for structural improvements -- Claude rewrote the architecture, added the SerpApi and Anthropic API integrations, expanded the search query list from 4 to 29 queries, built in checkpoint saving, added .env-based key protection, and hardcoded the output path so the CSV always saves in the right place.

I then brought Gemini's subsequent suggestions back to Claude for review. Some of Gemini's ideas were worth keeping -- the directory harvesting function and the deep link crawler. Claude integrated both while flagging where Gemini's version had quietly downgraded the script: dropping Claude extraction, cutting the query list, removing the .env security layer, reverting to free Google scraping that gets rate-limited.

The database migration, cleaning pipeline, Supabase integration, full directory website, admin panel, and all search and submission infrastructure were built in a subsequent session in June 2026, again iteratively with Claude.

The result reflects the best contributions from both systems, stress-tested and integrated by someone who knew what the data needed to do in the real world.

That's the model I bring to all of my work: gather the best available input, evaluate it honestly, integrate what serves the mission, and name what doesn't.

---

## Tech Stack

- **Python 3.14** -- core scripting language
- **SerpApi** -- search engine interface; eliminates Google rate limiting
- **Anthropic Claude API (claude-sonnet-4-6)** -- AI-powered structured extraction and industry categorization
- **Gemini (Google)** -- contributed directory harvesting and deep link crawling concepts during iterative development
- **BeautifulSoup4** -- HTML parsing and page text cleaning
- **pandas** -- data structuring and CSV processing
- **Supabase (PostgreSQL)** -- hosted relational database, REST API backend, and Row Level Security
- **PostgreSQL pg_trgm** -- trigram-based fuzzy search with similarity scoring
- **python-dotenv** -- secure API key management
- **D-Tale** -- interactive browser-based data exploration

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

Copy `.env.example` to `.env` and fill in your keys:
```
SERPAPI_KEY=your_serpapi_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_publishable_key
```

- SerpApi: [serpapi.com](https://serpapi.com) -- 100 free searches/month on the free tier
- Anthropic: [console.anthropic.com](https://console.anthropic.com) -- pay-as-you-go; approximately $0.75-1.00 per full categorization run
- Supabase: [supabase.com](https://supabase.com) -- free tier supports up to 500MB database storage

**4. Run the pipeline**
```
python ky_minority_business_scraper.py
python clean_ky_businesses.py
python upload_to_supabase.py
python categorize_industries.py
python fill_missing_services.py
```

**5. Open the directory**

Open `index.html` in any browser.

**6. Review submissions (local only)**

Open `admin.html` locally. This file is in `.gitignore` and is never pushed to GitHub.

---

## Cost Estimate

**Scraper cost (Anthropic API)**

| Database Size | Pages Scanned | Estimated Cost |
|---|---|---|
| 100 businesses | ~300 pages | ~$0.90 |
| 500 businesses | ~1,500 pages | ~$4.50 |
| 1,000 businesses | ~3,000 pages | ~$9.00 |

**Industry categorization (Anthropic API)**

| Records | Estimated Cost |
|---|---|
| 1,000 records | ~$0.75-$1.00 |

SerpApi's free tier (100 searches/month) covers approximately 3 full scraper runs per month.

---

## Maintenance Schedule

| Task | Script | Frequency |
|---|---|---|
| Add new businesses | `ky_minority_business_scraper.py` | Quarterly |
| Clean and deduplicate | `clean_ky_businesses.py` | After each scraper run |
| Upload to Supabase | `upload_to_supabase.py` | After cleaning |
| Categorize new records | `categorize_industries.py` | After upload |
| Fill missing services | `fill_missing_services.py` | After upload |
| Refresh link statuses | `check_link_status.py` | Monthly |
| Fix directory placeholders | `fix_buyblack_urls.py` | As needed |

---

## Security

API keys are loaded from a local `.env` file excluded from version control via `.gitignore`. Never commit your `.env` file to GitHub.

The Supabase `businesses` table has Row Level Security (RLS) enabled. Public read, insert, and update access is granted via explicit RLS policies. The admin panel (`admin.html`) is excluded from version control entirely and exists only on local machines.

The Supabase publishable key is intentionally hardcoded in `index.html` -- this is the correct and documented approach for browser-facing Supabase applications. The publishable key paired with RLS policies is the designed security model for public-facing Supabase projects.

---

## Known Limitations

- Minority type detection depends on explicit ownership language appearing in page text. Businesses that self-identify through badges or images rather than written copy may not be captured.
- Several national directories (NMSDC, WBENC, NGLCC) render listings via JavaScript and cannot be accessed with standard HTTP requests. Selenium or Playwright would unlock these -- a future enhancement.
- The database reflects what is publicly visible on the open web at the time of each scraper run. It is not a substitute for certified MBE data and should not be used for procurement compliance without independent verification.
- The `minority_type` field stores multiple categories as comma-separated strings. Future versions will normalize this into a junction table for more reliable filtering.

---

## Why This Matters

The removal of minority type data from Kentucky's state agency databases didn't happen by accident. It is part of a coordinated national rollback of DEI infrastructure that has made it harder for minority-owned businesses to be found, for corporations to diversify their supply chains, and for community organizations to connect resources to the people who need them most.

I built MBDKY.com because the data didn't exist in one place. I'm rebuilding it now because the data has been actively removed. The through-line is the same conviction I've carried from Charlottesville to Louisville: the infrastructure for equity shouldn't depend on political will at the state level to survive. When institutions pull back, the community builds forward.

This tool is open-sourced intentionally -- so other states, other organizers, and other DEI practitioners can adapt it for their own contexts. The code is the easy part to replicate. The harder thing to replicate is knowing why it needs to exist in the first place.

The most important step is always the next one.

---

## Contributing

Pull requests are welcome. If you adapt this script for another state, add support for JavaScript-rendered directories, or build on the submission and admin infrastructure, please open a PR and document what you changed and why. Community-maintained is the only kind of infrastructure that outlasts any one administration.

---

## Credits & Acknowledgements

**AI Systems**
- [Claude (Anthropic)](https://anthropic.com) -- architecture, SerpApi integration, Claude API extraction pipeline, database migration, website build, admin panel, search infrastructure, and iterative debugging throughout
- [Gemini (Google)](https://gemini.google.com) -- contributed directory harvesting concept and deep link crawling approach during early iterative development

**APIs and Data Services**
- [SerpApi](https://serpapi.com) -- Google search interface for business discovery
- [Supabase](https://supabase.com) -- PostgreSQL database, REST API, and Row Level Security
- [Anthropic Claude API](https://console.anthropic.com) -- structured field extraction and industry categorization

**Python Libraries**
- [BeautifulSoup4](https://pypi.org/project/beautifulsoup4/) -- HTML parsing
- [pandas](https://pandas.pydata.org) -- data structuring and CSV processing
- [python-dotenv](https://pypi.org/project/python-dotenv/) -- API key management
- [D-Tale](https://github.com/man-group/dtale) -- interactive data exploration
- [requests](https://pypi.org/project/requests/) -- HTTP client

**Frontend**
- [Google Fonts -- Michroma](https://fonts.google.com/specimen/Michroma) -- display typography
- [Google Favicon Service](https://www.google.com/s2/favicons) -- business favicons
- [PostgreSQL pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html) -- fuzzy search and trigram similarity

---

## Author

**Joe Frazier**
Applied Sociologist, DEI Strategist, Founder -- Education to Action LLC
Louisville, KY | [educationtoaction.net](https://educationtoaction.net) | [linkedin.com/in/jfraz1](https://linkedin.com/in/jfraz1)

**The People's Ledger** is live at [thepeoplesledger.net](https://thepeoplesledger.net)
Open source at [github.com/jfraz757/The_Peoples_Ledger](https://github.com/jfraz757/The_Peoples_Ledger)

*Built collaboratively with Claude (Anthropic) and Gemini (Google), May--June 2026.*
