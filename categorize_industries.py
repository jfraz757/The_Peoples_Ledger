"""
Industry Categorization Script
================================
Uses Claude API to assign a standardized industry category to each business
based on its name and services_products field, then writes the result to a
new 'industry' column in Supabase.

Requirements:
    pip install anthropic supabase python-dotenv

.env file:
    SUPABASE_URL=https://ursmecdpgtqckacyhnko.supabase.co
    SUPABASE_KEY=your_publishable_key_here
    ANTHROPIC_API_KEY=your_anthropic_key_here

Usage:
    python categorize_industries.py

Notes:
    - Processes in batches of 20 to stay within API rate limits
    - Saves progress after each batch so interrupted runs can resume
    - Skips records that already have an industry assigned
    - Estimated cost: ~$0.01-0.02 per 100 records
"""

import os
import time
import anthropic
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ───────────────────────────────────────────────────────────────────
SUPABASE_URL    = os.getenv("SUPABASE_URL")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY")
ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY")
BATCH_SIZE      = 20
SLEEP_BETWEEN   = 1.5  # seconds between API calls
# ─────────────────────────────────────────────────────────────────────────────

# Standardized industry categories
CATEGORIES = [
    "Accounting and Finance",
    "Architecture and Engineering",
    "Arts and Entertainment",
    "Business Consulting",
    "Construction and Remodeling",
    "Education and Training",
    "Food and Beverage",
    "Government and Public Services",
    "Health and Wellness",
    "Human Resources and DEI",
    "Information Technology",
    "Insurance and Risk Management",
    "Legal Services",
    "Logistics and Transportation",
    "Manufacturing and Industrial",
    "Marketing and Communications",
    "Media and Publishing",
    "Non-Profit and Social Services",
    "Real Estate and Property Management",
    "Retail and E-Commerce",
    "Staffing and Recruiting",
    "Travel and Hospitality",
    "Other Professional Services",
]

CATEGORY_LIST = "\n".join(f"- {c}" for c in CATEGORIES)


def classify_business(client, name, services):
    """Ask Claude to assign one industry category to a business."""
    prompt = f"""You are classifying businesses into standardized industry categories.

Business Name: {name}
Services / Products: {services or "Not provided"}

Choose the single most appropriate category from this list:
{CATEGORY_LIST}

Respond with ONLY the category name, exactly as written above. Nothing else."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )

    result = message.content[0].text.strip()

    # Validate response is in our list
    if result in CATEGORIES:
        return result
    # Fuzzy fallback — find closest match
    for cat in CATEGORIES:
        if cat.lower() in result.lower() or result.lower() in cat.lower():
            return cat
    return "Other Professional Services"


def main():
    print("Connecting to Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    claude   = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # Add industry column if it doesn't exist (safe to run multiple times)
    print("Fetching records without industry assigned...")
    all_records = []
    offset = 0
    while True:
        batch = supabase.table("businesses")            .select("id, business_name, services_products, industry")            .is_("industry", "null")            .range(offset, offset + 999)            .execute()
        if not batch.data:
            break
        all_records.extend(batch.data)
        if len(batch.data) < 1000:
            break
        offset += 1000
    to_process = all_records

    print(f"Total records:          {len(all_records)}")
    print(f"Already categorized:    {len(all_records) - len(to_process)}")
    print(f"Needs categorization:   {len(to_process)}")
    print()

    if not to_process:
        print("All records already categorized.")
        return

    processed = 0
    errors    = 0

    for i in range(0, len(to_process), BATCH_SIZE):
        batch = to_process[i:i + BATCH_SIZE]
        print(f"Processing batch {i // BATCH_SIZE + 1} ({i + 1}-{min(i + BATCH_SIZE, len(to_process))} of {len(to_process)})...")

        for record in batch:
            try:
                industry = classify_business(
                    claude,
                    record.get("business_name", ""),
                    record.get("services_products", "")
                )

                supabase.table("businesses")\
                    .update({"industry": industry})\
                    .eq("id", record["id"])\
                    .execute()

                processed += 1
                print(f"  [{processed}] {record['business_name'][:45]:<45} → {industry}")
                time.sleep(SLEEP_BETWEEN)

            except Exception as e:
                errors += 1
                print(f"  ERROR on {record.get('business_name', 'unknown')}: {e}")
                time.sleep(2)

        print(f"  Batch complete. Total processed: {processed}, Errors: {errors}")
        print()

    print(f"Done. {processed} records categorized, {errors} errors.")


if __name__ == "__main__":
    main()
