"""
Kentucky Minority Business - Resolve "Needs review"
===================================================
Re-checks the websites of the "Needs review" rows in businesses_prepared.csv and
tries to settle each one automatically by finding an address on the site.

For each Needs review row that has a website:
  - If the website is an aggregator/listicle (smileypete, the Tennessee Tribune,
    buyblack, social, a directory) or is blank, or the domain does not match the
    business name, it first looks up the business's REAL site via SerpApi.
  - Reads the page from the scraper's cache if present, otherwise fetches it
    (page fetches are free; only SerpApi and Claude cost money).
  - Also follows up to 3 About / Contact / Location subpages on the same domain.
  - Looks for an address in JSON-LD structured data first, then in visible text.

Then it updates the row:
  - Kentucky address found  -> Disposition "Good to go", Address + Kentucky Based
    filled, and the Website corrected to the real site when one was found
  - Out-of-state address    -> Disposition "Dropped", Reason notes it was resolved
  - Nothing found           -> left "Needs review" for a manual look

It writes the result back to data/businesses_prepared.csv in place. Run it,
then review only whatever is still "Needs review" by hand.

Usage:
    python pipeline/resolve_review.py            # process all Needs review rows
    python pipeline/resolve_review.py --limit 10 # try the first 10 only (test run)
    python pipeline/resolve_review.py --dry-run  # report, do not write the file
    python pipeline/resolve_review.py --no-serp  # skip the SerpApi real-site lookup
"""

import os
import re
import sys
import json
import time
import hashlib
import argparse
import requests
import pandas as pd
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
DATA_DIR     = os.path.join(REPO_ROOT, "data")
PREP_FILE    = os.path.join(DATA_DIR, "businesses_prepared.csv")
PAGE_CACHE   = os.path.join(DATA_DIR, "cache", "pages")   # same cache the scraper writes

TIMEOUT   = 8
SLEEP     = 1.0
MAX_SUBPAGES = 3
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
SUBPAGE_HINTS = ("about", "contact", "location", "visit", "find-us", "directions", "hours")

# Aggregator / listicle / directory / social domains. When a Needs-review row's
# "website" is one of these, it is the article the business was scraped FROM, not
# the business's own site, so reading it for an address is pointless. We look up
# the real site instead. Extend this list as new offenders show up.
AGGREGATOR_DOMAINS = {
    "smileypete.com", "tntribune.com", "thevoiceofblackcincinnati.com",
    "lexingtonweddingexpos.com", "modernfarmer.com", "courierpress.com", "amiba.net",
    "buyblack.org", "yelp.com", "facebook.com", "instagram.com", "linkedin.com",
    "twitter.com", "x.com", "tiktok.com", "tripadvisor.com", "mapquest.com",
    "yellowpages.com", "bbb.org", "chamberofcommerce.com", "indeed.com",
    "glassdoor.com", "crunchbase.com", "manta.com", "wikipedia.org", "google.com",
    "bizapedia.com", "opencorporates.com", "zoominfo.com", "nextdoor.com",
    "eventbrite.com", "meetup.com", "patch.com",
}
SERP_SKIP = AGGREGATOR_DOMAINS | {"bing.com", "duckduckgo.com", "youtube.com"}

# The aggregator that surfaced a business usually tells you WHERE it is. A city
# chamber, a local news site, or a municipal domain only covers its own area, so the
# domain is a free location hint for a row that has no address at all.
#
# This matters because find_real_site() searches '"{name}" official website', which is
# useless for a short generic name: '"Olla" official website' returns nothing usable.
# Add the city and the service and it resolves immediately -- Olla is a taqueria in
# Covington. Extend this map as new aggregators appear in the review pile.
DOMAIN_CITY_HINT = {
    "thecovky.gov":                "Covington",
    "somersetpulaskichamber.com":  "Somerset",
    "owensborotimes.com":          "Owensboro",
    "do502.com":                   "Louisville",   # 502 is the Louisville area code
    "nkytribune.com":              "Northern Kentucky",
    "louisville.coffee":           "Louisville",
    "linknky.com":                 "Northern Kentucky",
    "wdrb.com":                    "Louisville",
    "spectrumnews1.com":           "",             # statewide, no usable city
    "kyforky.com":                 "",             # statewide
}


def city_hint(url):
    """City implied by the aggregator that surfaced this business, or ''."""
    return DOMAIN_CITY_HINT.get(domain_of(url), "")


def load_env():
    env_path = os.path.join(REPO_ROOT, ".env")
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
SERPAPI_KEY = os.getenv("SERPAPI_KEY")


