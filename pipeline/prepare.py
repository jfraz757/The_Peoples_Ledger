"""
Kentucky Minority Business - Prepare
====================================
ONE step that replaces both triage_ky_v2.py and clean_ky_businesses.py.

It reads the raw scraper output and produces a SINGLE file,
data/businesses_prepared.csv, with a Disposition column you can filter:

  Good to go    Kentucky business, not a chain, ready to upload. These rows are
                also deduplicated and merged (the old clean step).
  Needs review  Organic or social find with no address. Verify it is Kentucky
                before you promote it. Edit its Disposition to "Good to go" to
                keep it, or "Dropped" to discard it.
  Dropped       A chain/franchise or an out-of-state address. See the Reason
                column. Nothing is deleted; you can override any call.

Workflow:
  1. Run this.
  2. Open businesses_prepared.csv (Supabase staging table, Excel, whatever you
     like) and work the "Needs review" rows: flip the keepers to "Good to go".
  3. Run upload_to_supabase.py. It uploads ONLY the "Good to go" rows.

Usage:
    python prepare.py
"""

import os
import re
import pandas as pd

# Portable paths: data/ sits next to the pipeline/ folder this script lives in.
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
DATA_DIR     = os.path.join(REPO_ROOT, "data")

INPUT_FILE   = os.path.join(DATA_DIR, "businesses_scraped.csv")
SOURCES_FILE = os.path.join(DATA_DIR, "businesses_scraped_sources.csv")
OUTPUT_FILE  = os.path.join(DATA_DIR, "businesses_prepared.csv")

OUTPUT_COLUMNS = [
    "Business Name", "Address", "Phone", "Services / Products",
    "Website", "Minority Type", "Status", "Kentucky Based",
    "Disposition", "Reason", "Source",
]

CHAIN_BLOCKLIST = [
    "walmart", "starbucks", "mcdonald", "qdoba", "buc-ee", "hobby lobby",
    "lowe's", "lowes", "home depot", "jcpenney", "jc penney", "kay jewelers",
    "belk", "bealls", "t.j. maxx", "tj maxx", "planet fitness", "dollar general",
    "office depot", "rural king", "kroger", "target", "fedex", "u-haul", "uhaul",
    "enterprise rent", "great american cookies", "cheddar's", "mellow mushroom",
    "duluth trading", "plato's closet", "clothes mentor", "goodwill",
    "factory connection", "airgas", "ace hardware", "holiday inn", "baymont",
    "wyndham", "by ihg", "neil huffman", "harley-davidson", "mclane",
    "international paper", "jabil", "metalsa", "kruger packaging", "servpro",
    "fastsigns", "signarama", "golden corral", "bath & body works",
    "dwain taylor", "david taylor chrysler", "glockner", "identogo",
    "earthwise pet", "agave & rye", "uptown cheapskate", "keller williams",
    "el toro ip targeting",
]

US_STATES = ("al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il",
             "in","ia","ks","la","me","md","ma","mi","mn","ms","mo","mt","ne",
             "nv","nh","nj","nm","ny","nc","nd","oh","ok","or","pa","ri","sc",
             "sd","tn","tx","ut","vt","va","wa","wv","wi","wy","dc")


# ── classification helpers ────────────────────────────────────────────────────
def addr_state(addr):
    a = str(addr or "").lower().strip()
    if not a:
        return "blank"
    if "kentucky" in a or re.search(r",?\s*ky\b", a):
        return "KY"
    m = re.search(r",\s*([a-z]{2})\s*\d{5}", a)
    if m and m.group(1) in US_STATES and m.group(1) != "ky":
        return "other-state"
    return "unclear"


def is_chain(name):
    n = str(name or "").lower()
    return any(c in n for c in CHAIN_BLOCKLIST)


