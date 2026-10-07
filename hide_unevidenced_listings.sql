-- Hide listings that have no ownership evidence from the public site.
--
-- STATUS: APPLIED 2026-10-07. First version (any non-blank certification counted) left 2,248
-- of 2,377 rows public. Revised the same day so an SBE-only certification does not count:
-- SBE (Small Business Enterprise) is a size certification and says nothing about ownership.
-- That hid 14 more rows (KY Transportation Cabinet SBE-only listings). Verified: publishable
-- key 2,234, service key 2,377; certification filters and suggest_search unchanged.
--
-- Run in the Supabase SQL editor for project ursmecdpgtqckacyhnko (The People's Ledger).
-- NOT Candidate Voice (lawteswyjpkovzagnshn).
--
-- WHY (October 2026)
-- About 130 listings came from the scraper's pre-July web lane, which added every business
-- MENTIONED on a page about minority-owned businesses. Their generic tags were removed as
-- unverified (Google shows no owner badge; their own sites say nothing about ownership), so
-- they appeared in the directory with no badge at all. Since July the publishing standard
-- is evidence: a Google owner attribute, a Kentucky certification, or the business's own
-- statement. These rows do not meet it.
--
-- HIDE, NOT DELETE. The rows stay in the table. The public read policy simply does not
-- return them, so a listing reappears by itself the moment any evidence field is filled
-- (an owner confirms it, a certification lands, a website statement is found).
--
-- ONE PLACE COVERS EVERYTHING PUBLIC. index.html (search, count, export, autocomplete),
-- about.html (the live count) and generate-business-pages.js all read with the publishable
-- key, and search_businesses / suggest_search are SECURITY INVOKER, so all of them run as
-- anon and see only what this policy allows. generate-business-pages.js then removes the
-- hidden listings' pages as orphans.
--
-- The pipeline reads with the SERVICE key (common.fetch_all prefers it), which bypasses
-- RLS, so duplicate checks still see hidden rows and a scrape cannot re-add them.
--
-- Evidence = any of:
--   minority_type                 non-blank: ownership demographics (Google attribute, own
--                                 site, owner)
--   self_reported_certification   non-blank: a certification the business states about itself
--   certification_type            holds at least one OWNERSHIP certification (MBE, WBE, MWBE,
--                                 DBE, DIBE, VOSB, SDVOSB, LGBTBE, ...) from Louisville HRC /
--                                 KY Transportation / KY Finance. SBE alone does not count, nor
--                                 the placeholders 'Unknown' / 'Not Certified'.
--
-- Safe to re-run from either earlier state: both old policy names are dropped first.

begin;

drop policy if exists "Allow public select" on public.businesses;
drop policy if exists "Public select: listings with ownership evidence" on public.businesses;

create policy "Public select: listings with ownership evidence"
  on public.businesses
  for select
  to public
  using (
    coalesce(btrim(minority_type), '') <> ''
    or coalesce(btrim(self_reported_certification), '') <> ''
    or exists (
      select 1
      from unnest(string_to_array(coalesce(certification_type, ''), ',')) as c(label)
      where btrim(c.label) not in ('', 'SBE', 'Unknown', 'Not Certified')
    )
  );

commit;


-- ---------------------------------------------------------------------------
-- VERIFY -- expect one SELECT policy on businesses, with the condition above.
-- ---------------------------------------------------------------------------
select polname, polcmd, polroles::regrole[] as roles, pg_get_expr(polqual, polrelid) as using_expr
from pg_policy
where polrelid = 'public.businesses'::regclass;

-- Then, with the publishable key, the directory count drops by the number of listings
-- without evidence (2,377 -> 2,234 on 2026-10-07), and the service key still sees all.

-- ROLLBACK (shows every row again):
--   drop policy if exists "Public select: listings with ownership evidence" on public.businesses;
--   create policy "Allow public select" on public.businesses for select to public using (true);
