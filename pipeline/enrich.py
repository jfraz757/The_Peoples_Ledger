"""
Kentucky Minority Business - Enrich (v2)
========================================
Post-upload enrichment of the Supabase `businesses` table, via Claude.

What changed from v1
--------------------
v1 only ever touched rows where the field was NULL, so any row that arrived
with a bad-but-non-null value (an off-list industry label, a too-short services
blurb, or a defensible-but-unhelpful category like a grocery filed under Retail)
was invisible to it forever. v2 can repair existing data:

  * Industries pass now targets rows that are NULL *or* carry an off-list label
    (anything not in the 23 categories), and normalizes them to the list.
  * Services pass now targets rows that are NULL *or* shorter than MIN_SERVICES_LEN,
    expanding thin blurbs instead of skipping them.
  * New --reclassify mode re-evaluates rows already sitting in named buckets
    (e.g. Retail and E-Commerce) and moves them only when the label actually
    changes. This is how a mis-shelved grocery gets corrected to Food and Beverage.
  * The classify prompt now carries disambiguation rules (food businesses are
    Food and Beverage, not Retail), so new scrapes stop mis-shelving in the first place.
  * --dry-run prints every proposed change without writing.
  * --limit N caps how many rows are processed, for testing small first.

Ordering still matters: industries before services, because services uses the
industry. Recommended full sequence after the deterministic SQL renames:
    python pipeline/enrich.py --industries --dry-run
    python pipeline/enrich.py --industries
    python pipeline/enrich.py --reclassify "Retail and E-Commerce,Other Professional Services" --dry-run
    python pipeline/enrich.py --reclassify "Retail and E-Commerce,Other Professional Services"
    python pipeline/enrich.py --services --dry-run
    python pipeline/enrich.py --services

.env (repo root):
    SUPABASE_URL=...
    SUPABASE_KEY=...          # publishable key is fine (read + update under your RLS)
    ANTHROPIC_API_KEY=...
"""

