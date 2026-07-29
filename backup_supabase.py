"""
Dump every Supabase table to timestamped JSON in backups/.

Why this exists: as of July 2026 the anon role holds DELETE, TRUNCATE, UPDATE and INSERT
grants on both `businesses` and `submissions`, and the publishable key that maps to anon
is in index.html's page source. RLS policies are the only thing standing between that key
and the whole directory. Supabase's free tier has no automatic backups.

The directory is also expensive to rebuild -- it is the output of paid SerpApi scraping
plus two Claude enrichment passes, not just typing. Losing it costs money, not only time.

Run it before any schema/RLS/grant change, and on a schedule if you can.

    python backup_supabase.py

Reads SUPABASE_URL and the service role key straight out of admin.html so the key lives
in exactly one place on disk. admin.html is gitignored; so is backups/. Neither this
script nor its output should ever contain a hardcoded key.

Output: backups/YYYY-MM-DD_HHMMSS/<table>.json, one file per table, plus manifest.json.
The dump is the full row set including pending submissions and submitter emails, so treat
the backups/ directory as sensitive.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
ADMIN_FILE = BASE_DIR / "admin.html"
BACKUP_ROOT = BASE_DIR / "backups"

# Every table the admin panel touches. Add here if a new table is introduced.
TABLES = ["businesses", "submissions"]

# PostgREST caps rows per response (default 1000), so paginate rather than trusting
# a single request to return everything. Silent truncation is the failure mode that
# makes a backup worthless exactly when you need it.
PAGE_SIZE = 1000


def read_config():
    """Pull SUPABASE_URL and the service role key out of admin.html."""
    if not ADMIN_FILE.exists():
        sys.exit(f"admin.html not found at {ADMIN_FILE}. It is gitignored -- restore your local copy.")

    text = ADMIN_FILE.read_text(encoding="utf-8")
    url = re.search(r'SUPABASE_URL\s*=\s*"([^"]+)"', text)
    key = re.search(r'SUPABASE_ADMIN_KEY\s*=\s*"([^"]+)"', text)

    if not url or not key:
        sys.exit("Could not parse SUPABASE_URL / SUPABASE_ADMIN_KEY from admin.html.")

    # The service role key is required: the anon key cannot read pending submissions
    # (no anon SELECT policy), so an anon-key backup would silently omit the queue.
    if "service_role" not in _jwt_role(key.group(1)):
        sys.exit("The key in SUPABASE_ADMIN_KEY is not a service_role key -- backup would be incomplete.")

    return url.group(1).rstrip("/"), key.group(1)


def _jwt_role(token):
    """Best-effort role extraction from an unverified JWT payload, for the sanity check above."""
    try:
        import base64

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role", "")
    except Exception:
        return ""


def fetch_table(url, key, table):
    """Fetch all rows of one table, paginating until a short page comes back."""
    rows = []
    offset = 0

    while True:
        req = urllib.request.Request(
            f"{url}/rest/v1/{table}?select=*&order=id.asc&limit={PAGE_SIZE}&offset={offset}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                page = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            sys.exit(f"[{table}] HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            sys.exit(f"[{table}] network error: {e.reason}")

        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def main():
    url, key = read_config()

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = BACKUP_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"taken_at": datetime.now().isoformat(timespec="seconds"), "project_url": url, "tables": {}}

    for table in TABLES:
        rows = fetch_table(url, key, table)
        path = out_dir / f"{table}.json"
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest["tables"][table] = {"rows": len(rows), "bytes": path.stat().st_size}
        print(f"  {table:20} {len(rows):>6} rows  ->  {path.name}")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    total = sum(t["rows"] for t in manifest["tables"].values())
    if total == 0:
        # An empty dump almost always means an auth or URL problem, not an empty database.
        # Exit non-zero so a scheduled run surfaces it instead of quietly "succeeding".
        sys.exit("\nAll tables returned 0 rows -- treat this backup as FAILED and check the key.")

    print(f"\n{total} rows total -> {out_dir}")


if __name__ == "__main__":
    main()
