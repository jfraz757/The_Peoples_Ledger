"""
Kentucky Minority-Owned Business Scraper  (v2)
==============================================
Searches the web statewide for minority-owned businesses in Kentucky and
extracts structured data, with a budget-first pipeline and full resume support.

WHAT CHANGED FROM v1 (diff against ky_minority_business_scraper.py):
  1.  Google Maps engine added (Phase 1). Structured name/address/phone/website
      straight from SerpApi local_results. No page fetch, no Claude call.
  2.  Extraction model switched Sonnet -> Haiku (EXTRACT_MODEL). Same JSON job,
      far lower per-record cost.
  3.  Listicle pages no longer truncated at 6000 chars. High-density pages are
      chunked and extracted in full so every business on a roundup is captured.
  4.  Directory harvesting now keeps INTERNAL profile links (e.g.
      /directory/business/123), not only outbound links. Deep-link discovery
      now reads the FULL page (nav/header/footer included) where About links live.
  5.  Optional JSON/XHR directory endpoints (DIRECTORY_API_ENDPOINTS) for the
      JS-rendered directories that return nothing to plain requests.
  6.  should_skip() now parses the host and matches exact/suffix domains, so it
      no longer drops valid sites like essex.com or commercelexington.com.
  7.  Statewide coverage. Organic and Maps searches run across STATEWIDE_CITIES,
      and organic search paginates (SEARCH_PAGES).
  8.  On-disk cache for fetched HTML and Claude extractions, so re-runs skip the
      fetch and skip re-paying for unchanged pages.

NEW IN THIS REVISION:
  A.  STATEWIDE. STATEWIDE_CITIES spans every region of Kentucky.
  B.  INSTAGRAM / FACEBOOK. Those domains are no longer skipped. Pages are read
      via their og: meta tags (which survive even a partial fetch), and optional
      site: searches (INCLUDE_SOCIAL_SEARCHES) surface business profiles directly.
  C.  PHASE-LEVEL RESUME. A progress file records exactly which Maps searches,
      directory harvests, organic searches, and URL scans have completed. If any
      phase fails, fix it and re-run: the script skips finished work (including
      already-paid SerpApi searches) and resumes where it stopped.
  D.  SKIP KNOWN BUSINESSES. With SKIP_KNOWN_BUSINESSES on, the scraper loads the
      existing Supabase directory at startup and skips any scan URL whose domain
      is already a known business website (no fetch, no Claude call), and drops
      exact name+website matches from the output. Needs SUPABASE_URL and
      SUPABASE_KEY in .env. Fuzzy de-duplication stays in clean_ky_businesses.py.
  E.  MAPS OWNERSHIP IS VERIFIED, NOT ASSUMED. A Maps result is kept only when
      Google's own self-identified ownership attribute is present, and it is
      tagged from that attribute, never from the search query. This stops nearby
      or popular non-minority businesses (chains, hardware stores, anything with
      "Black" in the name) from being mislabeled. Raw Maps responses are cached.
  F.  CLEAN HALT ON QUOTA, RATE LIMIT, OR AUTH FAILURE. SerpApi calls now tell a
      real failure apart from a genuine empty result. A quota/auth error or a
      rate limit that survives retries stops the run cleanly WITHOUT marking the
      failed query done, so a re-run resumes exactly there. A genuine no-results
      response is normal and does not halt. MAX_SEARCHES_PER_RUN is a hard
      per-run ceiling so a runaway loop can never drain the plan.

OUTPUT (renamed so you can compare against the v1 output):
  ky_minority_businesses_v2.csv             main result, same 6-column schema as v1
  ky_minority_businesses_v2_sources.csv     audit-only log of where each row came from
  checkpoint_ky_minority_businesses_v2.csv  rolling save
  scraper_progress_v2.json                  phase/step resume state
  cache_v2/                                 cached HTML + extractions

SETUP:
    pip install requests beautifulsoup4 pandas anthropic google-search-results python-dotenv
    Copy .env.example to .env, fill SERPAPI_KEY and ANTHROPIC_API_KEY.
    For the skip-known optimization, also add to .env:
        SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co
        SUPABASE_KEY=<the publishable key used in index.html>
    python ky_minority_business_scraper_v2.py

GITIGNORE additions (see the companion markdown file):
    cache_v2/
    scraper_progress_v2.json
    ky_minority_businesses_v2.csv
    checkpoint_ky_minority_businesses_v2.csv
    ky_minority_businesses_v2_sources.csv

SERPAPI BUDGET: statewide is not cheap. The script prints the projected search
count at startup so you can decide before it runs. Resume means a re-run never
repeats a completed search, so you only pay for searches once.
"""

import os
import json
import time
import random
import hashlib
import requests
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from anthropic import Anthropic
from dotenv import load_dotenv

# ---------------------------------------------
#  PATHS (portable: derived from this file's location, no hardcoded paths)
# ---------------------------------------------
PIPELINE_DIR      = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT         = os.path.dirname(PIPELINE_DIR)
DATA_DIR          = os.path.join(REPO_ROOT, "data")
OUTPUT_FILE       = os.path.join(DATA_DIR, "businesses_scraped.csv")
CHECKPOINT_FILE   = os.path.join(DATA_DIR, "businesses_scraped_checkpoint.csv")
SOURCES_LOG_FILE  = os.path.join(DATA_DIR, "businesses_scraped_sources.csv")
PROGRESS_FILE     = os.path.join(DATA_DIR, "scraper_progress.json")
CACHE_DIR         = os.path.join(DATA_DIR, "cache")

