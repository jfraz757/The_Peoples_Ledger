"""
website_ownership.py  --  look for an ownership statement on a business's OWN website

    python pipeline/website_ownership.py            # DRY RUN: writes data/website_ownership_plan.csv
    python pipeline/website_ownership.py --apply    # tag the confirmed ones (old values backed up)

For listings that carry no ownership tag and no state certification. Google's owner
attribute (verify_listings.py) only exists when an owner sets it; plenty of owners say it
on their own site instead. 1021 Specialist, LLC had no Google attribute, but its About page
says "1021 Specialist, LLC is an MBE Certified company" (October 2026).

This is NOT the old web-lane mistake. That read an ARTICLE or directory and stamped every
business on it with the page's topic. Here the page is the business's own website, so a
statement like "we are a woman-owned company" is the business describing itself. Even so,
every candidate statement goes to Claude with one question -- does this say THIS business
is owned by members of a group? -- because "we proudly support Black-owned businesses" and
client logos are on own sites too. The exact quote must appear in the page text or the
answer is discarded, and every accepted quote is saved as evidence in the plan CSV.

Reads the homepage plus up to three About/Story/Team/Contact pages (scrape.py's fetch,
cache and deep-link finder). Page fetches are free; one Claude call per site with a
candidate statement.
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE_DIR)
import common  # noqa: E402

PLAN_FILE = os.path.join(common.DATA_DIR, "website_ownership_plan.csv")
# Ownership DEMOGRAPHICS -> minority_type. Only when the site names the group -- or, for
# "Minority-Owned (general)", when the business calls ITSELF minority-owned without naming
# one ("A minority-owned general contractor founded and operated by Manuel Santos"). That is
# the business's own claim, unlike the old web-lane tag, which was the page's.
LABELS = ["Black-Owned", "Latine-Owned", "Asian-Owned", "Native American-Owned", "Women-Owned",
          "LGBTQ+-Owned", "Veteran-Owned", "Disability-Owned", "Muslim-Owned", "Minority-Owned (general)"]
# CERTIFICATIONS the business claims -> self_reported_certification (add_self_reported_cert.sql).
# Kept apart from the demographics, and never written to certification_type, which only
# the three Kentucky certifiers may fill. "We are MBE certified" with no group named is
# recorded as MBE, not guessed into a demographic.
CERTS = ["MBE", "WBE", "MWBE", "DBE", "VOSB", "SDVOSB", "WOSB", "DIBE", "LGBTBE"]
UNFETCHABLE = ("facebook.com", "instagram.com", "fb.com", "linktr.ee", "amazon.com")

SIGNAL = re.compile(
    r"(black|african[- ]american|minority|wom[ae]n|female|veteran|service[- ]disabled|latin[aoxe]|hispanic|"
    r"asian|lgbtq\+?|queer|gay|lesbian|trans|native[- ]american|indigenous|disab(?:led|ility)|muslim)"
    r"[- ]?(?:owned|led|operated)|\b(?:MBE|WBE|DBE|MWBE|SDVOSB|VOSB|WOSB|DBE)\b|minority business enterprise|"
    r"women'?s business enterprise|disadvantaged business enterprise", re.I)

PROMPT = """You are checking ownership claims for a public directory of minority-owned businesses in Kentucky.

Below are excerpts from the OWN WEBSITE of the business named "{name}". Decide whether the text
states that THIS business is owned by members of a particular group.

Count ONLY statements about this business's own ownership or its own certification, such as
"we are a Black-owned business", "{name} is a woman-owned company", "founded and owned by a veteran",
"we are MBE certified".

Do NOT count: support for, lists of, or links to OTHER businesses; client or partner logos; directory
or award listings of others; statements about the community the business serves; an owner's personal
background unless it says they own the business.

Report two things separately:
- "groups": ownership groups the text NAMES for this business. Use only: {labels}.
  Only list a group if the text names it ("Black-owned", "woman-owned", "veteran-owned"...).
  Do not infer a group from a certification: "MBE certified" alone names no group.
  Use "Minority-Owned (general)" ONLY when the business calls itself minority-owned without
  naming a group, and only if no specific group is named anywhere.
