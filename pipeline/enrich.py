"""
Kentucky Minority Business - Enrich
===================================
Post-upload enrichment of the Supabase `businesses` table, via Claude.
Replaces categorize_industries.py + fill_missing_services.py.

Runs two passes, in order (services uses the industry, so industries go first):
  1. Industries: assign a standardized category to rows missing `industry`.
  2. Services:   write a short services_products blurb for rows missing it.

Both passes skip rows that already have the field, so re-running is safe and
resumes naturally.

.env (repo root):
    SUPABASE_URL=...
    SUPABASE_KEY=...          # publishable key is fine (read + update under your RLS)
    ANTHROPIC_API_KEY=...

Usage:
    python pipeline/enrich.py                 # both passes
    python pipeline/enrich.py --industries    # industries only
    python pipeline/enrich.py --services      # services only
"""

import os
import time
import argparse
import anthropic
from supabase import create_client
from dotenv import load_dotenv

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
load_dotenv(os.path.join(REPO_ROOT, ".env"))

SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL         = "claude-sonnet-4-6"
SLEEP         = 1.2

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
CATEGORY_LIST = "\n".join(f"- {c}" for c in CATEGORIES)


def fetch_all(supabase, select, null_field):
    """Page through businesses where null_field IS NULL."""
    out, offset = [], 0
    while True:
        batch = (supabase.table("businesses").select(select)
                 .is_(null_field, "null").range(offset, offset + 999).execute())
        if not batch.data:
            break
        out.extend(batch.data)
        if len(batch.data) < 1000:
            break
        offset += 1000
    return out


def classify_industry(client, name, services):
    prompt = (f"You are classifying businesses into standardized industry categories.\n\n"
              f"Business Name: {name}\nServices / Products: {services or 'Not provided'}\n\n"
              f"Choose the single most appropriate category from this list:\n{CATEGORY_LIST}\n\n"
              f"Respond with ONLY the category name, exactly as written above. Nothing else.")
    txt = client.messages.create(model=MODEL, max_tokens=50,
            messages=[{"role": "user", "content": prompt}]).content[0].text.strip()
    if txt in CATEGORIES:
        return txt
    for c in CATEGORIES:
        if c.lower() in txt.lower() or txt.lower() in c.lower():
            return c
    return "Other Professional Services"


def infer_services(client, name, industry, address):
    prompt = (f"You are filling in missing business data for a minority business directory.\n\n"
              f"Business Name: {name}\nIndustry Category: {industry or 'Unknown'}\n"
              f"Address: {address or 'Kentucky'}\n\n"
              f"Write a brief 1-2 sentence description of the services or products this "
              f"business likely offers, based on the business name and industry. Be specific "
              f"and factual, do not fabricate details. If the name makes the services obvious, "
              f"state them plainly. If uncertain, keep it general but accurate to the industry.\n\n"
              f"Respond with ONLY the services description. No preamble, no quotes.")
    return client.messages.create(model=MODEL, max_tokens=150,
            messages=[{"role": "user", "content": prompt}]).content[0].text.strip()


def run_industries(supabase, claude):
    records = fetch_all(supabase, "id, business_name, services_products, industry", "industry")
    print(f"\n[Industries] needing categorization: {len(records)}")
    done = err = 0
    for i, r in enumerate(records, 1):
        try:
            ind = classify_industry(claude, r.get("business_name", ""), r.get("services_products", ""))
            supabase.table("businesses").update({"industry": ind}).eq("id", r["id"]).execute()
            done += 1
            print(f"  [{i}/{len(records)}] {str(r.get('business_name',''))[:42]:<42} -> {ind}")
            time.sleep(SLEEP)
        except Exception as e:
            err += 1
            print(f"  ERROR {r.get('business_name','?')}: {e}")
            time.sleep(2)
    print(f"[Industries] done: {done}, errors: {err}")


def run_services(supabase, claude):
    records = fetch_all(supabase, "id, business_name, industry, address, services_products",
                        "services_products")
    print(f"\n[Services] needing a description: {len(records)}")
    done = err = 0
    for i, r in enumerate(records, 1):
        try:
            svc = infer_services(claude, r.get("business_name", ""),
                                 r.get("industry", ""), r.get("address", ""))
            supabase.table("businesses").update({"services_products": svc}).eq("id", r["id"]).execute()
            done += 1
            print(f"  [{i}/{len(records)}] {str(r.get('business_name',''))[:42]:<42} -> {svc[:50]}...")
            time.sleep(SLEEP)
        except Exception as e:
            err += 1
            print(f"  ERROR {r.get('business_name','?')}: {e}")
            time.sleep(2)
    print(f"[Services] done: {done}, errors: {err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--industries", action="store_true", help="run only the industry pass")
    ap.add_argument("--services", action="store_true", help="run only the services pass")
    args = ap.parse_args()
    do_ind = args.industries or not args.services
    do_svc = args.services or not args.industries

    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    if do_ind:
        run_industries(supabase, claude)   # industries first
    if do_svc:
        run_services(supabase, claude)     # then services, which uses industry

    print("\nEnrichment complete.")


if __name__ == "__main__":
    main()