# ---------------------------------------------
#  LOAD KEYS FROM .env (repo root, so it is found regardless of working dir)
# ---------------------------------------------
load_dotenv(os.path.join(REPO_ROOT, ".env"))
SERPAPI_KEY       = os.getenv("SERPAPI_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Read-only public credentials, used to skip businesses already in the directory.
# These are the same publishable values used client-side in index.html.
SUPABASE_URL      = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY", "")

# ---------------------------------------------
#  TUNING
# ---------------------------------------------
CHECKPOINT_EVERY    = 5         # Save CSV every N businesses found
REQUEST_DELAY       = (2, 4)    # Seconds between page fetches
SEARCH_DELAY        = (3, 5)    # Seconds between SerpApi calls
REQUEST_TIMEOUT     = 12        # Seconds before giving up on a URL
RESULTS_PER_QUERY   = 10        # Google results per organic query page
SEARCH_PAGES        = 1         # Organic result pages per query (each page = 1 SerpApi search)
MAX_DEEP_LINKS      = 3         # Max subpages to check per business site
MAX_DIRECTORY_LINKS = 200       # Cap links harvested from any single directory page
CACHE_TTL_DAYS      = 30        # Re-fetch / re-extract a page only after it is this old

# Skip businesses already in the Supabase directory. When on, the scraper loads
# existing records at startup, skips scanning any URL whose domain is already a
# known business website (no fetch, no Claude call), and drops exact
# name+website matches from the output. Needs SUPABASE_URL and SUPABASE_KEY in
# .env. Fuzzy near-duplicate matching is left to clean_ky_businesses.py.
SKIP_KNOWN_BUSINESSES = True

# Hard backstop: stop the run before exceeding this many SerpApi searches in a
# single invocation, regardless of plan. A full statewide pass is about 690, so
# this leaves room for retries and social searches while making a runaway loop
# impossible. It is per-run, not cumulative; your monthly plan limit is separate.
MAX_SEARCHES_PER_RUN = 1500

# Extraction model. Haiku is fully adequate for pulling fields into JSON and is
# far cheaper than Sonnet at this volume. Dated string: claude-haiku-4-5-20251001
EXTRACT_MODEL       = "claude-haiku-4-5"

# Listicle handling: a long page with several ownership signals is treated as a
# roundup and chunked so we do not lose businesses past the first 6000 chars.
SINGLE_PAGE_CHARS       = 6000
LISTICLE_CHAR_THRESHOLD = 6000
CHUNK_SIZE              = 8000
CHUNK_OVERLAP           = 400
MAX_CHUNKS              = 6      # Hard ceiling so one huge page cannot run up the bill

# ---------------------------------------------
#  STATEWIDE COVERAGE
#  A spread of population centers and regional hubs across all of Kentucky.
#  Trim this list to cut SerpApi spend; extend it for finer coverage.
# ---------------------------------------------
STATEWIDE_CITIES = [
    "Louisville", "Lexington", "Bowling Green", "Owensboro", "Covington",
    "Florence", "Georgetown", "Richmond", "Elizabethtown", "Nicholasville",
    "Hopkinsville", "Frankfort", "Paducah", "Henderson", "Ashland",
    "Murray", "Somerset", "Madisonville", "London", "Pikeville",
    "Danville", "Winchester",
]
MAPS_CITIES    = STATEWIDE_CITIES
ORGANIC_CITIES = STATEWIDE_CITIES

# ---------------------------------------------
#  SOCIAL (Instagram / Facebook)
#  These domains are intentionally NOT skipped. Many small minority-owned
#  businesses live only on social. We read their og: meta tags and, optionally,
#  search the platforms directly with the site: operator.
# ---------------------------------------------
INCLUDE_SOCIAL_SEARCHES = True
SOCIAL_SEARCH_SITES     = ["instagram.com", "facebook.com"]
SOCIAL_DOMAINS          = ["instagram.com", "facebook.com"]

# ---------------------------------------------
#  MAPS OWNERSHIP VERIFICATION
#  A Google Maps search returns nearby and popular matches, not a verified
#  ownership list. We therefore NEVER tag a Maps result from the query. We only
#  keep a result when Google's own self-identified ownership attribute is present
#  in the result's extensions, and we tag it from that attribute. This is the
#  difference between a real Black-owned bakery and QDOBA showing up because it
#  is nearby. Raw Maps responses are cached so re-runs and logic tweaks are free.
MAPS_REQUIRE_ATTRIBUTE      = True
# Optional recall: send Maps results that have NO ownership attribute but DO have
# a website into the Phase 4 scan queue, where they must pass the same on-page
# evidence check as everything else before being added. Off by default to keep
# the corrected run fast and purely attribute-based. Turn on if attribute
# coverage proves too thin.
MAPS_VERIFY_LEADS_VIA_WEBSITE = False

# Google Business Profile self-identified ownership attributes, mapped to the
# directory's canonical labels. Matched only inside a result's extensions, never
# its title, so "Black Equipment" or "BlaCk OWned Outerwear" do not false-match.
GOOGLE_IDENTITY_ATTRIBUTES = [
    (("black-owned", "black owned"),                                   "Black-Owned"),
    (("latino-owned", "latino owned", "latina-owned", "latina owned"), "Latine-Owned"),
    (("women-owned", "women owned", "woman-owned", "woman owned"),     "Women-Owned"),
    (("veteran-owned", "veteran owned"),                               "Veteran-Owned"),
    (("lgbtq+ owned", "lgbtq owned", "lgbtq+-owned", "lgbtq-owned"),   "LGBTQ+-Owned"),
    (("asian-owned", "asian owned"),                                   "Asian-Owned"),
    (("disabled-owned", "disabled owned", "disability-owned"),         "Disability-Owned"),
    (("native american-owned", "native american owned",
      "indigenous-owned", "indigenous owned"),                         "Native American-Owned"),
]

# ---------------------------------------------
#  QUERY -> MINORITY TYPE PAIRS
#  Labels match the ownership filter pills in index.html exactly. This type also
#  tags Maps results (which carry no ownership text) and is the fallback for
#  organic pages where extraction returns no type.
# ---------------------------------------------
QUERY_TYPES = [
    ("black owned business",            "Black-Owned"),
    ("african american owned business", "Black-Owned"),
    ("black woman owned business",      "Black-Owned, Women-Owned"),
    ("hispanic owned business",         "Latine-Owned"),
    ("latino owned business",           "Latine-Owned"),
    ("latina owned business",           "Latine-Owned, Women-Owned"),
    ("asian owned business",            "Asian-Owned"),
    ("lgbtq owned business",            "LGBTQ+-Owned"),
    ("queer owned business",            "LGBTQ+-Owned"),
    ("women owned business",            "Women-Owned"),
    ("veteran owned business",          "Veteran-Owned"),
    ("native american owned business",  "Native American-Owned"),
    ("disability owned business",       "Disability-Owned"),
    ("muslim owned business",           "Muslim-Owned"),
    ("minority owned business",         "Minority-Owned (general)"),
]

# Known directory pages to harvest links from (HTML, scrapeable with requests)
DIRECTORY_URLS = [
    "https://www.commercelexington.com/minority-business-directory.html",
    "https://lul.org/blackbusiness/",
    "https://theaachamber.com/",
    "https://usblackchambers.org/chambers/",
    "https://www.navoba.com/directory",
    "https://disabilityin.org/what-we-do/supplier-diversity/",
    "https://ushcc.com/member-directory/",
]

# JS-rendered directories return an empty shell to requests. Selenium is the
# expensive fix. The budget fix: open the directory, watch the browser Network
# tab (F12 -> Network -> XHR), find the JSON request its search box fires, and
# put that endpoint here. Worked example below (commented).
#
# DIRECTORY_API_ENDPOINTS = [
#     {
#         "name": "NMSDC KY",
#         "url": "https://www.nmsdc.org/api/mbes?state=KY&page=1",  # example only
#         "records_path": ["data", "results"],   # walk the JSON to the list
#         "field_map": {
#             "business_name": "company_name",
#             "address":       "full_address",
#             "phone":         "phone",
#             "website":       "website_url",
#             "services":      "description",
#         },
#         "minority_type": "Minority-Owned (general)",
#     },
# ]
DIRECTORY_API_ENDPOINTS = []

# Domains to skip (bare hosts). Matched exactly or as a parent suffix.
# NOTE: facebook.com and instagram.com are deliberately absent -- we want them.
SKIP_DOMAINS = [
    "twitter.com", "x.com", "pinterest.com", "tiktok.com", "linkedin.com",
    "google.com", "bing.com", "yahoo.com",
    "wikipedia.org", "reddit.com", "quora.com",
    "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "yelp.com", "tripadvisor.com", "yellowpages.com",
    "bbb.org", "manta.com",
    # Reference, academic, archival, and stock-media sources. These surface in
    # the organic queries as history/encyclopedia material, never as a Kentucky
    # business to list, so skip them before fetching. News sites are NOT here,
    # since a local news profile occasionally names a real business.
    "jstor.org", "researchgate.net", "ssrn.com", "academia.edu",
    "semanticscholar.org", "britannica.com", "encyclopedia.com",
    "worldatlas.com", "worldometers.info", "gettyimages.com", "dokumen.pub",
    "archive.org", "congress.gov", "govinfo.gov", "un.org", "who.int",
    "bbc.com", "jstor.com",
]

# File types that are not HTML pages. Skipped before fetching so the parser
# never sees PDF or binary bytes (which previously crashed the whole run).
SKIP_FILE_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv",
    ".zip", ".rar", ".7z", ".gz", ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".svg", ".mp4", ".mov", ".mp3", ".wav", ".woff", ".woff2", ".ttf",
)