def domain_of(url):
    if not url:
        return ""
    u = re.sub(r"^https?://", "", str(url).lower())
    u = re.sub(r"^www\.", "", u)
    return u.split("/")[0]


def is_aggregator(url):
    d = domain_of(url)
    return any(d == a or d.endswith("." + a) for a in AGGREGATOR_DOMAINS)


def name_domain_mismatch(name, url):
    """True if the domain shares no significant word with the business name, i.e.
    the URL probably is not the business's own site. Short names are not judged."""
    host = domain_of(url).split(".")[0]
    if not host:
        return True
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", str(name).lower()).split() if len(t) >= 4]
    if not toks:
        return False
    return not any(t in host or host in t for t in toks)


def find_business_facts(name, service="", city=""):
    """Pull address / phone / website straight out of the search response.

    SerpApi returns a `knowledge_graph` (and sometimes `local_results`) for a business
    query, already parsed into fields:

        address   621 Main St, Covington, KY 41011
        phone     (859) 360-6051
        website   http://www.mamasonmain.com/

    resolve_review previously read only `organic_results`, picked a link, fetched that
    page, and tried to parse an address out of the HTML. For "Mama's on Main" that path
    picked an OpenTable booking page, found nothing scrapeable, and left the row in the
    review pile -- while the address sat unread in the same response.

    Returns {} when the search has no structured business result, in which case the
    caller falls back to the fetch-and-parse path, which still handles businesses that
    Google has no knowledge panel for.
    """
    if not SERPAPI_KEY:
        return {}
    parts = [f'"{name}"']
    if service:
        parts.append(" ".join(str(service).split()[:4]))
    if city:
        parts.append(city)
    parts.append("Kentucky")
    try:
        data = requests.get("https://serpapi.com/search",
                            params={"q": " ".join(parts), "api_key": SERPAPI_KEY,
                                    "num": 8, "gl": "us", "hl": "en"},
                            timeout=15).json()
    except Exception as e:
        print(f"    SerpApi error: {e}")
        return {}

    kg = data.get("knowledge_graph") or {}
    if not kg:
        loc = data.get("local_results") or {}
        places = loc.get("places") if isinstance(loc, dict) else loc
        if places:
            kg = places[0]

    out = {}
    for src, dst in (("address", "address"), ("phone", "phone"), ("website", "website")):
        val = str(kg.get(src) or "").strip()
        if val:
            out[dst] = val
    # Keep the organic results so the caller can still fall back without re-searching.
    out["_organic"] = data.get("organic_results", [])
    return out


def find_real_site(name, service="", city=""):
    """Find the business's real website via SerpApi. Returns a URL or None.
    Skips aggregators, social, and search engines in the results.

    July 2026: the query now includes the service and a city hint when available.
    It used to be '"{name}" official website' alone, which fails on short or generic
    names -- '"Olla" official website' finds nothing, while '"Olla" Restaurant
    Covington Kentucky' finds the taqueria on the first result. 28 of the 51 rows in
    the July review pile had a city recoverable from the aggregator domain, and every
    one of those rows had no address at all, so this context was the only signal
    available.
    """
    if not SERPAPI_KEY:
        return None
    parts = [f'"{name}"']
    if service:
        # First few words only: these descriptions can run to a full sentence, and a
        # long quoted phrase narrows the search to nothing.
        parts.append(" ".join(str(service).split()[:4]))
    if city:
        parts.append(city)
    parts.append("Kentucky")
    q = " ".join(parts)
    params = {"q": q, "api_key": SERPAPI_KEY,
              "num": 8, "gl": "us", "hl": "en"}
    try:
        data = requests.get("https://serpapi.com/search", params=params, timeout=12).json()
    except Exception as e:
        print(f"    SerpApi error: {e}")
        return None
    for r in data.get("organic_results", []):
        link = r.get("link", "")
        if not link.startswith("http"):
            continue
        if is_aggregator(link) or any(s in link for s in SERP_SKIP):
            continue
        return link
    return None

US_STATES = {"al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il",
             "in","ia","ks","la","me","md","ma","mi","mn","ms","mo","mt","ne",
             "nv","nh","nj","nm","ny","nc","nd","oh","ok","or","pa","ri","sc",
             "sd","tn","tx","ut","vt","va","wa","wv","wi","wy","dc"}

# "City, ST 40202" or "City, ST 40202-1234"
CITY_ST_ZIP = re.compile(r"([A-Za-z.\'\-]+(?:\s+[A-Za-z.\'\-]+){0,3}),\s*([A-Za-z]{2})\s+(\d{5})(?:-\d{4})?")
KY_SPELLED  = re.compile(r"([A-Za-z.\'\-]+(?:\s+[A-Za-z.\'\-]+){0,3}),\s*Kentucky\b", re.IGNORECASE)