import os
import time
import argparse
import anthropic
from dotenv import load_dotenv

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PIPELINE_DIR)
load_dotenv(os.path.join(REPO_ROOT, ".env"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = "claude-sonnet-4-6"
SLEEP = 1.2
MIN_SERVICES_LEN = 25  # services text shorter than this is treated as "thin"

CATEGORIES = [
    "Accounting and Finance", "Architecture and Engineering",
    "Arts and Entertainment", "Business Consulting",
    "Construction and Remodeling", "Education and Training",
    "Food and Beverage", "Government and Public Services",
    "Health and Wellness", "Human Resources and DEI",
    "Information Technology", "Insurance and Risk Management",
    "Legal Services", "Logistics and Transportation",
    "Manufacturing and Industrial", "Marketing and Communications",
    "Media and Publishing", "Non-Profit and Social Services",
    "Real Estate and Property Management", "Retail and E-Commerce",
    "Staffing and Recruiting", "Travel and Hospitality",
    "Other Professional Services",
]
CATEGORIES_SET = set(CATEGORIES)
CATEGORY_LIST = "\n".join(f"- {c}" for c in CATEGORIES)


# --- selection helpers (pure, unit-tested) ----------------------------------
def is_blank(value):
    return value is None or str(value).strip() == ""


def industry_needs_work(industry):
    """True if industry is null/blank or not one of the 23 canonical labels."""
    if is_blank(industry):
        return True
    return str(industry).strip() not in CATEGORIES_SET


def services_needs_work(services):
    """True if services text is null/blank or shorter than MIN_SERVICES_LEN."""
    if is_blank(services):
        return True
    return len(str(services).strip()) < MIN_SERVICES_LEN


def validate_category(txt):
    """Coerce a model reply to exactly one canonical category."""
    txt = (txt or "").strip()
    if txt in CATEGORIES_SET:
        return txt
    for c in CATEGORIES:
        if c.lower() == txt.lower():
            return c
    for c in CATEGORIES:
        if c.lower() in txt.lower() or txt.lower() in c.lower():
            return c
    return "Other Professional Services"


# --- prompts -----------------------------------------------------------------
def classify_industry(client, name, services):
    prompt = (
        "You are classifying a business into exactly one standardized industry "
        "category for a consumer-facing directory. Pick the category an everyday "
        "customer would look under, not the most technically literal one.\n\n"
        f"Business Name: {name}\nServices / Products: {services or 'Not provided'}\n\n"
        "Categories:\n" + CATEGORY_LIST + "\n\n"
        "Disambiguation rules:\n"
        "- Any business that sells food or drink to the public (grocery store, "
        "food market, ethnic or specialty market, restaurant, cafe, coffee shop, "
        "bakery, deli, butcher, caterer, food truck, juice bar, brewery, "
        "distillery) is 'Food and Beverage', even though selling goods is "
        "technically retail.\n"
        "- 'Retail and E-Commerce' is for NON-food goods: clothing, beauty "
        "products, electronics, general merchandise, online shops.\n"
        "- Use 'Other Professional Services' only when nothing more specific "
        "clearly fits, not as a default guess.\n\n"
        "Respond with ONLY the exact category name from the list. Nothing else."
    )
    txt = client.messages.create(
        model=MODEL, max_tokens=50,
        messages=[{"role": "user", "content": prompt}]).content[0].text.strip()
    return validate_category(txt)


def infer_services(client, name, industry, address, existing):
    if not is_blank(existing):
        instruction = ("Expand this brief note into a fuller one or two sentence "
                       f"description, keeping every fact in it: {existing}")
    else:
        instruction = ("Write a brief one or two sentence description of what this "
                       "business offers, based on its name and industry.")
    prompt = (
        "You are writing a short, factual services description for a consumer "
        "business directory. Lead with the plain business type in the words a "
        "customer would actually search (for example 'grocery store', 'law firm', "
        "'hair salon', 'general contractor', 'coffee shop'), then add specifics "
        "only if clearly implied by the name. Do not invent awards, years in "
        "business, specific clients, or any claim you cannot reasonably infer.\n\n"
        f"Business Name: {name}\nIndustry: {industry or 'Unknown'}\n"
        f"Address: {address or 'Kentucky'}\n\n"
        f"{instruction}\n\n"
        "Respond with ONLY the description. No preamble, no quotes."
    )
    return client.messages.create(
        model=MODEL, max_tokens=150,
        messages=[{"role": "user", "content": prompt}]).content[0].text.strip()


# --- supabase access ---------------------------------------------------------
def fetch_all_rows(supabase, select):
    out, offset = [], 0
    while True:
        batch = (supabase.table("businesses").select(select)
                 .range(offset, offset + 999).execute())
        if not batch.data:
            break
        out.extend(batch.data)
        if len(batch.data) < 1000:
            break
        offset += 1000
    return out


def fetch_in_buckets(supabase, buckets):
    return (supabase.table("businesses")
            .select("id, business_name, services_products, industry")
            .in_("industry", buckets).execute().data)


def update_field(supabase, row_id, field, value):
    supabase.table("businesses").update({field: value}).eq("id", row_id).execute()


# --- passes ------------------------------------------------------------------
def run_industries(supabase, claude, dry_run, limit):
    rows = [r for r in fetch_all_rows(
        supabase, "id, business_name, services_products, industry")
        if industry_needs_work(r.get("industry"))]
    if limit:
        rows = rows[:limit]
    print(f"\n[Industries] null or off-list: {len(rows)}"
          f"{' (DRY RUN)' if dry_run else ''}")
    done = err = 0
    for i, r in enumerate(rows, 1):
        try:
            old = r.get("industry")
            new = classify_industry(claude, r.get("business_name", ""),
                                    r.get("services_products", ""))
            tag = f"{old or '(blank)'} -> {new}"
            print(f"  [{i}/{len(rows)}] {str(r.get('business_name',''))[:38]:<38} {tag}")
            if not dry_run:
                update_field(supabase, r["id"], "industry", new)
            done += 1
            time.sleep(SLEEP)
        except Exception as e:
            err += 1
            print(f"  ERROR {r.get('business_name','?')}: {e}")
            time.sleep(2)
    print(f"[Industries] {'would change' if dry_run else 'changed'}: {done}, errors: {err}")


def run_reclassify(supabase, claude, buckets, dry_run, limit):
    rows = fetch_in_buckets(supabase, buckets)
    if limit:
        rows = rows[:limit]
    print(f"\n[Reclassify] reviewing {len(rows)} rows in {buckets}"
          f"{' (DRY RUN)' if dry_run else ''}")
    moved = same = err = 0
    for i, r in enumerate(rows, 1):
        try:
            old = r.get("industry")
            new = classify_industry(claude, r.get("business_name", ""),
                                    r.get("services_products", ""))
            if new != old:
                moved += 1
                print(f"  MOVE {str(r.get('business_name',''))[:38]:<38} {old} -> {new}")
                if not dry_run:
                    update_field(supabase, r["id"], "industry", new)
            else:
                same += 1
            time.sleep(SLEEP)
        except Exception as e:
            err += 1
            print(f"  ERROR {r.get('business_name','?')}: {e}")
            time.sleep(2)
    print(f"[Reclassify] {'would move' if dry_run else 'moved'}: {moved}, "
          f"unchanged: {same}, errors: {err}")


def run_services(supabase, claude, dry_run, limit):
    rows = [r for r in fetch_all_rows(
        supabase, "id, business_name, industry, address, services_products")
        if services_needs_work(r.get("services_products"))]
    if limit:
        rows = rows[:limit]
    print(f"\n[Services] null or thin (< {MIN_SERVICES_LEN} chars): {len(rows)}"
          f"{' (DRY RUN)' if dry_run else ''}")
    done = err = 0
    for i, r in enumerate(rows, 1):
        try:
            svc = infer_services(claude, r.get("business_name", ""),
                                 r.get("industry", ""), r.get("address", ""),
                                 r.get("services_products", ""))
            print(f"  [{i}/{len(rows)}] {str(r.get('business_name',''))[:32]:<32} -> {svc[:54]}")
            if not dry_run:
                update_field(supabase, r["id"], "services_products", svc)
            done += 1
            time.sleep(SLEEP)
        except Exception as e:
            err += 1
            print(f"  ERROR {r.get('business_name','?')}: {e}")
            time.sleep(2)
    print(f"[Services] {'would write' if dry_run else 'wrote'}: {done}, errors: {err}")


def run_reenrich_services(supabase, claude, buckets, dry_run, limit):
    """Rewrite services text for every row in the named industry buckets,
    regardless of current length, so each description leads with the plain
    searchable business type (e.g. 'grocery store'). Existing facts are kept;
    this exists to make long-but-unsearchable descriptions findable."""
    rows = fetch_in_buckets(supabase, buckets)
    # fetch_in_buckets selects a limited column set; pull address too
    ids = [r["id"] for r in rows]
    full = {r["id"]: r for r in fetch_all_rows(
        supabase, "id, business_name, industry, address, services_products")
        if r["id"] in set(ids)}
    rows = [full[i] for i in ids if i in full]
    if limit:
        rows = rows[:limit]
    print(f"\n[Re-enrich services] rewriting {len(rows)} rows in {buckets}"
          f"{' (DRY RUN)' if dry_run else ''}")
    done = err = 0
    for i, r in enumerate(rows, 1):
        try:
            svc = infer_services(claude, r.get("business_name", ""),
                                 r.get("industry", ""), r.get("address", ""),
                                 r.get("services_products", ""))
            print(f"  [{i}/{len(rows)}] {str(r.get('business_name',''))[:30]:<30} -> {svc[:56]}")
            if not dry_run:
                update_field(supabase, r["id"], "services_products", svc)
            done += 1
            time.sleep(SLEEP)
        except Exception as e:
            err += 1
            print(f"  ERROR {r.get('business_name','?')}: {e}")
            time.sleep(2)
    print(f"[Re-enrich services] {'would rewrite' if dry_run else 'rewrote'}: {done}, errors: {err}")


def reenrich_if_grocery(client, name, existing):
    """One call per row. If the business is a grocery retailer (sells food/goods
    to take home), return a rewritten description that leads with the searchable
    type and keeps the existing details. If it serves prepared food or drink,
    return the literal word SKIP."""
    prompt = (
        "You are deciding whether a Food and Beverage business is a GROCERY "
        "RETAILER (a place a customer goes to buy food and goods to take home: "
        "grocery store, supermarket, food market, ethnic or international market, "
        "butcher, produce stand, specialty food shop) versus a PREPARED-FOOD "
        "business (restaurant, cafe, coffee shop, bar, brewery, distillery, "
        "caterer, food truck, or a bakery selling prepared items).\n\n"
        f"Business Name: {name}\nCurrent description: {existing or 'Not provided'}\n\n"
        "If it is a GROCERY RETAILER, write a one or two sentence description that "
        "LEADS with the plain type a shopper would search (for example 'grocery "
        "store', 'Asian grocery store and food market', 'Latino supermarket and "
        "grocery'), using the word grocery or market plainly, and keep the "
        "existing product details.\n"
        "If it is a PREPARED-FOOD business, respond with exactly: SKIP\n\n"
        "Respond with ONLY the description, or the single word SKIP. No preamble, "
        "no quotes."
    )
    return client.messages.create(
        model=MODEL, max_tokens=160,
        messages=[{"role": "user", "content": prompt}]).content[0].text.strip()


def run_reenrich_groceries(supabase, claude, dry_run, limit):
    """Walk the Food and Beverage bucket, ask the model per row whether it is a
    grocery retailer, and rewrite only those, leaving restaurants and the rest
    untouched."""
    rows = [r for r in fetch_all_rows(
        supabase, "id, business_name, industry, services_products")
        if (r.get("industry") or "").strip() == "Food and Beverage"]
    if limit:
        rows = rows[:limit]
    print(f"\n[Re-enrich groceries] reviewing {len(rows)} Food and Beverage rows"
          f"{' (DRY RUN)' if dry_run else ''}")
    rewrote = skipped = err = 0
    for i, r in enumerate(rows, 1):
        try:
            out = reenrich_if_grocery(claude, r.get("business_name", ""),
                                      r.get("services_products", ""))
            if out.strip().rstrip(".").upper() == "SKIP":
                skipped += 1
                time.sleep(SLEEP)
                continue
            print(f"  GROCERY {str(r.get('business_name',''))[:30]:<30} -> {out[:52]}")
            if not dry_run:
                update_field(supabase, r["id"], "services_products", out)
            rewrote += 1
            time.sleep(SLEEP)
        except Exception as e:
            err += 1
            print(f"  ERROR {r.get('business_name','?')}: {e}")
            time.sleep(2)
    print(f"[Re-enrich groceries] {'would rewrite' if dry_run else 'rewrote'}: "
          f"{rewrote}, skipped (prepared-food): {skipped}, errors: {err}")


def main():
    ap = argparse.ArgumentParser(description="Enrich and repair the businesses table.")
    ap.add_argument("--industries", action="store_true",
                    help="repair null or off-list industry labels")
    ap.add_argument("--services", action="store_true",
                    help="fill null or expand thin services text")
    ap.add_argument("--reclassify", metavar="BUCKETS",
                    help="comma-separated industry buckets to re-evaluate, "
                         "moving rows only when the label changes")
    ap.add_argument("--reenrich-services", metavar="BUCKETS",
                    help="comma-separated industry buckets whose services text "
                         "should be rewritten regardless of length, to lead with "
                         "the searchable business type")
    ap.add_argument("--reenrich-groceries", action="store_true",
                    help="per-row classifier over Food and Beverage; rewrites "
                         "only the grocery and market businesses, skips "
                         "restaurants and other prepared-food businesses")
    ap.add_argument("--dry-run", action="store_true",
                    help="print proposed changes without writing")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap rows processed (0 = no cap)")
    args = ap.parse_args()

    # default (no pass flags): run the two repair passes, not reclassify
    explicit = (args.industries or args.services or args.reclassify
                or args.reenrich_services or args.reenrich_groceries)
    do_ind = args.industries or not explicit
    do_svc = args.services or not explicit

    from supabase import create_client  # imported here so tests need no network
    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    if do_ind:
        run_industries(supabase, claude, args.dry_run, args.limit)
    if args.reclassify:
        buckets = [b.strip() for b in args.reclassify.split(",") if b.strip()]
        run_reclassify(supabase, claude, buckets, args.dry_run, args.limit)
    if args.reenrich_services:
        buckets = [b.strip() for b in args.reenrich_services.split(",") if b.strip()]
        run_reenrich_services(supabase, claude, buckets, args.dry_run, args.limit)
    if args.reenrich_groceries:
        run_reenrich_groceries(supabase, claude, args.dry_run, args.limit)
    if do_svc:
        run_services(supabase, claude, args.dry_run, args.limit)

    print("\nEnrichment complete." + (" (dry run, nothing written)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