# Internal directory link paths that look like a business profile, not nav.
PROFILE_KEYWORDS = [
    "business", "profile", "member", "directory", "listing",
    "company", "vendor", "supplier", "detail",
]

# Minority signals used for pre-filtering before sending a page to Claude
MINORITY_SIGNALS = [
    "black-owned", "black owned", "african american owned",
    "hispanic owned", "latino owned", "latina owned", "latinx", "latine",
    "asian owned", "asian-owned",
    "lgbtq", "queer owned", "queer-owned", "gay owned",
    "women-owned", "woman-owned", "women owned", "woman owned", "female owned",
    "veteran-owned", "veteran owned", "sdvosb", "vosb",
    "native american owned", "indigenous owned",
    "minority-owned", "minority owned", "mbe certified",
    "bipoc", "disability owned", "muslim owned",
    "wbenc", "nglcc", "dobe",
]

# ---------------------------------------------
#  CLAUDE EXTRACTION PROMPT
# ---------------------------------------------
EXTRACT_PROMPT = """You are a data extraction assistant building a database of
minority-owned businesses in Kentucky. Read the webpage text below and extract
EVERY business it describes. Roundup and directory pages may list many
businesses, so do not stop at the first one.

Return ONLY a valid JSON array. Each element is one business. If no business
data is found, return an empty array: []

Each object must have exactly these fields:
  "business_name"  : string (official business name, or "")
  "address"        : string (full street address with city, state, zip, or "")
  "phone"          : string (phone number, or "")
  "services"       : string (brief description of products/services, or "")
  "website"        : string (business website URL; an Instagram or Facebook
                     profile URL is acceptable when there is no other site, or "")
  "minority_type"  : string (one or more of: Black-Owned, Latine-Owned,
                     Asian-Owned, Native American-Owned, Women-Owned,
                     LGBTQ+-Owned, Veteran-Owned, Disability-Owned,
                     Muslim-Owned, Minority-Owned (general). Comma-separate
                     multiples. Or "")

Rules:
- Only include businesses located in Kentucky.
- Do not invent or guess. Use "" for anything you cannot confirm.
- No Markdown, no code fences, no explanation. Return only the JSON array.

Webpage text:
{page_text}
"""

# ---------------------------------------------
#  SETUP
# ---------------------------------------------
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)


def normalize_name(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def normalize_website(url: str) -> str:
    v = str(url or "").strip().lower()
    for pre in ("http://", "https://"):
        if v.startswith(pre):
            v = v[len(pre):]
            break
    if v.startswith("www."):
        v = v[4:]
    return v.rstrip("/")


def get_host(url: str) -> str:
    # String based so it also works on scheme-less websites stored in the DB.
    v = normalize_website(url)
    return v.split("/")[0].split("?")[0].split("#")[0]


def business_key(name: str, website: str) -> str:
    combined = f"{normalize_name(name)}|{normalize_website(website)}"
    return hashlib.md5(combined.encode()).hexdigest()


def should_skip(url: str) -> bool:
    host = get_host(url)
    if not host:
        return True
    if urlparse(url).path.lower().endswith(SKIP_FILE_EXTENSIONS):
        return True
    return any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS)