def ky_zip(z):
    """Kentucky ZIPs run roughly 40003-42788."""
    try:
        return 40003 <= int(z) <= 42788
    except ValueError:
        return False


def fetch(url):
    """Return page text + soup, using the scraper's cache when available."""
    if not url.startswith("http"):
        url = "https://" + url
    cpath = os.path.join(PAGE_CACHE, hashlib.md5(url.encode()).hexdigest() + ".html")
    html = None
    if os.path.exists(cpath):
        try:
            with open(cpath, "r", encoding="utf-8", errors="ignore") as f:
                html = f.read()
        except Exception:
            html = None
    if html is None:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                return "", None
            html = resp.text
            os.makedirs(PAGE_CACHE, exist_ok=True)
            with open(cpath, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            return "", None
    try:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(" ", strip=True), soup
    except Exception:
        return "", None


def address_from_jsonld(soup):
    """Most reliable: schema.org PostalAddress. Returns (state, address) or (None, None)."""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            blob = json.loads(tag.string or "")
        except Exception:
            continue
        stack = blob if isinstance(blob, list) else [blob]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "@graph" in node and isinstance(node["@graph"], list):
                    stack.extend(node["@graph"])
                addr = node.get("address")
                if isinstance(addr, dict):
                    region = str(addr.get("addressRegion", "")).strip().lower()
                    street = addr.get("streetAddress", "")
                    city   = addr.get("addressLocality", "")
                    zc     = str(addr.get("postalCode", "")).strip()
                    full   = ", ".join(p for p in [street, city,
                              addr.get("addressRegion", ""), zc] if p)
                    if region in ("ky", "kentucky") or ky_zip(zc):
                        return "ky", full
                    if region in US_STATES:
                        return region, full
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)
    return None, None


def address_from_text(text):
    """Scan visible text. Returns (state, address) or (None, None). Prefers KY."""
    out_of_state = None
    for m in CITY_ST_ZIP.finditer(text):
        state, zc = m.group(2).lower(), m.group(3)
        if state == "ky" or ky_zip(zc):
            return "ky", m.group(0).strip()
        if state in US_STATES:
            out_of_state = out_of_state or ("oos", m.group(0).strip())
    m = KY_SPELLED.search(text)
    if m:
        return "ky", m.group(0).strip()
    return out_of_state if out_of_state else (None, None)


def find_subpages(soup, base_url):
    found, seen = [], set()
    base_host = urlparse(base_url if base_url.startswith("http") else "https://" + base_url).netloc
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text(" ") or "").lower()
        if any(h in href.lower() or h in text for h in SUBPAGE_HINTS):
            full = urljoin(base_url if base_url.startswith("http") else "https://" + base_url, href)
            if urlparse(full).netloc == base_host and full not in seen:
                seen.add(full)
                found.append(full)
        if len(found) >= MAX_SUBPAGES:
            break
    return found


