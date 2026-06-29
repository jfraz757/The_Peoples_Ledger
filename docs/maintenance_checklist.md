# The People's Ledger Maintenance Checklist

**Supabase project:** `ursmecdpgtqckacyhnko`
**Local repo:** `C:\Users\jfraz\The_Peoples_Ledger`
**Python:** `C:/Users/jfraz/AppData/Local/Python/pythoncore-3.14-64/python.exe`

> All scripts now live in `pipeline/` and derive their own paths, so run everything from the repo root in Git Bash.

---

## Monthly (run every month)

```bash
bash monthly_link_check.sh
```

| Script | What it does | Cost |
|---|---|---|
| `pipeline/maintain.py` | Re-checks all website URLs, updates the `status` field (Active / Inactive / No Website) | Free |

`maintain.py` can also fix buyblack.org placeholder URLs, but only when you pass `--buyblack`, which spends SerpApi. The monthly run does not pass it.

---

## Quarterly (run ~4x per year)

### Step 0: Manual downloads first (CAPTCHA-protected, must do by hand)

- [ ] **Louisville HRC CSV** from diversitycompliance.com
  - Still includes `Ethnicity` and `Certification Type` fields as of June 2026
  - Your most reliable source for certified businesses
- [ ] **KY Transportation Cabinet** B2GNow portal export
  - Note: minority type fields removed from export as of 2026 (anti-DEI legislation)
- [ ] **KY Finance & Administration Cabinet** MWBE listing `.xlsx`, convert to CSV
  - Same note: minority type fields no longer included

Then run:

```bash
bash quarterly_refresh.sh
```

### Pipeline order (the script runs these in sequence)

| Step | Script | What it does | Cost |
|---|---|---|---|
| 1 | `pipeline/scrape.py` | Lane 1 web discovery (Google Maps, listicles, social) | ~$9/1000 records |
| 2 | `pipeline/prepare.py` | Filter + dedupe the scrape into one dispositioned CSV (Good to go / Needs review / Dropped) | Free |
| 3 | `pipeline/upload_to_supabase.py` | Insert the "Good to go" rows into the businesses table in batches of 100 | Free |
| 4 | `pipeline/enrich.py` | Assign industry, then fill missing service descriptions, via Claude API | ~$0.75-$1.00/1000 records |
| 5 | `pipeline/clean_addresses.py --apply` | Strip stray "N/A" tokens from addresses so real KY rows are not mis-flagged (backs up every change) | Free |
| 6 | `pipeline/purge_out_of_state.py` | DRY RUN: report out-of-state rows and write the delete/review CSVs (nothing is deleted) | Free |

Steps 5 and 6 are the interim out-of-state safeguard. Lane 1 scraping is not yet state-gated at intake, so a fresh scrape can pull in businesses from neighboring states (Indiana, Ohio). Until a state filter is added to `prepare.py`, this quarterly cleanup is what keeps them out. See the Change Log in the Technical Reference for the durable fix.

### After quarterly refresh: review, purge, finish

- [ ] Review any script errors before closing Git Bash
- [ ] Open `data/out_of_state_to_delete_<ts>.csv` and `data/out_of_state_review_<ts>.csv` from step 6. Confirm the delete list is genuinely out-of-state, and that nothing flagged `kentucky_based='Yes'` is a real KY business.
- [ ] Apply the purge once the dry run looks right:
  ```bash
  python pipeline/purge_out_of_state.py --apply
  ```
- [ ] If a few genuinely out-of-state rows landed in the review CSV, delete those exact ids:
  ```bash
  python pipeline/purge_out_of_state.py --delete-from data/out_of_state_review_<ts>.csv   # dry run
  # then add --apply once it looks right
  ```
- [ ] Regenerate the static SEO pages and sitemap against the trimmed table:
  ```bash
  node generate-business-pages.js
  ```
- [ ] Spot-check new records in the Supabase dashboard
- [ ] If any **new industry categories** were added, update the hardcoded industry pills in `index.html`
- [ ] Update the record count in `README.md` and Section 1 of the Technical Reference
- [ ] Test locally, then commit and push:
  ```bash
  python -m http.server 8080   # open a business page, check the F12 console
  git add -A
  git commit -m "Quarterly refresh; remove out-of-state businesses"
  git push
  ```
- [ ] Run `monthly_link_check.sh` if not already done this month

---

## As Needed