def is_social(url: str) -> bool:
    host = get_host(url)
    return any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS)


def maps_extension_strings(result: dict) -> list[str]:
    """Collect attribute strings from a Maps result's extensions only, never
    its title or types, so ownership matching cannot trip on the business name."""
    out: list[str] = []
    for key in ("extensions", "unsupported_extensions"):
        block = result.get(key)
        if not isinstance(block, list):
            continue
        for item in block:
            if isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, list):
                        out.extend(str(x) for x in v)
                    elif isinstance(v, str):
                        out.append(v)
            elif isinstance(item, str):
                out.append(item)
    return out


def detect_ownership_from_maps(result: dict) -> str:
    """Return canonical ownership label(s) from Google's self-identified
    attributes, or '' if none are present. Matches the ownership form only, so
    'LGBTQ+ friendly' (a separate attribute) does not count as owned."""
    found: list[str] = []
    strings = [s.lower() for s in maps_extension_strings(result)]
    for phrases, label in GOOGLE_IDENTITY_ATTRIBUTES:
        if label in found:
            continue
        if any(p in s for s in strings for p in phrases):
            found.append(label)
    return ", ".join(found)


def polite_pause(search: bool = False):
    delay = SEARCH_DELAY if search else REQUEST_DELAY
    time.sleep(random.uniform(*delay))


def strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw[3:]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


# ---------------------------------------------
#  CACHE
# ---------------------------------------------
def ensure_cache_dirs():
    os.makedirs(os.path.join(CACHE_DIR, "pages"), exist_ok=True)
    os.makedirs(os.path.join(CACHE_DIR, "extractions"), exist_ok=True)
    os.makedirs(os.path.join(CACHE_DIR, "maps"), exist_ok=True)


def cache_fresh(path: str) -> bool:
    try:
        return (time.time() - os.path.getmtime(path)) < CACHE_TTL_DAYS * 86400
    except Exception:
        return False


def page_cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, "pages", hashlib.md5(url.encode()).hexdigest() + ".html")


def extraction_cache_path(content_hash: str) -> str:
    return os.path.join(CACHE_DIR, "extractions", content_hash + ".json")


# ---------------------------------------------
#  PROGRESS  (phase + step level resume)
# ---------------------------------------------
def load_progress() -> dict:
    p = {}
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                p = json.load(f)
        except Exception as e:
            print(f"  [Could not read progress file, starting fresh: {e}]")
            p = {}
    p.setdefault("maps_done", [])
    p.setdefault("api_done", [])
    p.setdefault("directories_done", [])
    p.setdefault("organic_done", [])
    p.setdefault("urls_collected", False)
    p.setdefault("url_list", [])
    p.setdefault("url_type_hint", {})
    p.setdefault("scanned_urls", [])
    return p


def save_progress(p: dict):
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(p, f, indent=0)
    except Exception as e:
        print(f"  [Progress save error] {e}")


# ---------------------------------------------
#  CHECKPOINT (business data)
# ---------------------------------------------
def load_checkpoint() -> tuple[list, set]:
    if not os.path.exists(CHECKPOINT_FILE):
        return [], set()
    try:
        df = pd.read_csv(CHECKPOINT_FILE, encoding="utf-8-sig")
        records = df.rename(columns={
            "Business Name":       "business_name",
            "Address":             "address",
            "Phone":               "phone",
            "Services / Products": "services",
            "Website":             "website",
            "Minority Type":       "minority_type",
        }).to_dict("records")
        seen = {business_key(str(r["business_name"]), str(r.get("website", ""))) for r in records}
        print(f"  [Resumed from checkpoint: {len(records)} businesses already saved]")
        return records, seen
    except Exception as e:
        print(f"  [Could not load checkpoint: {e}]")
        return [], set()


# ---------------------------------------------
#  KNOWN BUSINESSES (read existing directory from Supabase to avoid re-work)
# ---------------------------------------------
def fetch_known_businesses() -> tuple[set, set, set, int]:
    """
    Returns (known_keys, known_hosts, known_urls, count).
      known_keys  -> business_key(name, website) for exact-match output filtering
      known_hosts -> website hosts already captured (non-social) for scan skipping
      known_urls  -> normalized website URLs for exact social-profile skipping
    """
    if not (SUPABASE_URL and SUPABASE_KEY):
        print("  [SUPABASE_URL / SUPABASE_KEY not set in .env -- cannot skip "
              "known businesses; proceeding without that optimization]")
        return set(), set(), set(), 0

    keys, hosts, urls = set(), set(), set()
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json",
    }
    limit, offset, total = 1000, 0, 0
    try:
        while True:
            endpoint = (f"{SUPABASE_URL}/rest/v1/businesses"
                        f"?select=business_name,website&order=id.asc"
                        f"&limit={limit}&offset={offset}")
            resp = requests.get(endpoint, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                print(f"  [Supabase read failed: HTTP {resp.status_code}] {resp.text[:200]}")
                break
            rows = resp.json()
            if not rows:
                break
            for r in rows:
                name = r.get("business_name") or ""
                site = r.get("website") or ""
                if not name:
                    continue
                keys.add(business_key(name, site))
                if site:
                    nu = normalize_website(site)
                    urls.add(nu)
                    host = nu.split("/")[0]
                    if host and not any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS):
                        hosts.add(host)
                total += 1
            offset += limit
            if len(rows) < limit:
                break
    except Exception as e:
        print(f"  [Supabase read error] {e}")

    print(f"  [Loaded {total} existing businesses: {len(hosts)} known hosts, "
          f"{len(urls)} known URLs]")
    return keys, hosts, urls, total


