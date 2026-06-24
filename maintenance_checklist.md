# The People's Ledger — Maintenance Checklist

**Supabase project:** `ursmecdpgtqckacyhnko`
**Local repo:** `C:\Users\jfraz\The_Peoples_Ledger`
**Python:** `C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe`

---

## Monthly (run every month)

Run from Git Bash in the repo root:

```bash
bash monthly_link_check.sh
```

| Script | What it does | Cost |
|---|---|---|
| `check_link_status.py` | Re-checks all website URLs, updates status field (Active / Inactive / No Website) | Free |

---

## Quarterly (run ~4x per year)

### Step 0 — Manual downloads first (CAPTCHA-protected, must do by hand)

- [ ] **Louisville HRC CSV** — download from diversitycompliance.com
  - Still includes `Ethnicity` and `Certification Type` fields as of June 2026
  - This is your most reliable source for certified businesses
- [ ] **KY Transportation Cabinet** — B2GNow portal export
  - Note: minority type fields removed from export as of 2026 (anti-DEI legislation)
- [ ] **KY Finance & Administration Cabinet** — MWBE listing `.xlsx`, convert to CSV
  - Same note: minority type fields no longer included

Then run:

```bash
bash quarterly_refresh.sh
```

### Pipeline order (the script runs these in sequence)

| Step | Script | What it does | Cost |
|---|---|---|---|
| 1 | `ky_minority_business_scraper.py` | Scrapes new businesses from web | ~$9/1000 records |
| 2 | `clean_ky_businesses.py` | Deduplicates and merges CSV records | Free |
| 3 | `upload_to_supabase.py` | Loads cleaned CSV to Supabase in batches of 100 | Free |
| 4 | `categorize_industries.py` | Assigns industry category via Claude API | ~$0.75-$1.00/1000 records |
| 5 | `fill_missing_services.py` | Fills missing service descriptions via Claude API | Low |

### After quarterly refresh — verify these

- [ ] Review any script errors before closing Git Bash
- [ ] Spot-check new records in the Supabase dashboard
- [ ] If any **new industry categories** were added, update the hardcoded industry pills in `index.html`
- [ ] Update record count in `README.md` if it changed
- [ ] Run `monthly_link_check.sh` if not already done this month

---

## As Needed

| Script | When to use | Cost |
|---|---|---|
| `fix_buyblack_urls.py` | When you spot buyblack.org placeholder URLs in the directory | SerpApi |
| `view_database.py` | Local exploration of the CSV in D-Tale | Free |
| `data_gather_.ipynb` | Jupyter notebook for data prep and exploration | Free |

---

## Quick Reminders

- **Never push `admin.html` or `.env` to GitHub** — both are gitignored
- **CSV files are gitignored** — they are working files only
- **If you add a new industry category**, update both `categorize_industries.py` AND the hardcoded pills in `index.html`
- **If the `businesses` schema changes**, update the `search_businesses` and `suggest_search` RPC functions in Supabase — they are not auto-updated
- **Always test locally** before pushing: `python -m http.server 8080` from repo root, then open `http://localhost:8080/index.html`