def classify(row):
    """Return (disposition, reason)."""
    if is_chain(row["Business Name"]):
        return "Dropped", "chain/franchise"
    if row["_state"] == "other-state":
        return "Dropped", "out-of-state address"
    if row["_state"] == "blank" and row["Source"] in ("organic", "social", "unknown"):
        return "Needs review", "no address, verify Kentucky"
    return "Good to go", ""


# ── merge helpers (from the old clean step) ───────────────────────────────────
def most_complete(vals):
    vals = [v for v in vals if pd.notna(v) and str(v).strip() not in ("", "nan", "N/A")]
    return max(vals, key=len) if vals else ""


def best_minority_type(types):
    types = [t for t in types if pd.notna(t) and str(t).strip()]
    if not types:
        return ""
    specific = [t for t in types if "Minority-Owned (general)" not in t]
    pool = specific if specific else types
    return max(pool, key=len)


def best_ky(vals):
    vals = [v for v in vals if pd.notna(v) and str(v).strip()]
    return "Yes" if "Yes" in vals else (vals[0] if vals else "")


def merge_goodtogo(group):
    return pd.Series({
        "Address":             most_complete(group["Address"].tolist()),
        "Phone":               next((p for p in group["Phone"] if str(p).strip()), ""),
        "Services / Products": most_complete(group["Services / Products"].tolist()),
        "Website":             next((w for w in group["Website"] if str(w).strip()), ""),
        "Minority Type":       best_minority_type(group["Minority Type"].tolist()),
        "Status":              "",
        "Kentucky Based":      best_ky(group["Kentucky Based"].tolist()),
        "Disposition":         "Good to go",
        "Reason":              "",
        "Source":              ", ".join(sorted(set(s for s in group["Source"] if str(s).strip()))),
    })


def main():
    print(f"Loading: {INPUT_FILE}")
    df = pd.read_csv(INPUT_FILE, encoding="utf-8-sig").fillna("")
    print(f"Rows in: {len(df)}")

    if os.path.exists(SOURCES_FILE):
        src = pd.read_csv(SOURCES_FILE, encoding="utf-8-sig").fillna("")
        src["Source"] = src["Source"].replace("", "google_maps")
        src = src[["Business Name", "Website", "Source"]].drop_duplicates(["Business Name", "Website"])
        df = df.merge(src, on=["Business Name", "Website"], how="left")
        df["Source"] = df["Source"].fillna("google_maps")
    else:
        df["Source"] = "unknown"

    df["_state"] = df["Address"].apply(addr_state)
    df[["Disposition", "Reason"]] = df.apply(lambda r: pd.Series(classify(r)), axis=1)
    # Pre-fill Kentucky Based from the address; Status is left for review.
    df["Kentucky Based"] = df["_state"].apply(lambda s: "Yes" if s == "KY" else "")
    df["Status"] = ""

    good = df[df["Disposition"] == "Good to go"].copy()
    other = df[df["Disposition"] != "Good to go"].copy()

    # Deduplicate and merge only the Good to go rows (this is the old clean step).
    if len(good):
        merged = (good.groupby("Business Name", sort=False)
                      .apply(merge_goodtogo, include_groups=False)
                      .reset_index())
    else:
        merged = pd.DataFrame(columns=["Business Name"] + OUTPUT_COLUMNS[1:])

    other = other[OUTPUT_COLUMNS]
    merged = merged[OUTPUT_COLUMNS]
    out = pd.concat([merged, other], ignore_index=True)
    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print(f"\nWrote {len(out)} rows -> {os.path.basename(OUTPUT_FILE)}")
    print("\nBy disposition:")
    print(out["Disposition"].value_counts().to_string())
    dropped = out[out["Disposition"] == "Dropped"]
    if len(dropped):
        print("\nDropped by reason:")
        print(dropped["Reason"].value_counts().to_string())
    print("\nNext: review the 'Needs review' rows, flip keepers to 'Good to go', "
          "then run upload_to_supabase.py.")


if __name__ == "__main__":
    main()
