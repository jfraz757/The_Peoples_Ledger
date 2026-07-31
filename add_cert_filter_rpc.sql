-- Add a state-certification filter to search_businesses.
--
-- Run in the Supabase SQL editor for project ursmecdpgtqckacyhnko (The People's Ledger).
-- NOT Candidate Voice (lawteswyjpkovzagnshn) -- both projects have similarly named objects.
--
-- WHY DROP AND RECREATE RATHER THAN "CREATE OR REPLACE"
-- CREATE OR REPLACE only replaces a function whose signature matches exactly. Adding
-- cert_filter changes the signature, so it would create a second OVERLOAD alongside the
-- existing 6-argument version. PostgREST then cannot pick between them and every search
-- fails with "Could not choose the best candidate function". The old signature has to go.
--
-- DROPPING A FUNCTION ALSO DROPS ITS GRANTS. The GRANT at the bottom is not optional --
-- without it `anon` cannot execute the function and the public site's search returns 401
-- for every query. anon holds only SELECT on `businesses` (restrict_anon_grants.sql,
-- July 2026), and EXECUTE on these two RPCs is what makes search work at all.
--
-- WHAT CHANGED, AND NOTHING ELSE
-- The body is the June 2026 rebuild verbatim, plus one predicate in the WHERE clause.
-- Search ranking, the normalized-substring match, the word_similarity fallback, and the
-- area/ownership/industry filters are untouched.
--
-- cert_filter accepts:
--   'All'  (default)  no certification filtering
--   'Any'             any business holding a real state certification
--   'MBE' / 'WBE' / 'MWBE' / 'DBE' / 'SBE' / 'VOSB' / 'SDVOSB' / 'DIBE' / 'LGBTBE'
--
-- TOKEN MATCHING, NOT SUBSTRING. certification_type is a comma-separated string, and a
-- naive ILIKE '%VOSB%' would also match SDVOSB, silently inflating the veteran-owned
-- count with service-disabled rows. The comma-wrapped comparison below matches whole
-- labels only.
--
-- 'Unknown' and 'Not Certified' are legacy placeholder values, never real certifications.
-- They are excluded from 'Any' so the filter cannot report an uncertified business as
-- certified. One such row exists as of 2026-07-31.

DROP FUNCTION IF EXISTS public.search_businesses(text, text, text, text, integer, integer);

CREATE OR REPLACE FUNCTION public.search_businesses(
  search_text     text    DEFAULT ''::text,
  area_text       text    DEFAULT ''::text,
  ownership_type  text    DEFAULT 'All'::text,
  industry_filter text    DEFAULT 'All'::text,
  page_limit      integer DEFAULT 24,
  page_offset     integer DEFAULT 0,
  cert_filter     text    DEFAULT 'All'::text
)
RETURNS TABLE(
  id bigint, business_name text, address text, phone text, services_products text,
  website text, minority_type text, industry text, status text, kentucky_based text,
  certification_type text, total_count bigint
)
LANGUAGE sql
STABLE
AS $function$
  WITH q AS (
    SELECT
      NULLIF(btrim(search_text), '') AS term,
      NULLIF(btrim(area_text),   '') AS area,
      -- normalized term: lowercased, non-alphanumerics stripped
      regexp_replace(lower(COALESCE(search_text, '')), '[^a-z0-9]', '', 'g') AS term_norm
  ),
  filtered AS (
    SELECT b.*
    FROM businesses b, q
    WHERE
      (
        q.term IS NULL
        -- normalized substring match: "chois" now matches "Choi's"
        OR regexp_replace(lower(b.business_name), '[^a-z0-9]', '', 'g')
             ILIKE '%' || q.term_norm || '%'
        OR regexp_replace(lower(COALESCE(b.services_products,'')), '[^a-z0-9]', '', 'g')
             ILIKE '%' || q.term_norm || '%'
        OR COALESCE(b.industry, '') ILIKE '%' || q.term || '%'
        -- fuzzy fallback for typos
        OR word_similarity(q.term, b.business_name)                  > 0.45
        OR word_similarity(q.term, COALESCE(b.services_products,'')) > 0.50
      )
      AND (q.area IS NULL OR b.address ILIKE '%' || q.area || '%')
      AND (
        ownership_type = 'All' OR ownership_type IS NULL
        OR b.minority_type ILIKE '%' || ownership_type || '%'
      )
      AND (
        industry_filter = 'All' OR industry_filter IS NULL
        OR b.industry = industry_filter
      )
      -- state certification filter (added July 2026)
      AND (
        cert_filter = 'All' OR cert_filter IS NULL
        OR (
          cert_filter = 'Any'
          AND b.certification_type IS NOT NULL
          AND btrim(b.certification_type) <> ''
          -- a placeholder is not a certification
          AND btrim(b.certification_type) NOT IN ('Unknown', 'Not Certified')
        )
        OR (
          cert_filter NOT IN ('All', 'Any')
          -- whole-label match: ',MBE,WBE,' LIKE '%,MBE,%'. Wrapping both sides in commas
          -- and stripping spaces stops VOSB from matching inside SDVOSB.
          AND ',' || replace(COALESCE(b.certification_type, ''), ' ', '') || ','
              ILIKE '%,' || replace(cert_filter, ' ', '') || ',%'
        )
      )
  )
  SELECT
    f.id, f.business_name, f.address, f.phone, f.services_products,
    f.website, f.minority_type, f.industry, f.status, f.kentucky_based,
    f.certification_type,
    COUNT(*) OVER () AS total_count
  FROM filtered f, q
  ORDER BY
    (q.term IS NOT NULL
      AND regexp_replace(lower(f.business_name), '[^a-z0-9]', '', 'g')
            ILIKE '%' || q.term_norm || '%') DESC,
    CASE WHEN q.term IS NULL THEN 0
         ELSE word_similarity(q.term, f.business_name) END DESC,
    f.business_name ASC
  LIMIT  page_limit
  OFFSET page_offset;
$function$;

-- MANDATORY. DROP removed the old grants; without these the public site's search breaks.
GRANT EXECUTE ON FUNCTION public.search_businesses(text, text, text, text, integer, integer, text)
  TO anon, authenticated;


-- ---------------------------------------------------------------------------
-- VERIFY -- expect: exactly ONE search_businesses, with 7 arguments, and anon
-- holding EXECUTE. More than one row means an overload survived and search will fail.
-- ---------------------------------------------------------------------------
select p.proname,
       pg_get_function_identity_arguments(p.oid) as args,
       has_function_privilege('anon', p.oid, 'EXECUTE') as anon_can_execute
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'search_businesses';


-- Counts to sanity-check after running (compare against the site):
--   select count(*) from search_businesses('', '', 'All', 'All', 100000, 0, 'All');   -- 2315
--   select count(*) from search_businesses('', '', 'All', 'All', 100000, 0, 'Any');   -- ~589
--   select count(*) from search_businesses('', '', 'All', 'All', 100000, 0, 'VOSB');  -- 5, NOT 10
--   select count(*) from search_businesses('', '', 'All', 'All', 100000, 0, 'MBE');   -- ~193
--
-- ROLLBACK (restores the original 6-argument function, losing the cert filter):
--   drop function if exists public.search_businesses(text,text,text,text,integer,integer,text);
--   -- then re-run the June 2026 definition and re-grant EXECUTE to anon, authenticated.
