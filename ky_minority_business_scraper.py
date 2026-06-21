"""
Kentucky Minority-Owned Business Scraper
=========================================
Searches the web for minority-owned businesses in Kentucky and extracts
structured data using Claude AI for intelligent field parsing.

Requirements:
    pip install requests beautifulsoup4 pandas anthropic google-search-results python-dotenv

Setup:
    1. Copy .env.example to .env and fill in your API keys.
    2. Run: python ky_minority_business_scraper.py
    3. Output: ky_minority_businesses.csv

Getting API keys (both have free tiers to start):
    SerpApi:   https://serpapi.com  (100 free searches/month)
    Anthropic: https://console.anthropic.com (pay-as-you-go, ~$0.003/page)

Notes:
    - Never commit your .env file to GitHub. It is listed in .gitignore.
    - The checkpoint file saves every 5 businesses so interrupted runs resume.
    - Re-running the script picks up where it left off automatically.
"""

import os
import re
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

# ─────────────────────────────────────────────
#  LOAD KEYS FROM .env FILE
# ─────────────────────────────────────────────
load_dotenv()
SERPAPI_KEY       = os.getenv("SERPAPI_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
SCRIPT_DIR        = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE       = os.path.join(SCRIPT_DIR, "ky_minority_businesses.csv")
CHECKPOINT_FILE   = os.path.join(SCRIPT_DIR, "checkpoint_ky_minority_businesses.csv")
CHECKPOINT_EVERY  = 5       # Save progress every N businesses found
REQUEST_DELAY     = (2, 4)  # Seconds between page fetches
SEARCH_DELAY      = (3, 5)  # Seconds between SerpApi calls (paid, less risk)
REQUEST_TIMEOUT   = 12      # Seconds before giving up on a URL
RESULTS_PER_QUERY = 10      # Google results per query
MAX_DEEP_LINKS    = 3       # Max subpages to check per business site

# ─────────────────────────────────────────────
#  SEARCH QUERIES
# ─────────────────────────────────────────────
SEARCH_QUERIES = [
    # Black-owned
    '"black owned business" Louisville Kentucky',
    '"black owned business" Lexington Kentucky',
    '"black owned" restaurant Louisville KY',
    '"black owned" shop Louisville KY',
    '"African American owned" business Kentucky',
    '"black entrepreneur" Louisville Kentucky',
    '"black business" Louisville KY',
    '"black woman owned" business Kentucky',

    # Latino/Hispanic-owned
    '"hispanic owned" business Kentucky',
    '"latino owned" business Louisville Kentucky',
    '"latina owned" business Kentucky',

    # Asian-owned
    '"asian owned" business Kentucky',
    '"asian american owned" business Louisville',

    # LGBTQ+-owned
    '"lgbtq owned" business Kentucky',
    '"queer owned" business Louisville Kentucky',
    '"gay owned" business Kentucky',

    # Women-owned
    '"women owned" business Kentucky',
    '"woman owned" business Louisville Kentucky',
    '"female owned" business Kentucky',

    # Veteran-owned
    '"veteran owned" business Kentucky',
    '"service-disabled veteran" business Kentucky',

    # Other categories
    '"native american owned" business Kentucky',
    '"minority owned" business Louisville Kentucky',
    '"minority owned" business Lexington Kentucky',
    '"disability owned" business Kentucky',
    '"muslim owned" business Kentucky',
    '"bipoc owned" business Kentucky',
    '"minority business enterprise" Kentucky directory',
    '"mbe certified" business Kentucky',
]

# Known directories to scrape directly
DIRECTORY_URLS = [
    "https://www.commercelexington.com/minority-business-directory.html",
    "https://lul.org/blackbusiness/",
    "https://theaachamber.com/",
    "https://www.nmsdc.org/mbes/?state=KY",
    "https://www.wbenc.org/wbe-search/",
    "https://nglcc.org/business-search",
    "https://usblackchambers.org/chambers/",
    "https://www.navoba.com/directory",
    "https://disabilityin.org/what-we-do/supplier-diversity/",
    "https://ushcc.com/member-directory/",
]

# Domains to skip
SKIP_DOMAINS = [
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "pinterest.com", "tiktok.com",
    "google.com", "bing.com", "yahoo.com",
    "wikipedia.org", "reddit.com", "quora.com",
    "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "yelp.com", "tripadvisor.com", "yellowpages.com",
    "bbb.org", "manta.com", "chamber.com",
]

# Minority signals used for pre-filtering before sending to Claude
MINORITY_SIGNALS = [
    "black-owned", "black owned", "african american owned",
    "hispanic owned", "latino owned", "latina owned", "latinx",
    "asian owned", "asian-owned",
    "lgbtq", "queer owned", "queer-owned", "gay owned",
    "women-owned", "woman-owned", "women owned", "woman owned", "female owned",
    "veteran-owned", "veteran owned", "sdvosb", "vosb",
    "native american owned", "indigenous owned",
    "minority-owned", "minority owned", "mbe certified",
    "bipoc", "disability owned", "muslim owned",
    "wbenc", "nglcc", "dobe",
]

# ─────────────────────────────────────────────
#  CLAUDE EXTRACTION PROMPT
# ─────────────────────────────────────────────
EXTRACT_PROMPT = """
You are a data extraction assistant building a database of minority-owned
businesses in Kentucky. Read the webpage text below and extract business info.

Return ONLY a valid JSON array. Each element is one business. If no business
data is found, return an empty array: []

Each object must have exactly these fields:
  "business_name"  : string (official business name, or "")
  "address"        : string (full street address with city, state, zip, or "")
  "phone"          : string (phone number, or "")
  "services"       : string (brief description of products/services, or "")
  "website"        : string (business website URL, or "")
  "minority_type"  : string (one or more: Black-Owned, Latino-Owned, Asian-Owned,
                     Native American-Owned, Women-Owned, LGBTQ+-Owned,
                     Veteran-Owned, Disability-Owned, Muslim-Owned,
                     Minority-Owned (general). Comma-separate multiples. Or "")

Rules:
- Only include businesses located in Kentucky.
- Do not invent or guess. Use "" for anything you cannot confirm.
- No Markdown, no code fences, no explanation. Return only the JSON array.

Webpage text:
{page_text}
"""

# ─────────────────────────────────────────────
#  SETUP
# ─────────────────────────────────────────────
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)