- "certifications": certifications the text says this business HOLDS -- the text must say it
  is certified, or use the certification's name. Use only: {certs}.
  ("Minority Business Enterprise" = MBE, "Women's Business Enterprise" = WBE,
   "Disadvantaged Business Enterprise" = DBE, "Woman-Owned Small Business" = WOSB.)
  "Minority-owned" by itself is a group statement, NOT an MBE certification.

Reply with ONLY a JSON object, no code fences:
{{"groups": [...], "certifications": [...], "quote": "<the exact sentence from the text that supports it>", "url": "<page it came from>"}}
or, if nothing qualifies:
{{"groups": [], "certifications": [], "quote": "", "url": ""}}

Website excerpts:
{excerpts}
"""


def norm_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def site_text(url):
    """[(page_url, text)] for the homepage and up to three about/story/team pages."""
    import scrape
    text, soup = scrape.fetch_page(url if url.startswith("http") else "https://" + url)
    pages = [(url, text or "")]
    if soup:
        for d in scrape.find_deep_links(url if url.startswith("http") else "https://" + url, soup):
            t, _ = scrape.fetch_page(d)
            if t:
                pages.append((d, t))
    return pages


def excerpts_for(pages):
    out = []
    for u, t in pages:
        for m in SIGNAL.finditer(t):
            s = t[max(0, m.start() - 250): m.end() + 250]
            out.append(f"[{u}] ...{s}...")
    return out[:8]


def ask_claude(name, excerpts):
    import scrape
    msg = scrape.anthropic_client.messages.create(
        model=scrape.EXTRACT_MODEL, max_tokens=1000,
        messages=[{"role": "user", "content": PROMPT.format(
            name=name, labels=", ".join(LABELS), certs=", ".join(CERTS), excerpts="\n\n".join(excerpts))}])
    text = next((b.text for b in msg.content if b.type == "text"), "")
    data = json.loads(scrape.strip_fences(text))
    groups = [g for g in data.get("groups", []) if g in LABELS]
    if len(groups) > 1 and "Minority-Owned (general)" in groups:      # a named group wins
        groups.remove("Minority-Owned (general)")
    certs = [c for c in data.get("certifications", []) if c in CERTS]
    return groups, certs, data.get("quote", ""), data.get("url", "")


CERT_NAMES = {
    "MBE": r"\bMBE\b|minority business enterprise", "WBE": r"\bWBE\b|wom[ae]n'?s? business enterprise",
    "MWBE": r"\bM/?WBE\b", "DBE": r"\bDBE\b|disadvantaged business enterprise",
    "VOSB": r"\bVOSB\b|veteran[- ]owned small business", "SDVOSB": r"\bSDVOSB\b|service[- ]disabled veteran[- ]owned small business",
    "WOSB": r"\bE?WOSB\b|wom[ae]n[- ]owned small business", "DIBE": r"\bDIBE\b", "LGBTBE": r"\bLGBTBE\b",
}


def enforce_quote(groups, certs, quote):
    """Keep only what the quote itself says. The model is good at spotting statements but
    has read "eligible for diversity and government set-aside programs" as an MBE
    certification (Handy Manny's, October 2026). A certification counts only if its name
    is in the quote; "Minority-Owned (general)" only if the quote says minority-owned."""
    certs = [c for c in certs if re.search(CERT_NAMES[c], quote, re.I)]
    if "Minority-Owned (general)" in groups and not re.search(r"minority[- ]owned", quote, re.I):
        groups = [g for g in groups if g != "Minority-Owned (general)"]
    return groups, certs


def main():
    ap = argparse.ArgumentParser(description="Find ownership statements on businesses' own websites.")
    ap.add_argument("--apply", action="store_true",
                    help="write the plan saved by the last dry run (exactly what you reviewed)")
    args = ap.parse_args()

    url, key = common.require_service_credentials("updates ownership tags")
    rows = common.fetch_all("id,business_name,website,minority_type,certification_type", key=key)
    if args.apply:
        # Apply the REVIEWED plan, not a fresh scan: a re-scan asks the model again and can
        # word things differently from what was approved.
        if not os.path.exists(PLAN_FILE):
            sys.exit("No saved plan. Run the dry run first and review data/website_ownership_plan.csv.")
        with open(PLAN_FILE, newline="", encoding="utf-8-sig") as f:
            plan = list(csv.DictReader(f))
        for p_ in plan:
            p_["id"] = int(p_["id"])
        return apply_plan(plan, rows, url, key)
    targets = [r for r in rows
               if not (r.get("minority_type") or "").strip() and not (r.get("certification_type") or "").strip()
               and (r.get("website") or "").strip() and not any(h in r["website"].lower() for h in UNFETCHABLE)]
    print(f"Listings with no ownership evidence and a fetchable website: {len(targets)}")

    plan = []
    for i, r in enumerate(targets, 1):
        pages = site_text(r["website"].strip())
        ex = excerpts_for(pages)
        res = {"id": r["id"], "business_name": r["business_name"], "website": r["website"],
               "pages_read": len([p for p in pages if p[1]]), "groups": "", "certifications": "",
               "quote": "", "evidence_url": "", "note": ""}
        if not any(t for _, t in pages):
            res["note"] = "site did not load"
        elif not ex:
            res["note"] = "no ownership statement on site"
        else:
            try:
                groups, certs, quote, src = ask_claude(r["business_name"], ex)
            except Exception as e:
                res["note"] = f"check failed: {e}"
                groups, certs, quote, src = [], [], "", ""
            groups, certs = enforce_quote(groups, certs, quote or "")
            # The quote must really be on the site; otherwise the answer is discarded.
            alltext = norm_ws(" ".join(t for _, t in pages))
            if (groups or certs) and quote and norm_ws(quote)[:80] in alltext:
                res.update(groups=", ".join(groups), certifications=", ".join(certs),
                           quote=quote, evidence_url=src)
            elif groups or certs:
                res["note"] = "Claude's quote not found on the page -- discarded"
            else:
                res["note"] = "statements found, none about this business's ownership"
        plan.append(res)
        if i % 20 == 0:
            print(f"  checked {i}/{len(targets)}")

    with open(PLAN_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(plan[0]) if plan else ["id"])
        w.writeheader()
        w.writerows(plan)
    found = [p for p in plan if p["groups"] or p["certifications"]]
    print(f"\nOwnership or a certification stated on own website: {len(found)} of {len(plan)}")
    for p in found:
        tag = ", ".join(x for x in (p["groups"], p["certifications"] and p["certifications"] + " (self-reported)") if x)
        print(f"  {p['business_name'][:40]:40} {tag:36} \"{p['quote'][:90]}\"")
    print(f"Plan with evidence -> {PLAN_FILE}")
    print("\nDRY RUN. Nothing written. Review the plan, then: python pipeline/website_ownership.py --apply")


def apply_plan(plan, rows, url, key):
    import requests
    found = [p for p in plan if p["groups"] or p["certifications"]]
    if any(p["certifications"] for p in found):
        try:
            common.fetch_all("self_reported_certification", key=key, table="businesses")
        except Exception:
            sys.exit("The self_reported_certification column does not exist yet. "
                     "Run add_self_reported_cert.sql in the Supabase SQL editor first.")
    by_id = {r["id"]: r for r in rows}
    backup = os.path.join(common.DATA_DIR, f"website_ownership_backup_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(backup, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "business_name", "minority_type"])
        for p in found:
            w.writerow([p["id"], p["business_name"], by_id[p["id"]].get("minority_type") or ""])
    H = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=minimal"}
    for p in found:
        body = {}
        if p["groups"]:
            body["minority_type"] = p["groups"]
        if p["certifications"]:
            body["self_reported_certification"] = p["certifications"]
        requests.patch(f"{url}/rest/v1/businesses", params={"id": f"eq.{p['id']}"}, headers=H,
                       json=body, timeout=30).raise_for_status()
    print(f"\nUpdated {len(found)} listing(s). Old values backed up to {backup}")


if __name__ == "__main__":
    main()
