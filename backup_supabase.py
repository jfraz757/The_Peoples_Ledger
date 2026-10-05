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

PRUNING (opt-in; the scheduled task does not prune)
The daily task never deleted anything, so backups/ grows by one folder a day forever.

    python backup_supabase.py --prune          # REPORT what would be removed; deletes nothing
    python backup_supabase.py --prune --yes    # actually remove those folders

Kept: every backup from the last KEEP_DAYS days, plus the earliest backup of each
calendar month (a monthly history going back indefinitely). Only folders named like
YYYY-MM-DD_HHMMSS that contain a manifest.json are ever considered, and the newest
backup is always kept.
"""

import json
import re
import shutil
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


KEEP_DAYS = 14
_STAMP_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_\d{6}$")


def prune(apply=False):
    """Report (or, with apply=True, remove) daily backups outside the retention rule."""
    folders = sorted(p for p in BACKUP_ROOT.iterdir()
                     if p.is_dir() and _STAMP_RE.match(p.name) and (p / "manifest.json").exists())
    if not folders:
        print("No backups found.")
        return
    cutoff = datetime.now().timestamp() - KEEP_DAYS * 86400
    first_of_month = {}
    for p in folders:                                  # sorted, so the first seen is earliest
        first_of_month.setdefault(p.name[:7], p)
    keep = set(first_of_month.values()) | {folders[-1]}
    for p in folders:
        if datetime.strptime(p.name, "%Y-%m-%d_%H%M%S").timestamp() >= cutoff:
            keep.add(p)
    remove = [p for p in folders if p not in keep]

    print(f"{len(folders)} backups: keeping {len(keep)}, "
          f"{'removing' if apply else 'would remove'} {len(remove)}.")
    for p in remove:
        print(f"  {'removing' if apply else 'would remove'} {p.name}")
        if apply:
            shutil.rmtree(p)
    if remove and not apply:
        print("\nNothing deleted. Re-run with --prune --yes to remove these.")


def main():
    if "--prune" in sys.argv:
        prune(apply="--yes" in sys.argv)
        return
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