# ---------------------------------------------
#  Google Maps harvest (structured, no Claude)
# ---------------------------------------------
def get_maps_businesses(query_text: str, minority_type: str, city: str) -> list[dict]:
    """Returns a list of result dicts. Each carries _confirmed (True when Google's
    own ownership attribute matched) and, when not confirmed, _lead_hint (the
    query's type, used only if the website later shows on-page evidence)."""
    full_q = f"{query_text} {city} Kentucky"
    cache_key = hashlib.md5(full_q.encode()).hexdigest()
    cache_path = os.path.join(CACHE_DIR, "maps", cache_key + ".json")

    local = None
    if os.path.exists(cache_path) and cache_fresh(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                local = json.load(f)
            print(f"  [Maps cache] {full_q}")
        except Exception:
            local = None

    if local is None:
        print(f"  [Maps] {full_q}")
        params = {
            "engine":  "google_maps",
            "type":    "search",
            "q":       full_q,
            "api_key": SERPAPI_KEY,
        }
        results = serp_call(params)   # raises SerpApiHalt on quota/rate/auth failure
        local = results.get("local_results", [])
        if isinstance(local, dict):
            local = local.get("places", [])
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(local, f)
        except Exception:
            pass
        polite_pause(search=True)

    out, confirmed_count = [], 0
    for r in local:
        name = (r.get("title") or "").strip()
        if not name:
            continue
        owner_type = detect_ownership_from_maps(r)   # tag ONLY from Google's attribute
        confirmed = bool(owner_type)
        if confirmed:
            confirmed_count += 1
        out.append({
            "business_name": name,
            "address":       (r.get("address") or "").strip(),
            "phone":         (r.get("phone") or "").strip(),
            "services":      "",   # filled later by fill_missing_services.py
            "website":       (r.get("website") or "").strip(),
            "minority_type": owner_type,           # blank unless Google confirmed it
            "_confirmed":    confirmed,
            "_lead_hint":    minority_type,        # query type, used only as a last resort
            "_source":       "google_maps",
            "_query":        full_q,
        })
    print(f"  -> {len(out)} results, {confirmed_count} ownership-confirmed")
    return out


# ---------------------------------------------
#  Directory API endpoint harvest (structured)
# ---------------------------------------------
def harvest_from_api_endpoint(cfg: dict) -> list[dict]:
    print(f"  [Directory API] {cfg.get('name', cfg.get('url'))}")
    try:
        resp = requests.get(cfg["url"], timeout=REQUEST_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        data = resp.json()
        node = data
        for key in cfg.get("records_path", []):
            node = node.get(key, []) if isinstance(node, dict) else []
        if not isinstance(node, list):
            print("  -> records_path did not resolve to a list")
            return []
        fm = cfg.get("field_map", {})
        out = []
        for rec in node:
            if not isinstance(rec, dict):
                continue
            name = str(rec.get(fm.get("business_name", ""), "") or "").strip()
            if not name:
                continue
            out.append({
                "business_name": name,
                "address":       str(rec.get(fm.get("address", ""), "") or "").strip(),
                "phone":         str(rec.get(fm.get("phone", ""), "") or "").strip(),
                "services":      str(rec.get(fm.get("services", ""), "") or "").strip(),
                "website":       str(rec.get(fm.get("website", ""), "") or "").strip(),
                "minority_type": cfg.get("minority_type", ""),
                "_source":       "directory_api",
                "_query":        cfg.get("name", ""),
            })
        print(f"  -> {len(out)} records")
        return out
    except Exception as e:
        print(f"  [Directory API Error] {e}")
        return []


# ---------------------------------------------
#  Harvest links from a directory page (external + internal profiles)
# ---------------------------------------------
def harvest_links_from_directory(directory_url: str) -> list[str]:
    print(f"  [Directory] Harvesting links from: {directory_url}")
    _, soup = fetch_page(directory_url)
    if not soup:
        return []
    base_domain = get_host(directory_url)
    external, internal = [], []
    for tag in soup.find_all("a", href=True):
        full = urljoin(directory_url, tag["href"])
        if not full.startswith("http") or should_skip(full):
            continue
        host = get_host(full)
        if host != base_domain:
            external.append(full)
        else:
            path = urlparse(full).path.lower()
            if any(kw in path for kw in PROFILE_KEYWORDS):
                internal.append(full)
    found = list(dict.fromkeys(external + internal))[:MAX_DIRECTORY_LINKS]
    print(f"  -> {len(external)} external, {len(internal)} internal profile links")
    return found


# ---------------------------------------------
#  SerpApi organic search
# ---------------------------------------------
# ---------------------------------------------
#  SERPAPI CALL WRAPPER (clean halt on quota/rate/auth, runaway backstop)
# ---------------------------------------------
class SerpApiHalt(Exception):
    """Raised when SerpApi cannot continue (quota exhausted, auth failure, rate
    limit after retries, or the per-run cap). It stops the run cleanly WITHOUT
    marking the in-flight query done, so a later re-run resumes at that query."""


_SEARCH_COUNT = 0

# A genuine no-results response is normal and must not halt the run.
SERP_BENIGN_ERROR_HINTS = [
    "hasn't returned any results", "has not returned any results",
    "no results found", "didn't return any results", "fully empty",
]
# Temporary; worth a wait-and-retry before giving up.
SERP_RATE_ERROR_HINTS = ["rate", "throughput", "too many requests", "429", "slow down"]
# Account-level; retrying is pointless, halt immediately.
SERP_FATAL_ERROR_HINTS = [
    "run out", "ran out", "out of searches", "exhausted", "exceeded",
    "plan limit", "monthly", "limit reached", "invalid api key",
    "unauthorized", "401", "no api key", "account",
]


def serp_call(params: dict, max_retries: int = 3) -> dict:
    global _SEARCH_COUNT
    if _SEARCH_COUNT >= MAX_SEARCHES_PER_RUN:
        raise SerpApiHalt(f"per-run search cap reached ({MAX_SEARCHES_PER_RUN})")

    from serpapi import GoogleSearch
    attempt = 0
    while True:
        attempt += 1
        try:
            _SEARCH_COUNT += 1
            results = GoogleSearch(params).get_dict()
        except Exception as e:
            if attempt <= max_retries:
                wait = 10 * attempt
                print(f"  [SerpApi network error, retry {attempt}/{max_retries} in {wait}s] {e}")
                time.sleep(wait)
                continue
            raise SerpApiHalt(f"network error after {max_retries} retries: {e}")

        if not isinstance(results, dict):
            raise SerpApiHalt("unexpected SerpApi response (not a dict)")

        err = (results.get("error") or "").lower()
        if not err:
            return results
        if any(h in err for h in SERP_BENIGN_ERROR_HINTS):
            return results   # legitimate empty result, caller treats as no data
        if any(h in err for h in SERP_RATE_ERROR_HINTS) and attempt <= max_retries:
            wait = 20 * attempt
            print(f"  [SerpApi rate limit, waiting {wait}s then retrying ({attempt}/{max_retries})]")
            time.sleep(wait)
            continue
        # Fatal account error, unknown error, or rate limit that survived retries.
        raise SerpApiHalt(f"SerpApi error: {results.get('error')}")


def serp_organic(full_q: str, page: int = 0) -> list[str]:
    print(f"  [Search p{page}] {full_q}")
    params = {
        "engine":   "google",
        "q":        full_q,
        "location": "Kentucky, United States",
        "api_key":  SERPAPI_KEY,
        "num":      RESULTS_PER_QUERY,
        "start":    page * RESULTS_PER_QUERY,
    }
    results = serp_call(params)   # raises SerpApiHalt on quota/rate/auth failure
    urls = [r["link"] for r in results.get("organic_results", []) if "link" in r]
    polite_pause(search=True)
    return urls


def get_search_urls(query_text: str, city: str, page: int = 0) -> list[str]:
    return serp_organic(f'"{query_text}" {city} Kentucky', page)


# ---------------------------------------------
#  Fetch page (cached). Returns cleaned text (with og: meta) + FULL soup.
# ---------------------------------------------
def fetch_page(url: str) -> tuple[str, BeautifulSoup | None]:
    cpath = page_cache_path(url)
    html = None
    if os.path.exists(cpath) and cache_fresh(cpath):
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                html = f.read()
        except Exception:
            html = None

    if html is None:
        try:
            resp = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if resp.status_code != 200:
                return "", None
            # Only parse actual web pages. A PDF, image, or other binary is not
            # HTML and previously crashed the parser, killing the whole run.
            ctype = resp.headers.get("Content-Type", "").lower()
            if ctype and not any(t in ctype for t in ("html", "xml", "text/plain")):
                return "", None
            html = resp.text
            try:
                with open(cpath, "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception:
                pass
            polite_pause()
        except Exception:
            return "", None

    # All parsing is wrapped: a malformed or binary page returns empty rather
    # than raising and ending the run.
    try:
        # Full soup keeps nav/header/footer so deep-link and directory harvesting
        # can see About/Contact/profile links that often live there.
        soup_full = BeautifulSoup(html, "html.parser")

        # og: meta tags carry the business name and bio even on social pages that
        # otherwise return little body text behind a wall.
        meta_bits = []
        for m in soup_full.find_all("meta"):
            prop = (m.get("property") or m.get("name") or "").lower()
            if prop in ("og:title", "og:description", "og:site_name", "description"):
                content = m.get("content")
                if content:
                    meta_bits.append(content)
        meta_text = " ".join(meta_bits)

        # Separate soup, stripped, for clean body text.
        soup_text = BeautifulSoup(html, "html.parser")
        for tag in soup_text(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        body_text = " ".join(soup_text.get_text(separator=" ", strip=True).split())
    except Exception:
        return "", None

    text = (meta_text + " " + body_text).strip()
    return text, soup_full


# ---------------------------------------------
#  Deep link crawler (reads the full soup)
# ---------------------------------------------
def find_deep_links(url: str, soup: BeautifulSoup) -> list[str]:
    base_domain = get_host(url)
    deep_keywords = ["about", "story", "our-story", "mission", "contact",
                     "who-we-are", "team", "history", "founders", "owner"]
    found = []
    for link in soup.find_all("a", href=True):
        href = link["href"].lower()
        if any(kw in href for kw in deep_keywords):
            full_url = urljoin(url, link["href"])
            if get_host(full_url) == base_domain and full_url not in found:
                found.append(full_url)
        if len(found) >= MAX_DEEP_LINKS:
            break
    return found


# ---------------------------------------------
#  Claude extraction (cached per text chunk)
# ---------------------------------------------
def claude_extract_chunk(chunk: str) -> list[dict]:
    content_hash = hashlib.md5(chunk.encode()).hexdigest()
    cpath = extraction_cache_path(content_hash)
    if os.path.exists(cpath) and cache_fresh(cpath):
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, list):
                return cached
        except Exception:
            pass

    data: list = []
    try:
        prompt = EXTRACT_PROMPT.format(page_text=chunk)
        response = anthropic_client.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = strip_fences(response.content[0].text)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            data = parsed
    except (json.JSONDecodeError, IndexError) as e:
        print(f"  [Claude Parse Error] {e}")
    except Exception as e:
        print(f"  [Claude API Error] {e}")

    try:
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass
    return data


def chunk_text(text: str, size: int, overlap: int, max_chunks: int) -> list[str]:
    chunks, start = [], 0
    step = max(size - overlap, 1)
    while start < len(text) and len(chunks) < max_chunks:
        chunks.append(text[start:start + size])
        start += step
    return chunks


def extract_businesses_from_text(url: str, text: str, type_hint: str = "") -> list[dict]:
    signal_count = sum(1 for s in MINORITY_SIGNALS if s in text.lower())
    is_listicle = len(text) > LISTICLE_CHAR_THRESHOLD and signal_count >= 2

    raw_records: list = []
    if is_listicle:
        chunks = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP, MAX_CHUNKS)
        print(f"  [Listicle: {len(text)} chars, {signal_count} signals -> {len(chunks)} chunks]")
        for ch in chunks:
            raw_records.extend(claude_extract_chunk(ch))
    else:
        raw_records.extend(claude_extract_chunk(text[:SINGLE_PAGE_CHARS]))

    source = "social" if is_social(url) else "organic"
    seen_local, out = set(), []
    for b in raw_records:
        if not isinstance(b, dict):
            continue
        for field in ("business_name", "address", "phone", "services", "website", "minority_type"):
            b.setdefault(field, "")
        name = (b.get("business_name") or "").strip()
        if not name:
            continue
        if not b.get("website"):
            b["website"] = "/".join(url.split("/")[:3])
        # The page passed the ownership-signal check, so some ownership language
        # is present, but not necessarily the specific type the query implied.
        # Fall back to the general label rather than overclaiming a specific one.
        if not (b.get("minority_type") or "").strip():
            b["minority_type"] = "Minority-Owned (general)"
        key = (name.lower(), (b.get("website") or "").lower())
        if key in seen_local:
            continue
        seen_local.add(key)
        b["_source"] = source
        b["_query"] = url
        out.append(b)
    return out


# ---------------------------------------------
#  Full pipeline for one URL
# ---------------------------------------------
def process_url(url: str, type_hint: str = "") -> list[dict]:
    text, soup = fetch_page(url)
    if not text or not soup or len(text) < 60:
        return []

    has_signal = any(sig in text.lower() for sig in MINORITY_SIGNALS)

    # Deep-crawl only real sites, not social profiles (no useful subpages there).
    if not has_signal and soup and not is_social(url):
        for deep_url in find_deep_links(url, soup):
            print(f"  -> Checking: {deep_url}")
            deep_text, _ = fetch_page(deep_url)
            if deep_text and any(sig in deep_text.lower() for sig in MINORITY_SIGNALS):
                text = text + " " + deep_text
                has_signal = True
                break

    if not has_signal:
        return []

    return extract_businesses_from_text(url, text, type_hint)


# ---------------------------------------------
#  Save CSV (schema identical to v1) + sources audit log
# ---------------------------------------------
def save_csv(data: list[dict], filename: str):
    columns = ["business_name", "address", "phone", "services", "website", "minority_type"]
    df = pd.DataFrame(data, columns=columns)   # extra keys like _source are ignored
    df.columns = ["Business Name", "Address", "Phone",
                  "Services / Products", "Website", "Minority Type"]
    df.drop_duplicates(subset=["Business Name", "Website"], inplace=True)
    df.sort_values("Business Name", inplace=True)
    df.to_csv(filename, index=False, encoding="utf-8-sig")


def save_sources_log(data: list[dict]):
    rows = [{
        "Business Name": b.get("business_name", ""),
        "Website":       b.get("website", ""),
        "Source":        b.get("_source", ""),
        "Found Via":     b.get("_query", ""),
    } for b in data]
    pd.DataFrame(rows).to_csv(SOURCES_LOG_FILE, index=False, encoding="utf-8-sig")


def add_business(b: dict, database: list, seen: set) -> bool:
    for field in ("business_name", "address", "phone", "services", "website", "minority_type"):
        b.setdefault(field, "")
    name = (b.get("business_name") or "").strip()
    if not name:
        return False
    key = business_key(name, b.get("website", ""))
    if key in seen:
        return False
    seen.add(key)
    database.append(b)
    print(f"  -> Added: {name} | {b.get('minority_type', '')} | {b.get('_source', '')}")
    if len(database) % CHECKPOINT_EVERY == 0:
        save_csv(database, CHECKPOINT_FILE)
    return True


# ---------------------------------------------
#  MAIN
# ---------------------------------------------
def build_database():
    global _SEARCH_COUNT
    _SEARCH_COUNT = 0
    ensure_cache_dirs()
    database, seen_businesses = load_checkpoint()
    p = load_progress()

    # Load existing directory so we do not re-fetch, re-extract, or re-emit
    # businesses we already have.
    known_keys, known_hosts, known_urls = set(), set(), set()
    skipped_known = 0
    if SKIP_KNOWN_BUSINESSES:
        print("\n=== Loading existing directory from Supabase ===")
        known_keys, known_hosts, known_urls, _ = fetch_known_businesses()
        seen_businesses |= known_keys   # exact name+website matches are filtered from output

    maps_done        = set(p["maps_done"])
    api_done         = set(p["api_done"])
    directories_done = set(p["directories_done"])
    organic_done     = set(p["organic_done"])
    scanned_urls     = set(p["scanned_urls"])

    def persist(flush_csv: bool = False):
        # Order matters for crash safety: write business rows to disk BEFORE
        # marking the work done, so a crash never loses data it claimed to finish.
        if flush_csv and database:
            save_csv(database, CHECKPOINT_FILE)
        p["maps_done"]        = sorted(maps_done)
        p["api_done"]         = sorted(api_done)
        p["directories_done"] = sorted(directories_done)
        p["organic_done"]     = sorted(organic_done)
        p["scanned_urls"]     = sorted(scanned_urls)
        save_progress(p)

    # --- Budget estimate -----------------------------------------------------
    maps_calls    = len(QUERY_TYPES) * len(MAPS_CITIES)
    organic_calls = len(QUERY_TYPES) * len(ORGANIC_CITIES) * SEARCH_PAGES
    social_calls  = len(QUERY_TYPES) * len(SOCIAL_SEARCH_SITES) if INCLUDE_SOCIAL_SEARCHES else 0
    total_calls   = maps_calls + organic_calls + social_calls
    already_done  = len(maps_done) + len(organic_done)
    print("\n=== Projected SerpApi searches for a full fresh run ===")
    print(f"  Maps:    {maps_calls}")
    print(f"  Organic: {organic_calls}")
    print(f"  Social:  {social_calls}")
    print(f"  TOTAL:   {total_calls}   (free tier is 100/month)")
    if already_done:
        print(f"  Resuming: {already_done} searches already completed and will be skipped.")
    print("  Directory page fetches do not use SerpApi.\n")

    # --- Phase 1: Google Maps (tag only from Google's ownership attribute) ----
    print("=== Phase 1: Google Maps ===")
    maps_confirmed, maps_leads = 0, 0
    for query_text, mtype in QUERY_TYPES:
        for city in MAPS_CITIES:
            unit = f"{query_text}|{city}"
            if unit in maps_done:
                continue
            for b in get_maps_businesses(query_text, mtype, city):
                if b["_confirmed"]:
                    if add_business(b, database, seen_businesses):
                        maps_confirmed += 1
                elif MAPS_VERIFY_LEADS_VIA_WEBSITE and b.get("website") and not is_social(b["website"]):
                    # Queue the website for the Phase 4 evidence check rather than
                    # trusting the query. It is added only if its page shows ownership.
                    site = b["website"]
                    if site not in p["url_type_hint"]:
                        p.setdefault("url_list", []).append(site)
                        p.setdefault("url_type_hint", {})[site] = b["_lead_hint"]
                        maps_leads += 1
            maps_done.add(unit)
            persist(flush_csv=True)
    print(f"  Phase 1 complete: {maps_confirmed} ownership-confirmed businesses added"
          + (f", {maps_leads} unconfirmed leads queued for evidence check" if MAPS_VERIFY_LEADS_VIA_WEBSITE else ""))

    # --- Phase 2: Directory JSON endpoints (structured) ----------------------
    if DIRECTORY_API_ENDPOINTS:
        print("\n=== Phase 2: Directory JSON endpoints ===")
        for cfg in DIRECTORY_API_ENDPOINTS:
            unit = cfg.get("name", cfg.get("url", ""))
            if unit in api_done:
                continue
            for b in harvest_from_api_endpoint(cfg):
                add_business(b, database, seen_businesses)
            api_done.add(unit)
            persist(flush_csv=True)

    # --- Phase 3: Collect URLs (directories + organic + social) --------------
    if not p["urls_collected"]:
        print("\n=== Phase 3: Collecting URLs ===")
        url_list = list(p.get("url_list", []))
        url_type_hint = dict(p.get("url_type_hint", {}))

        for directory_url in DIRECTORY_URLS:
            if directory_url in directories_done:
                continue
            for u in harvest_links_from_directory(directory_url):
                url_list.append(u)
            url_list.append(directory_url)
            directories_done.add(directory_url)
            p["url_list"] = url_list
            p["url_type_hint"] = url_type_hint
            persist()

        for query_text, mtype in QUERY_TYPES:
            for city in ORGANIC_CITIES:
                for page in range(SEARCH_PAGES):
                    unit = f"{query_text}|{city}|{page}"
                    if unit in organic_done:
                        continue
                    for u in get_search_urls(query_text, city, page):
                        url_list.append(u)
                        url_type_hint.setdefault(u, mtype)
                    organic_done.add(unit)
                    p["url_list"] = url_list
                    p["url_type_hint"] = url_type_hint
                    persist()

        if INCLUDE_SOCIAL_SEARCHES:
            for query_text, mtype in QUERY_TYPES:
                for site in SOCIAL_SEARCH_SITES:
                    unit = f"social|{site}|{query_text}"
                    if unit in organic_done:
                        continue
                    for u in serp_organic(f'site:{site} "{query_text}" Kentucky'):
                        url_list.append(u)
                        url_type_hint.setdefault(u, mtype)
                    organic_done.add(unit)
                    p["url_list"] = url_list
                    p["url_type_hint"] = url_type_hint
                    persist()

        # Dedup + skip, then freeze the final list.
        seen_set, unique_urls = set(), []
        for url in url_list:
            if url not in seen_set and not should_skip(url):
                seen_set.add(url)
                unique_urls.append(url)
        p["url_list"] = unique_urls
        p["url_type_hint"] = url_type_hint
        p["urls_collected"] = True
        persist()

    unique_urls = p["url_list"]
    url_type_hint = p["url_type_hint"]

    # --- Phase 4: Scan each URL ----------------------------------------------
    print(f"\n=== Phase 4: Scanning {len(unique_urls)} unique URLs ===\n")
    for i, url in enumerate(unique_urls, 1):
        if url in scanned_urls:
            continue
        # Skip PDFs, binaries, and reference/academic junk that may already be in
        # the collected list from before this filter existed.
        if should_skip(url):
            scanned_urls.add(url)
            persist()
            continue
        # Skip URLs we already have: a known business website (by exact URL or by
        # host) costs nothing to skip and saves the fetch plus the Claude call.
        # Social hosts are never blanket-skipped, only their exact known profile.
        host = get_host(url)
        if SKIP_KNOWN_BUSINESSES and (
            normalize_website(url) in known_urls
            or (host in known_hosts and not is_social(url))
        ):
            print(f"[{i}/{len(unique_urls)}] {url}\n  -> Already in directory, skipping")
            skipped_known += 1
            scanned_urls.add(url)
            persist()
            continue
        print(f"[{i}/{len(unique_urls)}] {url}")
        added_any = False
        for b in process_url(url, url_type_hint.get(url, "")):
            if add_business(b, database, seen_businesses):
                added_any = True
        if not added_any:
            print("  -> No new match")
        scanned_urls.add(url)
        persist(flush_csv=added_any)   # flush rows to disk only when this URL produced any

    # --- Phase 5: Final export -----------------------------------------------
    if database:
        save_csv(database, OUTPUT_FILE)
        save_sources_log(database)
        persist()
        print(f"\nDone. {len(database)} businesses saved to {OUTPUT_FILE}")
        print(f"SerpApi searches used this run: {_SEARCH_COUNT}")
        if SKIP_KNOWN_BUSINESSES:
            print(f"Skipped {skipped_known} URLs already in the directory "
                  f"(no fetch, no extraction spent on them).")
        print(f"Source audit log written to {SOURCES_LOG_FILE}")
        print("Delete scraper_progress_v2.json to force a full fresh run next time.")
    else:
        print("\nNo businesses found. Check your API keys in the .env file.")


# ---------------------------------------------
#  ENTRY POINT
# ---------------------------------------------
if __name__ == "__main__":
    errors = []
    if not SERPAPI_KEY:
        errors.append("SERPAPI_KEY not found in .env file")
    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY not found in .env file")

    if errors:
        print("ERROR -- missing API keys:")
        for e in errors:
            print(f"  - {e}")
        print("\nCreate a .env file in this folder with your keys.")
        print("See .env.example for the format.")
    else:
        try:
            build_database()
        except SerpApiHalt as e:
            print(f"\n!!! Run halted: {e}")
            print(f"SerpApi searches used this run: {_SEARCH_COUNT}")
            print("Your progress and checkpoint are saved. Re-run the same command "
                  "to resume exactly where this stopped. No completed query was lost, "
                  "and the query that failed was NOT marked done, so it will retry.")