| Script | When to use | Cost |
|---|---|---|
| `pipeline/clean_addresses.py` | Anytime addresses show stray "N/A" tokens (dry-run default; `--apply` to write) | Free |
| `pipeline/purge_out_of_state.py` | Remove out-of-state rows on demand; `--delete-from <csv>` deletes a reviewed id list | Free |
| `pipeline/discover_categories.py` | Lane 1b: surface immigrant/ethnic retail the main scraper misses. See the section below. | SerpApi |
| `pipeline/dedupe_live.py` | Merge duplicate rows on the live table | Free |
| `pipeline/maintain.py --buyblack` | When you spot buyblack.org placeholder URLs in the directory | SerpApi |
| `pipeline/view_database.py` | Local exploration of a `data/` CSV in D-Tale | Free |
| `pipeline/reconcile_certifications.py` | Lane 2 certification spreadsheets (HRC, KY Transportation, KY Finance). Not yet built. | Free |

---

## Discovery: category lane (Lane 1b)

`discover_categories.py` finds businesses the main scraper structurally misses: immigrant- and ethnic-owned retail (carnicerias, mercados, asian markets, halal grocers) whose owners almost never set Google's self-identified ownership attribute, so the attribute-gated main lane drops them. It searches Maps on category terms, then sorts every hit into Tier A (Google attribute present, auto-tagged), Tier B (business website states ownership), or Tier C (everything else, manual review). The category term decides what is surfaced, never what is tagged, so this cannot reintroduce mislabeling.

Run it when you want to expand coverage of this segment. It is not part of the quarterly refresh, because it produces a large manual-review pile rather than ready-to-upload rows.

### Workflow

```bash
python pipeline/discover_categories.py            # 1. run Maps discovery (writes CSVs only, no DB writes)
python pipeline/discover_categories.py --triage   # 2. label + sort category_review.csv for a fast manual pass
#    3. open data/category_review.csv: set Keep? = yes on keepers, fill a type on Ambiguous rows
python pipeline/discover_categories.py --promote  # 4. move kept, typed rows into the passes file
python pipeline/prepare.py                        # 5. reads the category passes alongside the main scrape
python pipeline/upload_to_supabase.py             # 6. uploads only "Good to go" rows
```

`--triage` adds Triage, Name Corroborates, and KY columns and sorts the file Strong, Review, Ambiguous, Drop?. Strong rows (the business name and the search term agree, and the address is Kentucky) are a fast skim. Review rows mostly drop (this is where generic American butchers and chains that surfaced under an ethnic term sit). Ambiguous rows (international grocery, halal market, and similar) arrive with a blank suggested type by design and need real ownership verification.

### Known limitations (read before scaling)

- **Tier B is effectively zero for this segment.** Grocery and market websites carry hours and locations, not ownership statements, so the on-page evidence check almost never fires. Expect rare Tier A plus a large Tier C. The first pilot (21 terms x 6 cities, 126 searches) produced 33 Tier A, 0 Tier B, and 434 Tier C.
- **Manual-review volume scales with cities.** A full statewide pass will roughly multiply the Tier C pile, on the order of 800 to 1,000 more rows to hand-review. Targeting the metros with the densest immigrant retail (Louisville, Lexington, Bowling Green, Owensboro, Covington, Florence, plus Paducah, Henderson, Hopkinsville) usually beats a blind statewide sweep. Set `CATEGORY_CITIES = scrape.STATEWIDE_CITIES` only when you accept that labor.
- **Even Strong rows are corroboration, not proof of ownership.** Keeping a "Supermercado" on the strength of its name is a defensible standard for a consumer directory of underrepresented businesses, but it is a values call you set, which is why nothing here auto-tags.
- Outputs live in `data/`: `businesses_scraped_categories.csv` (passes), `category_review.csv` (manual queue), `category_progress.json` (resume). All gitignored.

---

## Quick Reminders

- **Never push `admin.html` or `.env` to GitHub.** Both are gitignored.
- **CSV files and the entire `data/` folder are gitignored.** They are working files only.
- **Purge and cleanup scripts default to a dry run.** Nothing is deleted until you pass `--apply`.
- **Service-role key needed** for `clean_addresses.py`, `purge_out_of_state.py`, and `upload_to_supabase.py`. The publishable key cannot write or delete.
- **If you add a new industry category**, update both `pipeline/enrich.py` AND the hardcoded pills in `index.html`.
- **If the `businesses` schema changes**, update the `search_businesses` and `suggest_search` RPC functions in Supabase. They are not auto-updated.
- **After any data change, regenerate the static pages**: `node generate-business-pages.js`, then push.
- **Always test locally** before pushing: `python -m http.server 8080` from the repo root, then open `http://localhost:8080/index.html`.
