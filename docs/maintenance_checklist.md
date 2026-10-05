# The People's Ledger Maintenance Checklist

**Supabase project:** `ursmecdpgtqckacyhnko`
**Local repo:** `C:\Users\jfraz\The_Peoples_Ledger`
**Python:** `C:\Python314\python.exe`

> Run everything from the repo root through the runner, `pipeline/ledger.py`. It works the
> same from PowerShell (`py pipeline\ledger.py <verb>`) and Git Bash
> (`python pipeline/ledger.py <verb>`), and uses whichever Python launched it -- so a
> moved Python install cannot silently break it the way it broke the old `.sh` wrappers.
> `python pipeline/ledger.py` with no verb prints the list.

---

## Monthly -- automatic

**Nothing to do.** The "PeoplesLedger Monthly Link Check" scheduled task runs
`python pipeline/ledger.py monthly` at 10:00 on the 1st of every month (or as soon as the
PC is next on): it re-checks every business website, updates `status` (Active / Inactive /
No Website), regenerates the pages, and commits and pushes `businesses/` so the new
statuses go live. Output is appended to `data/monthly_links.log` -- glance at it if you
want to confirm a run. It only pushes from `main`.

The check is free. It runs 16 sites at a time, re-checks any failure once before calling
it Inactive, and writes only the statuses that changed.

```powershell
Start-ScheduledTask -TaskName 'PeoplesLedger Monthly Link Check'          # run it now
powershell -ExecutionPolicy Bypass -File install_monthly_links.ps1 -Uninstall   # remove the schedule
```

To run only the link check by hand, without committing: `python pipeline/ledger.py links`.

`maintain.py --buyblack` also fixes buyblack.org placeholder URLs, but it spends SerpApi,
so the monthly run does not do it.

---

## Quarterly (~4x per year)

### SerpApi: pay for one month only

The quarterly scrape is the only step that needs more than SerpApi's free 100
searches/month: a full run is **612 searches** (18 terms x 34 cities, Google Maps only).
Subscribe for the month you scrape, set `SERPAPI_HOURLY_LIMIT` in `pipeline/scrape.py`
to that plan's hourly cap, and drop back to the free tier afterwards. If the quota runs
out mid-run, the scrape halts cleanly and resumes where it stopped.

### Step 0: Manual downloads (CAPTCHA-protected, must do by hand)

- [ ] **Louisville HRC CSV** from diversitycompliance.com
  - Still includes `Ethnicity` and `Certification Type` fields as of June 2026
  - Your most reliable source for certified businesses
- [ ] **KY Transportation Cabinet** B2GNow portal export
  - Note: minority type fields removed from export as of 2026 (anti-DEI legislation)
- [ ] **KY Finance & Administration Cabinet** MWBE listing `.xlsx` (no need to convert it)
  - Same note: minority type fields no longer included

Put each file in its folder under `Minority_Biz_Database_Project/Spreadsheets/` and move
the previous export into that folder's `archive/`. The reconcile reads the raw downloads
directly, so the notebook's convert/rename cells are optional.

### Steps, in order

```bash
python pipeline/ledger.py new-cycle   # 1. archive last cycle's progress + output
python pipeline/ledger.py scrape      # 2. SerpApi Google Maps discovery
python pipeline/ledger.py prep        # 3. prepare + resolve_review + flag_review, then STOP
#    4. review data/businesses_prepared.csv (filter Disposition = 'Needs review')
python pipeline/ledger.py publish     # 5. upload + enrich + regenerate pages
python pipeline/ledger.py certs       # 6. Lane 2 dry run against the new downloads
python pipeline/ledger.py maintain    # 7. dry-run health checks of the live table
```

| Step | What it does | Cost |
|---|---|---|
| new-cycle | Records your manual drops, moves `scraper_progress.json` and the old scrape/prepare files into `data/archive/<timestamp>_cycle/`. Skip it and the scraper treats last quarter as finished and finds nothing. | Free |
| scrape | Lane 1 discovery. Keeps only results carrying Google's owner-set ownership attribute. Drops chains, out-of-state and already-listed businesses at intake. | 612 SerpApi searches |
| prep | Dispositions every row (Good to go / Needs review / Dropped), looks up real websites and addresses for the address-less rows, and moves anything flagged (national brand, likely duplicate, out of state) into Needs review. | A few SerpApi searches |
| review | You. Set keepers to "Good to go", the rest to "Dropped". Dropped rows are remembered automatically. | -- |
| publish | Inserts the Good to go rows, fills industry + services with Claude, regenerates the static pages. | ~$1 per 1,000 records |
| certs | Dry run: how many certifications would be backfilled, how many certified businesses would be added. Then `reconcile_certifications.py --apply [--insert-new]`. | Free |
| maintain | Reports stray N/A addresses, out-of-state rows and duplicates. Nothing changes until you run the individual script with `--apply`. | Free |

### Finish

- [ ] Apply any health-check fixes you agree with (`--apply` on the individual script), then `node generate-business-pages.js`
- [ ] Spot-check new records in the Supabase dashboard
- [ ] If any **new industry categories** were added, update the hardcoded industry pills in `index.html`
- [ ] Update the record count in `README.md` and Section 1 of the Technical Reference
- [ ] Test locally, then commit and push:
  ```bash
  python -m http.server 8080   # open a business page, check the F12 console
  git add -A
  git commit -m "Quarterly refresh"
  git push
  ```
- [ ] Cancel or downgrade the SerpApi plan

---

## After approving a community submission (admin.html)

Approving a "new business" submission in admin.html inserts straight into
the live `businesses` table with no `industry` and whatever `services_products`
text the submitter typed (often blank or thin). Run this after any batch of
approvals so new submissions are actually discoverable:

```bash
python pipeline/ledger.py publish-submissions   # enrich + regenerate pages + commit
git push
```

`enrich_submissions.py` only looks at submissions approved since its last run
(tracked in `data/.enrich_submissions_state.json`, gitignored), matches each
one to its `businesses` row by exact name, and fills industry/services via
Claude — cheaper than a full-table `enrich.py` pass and safe to re-run anytime.
`--dry-run` previews without writing or advancing the watermark; `--since
<ISO timestamp>` overrides it if you need to re-process older approvals.

---

## As Needed

| Script | When to use | Cost |
|---|---|---|
| `pipeline/clean_addresses.py` | Anytime addresses show stray "N/A" tokens (dry-run default; `--apply` to write) | Free |
| `pipeline/purge_out_of_state.py` | Remove out-of-state rows on demand; `--delete-from <csv>` deletes a reviewed id list | Free |
| `pipeline/discover_categories.py` | Lane 1b: surface immigrant/ethnic retail the main scraper misses. See the section below. | SerpApi |
| `pipeline/dedupe_live.py` | Merge duplicate rows on the live table (dry run default; `--apply`) | Free |
| `pipeline/maintain.py --buyblack` | When you spot buyblack.org placeholder URLs in the directory | SerpApi |
| `pipeline/view_database.py` | Local exploration of a `data/` CSV in D-Tale | Free |
| `backup_supabase.py --prune` | Report backups outside retention (14 days + one per month); add `--yes` to remove them | Free |
| `pipeline/reconcile_certifications.py` | Lane 2 certification spreadsheets (HRC, KY Transportation, KY Finance). `ledger.py certs` runs the dry run. | Free |

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

---

## Routine batch (the simple path)

The quarterly steps above are the routine path. In short:

```bash
python pipeline/ledger.py new-cycle
python pipeline/ledger.py scrape
python pipeline/ledger.py prep       # stops for your review
python pipeline/ledger.py publish
```

Full map in `docs/order_of_operations.md`.
