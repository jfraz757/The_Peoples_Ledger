"""
verify_listings.py  --  re-check old web-lane listings against Google Maps

    python pipeline/verify_listings.py            # DRY RUN: searches (cached), writes the plan CSV
    python pipeline/verify_listings.py --apply    # write the changes (backs up every old value)

WHY THIS EXISTS (October 2026)
Before July 2026 the scraper's web lane read roundup and directory pages and extracted
every business on them. Two defects from that era are still in the live table:

  1. OWNERSHIP INHERITED FROM THE PAGE. When a page carried ownership language but did
     not attribute it to a specific group, the business was tagged
     "Minority-Owned (general)". 163 live listings carry only that tag -- no specific
     group, no ownership certification. It was never evidence (technical reference 6b).

  2. THE PAGE'S HOMEPAGE SAVED AS THE BUSINESS'S WEBSITE. When the extraction found no
     website, the scraper wrote the SOURCE page's homepage instead
     (scrape.extract_businesses_from_text). 331 listings link to an article or directory:
     30 to lexingtonweddingexpos.com, 36 to veteranownedbusiness.com, 21 to
     kentuckytourism.com, and nine to the literal text "please provide website address,
     if applicable" copied from a form.

One Google Maps search per affected listing answers both: the result carries Google's
self-identified owner attribute (the same evidence the scraper requires today) and the
business's own website.

RULES
  generic-only tag   Google attribute found -> replace with the specific label(s).
                     Not found              -> clear the tag. The listing stays (it is still
                                               findable by name, city, industry); it just
                                               stops claiming an ownership it cannot back up.
  generic + specific drop the generic part (no search needed).
  wrong website      Maps has a website     -> use it.
                     Maps has none / no match -> clear it (status "No Website"). A blank
                                               link is better than one to someone else.
  any match          fill a blank address or phone from the Maps result.

A Maps result counts as a match only if its name is a close fuzzy match (token-set >= 88)
and its address, when it has one, is in Kentucky. Searches are cached in
data/cache/verify/, so re-running the dry run or the apply costs nothing.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE_DIR)
import common  # noqa: E402

from rapidfuzz import fuzz  # noqa: E402

CACHE = os.path.join(common.DATA_DIR, "cache", "verify")
PLAN_FILE = os.path.join(common.DATA_DIR, "verify_listings_plan.csv")
GENERIC = {"minority-owned (general)", "minority-owned"}
OWNERSHIP_CERTS = {"MBE", "WBE", "MWBE", "DBE", "VOSB", "SDVOSB", "DIBE", "LGBTBE"}
# Hosts that legitimately serve many businesses' own pages. A shared host here is not a
# sign of a wrong website.
PLATFORM_HOSTS = ("facebook.com", "instagram.com", "linktr.ee", "square.site", "wixsite.com",
                  "squarespace.com", "business.site", "godaddysites.com", "google.com")
MATCH_SCORE = 90


def tags(r):
    return [t.strip() for t in (r.get("minority_type") or "").split(",") if t.strip()]


def certs(r):
    return {c.strip().upper() for c in (r.get("certification_type") or "").split(",") if c.strip()}


def host(u):
    u = re.sub(r"^https?://", "", (u or "").strip().lower())
    return re.sub(r"^www\.", "", u).split("/")[0]


def clean_url(u):
    """Drop Google's tracking parameters (utm_*) from a website link."""
    if "?" not in u:
        return u
    base, qs = u.split("?", 1)
    keep = [p for p in qs.split("&") if p and not p.lower().startswith("utm_")]
    return base + ("?" + "&".join(keep) if keep else "")


def norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower().replace("&", " and "))


STATE_WORDS = r"kentucky|ohio|indiana|tennessee|united states( of america)?|usa"


def city_of(address):
    parts = [p.strip() for p in str(address or "").split(",") if p.strip()]
    for p in reversed(parts):
        # skip "KY 40503", "40503", "Kentucky", "Kentucky 40503", "United States"
        if re.fullmatch(rf"([A-Za-z]{{2}}|{STATE_WORDS})?(\s*\d{{5}}(-\d{{4}})?)?", p, re.I):
            continue
        if re.match(r"^\d", p):          # a street line, not a city
            return ""
        return p
    return ""


