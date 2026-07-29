-- URGENT: strip the anon role back to what the public site actually needs.
--
-- THE PROBLEM (confirmed by probe, 2026-07-29)
-- The anon role holds DELETE, INSERT, REFERENCES, SELECT, TRIGGER, TRUNCATE and UPDATE on
-- BOTH `businesses` and `submissions`. The publishable key that maps to anon is in
-- index.html's page source, so it is public by design.
--
-- RLS is enabled but is NOT blocking any of it. Verified empirically with zero-row filters
-- (`?id=eq.-1`, which matches nothing and modifies nothing): UPDATE and DELETE both
-- returned 204 on both tables. Permitted, not denied.
--
-- That means anyone reading the page source can currently:
--
--   DELETE /rest/v1/businesses?id=gt.0      -- destroy all 1,443 businesses
--   PATCH  /rest/v1/businesses?id=gt.0      -- rewrite every record
--   GET    /rest/v1/submissions?select=*    -- read all 18 submitter names + emails
--   DELETE /rest/v1/submissions?id=gt.0     -- wipe the moderation queue
--
-- The submissions read is a privacy exposure, not just an integrity one: submitter_name and
-- submitter_email were provided on the understanding they reach the site operator.
--
-- Note README.md line 193 claims "a read-only publishable key paired with RLS policies".
-- That was not true. The technical reference (line 324) was closer -- it said insert and
-- update were granted -- but neither document mentioned DELETE or TRUNCATE.
--
-- WHAT THE PUBLIC SITE ACTUALLY NEEDS
--   businesses  -> SELECT only  (directory listing + the search_businesses/suggest_search RPCs)
--   submissions -> INSERT only  (the "add a business" and "suggest a correction" forms)
-- index.html contains no PATCH or DELETE against either table. Verified by grep.
--
-- Admin writes are unaffected: admin.html uses SUPABASE_ADMIN_KEY (service_role), which
-- bypasses both grants and RLS.
--
-- BEFORE RUNNING: take a backup. `python backup_supabase.py`
--
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor -> New query).

begin;

-- `revoke all` clears the whole set (including TRUNCATE and REFERENCES, which anon has no
-- business holding), then each table gets back exactly the one privilege the site uses.
revoke all on public.businesses  from anon;
grant  select on public.businesses to anon;

revoke all on public.submissions  from anon;
grant  insert on public.submissions to anon;

-- `authenticated` holds the same wide set. Nothing currently uses that role -- the public
-- site is anon and the admin panel is service_role -- so it is not exploitable today, but
-- leaving it wide means the moment you add real user logins the hole reopens silently.
revoke all on public.businesses  from authenticated;
grant  select on public.businesses to authenticated;

revoke all on public.submissions  from authenticated;
grant  insert on public.submissions to authenticated;

commit;


-- ---------------------------------------------------------------------------
-- VERIFY -- expect exactly two rows: anon/SELECT on businesses,
-- anon/INSERT on submissions (and the same pair for authenticated).
-- No UPDATE, no DELETE, no TRUNCATE.
-- ---------------------------------------------------------------------------

select table_name, grantee, privilege_type
from information_schema.table_privileges
where table_schema = 'public'
  and table_name in ('businesses', 'submissions')
  and grantee in ('anon', 'authenticated')
order by table_name, grantee, privilege_type;


-- ---------------------------------------------------------------------------
-- ALSO WORTH DOING: the permissive RLS policies are still there
--
-- Revoking the grant is sufficient -- a request must pass BOTH the grant and RLS, so
-- failing the grant blocks it outright. But the over-permissive policies that allowed
-- anon UPDATE/DELETE are still defined, and leaving them is a trap: re-granting a
-- privilege later would silently reopen everything.
--
-- List them, then drop the ones covering UPDATE / DELETE / ALL for anon:
--
--   select polname, polcmd, polpermissive,
--          pg_get_expr(polqual, polrelid)      as using_expr,
--          pg_get_expr(polwithcheck, polrelid) as with_check,
--          polroles::regrole[]                 as roles
--   from pg_policy
--   where polrelid in ('public.businesses'::regclass, 'public.submissions'::regclass);
--
-- polcmd: r = SELECT, a = INSERT, w = UPDATE, d = DELETE, * = ALL
--
-- Keep: SELECT on businesses, INSERT on submissions.
-- Drop: anything granting anon UPDATE, DELETE, or ALL.
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- END-TO-END CHECK after applying (safe -- id=eq.-1 matches zero rows)
--
--   # should now be 401/403 with code 42501:
--   curl -s -X DELETE "https://ursmecdpgtqckacyhnko.supabase.co/rest/v1/businesses?id=eq.-1" \
--        -H "apikey: <publishable key>" -H "Authorization: Bearer <publishable key>"
--
--   # should still be 200 -- the directory must keep loading:
--   curl -s -o /dev/null -w "%{http_code}\n" \
--        "https://ursmecdpgtqckacyhnko.supabase.co/rest/v1/businesses?select=id&limit=1" \
--        -H "apikey: <publishable key>" -H "Authorization: Bearer <publishable key>"
--
--   # should now be 401/403 -- the queue must stop being publicly readable:
--   curl -s "https://ursmecdpgtqckacyhnko.supabase.co/rest/v1/submissions?select=submitter_email&limit=1" \
--        -H "apikey: <publishable key>" -H "Authorization: Bearer <publishable key>"
--
-- Then load thepeoplesledger.net and submit a test business through the form. The search
-- box uses the search_businesses / suggest_search RPCs -- exercise those too, since a
-- SECURITY INVOKER function runs as anon and depends on the SELECT grant retained above.
--
-- ROLLBACK (restores the vulnerability -- prefer fixing forward):
--   grant all on public.businesses  to anon;
--   grant all on public.submissions to anon;
-- ---------------------------------------------------------------------------
