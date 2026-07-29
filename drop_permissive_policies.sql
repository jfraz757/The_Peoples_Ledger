-- Drop the over-permissive RLS policies on `businesses` and `submissions`.
--
-- STATUS: APPLIED 2026-07-29. Verified -- pg_policy now returns exactly two rows
-- ("Allow public select" on businesses, "Allow public insert on submissions"), and the
-- live site still works: directory SELECT 200, search_businesses total_count=1443,
-- suggest_search returning results, submissions INSERT permission intact.
-- Kept for the record and for rebuilding the reasoning; re-running it is a no-op.
--
-- Run this AFTER restrict_anon_grants.sql. That file revoked the grants, which is what
-- actually blocks access today (a request must pass BOTH the grant and the policy). These
-- policies are now unreachable -- but they are a trap: re-granting any privilege later,
-- for any reason, would silently reopen everything. Remove them so the policy layer is
-- also correct on its own.
--
-- Captured policy state (pg_policy, 2026-07-29). polcmd: r = SELECT, a = INSERT, w = UPDATE.
-- roles `{-}` = PUBLIC, i.e. every role including anon and authenticated.
--
--   businesses   "Allow public insert"                 a   -- DROP
--   businesses   "Allow public select"     using true  r   -- KEEP (directory listing)
--   businesses   "Allow public update"     using true  w   -- DROP
--   submissions  "Allow public insert on submissions"  a   -- KEEP (the public forms)
--   submissions  "Allow public select on submissions"  r   -- DROP (this was the PII leak)
--   submissions  "Allow public update on submissions"  w   -- DROP
--
-- Keep 2, drop 4. Nothing legitimate needs the four being dropped:
--   * The public site only reads `businesses` and inserts into `submissions`.
--   * Admin approval and every pipeline script use the service role key, which bypasses
--     RLS and grants entirely. (Four pipeline scripts were accidentally using the
--     publishable key instead; fixed in the same pass -- see the note at the bottom.)
--
-- BEFORE RUNNING: take a backup. `python backup_supabase.py`
--
-- Run in the Supabase SQL editor for project ursmecdpgtqckacyhnko (thepeoplesledger.net).
-- NOT Candidate Voice -- that project is lawteswyjpkovzagnshn and has no `businesses` table.

begin;

-- `businesses`: keep public SELECT only.
drop policy if exists "Allow public insert" on public.businesses;
drop policy if exists "Allow public update" on public.businesses;

-- `submissions`: keep public INSERT only. Dropping SELECT is what closes the submitter
-- name/email exposure at the policy layer; the admin panel reads this table via the
-- service role key, so it is unaffected.
drop policy if exists "Allow public select on submissions" on public.submissions;
drop policy if exists "Allow public update on submissions" on public.submissions;

commit;


-- ---------------------------------------------------------------------------
-- VERIFY -- expect exactly two rows:
--   businesses   "Allow public select"                r
--   submissions  "Allow public insert on submissions" a
-- ---------------------------------------------------------------------------

select c.relname            as table_name,
       p.polname            as policy_name,
       p.polcmd             as cmd,
       p.polroles::regrole[] as roles
from pg_policy p
join pg_class c on c.oid = p.polrelid
where c.relname in ('businesses', 'submissions')
order by c.relname, p.polname;


-- Also confirm RLS is actually enabled. "RLS is enabled" was asserted in the docs but never
-- verified, and if relrowsecurity is false the policies are inert regardless of what they say.
select relname, relrowsecurity as rls_enabled, relforcerowsecurity as rls_forced
from pg_class
where relname in ('businesses', 'submissions');


-- ---------------------------------------------------------------------------
-- AFTER RUNNING, re-test the live site:
--   * thepeoplesledger.net loads the directory            (public SELECT on businesses)
--   * the search box and autocomplete return results      (search_businesses / suggest_search)
--   * submitting a business through the form succeeds      (public INSERT on submissions)
--   * the admin panel still lists pending submissions      (service role, bypasses all this)
--
-- ROLLBACK is not a simple inverse -- these policies would have to be recreated by hand.
-- That is deliberate: the definitions being dropped are the vulnerability. If something
-- breaks, the fix is to add a narrow policy for the specific operation that needs it, not
-- to restore `using (true)` for PUBLIC.
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- WHY THESE GRANTS AND POLICIES EXISTED IN THE FIRST PLACE
--
-- Four pipeline scripts -- enrich.py, enrich_submissions.py, maintain.py and
-- upload_to_supabase.py -- were writing to `businesses` with the PUBLISHABLE key, not the
-- service role key. They read `os.getenv("SUPABASE_KEY")`, and .env defined SUPABASE_KEY
-- twice (once `sb_secret_...`, once `sb_publishable_...`). dotenv keeps only the LAST
-- occurrence, so they silently got the publishable key.
--
-- The scripts' own docstrings disagreed about which key belonged there: upload_to_supabase.py
-- said "the service role key, NOT the read-only publishable key", while enrich.py said
-- "publishable key is fine (read + update under your RLS)". The duplicate .env entry looks
-- like the result of swapping the value by hand depending on which script was being run.
--
-- So the public write access was not an oversight -- it was load-bearing. It existed because
-- the pipeline depended on it, which is exactly why it survived review: removing it appeared
-- to break things. Those scripts now read SUPABASE_SERVICE_ROLE_KEY explicitly and fail
-- loudly if it is absent, so the public grants are genuinely unused.
--
-- The general lesson: when an over-broad permission looks deliberate, find out what depends
-- on it before assuming it is intentional. Here the dependency was a typo-level bug in how
-- an environment variable was named.
-- ---------------------------------------------------------------------------
