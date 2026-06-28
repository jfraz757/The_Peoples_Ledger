"""
Kentucky Minority Business - Resolve "Needs review"
===================================================
Re-checks the websites of the "Needs review" rows in businesses_prepared.csv and
tries to settle each one automatically by finding an address on the site.

For each Needs review row that has a website:
  - Reads the page from the scraper's cache if present, otherwise fetches it
    (page fetches are free; only SerpApi and Claude cost money).
  - Also follows up to 3 About / Contact / Location subpages on the same domain.
  - Looks for an address in JSON-LD structured data first, then in visible text.

Then it updates the row:
  - Kentucky address found  -> Disposition "Good to go", Address + Kentucky Based filled
  - Out-of-state address    -> Disposition "Dropped", Reason notes it was resolved
  - Nothing found           -> left "Needs review" for a manual look

It writes the result back to data/businesses_prepared.csv in place. Run it,
then review only whatever is still "Needs review" by hand.

Usage:
    python pipeline/resolve_review.py            # process all Needs review rows
    python pipeline/resolve_review.py --limit 10 # try the first 10 only (test run)
    python pipeline/resolve_review.py --dry-run  # report, do not write the file
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
    args = ap.parse_args()

    df = pd.read_csv(PREP_FILE, encoding="utf-8-sig").fillna("")
    mask = df["Disposition"] == "Needs review"
    idxs = list(df[mask].index)
    if args.limit:
        idxs = idxs[:args.limit]
    print(f"Needs review rows to check: {len(idxs)}")

    promoted = dropped = unresolved = skipped = 0
    for n, i in enumerate(idxs, 1):
        name = str(df.at[i, "Business Name"])
        site = str(df.at[i, "Website"]).strip()
        if not site:
            skipped += 1
            print(f"  [{n}/{len(idxs)}] {name[:40]:<40} no website, skip")
            continue

        state, addr = resolve_one(site)
        if state == "ky":
            df.at[i, "Disposition"]    = "Good to go"
            df.at[i, "Kentucky Based"] = "Yes"
            if addr and not str(df.at[i, "Address"]).strip():
                df.at[i, "Address"] = addr
            df.at[i, "Reason"] = "KY address confirmed by re-scrape"
            promoted += 1
            print(f"  [{n}/{len(idxs)}] {name[:40]:<40} -> Good to go ({addr})")
        elif state == "oos":
            df.at[i, "Disposition"] = "Dropped"
            df.at[i, "Reason"] = "out-of-state (resolved by re-scrape)"
            dropped += 1
            print(f"  [{n}/{len(idxs)}] {name[:40]:<40} -> Dropped ({addr})")
        else:
            df.at[i, "Reason"] = "no address found on site"
            unresolved += 1
            print(f"  [{n}/{len(idxs)}] {name[:40]:<40} -> still needs review")
        time.sleep(SLEEP)

    print(f"\nPromoted to Good to go: {promoted}")
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
