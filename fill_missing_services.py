"""
Services Gap Filler
====================
Uses Claude API to generate a brief services description for businesses
that have no services_products entry in Supabase.

Requirements:
    pip install anthropic supabase python-dotenv

Usage:
    python fill_missing_services.py
"""

import os
import time
import anthropic
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ───────────────────────────────────────────────────────────────────
SUPABASE_URL  = os.getenv("SUPABASE_URL")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
SLEEP         = 1.0
# ─────────────────────────────────────────────────────────────────────────────


def infer_services(client, name, industry, address):
    """Ask Claude to infer a brief services description."""
    prompt = f"""You are filling in missing business data for a minority business directory.

Business Name: {name}
Industry Category: {industry or "Unknown"}
Address: {address or "Kentucky"}

Write a brief 1-2 sentence description of the services or products this business likely offers,
based on the business name and industry. Be specific and factual — do not fabricate details.
If the business name makes the services obvious, state them plainly.
If uncertain, keep it general but accurate to the industry.

Respond with ONLY the services description. No preamble, no quotes."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text.strip()


def main():
    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    claude   = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    print("Fetching records with missing services...")
    response = supabase.table("businesses")\
        .select("id, business_name, industry, address, services_products")\
        .or_("services_products.is.null,services_products.eq.")\
        .execute()

    records = response.data
    print(f"Records to fill: {len(records)}")
    print()

    updated = 0
    errors  = 0

    for i, record in enumerate(records, 1):
        name     = record.get("business_name", "")
        industry = record.get("industry", "")
        address  = record.get("address", "")

        print(f"[{i}/{len(records)}] {name}")

        try:
            services = infer_services(claude, name, industry, address)
            supabase.table("businesses")\
                .update({"services_products": services})\
                .eq("id", record["id"])\
                .execute()

            print(f"  → {services[:80]}...")
            updated += 1

        except Exception as e:
            errors += 1
            print(f"  ERROR: {e}")

        time.sleep(SLEEP)

    print(f"\nDone. {updated} updated, {errors} errors.")


if __name__ == "__main__":
    main()