def business_key(name: str, website: str) -> str:
    combined = f"{name.lower().strip()}|{website.lower().strip()}"
    return hashlib.md5(combined.encode()).hexdigest()


def should_skip(url: str) -> bool:
    return any(domain in url for domain in SKIP_DOMAINS)


def polite_pause(search=False):
    delay = SEARCH_DELAY if search else REQUEST_DELAY
    time.sleep(random.uniform(*delay))


# ─────────────────────────────────────────────
#  CHECKPOINT
# ─────────────────────────────────────────────
def load_checkpoint() -> tuple[list, set]:
    if not os.path.exists(CHECKPOINT_FILE):
        return [], set()
    try:
        df = pd.read_csv(CHECKPOINT_FILE, encoding="utf-8-sig")
        records = df.rename(columns={
            "Business Name":    "business_name",
            "Address":          "address",
            "Phone":            "phone",
            "Services / Products": "services",
            "Website":          "website",
            "Minority Type":    "minority_type",
        }).to_dict("records")
        seen = {business_key(r["business_name"], r.get("website", "")) for r in records}
        print(f"  [Resumed from checkpoint: {len(records)} businesses already saved]")
        return records, seen
    except Exception as e:
        print(f"  [Could not load checkpoint: {e}]")
        return [], set()


# ─────────────────────────────────────────────
#  STEP 1a: Harvest business links from directories
# ─────────────────────────────────────────────
def harvest_links_from_directory(directory_url: str) -> list[str]:
    """
    Extracts individual business URLs listed inside a public directory page.
    Smarter than just scraping the directory itself -- pulls out each
    business link so they each get their own Claude extraction pass.
    """
    print(f"  [Directory] Harvesting links from: {directory_url}")
    found_links = []
    try:
        resp = requests.get(
            directory_url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            base_domain = urlparse(directory_url).netloc

            for tag in soup.find_all("a", href=True):
                href = tag["href"]
                # Only keep external links (actual business sites, not nav links)
                if href.startswith("http"):
                    target_domain = urlparse(href).netloc
                    if target_domain != base_domain and not should_skip(href):
                        found_links.append(href)

        unique = list(set(found_links))
        print(f"  → Found {len(unique)} external business links")
        return unique

    except Exception as e:
        print(f"  [Directory Error] {e}")
        return []


# ─────────────────────────────────────────────
#  STEP 1b: SerpApi search
# ─────────────────────────────────────────────
def get_search_urls(query: str) -> list[str]:
    print(f"  [Search] {query}")
    try:
        from serpapi import GoogleSearch
        params = {
            "engine":   "google",
            "q":        query,
            "location": "Kentucky, United States",
            "api_key":  SERPAPI_KEY,
            "num":      RESULTS_PER_QUERY,
        }
        results = GoogleSearch(params).get_dict()
        urls = [r["link"] for r in results.get("organic_results", []) if "link" in r]
        polite_pause(search=True)
        return urls
    except Exception as e:
        print(f"  [Search Error] {e}")
        return []


# ─────────────────────────────────────────────
#  STEP 2: Fetch page
# ─────────────────────────────────────────────
def fetch_page(url: str) -> tuple[str, BeautifulSoup | None]:
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

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = " ".join(soup.get_text(separator=" ", strip=True).split())
        return text, soup

    except Exception:
        return "", None


# ─────────────────────────────────────────────
#  STEP 3: Deep link crawler
# ─────────────────────────────────────────────
def find_deep_links(url: str, soup: BeautifulSoup) -> list[str]:
    """
    Finds About, Story, Contact, or Mission subpages on the same domain.
    Many businesses only self-identify on their About page, not the homepage.
    """
    base_domain = urlparse(url).netloc
    deep_keywords = ["about", "story", "our-story", "mission", "contact",
                     "who-we-are", "team", "history", "founders", "owner"]
    found = []

    for link in soup.find_all("a", href=True):
        href = link["href"].lower()
        if any(kw in href for kw in deep_keywords):
            full_url = urljoin(url, link["href"])
            if urlparse(full_url).netloc == base_domain and full_url not in found:
                found.append(full_url)
        if len(found) >= MAX_DEEP_LINKS:
            break

    return found


# ─────────────────────────────────────────────
#  STEP 4: Claude extraction
# ─────────────────────────────────────────────
def extract_with_claude(url: str, page_text: str) -> list[dict]:
    try:
        prompt = EXTRACT_PROMPT.format(page_text=page_text[:6000])
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        businesses = json.loads(raw)
        if not isinstance(businesses, list):
            return []

        for b in businesses:
            b.setdefault("business_name", "")
            b.setdefault("address", "")
            b.setdefault("phone", "")
            b.setdefault("services", "")
            b.setdefault("minority_type", "")
            if not b.get("website"):
                b["website"] = "/".join(url.split("/")[:3])

        return [b for b in businesses if b.get("business_name")]

    except (json.JSONDecodeError, IndexError) as e:
        print(f"  [Claude Parse Error] {e}")
        return []
    except Exception as e:
        print(f"  [Claude API Error] {e}")
        return []


# ─────────────────────────────────────────────
#  STEP 5: Full pipeline for one URL
# ─────────────────────────────────────────────
def process_url(url: str) -> list[dict]:
    text, soup = fetch_page(url)
    if not text or not soup or len(text) < 100:
        return []

    # Pre-filter: check for any minority signal before spending API tokens
    has_signal = any(sig in text.lower() for sig in MINORITY_SIGNALS)

    # If homepage has no signal, check About/Story/Contact pages
    if not has_signal and soup:
        deep_links = find_deep_links(url, soup)
        for deep_url in deep_links:
            print(f"  → Checking: {deep_url}")
            deep_text, _ = fetch_page(deep_url)
            if deep_text and any(sig in deep_text.lower() for sig in MINORITY_SIGNALS):
                text = text + " " + deep_text  # Merge for richer extraction
                has_signal = True
                break
            polite_pause()

    if not has_signal:
        return []

    # Send to Claude for structured extraction
    return extract_with_claude(url, text)


# ─────────────────────────────────────────────
#  STEP 6: Save CSV
# ─────────────────────────────────────────────
def save_csv(data: list[dict], filename: str):
    columns = ["business_name", "address", "phone", "services", "website", "minority_type"]
    df = pd.DataFrame(data, columns=columns)
    df.columns = ["Business Name", "Address", "Phone",
                  "Services / Products", "Website", "Minority Type"]
    df.drop_duplicates(subset=["Business Name", "Website"], inplace=True)
    df.sort_values("Business Name", inplace=True)
    df.to_csv(filename, index=False, encoding="utf-8-sig")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def build_database():
    database, seen_businesses = load_checkpoint()

    # ── Collect URLs ──────────────────────────────────────────────────────
    print("\n=== Phase 1: Harvesting business links from directories ===")
    all_urls = []
    for directory_url in DIRECTORY_URLS:
        harvested = harvest_links_from_directory(directory_url)
        all_urls.extend(harvested)
        # Also add the directory page itself in case it lists businesses inline
        all_urls.append(directory_url)

    print("\n=== Phase 2: Collecting URLs from SerpApi ===")
    for query in SEARCH_QUERIES:
        urls = get_search_urls(query)
        all_urls.extend(urls)

    # Deduplicate while preserving order
    seen_set    = set()
    unique_urls = []
    for url in all_urls:
        if url not in seen_set and not should_skip(url):
            seen_set.add(url)
            unique_urls.append(url)

    print(f"\n=== Scanning {len(unique_urls)} unique URLs ===\n")

    # ── Scan each URL ─────────────────────────────────────────────────────
    scanned_urls = set()

    for i, url in enumerate(unique_urls, 1):
        if url in scanned_urls:
            continue
        scanned_urls.add(url)
        print(f"[{i}/{len(unique_urls)}] {url}")

        businesses = process_url(url)

        new_count = 0
        for b in businesses:
            bkey = business_key(b["business_name"], b.get("website", ""))
            if bkey not in seen_businesses:
                seen_businesses.add(bkey)
                database.append(b)
                new_count += 1
                print(f"  → Added: {b['business_name']} | {b['minority_type']}")

        if new_count == 0:
            print(f"  → No match")

        if len(database) % CHECKPOINT_EVERY == 0 and len(database) > 0:
            save_csv(database, CHECKPOINT_FILE)
            print(f"  [Checkpoint saved: {len(database)} businesses so far]")

        polite_pause()

    # ── Final export ──────────────────────────────────────────────────────
    if database:
        save_csv(database, OUTPUT_FILE)
        print(f"\nDone. {len(database)} businesses saved to {OUTPUT_FILE}")
    else:
        print("\nNo businesses found. Check your API keys in the .env file.")


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
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
        build_database()