def resolve_one(website):
    """Return (state, address) where state is 'ky', 'oos', or None."""
    text, soup = fetch(website)
    if soup is None:
        return None, None
    state, addr = address_from_jsonld(soup)
    if state == "ky":
        return state, addr
    state_t, addr_t = address_from_text(text)
    if state_t == "ky":
        return "ky", addr_t
    # homepage gave no KY hit; check a few subpages before giving up
    for sub in find_subpages(soup, website):
        time.sleep(SLEEP)
        sub_text, sub_soup = fetch(sub)
        if sub_soup is None:
            continue
        s2, a2 = address_from_jsonld(sub_soup)
        if s2 == "ky":
            return "ky", a2
        s3, a3 = address_from_text(sub_text)
        if s3 == "ky":
            return "ky", a3
        state = state or s2 or s3
        addr = addr or a2 or a3
    # no KY anywhere; report out-of-state only if we actually saw one
    if state in US_STATES or state == "oos":
        return "oos", addr
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="process only the first N rows")
    ap.add_argument("--dry-run", action="store_true", help="report but do not write the file")
    ap.add_argument("--no-serp", action="store_true",
                    help="skip the SerpApi real-site lookup (page fetches only)")
    args = ap.parse_args()

    if not SERPAPI_KEY and not args.no_serp:
        print("Note: SERPAPI_KEY not found in .env; aggregator/blank-site rows can't be "
              "resolved to a real site this run. Continuing with page fetches only.\n")

    df = pd.read_csv(PREP_FILE, encoding="utf-8-sig").fillna("")
    mask = df["Disposition"] == "Needs review"
    idxs = list(df[mask].index)
    if args.limit:
        idxs = idxs[:args.limit]
    print(f"Needs review rows to check: {len(idxs)}")

    promoted = dropped = unresolved = skipped = looked_up = 0
    for n, i in enumerate(idxs, 1):
        name = str(df.at[i, "Business Name"])
        site = str(df.at[i, "Website"]).strip()

        # If the listed site is an aggregator/listicle/social, blank, or its
        # domain does not match the name, find the business's REAL site first.
        real_site = ""
        service = str(df.at[i, "Services / Products"]).strip()
        city = city_hint(site)

        if (not args.no_serp) and (not site or is_aggregator(site) or name_domain_mismatch(name, site)):
            # Ask the search engine what it already knows about this business before
            # trying to fetch and parse anything. One search, and the response usually
            # carries a structured address, phone and real website.
            facts = find_business_facts(name, service, city)
            kg_addr = facts.get("address", "")
            # Reuse the same classifier the page-scraping path uses, so a search-derived
            # address is judged by exactly the same rules as a scraped one.
            state_kg = address_from_text(kg_addr)[0] if kg_addr else None

            if state_kg == "ky":
                df.at[i, "Disposition"]    = "Good to go"
                df.at[i, "Kentucky Based"] = "Yes"
                df.at[i, "Address"] = kg_addr
                if facts.get("website"):
                    df.at[i, "Website"] = facts["website"]
                if facts.get("phone") and not str(df.at[i, "Phone"]).strip():
                    df.at[i, "Phone"] = facts["phone"]
                df.at[i, "Reason"] = "KY address from search result"
                promoted += 1
                looked_up += 1
                print(f"  [{n}/{len(idxs)}] {name[:38]:<38} -> Good to go [search] ({kg_addr})")
                time.sleep(SLEEP)
                continue
            if state_kg == "oos":
                if facts.get("website"):
                    df.at[i, "Website"] = facts["website"]
                df.at[i, "Disposition"] = "Dropped"
                df.at[i, "Reason"] = "out-of-state (from search result)"
                dropped += 1
                print(f"  [{n}/{len(idxs)}] {name[:38]:<38} -> Dropped [search] ({kg_addr})")
                time.sleep(SLEEP)
                continue

            # No usable structured address. Fall back to the original path: pick a real
            # website out of the organic results and scrape it. Reuse the results we
            # already paid for rather than searching again.
            for r in facts.get("_organic", []):
                link = r.get("link", "")
                if link.startswith("http") and not is_aggregator(link) \
                        and not any(s in link for s in SERP_SKIP):
                    real_site = link
                    break
            if real_site:
                looked_up += 1
                ctx = f" [{city}]" if city else ""
                print(f"  [{n}/{len(idxs)}] {name[:38]:<38} real site -> {real_site}{ctx}")
        resolve_site = real_site or site

        if not resolve_site:
            skipped += 1
            print(f"  [{n}/{len(idxs)}] {name[:40]:<40} no website found, skip")
            continue

        state, addr = resolve_one(resolve_site)
        if state == "ky":
            df.at[i, "Disposition"]    = "Good to go"
            df.at[i, "Kentucky Based"] = "Yes"
            if addr and not str(df.at[i, "Address"]).strip():
                df.at[i, "Address"] = addr
            if real_site:                       # correct the bad listicle URL too
                df.at[i, "Website"] = real_site
            df.at[i, "Reason"] = "KY address confirmed by re-scrape"
            promoted += 1
            print(f"  [{n}/{len(idxs)}] {name[:40]:<40} -> Good to go ({addr})")
        elif state == "oos":
            if real_site:
                df.at[i, "Website"] = real_site
            df.at[i, "Disposition"] = "Dropped"
            df.at[i, "Reason"] = "out-of-state (resolved by re-scrape)"
            dropped += 1
            print(f"  [{n}/{len(idxs)}] {name[:40]:<40} -> Dropped ({addr})")
        else:
            df.at[i, "Reason"] = "no address found on site"
            unresolved += 1
            print(f"  [{n}/{len(idxs)}] {name[:40]:<40} -> still needs review")
        time.sleep(SLEEP)

    print(f"\nReal sites found via lookup: {looked_up}")
    print(f"Promoted to Good to go: {promoted}")
    print(f"Dropped (out-of-state): {dropped}")
    print(f"Still needs review:     {unresolved}")
    print(f"Skipped (no website):   {skipped}")

    if args.dry_run:
        print("\nDry run: file NOT written.")
    else:
        df.to_csv(PREP_FILE, index=False, encoding="utf-8-sig")
        print(f"\nUpdated {os.path.basename(PREP_FILE)}. "
              f"Review the remaining 'Needs review' rows by hand, then upload.")


if __name__ == "__main__":
    main()
