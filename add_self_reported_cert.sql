-- Add a SELF-REPORTED certification field to `businesses`, and return it from search.
--
-- Run in the Supabase SQL editor for project ursmecdpgtqckacyhnko (The People's Ledger).
-- NOT Candidate Voice (lawteswyjpkovzagnshn).
--
-- WHY A THIRD FIELD (October 2026)
-- Some businesses state a certification on their own website -- "1021 Specialist, LLC is
-- an MBE Certified company", "22nd Century Technologies is a certified MBE" -- without
-- naming an ownership group, and the certificate is not on any of the three Kentucky lists
-- (often it is national or from another state). Neither existing field fits:
--
--   certification_type  may ONLY come from Louisville HRC, KY Transportation or KY Finance.
--                       Its badge and the State Certification filter mean "a Kentucky
--                       certifier verified this". A self-claim would break that promise.
--   minority_type       holds ownership DEMOGRAPHICS only. Putting certification vocabulary
--                       there is the July 2026 mistake ("two vocabularies in one column
--                       cannot be filtered apart").
--
-- self_reported_certification holds certification labels a business states about itself
-- (MBE, WBE, DBE, MWBE, VOSB, SDVOSB, WOSB, ...), comma-separated. It is displayed as
-- "MBE (self-reported)" and is deliberately NOT part of the State Certification filter.
-- Written only by pipeline/website_ownership.py, which keeps the quote and page as evidence.
--
-- DROP AND RECREATE, NOT "CREATE OR REPLACE": adding a column to RETURNS TABLE changes the
-- function's return type, which CREATE OR REPLACE cannot do. Dropping removes the grant,
-- so the GRANT at the bottom is mandatory -- without it the public search returns 401.
--
-- BEFORE RUNNING: python backup_supabase.py

begin;

alter table public.businesses
  add column if not exists self_reported_certification text;

drop function if exists public.search_businesses(text, text, text, text, integer, integer, text);

create function public.search_businesses(
  search_text     text    default ''::text,
  area_text       text    default ''::text,
  ownership_type  text    default 'All'::text,
  industry_filter text    default 'All'::text,
  page_limit      integer default 24,
  page_offset     integer default 0,
  cert_filter     text    default 'All'::text
)
returns table(
  id bigint, business_name text, address text, phone text, services_products text,
  website text, minority_type text, industry text, status text, kentucky_based text,
  certification_type text, self_reported_certification text, total_count bigint
)
language sql
stable
as $function$
  with q as (
    select
      nullif(btrim(search_text), '') as term,
      nullif(btrim(area_text),   '') as area,
      regexp_replace(lower(coalesce(search_text, '')), '[^a-z0-9]', '', 'g') as term_norm
  ),
  filtered as (
    select b.*
    from businesses b, q
    where
      (
        q.term is null
        or regexp_replace(lower(b.business_name), '[^a-z0-9]', '', 'g')
             ilike '%' || q.term_norm || '%'
        or regexp_replace(lower(coalesce(b.services_products,'')), '[^a-z0-9]', '', 'g')
             ilike '%' || q.term_norm || '%'
        or coalesce(b.industry, '') ilike '%' || q.term || '%'
        or word_similarity(q.term, b.business_name)                  > 0.45
        or word_similarity(q.term, coalesce(b.services_products,'')) > 0.50
      )
      and (q.area is null or b.address ilike '%' || q.area || '%')
      and (
        ownership_type = 'All' or ownership_type is null
        or b.minority_type ilike '%' || ownership_type || '%'
      )
      and (
        industry_filter = 'All' or industry_filter is null
        or b.industry = industry_filter
      )
      -- state certification filter: certification_type ONLY. Self-reported certifications
      -- are intentionally not matched here.
      and (
        cert_filter = 'All' or cert_filter is null
        or (
          cert_filter = 'Any'
          and b.certification_type is not null
          and btrim(b.certification_type) <> ''
          and btrim(b.certification_type) not in ('Unknown', 'Not Certified')
        )
        or (
          cert_filter not in ('All', 'Any')
          and ',' || replace(coalesce(b.certification_type, ''), ' ', '') || ','
              ilike '%,' || replace(cert_filter, ' ', '') || ',%'
        )
      )
  )
  select
    f.id, f.business_name, f.address, f.phone, f.services_products,
    f.website, f.minority_type, f.industry, f.status, f.kentucky_based,
    f.certification_type, f.self_reported_certification,
    count(*) over () as total_count
  from filtered f, q
  order by
    (q.term is not null
      and regexp_replace(lower(f.business_name), '[^a-z0-9]', '', 'g')
            ilike '%' || q.term_norm || '%') desc,
    case when q.term is null then 0
         else word_similarity(q.term, f.business_name) end desc,
    f.business_name asc
  limit  page_limit
  offset page_offset;
$function$;

-- MANDATORY. DROP removed the old grant; without this the public site's search breaks.
grant execute on function public.search_businesses(text, text, text, text, integer, integer, text)
  to anon, authenticated;

commit;


-- ---------------------------------------------------------------------------
-- VERIFY -- expect: the new column exists, exactly ONE search_businesses with 7
-- arguments, and anon holding EXECUTE.
-- ---------------------------------------------------------------------------
select column_name, data_type from information_schema.columns
where table_schema = 'public' and table_name = 'businesses' and column_name = 'self_reported_certification';

select p.proname, pg_get_function_identity_arguments(p.oid) as args,
       has_function_privilege('anon', p.oid, 'EXECUTE') as anon_can_execute
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'search_businesses';

-- Sanity count (should equal the directory size):
--   select count(*) from search_businesses('', '', 'All', 'All', 100000, 0, 'All');

-- ROLLBACK: drop the 7-arg function, re-run add_cert_filter_rpc.sql (restores the previous
-- return shape and grant), then: alter table public.businesses drop column self_reported_certification;