def select_targets(rows):

    by_host = defaultdict(set)
    for r in rows:
        if r.get("website"):
            by_host[host(r["website"])].add(r["business_name"].lower())

    def aggregator(h):
        # A host is someone else's site only if DIFFERENT businesses point at it. Two
        # listings of one business (Thompson Catering x2 -> partyky.com, its real site)
        # or branches of one company do not make it an aggregator.
        #
        # At least THREE mutually different businesses are required. With only two, a
        # wrong link (Fitnatics -> flygirlcandles.com) looks exactly like two related
        # listings (Go 2 Girl / Go2Girl, Tabla's two locations, Metro Fence and its
        # wholesale arm), and the October 2026 second pass would have cleared the real
        # sites of the latter. Two-business cases are left for a human.
        names = sorted(by_host[h])
        distinct = []
        for n in names:
            if all(name_score(n, d) < 80 for d in distinct):
                distinct.append(n)
        return len(distinct) >= 3

    def own_page(name, w):
        # A deep link whose path carries the business's name is that business's own page
        # on a shared platform: vagaro.com/adorehairstudio, yelp.com/biz/a-touch-of-jolie.
        path = re.sub(r"^https?://[^/]+", "", w.strip().lower())
        slug = lambda s: re.sub(r"[^a-z0-9]", "", s)
        p = slug(path)
        if len(p) < 5:
            return False
        # "&" may appear in the URL as "and" or not at all: try both spellings.
        variants = {slug(norm(name)), slug(norm(name).replace(" and ", " "))}
        return any(len(n) >= 5 and fuzz.partial_ratio(n, p) >= 90 for n in variants)

    def own_domain(name, h):
        # On a host several different businesses share, only the business whose name the
        # domain actually spells out owns it. A shared word is not enough: "Our Wedding
        # Cabinet" shares "wedding" with lexingtonweddingexpos.com.
        label = re.sub(r"[^a-z0-9]", "", h.split(".")[0])
        slug = lambda s: re.sub(r"[^a-z0-9]", "", s)
        variants = {slug(norm(name)), slug(norm(name).replace(" and ", " "))}
        return any(len(n) >= 4 and (fuzz.ratio(n, label) >= 85 or (len(label) >= 6 and label in n))
                   for n in variants)

    def bad_site(r):
        w = (r.get("website") or "").strip()
        if not w:
            return False
        h = host(w)
        if " " in w or "." not in h:
            return True
        if len(by_host[h]) < 2 or h.endswith(PLATFORM_HOSTS) or not aggregator(h):
            return False
        return not own_domain(r["business_name"], h) and not own_page(r["business_name"], w)

    generic_only, mixed, bad = {}, {}, {}
    for r in rows:
        t = tags(r)
        if t and all(x.lower() in GENERIC for x in t) and not (certs(r) & OWNERSHIP_CERTS):
            generic_only[r["id"]] = r
        elif any(x.lower() in GENERIC for x in t) and any(x.lower() not in GENERIC for x in t):
            mixed[r["id"]] = r
        if bad_site(r):
            bad[r["id"]] = r
    shared_hosts = {h for h, names in by_host.items() if len(names) >= 2 and not h.endswith(PLATFORM_HOSTS)}
    return generic_only, mixed, bad, shared_hosts


def maps_lookup(name, address):
    """Raw Maps candidates for one business, cached. Returns a list of result dicts."""
    import scrape
    city = city_of(address)
    q = f"{name} {city} Kentucky" if city else f"{name} Kentucky"
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.md5(q.encode()).hexdigest() + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    res = scrape.serp_call({"engine": "google_maps", "type": "search", "q": q,
                            "api_key": scrape.SERPAPI_KEY})
    # One exact hit comes back as `place_results` (a dict), several as `local_results`.
    cands = []
    if isinstance(res.get("place_results"), dict):
        cands.append(res["place_results"])
    local = res.get("local_results") or []
    if isinstance(local, dict):
        local = local.get("places", [])
    cands.extend(local)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cands, f)
    return cands


def name_score(a, b):
    """How sure we are two business names are the same business.

    token_set_ratio alone was too lenient: it scores a shared word against the shorter
    name, so "Construction MR LLC" vs "Miranda Construction, LLC" scored 91 and
    "Consultants With Energy" vs "EHC Consultants" 88. Accept only an overall match
    (token_sort >= 90), or one name contained whole in the other (token_set >= 95:
    "Arthur Murray Dance Studio" in "Arthur Murray Dance Studio of Lexington").
    Corporate suffixes are stripped first, as everywhere else in the pipeline."""
    from prepare import _dedup_name
    x, y = _dedup_name(a), _dedup_name(b)
    sort_ = fuzz.token_sort_ratio(x, y)
    set_ = fuzz.token_set_ratio(x, y)
    # Containment needs at least two words in the shorter name: "Story" sits inside
    # "Story Spaces" too, and one shared word is not an identity.
    contained_ok = set_ >= 95 and min(len(x.split()), len(y.split())) >= 2
    return sort_ if sort_ >= 90 else (set_ if contained_ok else min(sort_, set_))


def best_match(name, cands, address=""):
    """The Maps candidate that is this business, or None.

    Also required: the candidate's address, when it has one, is in Kentucky (a London,
    England consultancy once matched a Louisville one), and when the listing names a
    city, the candidate is in the same city -- a same-named business elsewhere in the
    state is a different business (B&B Tree Service, London vs Falmouth)."""
    from prepare import addr_state
    want_city = city_of(address).lower()
    best, best_score = None, 0
    for c in cands:
        addr = c.get("address") or ""
        if addr and addr_state(addr) != "KY":
            continue
        got_city = city_of(addr).lower()
        if want_city and got_city and want_city != got_city:
            continue
        score = name_score(name, c.get("title") or "")
        if score > best_score:
            best, best_score = c, score
    return (best, best_score) if best_score >= MATCH_SCORE else (None, best_score)


