"""
Shared helpers for the pipeline scripts: paths, .env loading, Supabase keys,
paged reads, and archiving a finished cycle's working files.

Before October 2026 each script carried its own copy of these. .env was loaded six
different ways, the 1,000-row paging loop existed in about ten places, and the
service-role key was looked up under three different names depending on the script.
Copies drift: the July 2026 key mix-up (scripts silently writing with the public key)
started exactly that way. Add new shared behaviour here, not in a script.
"""

import os
import re
import shutil
import sys
from datetime import datetime

PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(PIPELINE_DIR)
DATA_DIR     = os.path.join(REPO_ROOT, "data")
ENV_FILE     = os.path.join(REPO_ROOT, ".env")

PROJECT_REF  = "ursmecdpgtqckacyhnko"   # The People's Ledger (not Candidate Voice)
PAGE_SIZE    = 1000                     # PostgREST's max rows per response


# --- .env -------------------------------------------------------------------
_env_loaded = False


def load_env():
    """Load REPO_ROOT/.env into os.environ once. Values already set in the real
    environment win. Works with or without python-dotenv installed."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    if not os.path.exists(ENV_FILE):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE)
        return
    except ImportError:
        pass
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def env(name, default=""):
    load_env()
    return os.getenv(name, default)


# --- Supabase keys ------------------------------------------------------------
def supabase_url():
    return env("SUPABASE_URL").rstrip("/")


def publishable_key():
    """The public read key (the same one index.html ships). SELECT only."""
    return env("SUPABASE_KEY")


def service_key():
    """The service-role key. SUPABASE_SERVICE_ROLE_KEY is the canonical name; the
    two older spellings some scripts used are still accepted."""
    return (env("SUPABASE_SERVICE_ROLE_KEY") or env("SUPABASE_SERVICE_KEY")
            or env("SUPABASE_SECRET_KEY"))


def require_service_credentials(purpose="writes to the database"):
    """(url, service_key), or exit with a clear message. Also refuses to run
    against any project other than The People's Ledger."""
    url, key = supabase_url(), service_key()
    if not url or not key:
        sys.exit(
            f"SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env -- this script "
            f"{purpose}, which the publishable key cannot do. Do not substitute the "
            f"publishable key, and do not re-grant anon write access to make it work."
        )
    if PROJECT_REF not in url:
        sys.exit(f"Refusing to run: SUPABASE_URL is not the People's Ledger project.\n  got: {url}")
    return url, key


# --- reads ------------------------------------------------------------------
def fetch_all(select, key=None, table="businesses", order="id.asc", timeout=60):
    """Every row of `table`, paging past PostgREST's 1,000-row cap. Raises on an
    HTTP error rather than returning a silently truncated list.

    Reads with the service key when .env has one. The publishable key cannot see
    listings hidden for lacking ownership evidence (hide_unevidenced_listings.sql), so
    a duplicate check made with it would miss them and let a scrape re-add them."""
    import requests
    key = key or service_key() or publishable_key()
    url = supabase_url()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and a Supabase key must be set in .env")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}
    select = select.replace(" ", "")
    rows, offset = [], 0
    while True:
        r = requests.get(f"{url}/rest/v1/{table}", headers=headers, timeout=timeout,
                         params={"select": select, "order": order,
                                 "limit": PAGE_SIZE, "offset": offset})
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


# --- cycle archiving ----------------------------------------------------------
# Working files that belong to ONE quarterly cycle. Leaving them in data/ is what made
# prepare.py re-process 1,670 old rows in July 2026, and what makes scrape.py treat a
# finished cycle's progress file as "everything already done".
CYCLE_FILES = [
    "scraper_progress.json",
    "businesses_scraped.csv",
    "businesses_scraped_checkpoint.csv",
    "businesses_scraped_sources.csv",
    "businesses_scraped_categories.csv",
    "businesses_scraped_categories_sources.csv",
    "businesses_prepared.csv",
]


def archive_files(names, label=""):
    """Move the named data/ files into data/archive/<timestamp>[_label]/. Nothing is
    deleted. Returns (archive_dir, moved_names)."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    folder = stamp + (f"_{re.sub(r'[^a-z0-9-]+', '-', label.lower())}" if label else "")
    archive_dir = os.path.join(DATA_DIR, "archive", folder)
    moved = []
    for name in names:
        src = os.path.join(DATA_DIR, name)
        if os.path.exists(src):
            os.makedirs(archive_dir, exist_ok=True)
            shutil.move(src, os.path.join(archive_dir, name))
            moved.append(name)
    return archive_dir, moved