def build_plan(rows):
    import scrape
    generic_only, mixed, bad, shared_hosts = select_targets(rows)
    to_search = {**generic_only, **bad}
    print(f"generic-only tags: {len(generic_only)}   generic+specific: {len(mixed)}   "
          f"wrong websites: {len(bad)}   searches needed: {len(to_search)}")

    plan = []
    for i, (rid, r) in enumerate(sorted(to_search.items()), 1):
        cands = maps_lookup(r["business_name"], r.get("address"))
        match, score = best_match(r["business_name"], cands, r.get("address"))
        labels = scrape.detect_ownership_from_maps(match) if match else ""
        change = {"id": rid, "business_name": r["business_name"], "matched": match.get("title") if match else "",
                  "score": round(score)}
        if rid in generic_only:
            change["old_minority_type"] = r.get("minority_type") or ""
            change["new_minority_type"] = labels          # "" clears it
        if rid in bad:
            site = clean_url((match or {}).get("website") or "")
            if site and host(site) in shared_hosts:
                site = ""
            change["old_website"] = r.get("website") or ""
            change["new_website"] = site
        if match:
            # Fill a blank address, or upgrade a city-only one ("Harrodsburg, KY") to the
            # street address Google has for the same business.
            old_addr = (r.get("address") or "").strip()
            new_addr = (match.get("address") or "").strip()
            if new_addr and (not old_addr or (not re.match(r"^\d", old_addr) and re.match(r"^\d", new_addr))):
                change["new_address"] = new_addr
            if not (r.get("phone") or "").strip() and match.get("phone"):
                change["new_phone"] = match["phone"]
        plan.append(change)
        if i % 50 == 0:
            print(f"  looked up {i}/{len(to_search)}")

    # A listing can be in both groups (generic + specific tag AND a wrong website): fold
    # the tag change into its existing entry so each listing gets exactly one change.
    by_id = {c["id"]: c for c in plan}
    for rid, r in mixed.items():
        kept = [t for t in tags(r) if t.lower() not in GENERIC]
        c = by_id.get(rid)
        if c is None:
            c = {"id": rid, "business_name": r["business_name"]}
            plan.append(c)
        c["old_minority_type"] = r.get("minority_type") or ""
        c["new_minority_type"] = ", ".join(kept)
    return plan, generic_only, bad


def patch_for(c):
    p = {}
    if "new_minority_type" in c:
        p["minority_type"] = c["new_minority_type"] or None
    if "new_website" in c:
        p["website"] = c["new_website"] or None
        if not c["new_website"]:
            p["status"] = "No Website"
    if c.get("new_address"):
        p["address"] = c["new_address"]
    if c.get("new_phone"):
        p["phone"] = c["new_phone"]
    return p


def main():
    ap = argparse.ArgumentParser(description="Re-check old web-lane listings against Google Maps.")
    ap.add_argument("--apply", action="store_true", help="write the changes (dry run otherwise)")
    args = ap.parse_args()

    url, key = common.require_service_credentials("updates listings")
    rows = common.fetch_all("id,business_name,address,phone,website,minority_type,certification_type,status",
                            key=key)
    plan, generic_only, bad = build_plan(rows)

    fields = ["id", "business_name", "matched", "score", "old_minority_type", "new_minority_type",
              "old_website", "new_website", "new_address", "new_phone"]
    with open(PLAN_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in plan:
            w.writerow({k: c.get(k, "") for k in fields})

    g = [c for c in plan if c["id"] in generic_only]
    b = [c for c in plan if c["id"] in bad]
    print(f"\nGeneric-only tags ({len(g)}): {sum(1 for c in g if c['new_minority_type'])} confirmed by Google "
          f"and re-tagged, {sum(1 for c in g if not c['new_minority_type'])} cleared")
    print(f"Wrong websites ({len(b)}): {sum(1 for c in b if c['new_website'])} replaced with the real site, "
          f"{sum(1 for c in b if not c['new_website'])} cleared")
    print(f"Blank address/phone filled: {sum(1 for c in plan if c.get('new_address'))} / "
          f"{sum(1 for c in plan if c.get('new_phone'))}")
    print(f"Plan -> {PLAN_FILE}")

    if not args.apply:
        print("\nDRY RUN. Nothing written. Re-run with --apply.")
        return

    import requests
    by_id = {r["id"]: r for r in rows}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(common.DATA_DIR, f"verify_listings_backup_{stamp}.csv")
    with open(backup, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "business_name", "address", "phone", "website",
                                          "minority_type", "status"])
        w.writeheader()
        for c in plan:
            w.writerow({k: by_id[c["id"]].get(k) or "" for k in w.fieldnames})
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    n = 0
    for c in plan:
        p = patch_for(c)
        if not p:
            continue
        r = requests.patch(f"{url}/rest/v1/businesses", params={"id": f"eq.{c['id']}"},
                           headers=headers, json=p, timeout=30)
        r.raise_for_status()
        n += 1
    print(f"\nUpdated {n} listing(s). Old values backed up to {backup}")
    print("Next: python pipeline/maintain.py --all   (check the new websites), then regenerate pages.")


if __name__ == "__main__":
    main()
