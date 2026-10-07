// The People's Ledger — Business SEO Page Generator
// Run from repo root: node generate-business-pages.js
// Requires Node 18+ (native fetch). Older Node: npm install node-fetch and require it.
//
// What this does:
//   1. Queries Supabase for all businesses (paginated — handles 1,191+ records)
//   2. Writes a static HTML file to /businesses/{slug}.html for each business
//   3. Writes /directory/ pages grouping businesses by city, category and ownership
//   4. Writes /businesses/sitemap.xml listing every business and directory page
//
// After running:
//   git add businesses/ directory/
//   git commit -m "Regenerate business SEO pages"
//   git push

const fs   = require("fs");
const path = require("path");

// ── Config ────────────────────────────────────────────────────────────────────

const SUPABASE_URL = "https://ursmecdpgtqckacyhnko.supabase.co";
const SUPABASE_KEY = "sb_publishable_A0zmuZVHVPtosZrNdFE4GQ_sITuTrkg";
const SITE_URL     = "https://thepeoplesledger.net";
const OUT_DIR      = path.join(__dirname, "businesses");

// ── Helpers ───────────────────────────────────────────────────────────────────

function slugify(name) {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// Assign every business ONE unique slug, used for the filename, the sitemap <loc>, and
// the canonical/og:url inside the page. Those three were computing slugify(name)
// independently, so two businesses sharing a name silently shared a URL: the second
// write overwrote the first, the sitemap listed the same <loc> twice, and one business
// had no page at all.
//
// Found July 2026 with 2,066 businesses producing only 2,064 files. Both collisions were
// legitimate multi-location businesses, not bad data -- Fiesta Mexico in Campbellsville
// AND Nicholasville, V.C. Veterans Contracting in Lexington AND Richmond. prepare.py
// deliberately keeps same-name rows with different street numbers apart, so this will
// recur whenever a business has two locations.
//
// EXISTING URLS MUST NOT CHANGE: over a thousand of these pages are indexed by Google.
// So the first business to claim a slug keeps the bare form and only later collisions
// get a suffix. Ordering is by slug then id, never by fetch order, so the same business
// resolves to the same URL on every regeneration.
function cityFromAddress(address) {
  // "801 S Main St, Nicholasville, KY 40356" -> "Nicholasville"
  const parts = String(address || "").split(",").map(s => s.trim()).filter(Boolean);
  if (parts.length < 2) return "";
  // Walk back past the "KY 40356" / "KY" tail to the city segment.
  for (let i = parts.length - 1; i >= 0; i--) {
    if (/^[A-Za-z]{2}(\s+\d{5}(-\d{4})?)?$/.test(parts[i])) continue;
    if (/^\d{5}(-\d{4})?$/.test(parts[i])) continue;
    if (i === 0) return "";           // only a street line, no city
    return parts[i];
  }
  return "";
}

// The city used for directory pages. cityFromAddress is left as-is because slug
// collisions depend on it (existing URLs must not change); this one also drops the
// "Kentucky" / "United States" tails that some addresses carry instead of a city, and
// normalises case so "LOUISVILLE" and "Louisville" land on the same page.
function cityOf(biz) {
  // Walks back past the tail segments addresses carry after the city: state ("KY",
  // "Kentucky", "KY 40202"), ZIP ("40245", "40245.0" from spreadsheet imports) and
  // country. A lone first segment counts as the city when it has no digits, so
  // "Paducah, KY" works where cityFromAddress reads it as a street line.
  const parts = String(biz.address || "").split(",").map(x => x.trim()).filter(Boolean);
  const tail = /^((ky|kentucky)(\s+\d{5}(-\d{4})?(\.0)?)?|\d{5}(-\d{4})?(\.0)?|united states( of america)?|usa|us)$/i;
  let i = parts.length - 1;
  while (i >= 0 && tail.test(parts[i])) i--;
  // Another state's code ("Jeffersonville, IN"): not a Kentucky city, so no city page.
  if (i >= 1 && /^[A-Za-z]{2}(\s+\d{5}(-\d{4})?)?$/.test(parts[i])) return "";
  if (i < 0 || (i === 0 && /\d/.test(parts[0]))) return "";
  const c = parts[i].replace(/\s+/g, " ");
  if (/\d/.test(c) || c.length > 30) return "";
  return c.toLowerCase().replace(/\b[a-z]/g, ch => ch.toUpperCase());
}

function assignSlugs(businesses) {
  const ordered = [...businesses].sort((a, b) => {
    const sa = slugify(a.business_name || ""), sb = slugify(b.business_name || "");
    return sa < sb ? -1 : sa > sb ? 1 : (a.id || 0) - (b.id || 0);
  });
  const used = new Set();
  let collisions = 0;
  for (const b of ordered) {
    const base = slugify(b.business_name || "");
    if (!base) { b._slug = ""; continue; }
    if (!used.has(base)) { b._slug = base; used.add(base); continue; }
    collisions++;
    const city = slugify(cityFromAddress(b.address));
    let candidate = city ? `${base}-${city}` : `${base}-${b.id}`;
    if (used.has(candidate)) candidate = `${base}-${b.id}`;
    b._slug = candidate;
    used.add(candidate);
  }
  if (collisions) {
    console.log(`  ${collisions} name collision(s) disambiguated by city (existing URLs unchanged).`);
  }
  return businesses;
}

// Split comma-separated fields into clean arrays
function splitField(val) {
  if (!val || !val.trim()) return [];
  return val.split(",").map(s => s.trim()).filter(Boolean);
}

// Format phone number for display — leaves it as-is if already formatted
function formatPhone(phone) {
  if (!phone) return null;
  const digits = phone.replace(/\D/g, "");
  if (digits.length === 10) {
    return `(${digits.slice(0,3)}) ${digits.slice(3,6)}-${digits.slice(6)}`;
  }
  return phone;
}

// Strip protocol for display
function displayUrl(url) {
  if (!url) return null;
  return url.replace(/^https?:\/\//, "").replace(/\/$/, "");
}

// "Minority-Owned (general)" -> "Minority-Owned"; the label used in headings and links.
function ownershipLabel(type) {
  return type.replace(/\s*\(general\)\s*$/i, "");
}

// Joins ["Black-Owned", "Women-Owned"] -> "a Black-owned, women-owned" for running text.
// Proper-noun prefixes (Black, Latine, LGBTQ+) keep their case; "-Owned" never does.
function ownershipPhrase(list) {
  const words = list.map(ownershipLabel).map(t => t.replace(/-Owned$/, "-owned"))
    .map(t => /^(Women|Veteran|Minority|Disability|Disabled|Family|Service)/.test(t) ? t.toLowerCase() : t);
  return (/^(LGBTQ|[AEIOU])/i.test(words[0]) ? "an " : "a ") + words.join(", ");
}

// JSON-LD for a page. "</" is escaped so a business name can never close the script tag.
function jsonLd(obj) {
  return `<script type="application/ld+json">${JSON.stringify(obj).replace(/<\//g, "<\\/")}</script>`;
}

// Writes only when the content differs (line endings normalised), for the reasons given
// in main(): honest git diffs and file mtimes that the sitemap's lastmod can trust.
function writeIfChanged(file, content) {
  let existing = null;
  try { existing = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n"); } catch { /* new file */ }
  if (existing === content) return false;
  fs.writeFileSync(file, content, "utf8");
  return true;
}

// Ensure URL has protocol for hrefs, and only ever emit an http(s) link.
function fullUrl(url) {
  if (!url) return null;
  const u = String(url).trim();
  const full = /^https?:\/\//i.test(u) ? u : "https://" + u;
  try { return new URL(full).protocol.startsWith("http") ? full : null; } catch { return null; }
}

// Every database value is escaped before it goes into a page. Names, addresses and
// descriptions can arrive from public submissions, and these pages are served from the
// site's own domain, so unescaped text was one approval away from running script there.
function esc(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Fetch all businesses ──────────────────────────────────────────────────────

async function fetchAllBusinesses(withSelfReported = true) {
  const fields = [
    "id", "business_name", "address", "phone", "website",
    "services_products", "minority_type", "industry",
    "status", "kentucky_based", "certification_type",
    ...(withSelfReported ? ["self_reported_certification"] : []),
  ].join(",");

  let all    = [];
  let offset = 0;
  const limit = 1000;

  while (true) {
    const url = `${SUPABASE_URL}/rest/v1/businesses?select=${fields}&order=business_name.asc,id.asc&limit=${limit}&offset=${offset}`;
    const res = await fetch(url, {
      headers: {
        apikey:        SUPABASE_KEY,
        Authorization: `Bearer ${SUPABASE_KEY}`,
      },
    });
    if (!res.ok) {
      const body = await res.text();
      // Before add_self_reported_cert.sql has been run the column does not exist. Fall back
      // rather than fail, so the scheduled monthly run keeps working either way.
      if (withSelfReported && body.includes("self_reported_certification")) {
        console.log("  (self_reported_certification column not found -- run add_self_reported_cert.sql)");
        return fetchAllBusinesses(false);
      }
      throw new Error(`Supabase error: ${res.status} ${body}`);
    }
    const batch = await res.json();
    all = all.concat(batch);
    if (batch.length < limit) break;
    offset += limit;
  }

  return all;
}

// Styles shared by the business pages and the directory pages.
const BASE_CSS = `    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: Arial, Helvetica, sans-serif;
      background: #1a1a1a;
      color: rgba(255,255,255,0.78);
      min-height: 100vh;
    }

    /* Nav */
    .nav {
      background: rgba(30,30,30,0.92);
      border-bottom: 1px solid rgba(255,215,0,0.2);
      padding: 0 1.5rem;
      display: flex;
      align-items: center;
      gap: 1rem;
      height: 56px;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .nav-brand {
      font-family: 'Michroma', sans-serif;
      color: #FFD700;
      font-size: 14px;
      letter-spacing: 0.05em;
      text-decoration: none;
      white-space: nowrap;
    }
    .nav-spacer { flex: 1; }
    .nav a.nav-link {
      color: #FFD700;
      text-decoration: none;
      font-size: 12px;
      font-weight: 500;
      padding: 5px 14px;
      border-radius: 20px;
      border: 1px solid rgba(255,215,0,0.5);
      white-space: nowrap;
      transition: background 0.2s, color 0.2s;
    }
    .nav a.nav-link:hover {
      background: #FFD700;
      color: #111;
    }

    /* Container */
    .container {
      max-width: 720px;
      margin: 2rem auto;
      padding: 0 1.25rem;
    }

    /* Header card */
    .header-card {
      background: rgba(30,30,30,0.85);
      border: 1px solid rgba(255,215,0,0.2);
      border-left: 4px solid #FFD700;
      border-radius: 14px;
      padding: 1.75rem;
      margin-bottom: 1.25rem;
      display: flex;
      align-items: flex-start;
      gap: 1rem;
    }
    .header-card img.favicon {
      width: 40px;
      height: 40px;
      border-radius: 8px;
      flex-shrink: 0;
      margin-top: 4px;
    }
    .header-card h1 {
      font-family: 'Michroma', sans-serif;
      color: #FFD700;
      font-size: 1.35rem;
      letter-spacing: 0.04em;
      line-height: 1.3;
      margin-bottom: 0.5rem;
    }
    .header-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
    }

    /* Badges */
    .badge {
      font-size: 11px;
      font-weight: 600;
      padding: 3px 10px;
      border-radius: 20px;
    }
    .badge-active   { background: rgba(34,197,94,0.15); color: #4ade80; border: 1px solid rgba(74,222,128,0.3); }
    .badge-inactive { background: rgba(239,68,68,0.15);  color: #f87171; border: 1px solid rgba(248,113,113,0.3); }
    .badge-nosite   { background: rgba(156,163,175,0.15); color: #9ca3af; border: 1px solid rgba(156,163,175,0.3); }

    /* Tags */
    .tag {
      font-size: 11px;
      font-weight: 600;
      padding: 3px 10px;
      border-radius: 20px;
      background: rgba(255,215,0,0.1);
      color: #FFD700;
      border: 1px solid rgba(255,215,0,0.3);
    }
    .tag-cert {
      background: rgba(167,139,250,0.1);
      color: #c4b5fd;
      border: 1px solid rgba(196,181,253,0.3);
    }
    /* Self-reported certification: peach split pill, "MBE | Self-reported" (same look as the
       directory cards) -- easy to spot, never mistaken for a state certification. */
    .tag-self {
      display: inline-flex;
      align-items: stretch;
      padding: 0;
      overflow: hidden;
      background: rgba(255,150,110,0.1);
      color: #FFB896;
      border: 1px solid rgba(255,184,150,0.45);
    }
    .tag-self-code { padding: 3px 8px 3px 10px; }
    .tag-self-note {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 10px 3px 8px;
      font-size: 10px;
      font-weight: 500;
      letter-spacing: 0.3px;
      color: #FFDCCB;
      background: rgba(255,150,110,0.2);
      border-left: 1px solid rgba(255,184,150,0.3);
    }
    .tag-self-note svg { width: 10px; height: 10px; flex-shrink: 0; }

    /* Detail sections */
    .section {
      background: rgba(30,30,30,0.85);
      border: 1px solid rgba(255,215,0,0.2);
      border-radius: 14px;
      padding: 1.5rem;
      margin-bottom: 1.25rem;
    }
    .section-title {
      font-family: 'Michroma', sans-serif;
      color: #FFD700;
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin-bottom: 1rem;
    }
    .detail-row {
      display: flex;
      gap: 0.75rem;
      margin-bottom: 0.75rem;
      align-items: flex-start;
    }
    .detail-row:last-child { margin-bottom: 0; }
    .detail-label {
      font-size: 12px;
      color: rgba(255,255,255,0.45);
      min-width: 110px;
      flex-shrink: 0;
      padding-top: 1px;
    }
    .detail-value {
      font-size: 14px;
      color: rgba(255,255,255,0.78);
      line-height: 1.5;
    }
    .detail-value a {
      color: #FFD700;
      text-decoration: none;
    }
    .detail-value a:hover { text-decoration: underline; }

    .tags-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }

    /* Services */
    .services-text {
      font-size: 14px;
      color: rgba(255,255,255,0.78);
      line-height: 1.7;
    }

    /* CTA */
    .cta {
      background: rgba(30,30,30,0.85);
      border: 1px solid rgba(255,215,0,0.2);
      border-radius: 14px;
      padding: 1.5rem;
      text-align: center;
      margin-bottom: 1.25rem;
    }
    .cta p {
      font-size: 14px;
      color: rgba(255,255,255,0.6);
      margin-bottom: 1rem;
    }
    .btn {
      display: inline-block;
      font-size: 13px;
      font-weight: 600;
      padding: 10px 22px;
      border-radius: 20px;
      text-decoration: none;
      border: 1px solid #FFD700;
      color: #FFD700;
      background: transparent;
      margin: 0 6px;
      transition: background 0.2s, color 0.2s;
    }
    .btn:hover { background: #FFD700; color: #111; }
    .btn-primary { background: #FFD700; color: #111; }
    .btn-primary:hover { background: #e6c200; border-color: #e6c200; }

    /* Footer */
    .footer {
      text-align: center;
      font-size: 12px;
      color: rgba(255,255,255,0.3);
      padding: 2rem 0;
    }
    .footer a { color: rgba(255,255,255,0.4); text-decoration: none; }
    .footer a:hover { color: #FFD700; }

    @media (max-width: 600px) {
      .header-card { flex-direction: column; }
      .detail-label { min-width: 90px; }
      .nav-brand { font-size: 12px; }
      .nav { gap: 0.6rem; padding: 0 1rem; }
      .nav a.nav-home { display: none; }  /* the brand already links home */
    }

    /* Breadcrumbs, summaries and link lists (business + directory pages) */
    .crumbs { font-size: 12px; color: rgba(255,255,255,0.45); margin-bottom: 1rem; }
    .crumbs a { color: rgba(255,215,0,0.8); text-decoration: none; }
    .crumbs a:hover { text-decoration: underline; }
    .about-text { font-size: 14px; line-height: 1.7; color: rgba(255,255,255,0.78); }
    .page-intro { font-size: 15px; line-height: 1.7; color: rgba(255,255,255,0.7); margin-top: 0.5rem; }
    .link-list { display: flex; flex-wrap: wrap; gap: 0.5rem; list-style: none; }
    .link-list a {
      display: inline-block; font-size: 12px; padding: 5px 12px; border-radius: 20px;
      color: #FFD700; text-decoration: none; border: 1px solid rgba(255,215,0,0.35);
    }
    .link-list a:hover { background: #FFD700; color: #111; }
    .link-list .count { color: rgba(255,255,255,0.45); margin-left: 6px; }
    .link-list a:hover .count { color: #333; }
    .biz-list { list-style: none; }
    .biz-list li { padding: 0.75rem 0; border-bottom: 1px solid rgba(255,255,255,0.08); }
    .biz-list li:last-child { border-bottom: none; }
    .biz-list a.biz-name { color: #FFD700; font-weight: 600; text-decoration: none; font-size: 14px; }
    .biz-list a.biz-name:hover { text-decoration: underline; }
    .biz-meta { font-size: 12px; color: rgba(255,255,255,0.5); margin-top: 3px; }
    .biz-snippet { font-size: 13px; color: rgba(255,255,255,0.65); margin-top: 4px; line-height: 1.5; }
    .dir-h1 { font-family: 'Michroma', sans-serif; color: #FFD700; font-size: 1.3rem; line-height: 1.4; letter-spacing: 0.03em; }
`;

// Shared navigation and footer for every generated page.
const NAV_HTML = `<nav class="nav">
  <a class="nav-brand" href="${SITE_URL}/">The People's Ledger</a>
  <div class="nav-spacer"></div>
  <a class="nav-link nav-home" href="${SITE_URL}/">← Directory</a>
  <a class="nav-link" href="${SITE_URL}/directory/">Browse</a>
  <a class="nav-link" href="${SITE_URL}/about">About</a>
</nav>`;

const FOOTER_HTML = `<footer class="footer">
  &copy; 2025 The People's Ledger &nbsp;|&nbsp; Operated by Education to Action LLC &nbsp;|&nbsp;
  <a href="${SITE_URL}/directory/">Browse the Directory</a> &nbsp;|&nbsp;
  <a href="${SITE_URL}/about">About</a>
  <br /><br />
  Money Talks. Spend Where It Counts.
</footer>`;

function breadcrumbLd(crumbs) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: crumbs.map((c, i) => ({ "@type": "ListItem", position: i + 1, name: c.name, item: c.url })),
  };
}

// Crumb names are raw text; escaped here.
function crumbsHtml(crumbs) {
  return `<nav class="crumbs" aria-label="Breadcrumb">${crumbs.map((c, i) =>
    i === crumbs.length - 1 ? esc(c.name) : `<a href="${c.url}">${esc(c.name)}</a>`).join(" › ")}</nav>`;
}

// links: [{ name, url, count? }] with raw names.
function linkListHtml(links) {
  return `<ul class="link-list">${links.map(l =>
    `<li><a href="${l.url}">${esc(l.name)}${l.count != null ? `<span class="count">${l.count}</span>` : ""}</a></li>`).join("")}</ul>`;
}

// A compact list of businesses; the services snippet is left off long lists to keep
// pages like "Women-Owned Businesses in Kentucky" (1,400+ entries) a sensible size.
function bizListHtml(list, withSnippet) {
  return `<ul class="biz-list">${list.map(b => {
    const meta = [cityOf(b), splitField(b.industry)[0], ...splitField(b.minority_type).map(ownershipLabel)].filter(Boolean).map(esc).join(" · ");
    const svc = String(b.services_products || "").trim();
    const snippet = withSnippet && svc ? `<div class="biz-snippet">${esc(svc.length > 160 ? svc.slice(0, 157).replace(/\s+\S*$/, "") + "…" : svc)}</div>` : "";
    return `<li><a class="biz-name" href="${SITE_URL}/businesses/${b._slug}">${esc(b.business_name)}</a><div class="biz-meta">${meta}</div>${snippet}</li>`;
  }).join("")}</ul>`;
}

// ── Directory model (city / category / ownership pages) ───────────────────────

// A directory page needs at least this many businesses; smaller ones would be thin.
const MIN_DIR_PAGE = 5;
const DIR_DIR = path.join(__dirname, "directory");

// Groups every listed business by city, industry and ownership, plus the pairings
// people actually search ("Black-owned businesses in Louisville"). Returns the pages
// to write and linksFor(biz), which the business pages use for breadcrumbs and links.
function buildDirectory(businesses) {
  const listed = businesses.filter(b => b._slug && b.business_name && b.business_name.trim());
  const pages = new Map();
  const add = (slug, def, biz) => {
    if (!slug) return;
    let p = pages.get(slug);
    if (!p) { p = { slug, ...def, items: [] }; pages.set(slug, p); }
    else if (p.kind !== def.kind) return;   // slug clash across kinds: first kind keeps it
    if (!p.items.includes(biz)) p.items.push(biz);
  };

  for (const b of listed) {
    const city = cityOf(b);
    const inds = splitField(b.industry);
    const owns = splitField(b.minority_type).map(ownershipLabel);
    const cs = slugify(city);
    if (city) add(cs, { kind: "city", city }, b);
    for (const ind of inds) {
      add(slugify(ind), { kind: "industry", ind }, b);
      if (city) add(`${slugify(ind)}-${cs}`, { kind: "industry-city", ind, city }, b);
    }
    for (const own of owns) {
      add(slugify(own), { kind: "ownership", own }, b);
      if (city) add(`${slugify(own)}-${cs}`, { kind: "ownership-city", own, city }, b);
      for (const ind of inds) add(`${slugify(own)}-${slugify(ind)}`, { kind: "ownership-industry", own, ind }, b);
    }
  }

  for (const [slug, p] of pages) {
    if (p.items.length < MIN_DIR_PAGE) { pages.delete(slug); continue; }
    p.items.sort((a, c) => a.business_name.localeCompare(c.business_name) || a.id - c.id);
    p.url = `${SITE_URL}/directory/${slug}`;
    const where = p.city ? `${p.city}, KY` : "Kentucky";
    const whereLong = p.city ? `${p.city}, Kentucky` : "Kentucky";
    switch (p.kind) {
      case "city":
        p.name = p.city;
        p.h1 = `Black-Owned, Women-Owned & Minority-Owned Businesses in ${where}`;
        p.title = `Minority-Owned & Women-Owned Businesses in ${where} | The People's Ledger`;
        p.phrase = `underrepresented-owned businesses in ${whereLong}`;
        break;
      case "industry":
        p.name = p.ind;
        p.h1 = `Minority-Owned & Women-Owned ${p.ind} Businesses in Kentucky`;
        p.title = `${p.ind} – Minority-Owned & Women-Owned Businesses in Kentucky | The People's Ledger`;
        p.phrase = `underrepresented-owned ${p.ind} businesses in Kentucky`;
        break;
      case "ownership":
        p.name = p.own;
        p.h1 = `${p.own} Businesses in Kentucky`;
        p.title = `${p.own} Businesses in Kentucky | The People's Ledger`;
        p.phrase = `${p.own.toLowerCase()} businesses in Kentucky`;
        break;
      case "industry-city":
        p.name = `${p.ind} in ${p.city}`;
        p.h1 = `Minority-Owned & Women-Owned ${p.ind} Businesses in ${where}`;
        p.title = `${p.ind} Businesses in ${where} – Minority & Women-Owned | The People's Ledger`;
        p.phrase = `underrepresented-owned ${p.ind} businesses in ${whereLong}`;
        break;
      case "ownership-city":
        p.name = `${p.own} in ${p.city}`;
        p.h1 = `${p.own} Businesses in ${where}`;
        p.title = `${p.own} Businesses in ${where} | The People's Ledger`;
        p.phrase = `${p.own.toLowerCase()} businesses in ${whereLong}`;
        break;
      case "ownership-industry":
        p.name = `${p.own} ${p.ind}`;
        p.h1 = `${p.own} ${p.ind} Businesses in Kentucky`;
        p.title = `${p.own} ${p.ind} Businesses in Kentucky | The People's Ledger`;
        p.phrase = `${p.own.toLowerCase()} ${p.ind} businesses in Kentucky`;
        break;
    }
  }

  const get = slug => pages.get(slug) || null;
  const link = p => ({ name: p.name, url: p.url, count: p.items.length });
  const byCount = (a, c) => c.items.length - a.items.length || a.name.localeCompare(c.name);
  const all = [...pages.values()];

  // Parent pages (broader) and child pages (narrower) for a directory page.
  function related(p) {
    const parents = [], children = [];
    const own = p.own && get(slugify(p.own)), ind = p.ind && get(slugify(p.ind)), city = p.city && get(slugify(p.city));
    if (p.kind === "industry-city") parents.push(city, ind);
    if (p.kind === "ownership-city") parents.push(city, own);
    if (p.kind === "ownership-industry") parents.push(own, ind);
    if (p.kind === "city") children.push(...all.filter(q => q.city === p.city && q.kind !== "city"));
    if (p.kind === "industry") children.push(...all.filter(q => q.ind === p.ind && q.kind !== "industry"));
    if (p.kind === "ownership") children.push(...all.filter(q => q.own === p.own && q.kind !== "ownership"));
    return { parents: parents.filter(Boolean), children: children.sort(byCount) };
  }

  // Breadcrumb, explore links and similar businesses for one business page.
  function linksFor(b) {
    const city = cityOf(b), cs = slugify(city);
    const inds = splitField(b.industry), owns = splitField(b.minority_type).map(ownershipLabel);
    const cityPage = city ? get(cs) : null;
    const candidates = [
      ...owns.map(o => city && get(`${slugify(o)}-${cs}`)),
      ...inds.map(i => city && get(`${slugify(i)}-${cs}`)),
      cityPage,
      ...owns.map(o => get(slugify(o))),
      ...inds.map(i => get(slugify(i))),
      ...owns.flatMap(o => inds.map(i => get(`${slugify(o)}-${slugify(i)}`))),
    ].filter(Boolean);
    const explore = [...new Set(candidates)].map(link);

    // Same industry in the same city first, then same industry anywhere in Kentucky,
    // then anything else in the same city.
    const pool = [
      ...(inds[0] && city ? (get(`${slugify(inds[0])}-${cs}`)?.items || []) : []),
      ...(inds[0] ? (get(slugify(inds[0]))?.items || []) : []),
      ...(cityPage?.items || []),
    ];
    const similar = [...new Set(pool)].filter(x => x !== b).slice(0, 6);
    const crumbPage = cityPage || (inds[0] && get(slugify(inds[0]))) || null;
    return { crumb: crumbPage ? link(crumbPage) : null, explore, similar };
  }

  return { pages: all, get, related, linksFor, link, byCount };
}

function dirPageShell({ title, description, canonical, ld, body }) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <script type="text/javascript">
    (function(c,l,a,r,i,t,y){
        c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};
        t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;
        y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);
    })(window, document, "clarity", "script", "xr6q4yw2ld");
  </script>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${esc(title)}</title>
  <link rel="icon" href="/favicon.ico" sizes="32x32" />
  <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
  <meta name="description" content="${esc(description)}" />
  <meta property="og:title" content="${esc(title)}" />
  <meta property="og:description" content="${esc(description)}" />
  <meta property="og:url" content="${canonical}" />
  <meta property="og:type" content="website" />
  <link rel="canonical" href="${canonical}" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Michroma&display=swap" rel="stylesheet" />
  <style>
${BASE_CSS}  </style>
  ${ld.map(jsonLd).join("\n  ")}
</head>
<body>

${NAV_HTML}

<div class="container">
${body}
</div>

${FOOTER_HTML}

</body>
</html>`;
}

function buildDirectoryPage(p, dir) {
  const n = p.items.length;
  const { parents, children } = dir.related(p);
  const crumbs = [{ name: "Directory", url: `${SITE_URL}/directory/` }, ...parents.slice(0, 1).map(dir.link), { name: p.name, url: p.url }];
  const description = `Browse ${n} ${p.phrase}, with contact details, ownership, and certifications. Part of The People's Ledger, Kentucky's directory of underrepresented businesses.`;
  const intro = `The People's Ledger lists ${n} ${esc(p.phrase)}. Each listing links to the business's own page with its address, phone, website, ownership, and any certifications. ` +
    `Know a business that should be here? <a href="${SITE_URL}/" style="color:#FFD700;">Submit it from the directory</a>.`;

  // Narrower pages grouped by what they narrow to, so a city page shows "By category"
  // and "By ownership" separately.
  const labelFor = c =>
    c.kind === "ownership-city" ? (p.kind === "ownership" ? "By city" : "By ownership")
    : c.kind === "industry-city" ? (p.kind === "industry" ? "By city" : "By category")
    : (p.kind === "ownership" ? "By category" : "By ownership");
  const groups = {};
  for (const c of children) (groups[labelFor(c)] = groups[labelFor(c)] || []).push(dir.link(c));
  const childHtml = Object.entries(groups).map(([label, links]) => `
  <div class="section">
    <div class="section-title">${label}</div>
    ${linkListHtml(links)}
  </div>`).join("");
  const parentHtml = parents.length ? `
  <div class="section">
    <div class="section-title">Related</div>
    ${linkListHtml(parents.map(dir.link))}
  </div>` : "";

  const ld = [{
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: p.h1,
    url: p.url,
    description,
    mainEntity: {
      "@type": "ItemList",
      numberOfItems: n,
      itemListElement: p.items.slice(0, 100).map((b, i) => ({ "@type": "ListItem", position: i + 1, url: `${SITE_URL}/businesses/${b._slug}`, name: b.business_name })),
    },
  }, breadcrumbLd(crumbs)];

  const body = `
  ${crumbsHtml(crumbs)}

  <div class="header-card">
    <div style="min-width:0;flex:1;">
      <h1 class="dir-h1">${esc(p.h1)}</h1>
      <p class="page-intro">${intro}</p>
    </div>
  </div>
${childHtml}
  <div class="section">
    <div class="section-title">${n} Businesses</div>
    ${bizListHtml(p.items, n <= 150)}
  </div>
${parentHtml}`;

  return dirPageShell({ title: p.title, description, canonical: p.url, ld, body });
}

function buildDirectoryIndex(dir) {
  const url = `${SITE_URL}/directory/`;
  const of = kind => dir.pages.filter(p => p.kind === kind).sort(dir.byCount).map(dir.link);
  const sections = [
    ["By ownership", of("ownership")],
    ["By category", of("industry")],
    ["By city", of("city")],
  ].filter(([, links]) => links.length);
  const description = "Browse Kentucky's Black-owned, women-owned, veteran-owned, Latine-owned, Asian-owned, LGBTQ+-owned and other underrepresented businesses by city, category, and ownership.";
  const crumbs = [{ name: "Directory", url }];
  const body = `
  <div class="header-card">
    <div style="min-width:0;flex:1;">
      <h1 class="dir-h1">Browse Kentucky's Underrepresented Businesses</h1>
      <p class="page-intro">${esc(description)} Or <a href="${SITE_URL}/" style="color:#FFD700;">search the full directory</a>.</p>
    </div>
  </div>
${sections.map(([label, links]) => `
  <div class="section">
    <div class="section-title">${label}</div>
    ${linkListHtml(links)}
  </div>`).join("")}`;
  const ld = [{ "@context": "https://schema.org", "@type": "CollectionPage", name: "Browse The People's Ledger", url, description }, breadcrumbLd(crumbs)];
  return dirPageShell({ title: "Browse Minority-Owned & Women-Owned Businesses in Kentucky | The People's Ledger", description, canonical: url, ld, body });
}

// ── Generate individual business HTML ─────────────────────────────────────────

function buildBusinessPage(biz, dir) {
  // Raw values are kept for URL-encoding and comparisons; everything written into the
  // HTML goes through esc().
  const raw = biz;
  const {
    status, kentucky_based,
  } = biz;
  const business_name     = esc(raw.business_name);
  const address           = raw.address ? esc(raw.address) : "";
  const phone             = raw.phone;
  const website           = raw.website;
  const services_products = raw.services_products ? esc(raw.services_products) : "";
  const industry          = raw.industry ? esc(raw.industry) : "";

  const slug         = biz._slug || slugify(raw.business_name);
  const minorityList = splitField(raw.minority_type).map(esc);
  const certList     = splitField(raw.certification_type).map(esc);
  const phoneDisplay = phone ? esc(formatPhone(phone)) : null;
  const phoneHref    = phone ? esc(phone) : null;
  const websiteHref    = fullUrl(website);
  const websiteDisplay = websiteHref ? esc(displayUrl(website)) : null;
  const faviconUrl     = websiteHref
    ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(websiteHref)}&sz=32`
    : null;

  const isActive  = status === "Active";
  const hasWebsite = status !== "No Website" && websiteHref;

  // Status badge
  const statusBadge = isActive
    ? `<span class="badge badge-active">Active</span>`
    : status === "Inactive"
      ? `<span class="badge badge-inactive">Inactive</span>`
      : `<span class="badge badge-nosite">No Website</span>`;

  // Minority type tags
  const minorityTags = minorityList.length
    ? minorityList.map(t => `<span class="tag">${t}</span>`).join("")
    : "";

  // Certification tags — suppress "Unknown" and "Not Certified" from display
  const certDisplay = certList.filter(c => c !== "Unknown" && c !== "Not Certified");
  const certTags = certDisplay.length
    ? certDisplay.map(c => `<span class="tag tag-cert">${c}</span>`).join("")
    : "";
  // Self-reported: stated on the business's own website, not verified by a Kentucky
  // certifier (see add_self_reported_cert.sql). Its own row and a peach split pill.
  const selfIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.5 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 12.5 3 8.5 8.5 0 0 1 21 11.5z"/></svg>`;
  const selfList = splitField(raw.self_reported_certification).map(esc);
  const selfTags = selfList.length
    ? selfList.map(c => `<span class="tag tag-self"><span class="tag-self-code">${c}</span><span class="tag-self-note">${selfIcon}Self-reported</span></span>`).join("")
    : "";

  // Description for meta tags
  const ownershipStr = minorityList.length ? minorityList.join(", ") + " business" : "underrepresented business";
  const industryStr  = industry ? ` in ${industry}` : "";
  const city         = cityOf(raw);
  const cityEsc      = esc(city);
  const locationStr  = city ? ` located in ${cityEsc}, Kentucky` : " in Kentucky";
  const description  = `${business_name} is a ${ownershipStr}${industryStr}${locationStr}. Find contact info, services, and certification details on The People's Ledger.`;

  // Title carries what people search for: ownership, industry, city.
  const ownerShort   = minorityList.length ? minorityList.slice(0, 2).map(ownershipLabel).join(" & ") : "Underrepresented";
  const industryList = splitField(raw.industry);
  const titleKind    = `${ownerShort} ${industryList.length ? esc(industryList[0]) + " " : ""}Business`;
  const pageTitle    = `${business_name} – ${titleKind} in ${city ? cityEsc + ", KY" : "Kentucky"} | The People's Ledger`;

  // A plain-language summary built from the listing's own fields.
  const certPhrase   = certDisplay.length ? ` It holds ${certDisplay.join(", ")} certification.` : "";
  const aboutText    = `${business_name} is ${minorityList.length ? ownershipPhrase(splitField(raw.minority_type)) : "an underrepresented-owned"} business` +
    `${industryList.length ? " in the " + industryList.map(esc).join(" and ") + " " + (industryList.length > 1 ? "industries" : "industry") : ""}` +
    `${city ? `, based in ${cityEsc}, Kentucky` : kentucky_based === "Yes" ? ", based in Kentucky" : ""}.${certPhrase}` +
    ` It is listed in The People's Ledger, a directory of Black-owned, women-owned, veteran-owned, and other underrepresented businesses in Kentucky.`;

  const pageUrl  = `${SITE_URL}/businesses/${slug}`;
  const links    = dir ? dir.linksFor(raw) : { crumb: null, explore: [], similar: [] };
  const crumbs   = [{ name: "Directory", url: `${SITE_URL}/directory/` }];
  if (links.crumb) crumbs.push({ name: links.crumb.name, url: links.crumb.url });
  crumbs.push({ name: raw.business_name, url: pageUrl });

  const ld = [{
    "@context": "https://schema.org",
    "@type": "LocalBusiness",
    name: raw.business_name,
    url: pageUrl,
    ...(websiteHref ? { sameAs: [websiteHref] } : {}),
    ...(phone ? { telephone: phone } : {}),
    ...(raw.address ? { address: raw.address } : {}),
    ...(raw.services_products ? { description: raw.services_products.slice(0, 300) } : {}),
    ...(raw.industry ? { knowsAbout: industryList } : {}),
  }, breadcrumbLd(crumbs)];

  // Directory back-link — links to index with business name pre-filled in search
  // (index.html reads ?search= on load).
  const directoryLink = `${SITE_URL}/?search=${encodeURIComponent(raw.business_name)}`;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <script type="text/javascript">
    (function(c,l,a,r,i,t,y){
        c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};
        t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;
        y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);
    })(window, document, "clarity", "script", "xr6q4yw2ld");
  </script>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${pageTitle}</title>
  <link rel="icon" href="/favicon.ico" sizes="32x32" />
  <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
  <meta name="description" content="${description}" />
  <meta property="og:title" content="${business_name} | The People's Ledger" />
  <meta property="og:description" content="${description}" />
  <meta property="og:url" content="${SITE_URL}/businesses/${slug}" />
  <meta property="og:type" content="website" />
  <link rel="canonical" href="${SITE_URL}/businesses/${slug}" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Michroma&display=swap" rel="stylesheet" />
  <style>
${BASE_CSS}  </style>
  ${ld.map(jsonLd).join("\n  ")}
</head>
<body>

${NAV_HTML}

<div class="container">

  ${crumbsHtml(crumbs)}

  <!-- Header -->
  <div class="header-card">
    ${faviconUrl ? `<img class="favicon" src="${faviconUrl}" alt="${business_name} logo" />` : ""}
    <div style="min-width:0;flex:1;">
      <h1>${business_name}</h1>
      <div class="header-meta">
        ${statusBadge}
        ${industry ? `<span style="font-size:12px;color:rgba(255,255,255,0.45);">${industry}</span>` : ""}
      </div>
    </div>
  </div>

  <!-- Ownership & Certification -->
  ${(minorityTags || certTags || selfTags) ? `
  <div class="section">
    <div class="section-title">Ownership &amp; Certification</div>
    ${minorityTags ? `
    <div class="detail-row">
      <div class="detail-label">Ownership</div>
      <div class="detail-value"><div class="tags-row">${minorityTags}</div></div>
    </div>` : ""}
    ${certTags ? `
    <div class="detail-row">
      <div class="detail-label">Certifications</div>
      <div class="detail-value"><div class="tags-row">${certTags}</div></div>
    </div>` : ""}
    ${selfTags ? `
    <div class="detail-row">
      <div class="detail-label">Self-reported</div>
      <div class="detail-value"><div class="tags-row">${selfTags}</div>
        <div style="font-size:12px;color:rgba(255,255,255,0.45);margin-top:6px;">Stated on the business's own website; not verified by a Kentucky certifier.</div></div>
    </div>` : ""}
  </div>` : ""}

  <!-- Contact Info -->
  <div class="section">
    <div class="section-title">Contact</div>
    ${address ? `
    <div class="detail-row">
      <div class="detail-label">Address</div>
      <div class="detail-value">
        <a href="https://maps.google.com/?q=${encodeURIComponent(raw.address)}" target="_blank" rel="noopener">${address}</a>
      </div>
    </div>` : ""}
    ${phoneDisplay ? `
    <div class="detail-row">
      <div class="detail-label">Phone</div>
      <div class="detail-value"><a href="tel:${phoneHref}">${phoneDisplay}</a></div>
    </div>` : ""}
    ${hasWebsite ? `
    <div class="detail-row">
      <div class="detail-label">Website</div>
      <div class="detail-value">
        <a href="${esc(websiteHref)}" target="_blank" rel="noopener">${websiteDisplay}</a>
      </div>
    </div>` : ""}
    ${kentucky_based === "Yes" ? `
    <div class="detail-row">
      <div class="detail-label">Location</div>
      <div class="detail-value">Kentucky-based</div>
    </div>` : ""}
  </div>

  <!-- About -->
  <div class="section">
    <div class="section-title">About</div>
    <p class="about-text">${aboutText}</p>
  </div>

  <!-- Services -->
  ${services_products ? `
  <div class="section">
    <div class="section-title">Services &amp; Products</div>
    <div class="services-text">${services_products}</div>
  </div>` : ""}

  ${links.similar.length ? `
  <!-- Similar businesses -->
  <div class="section">
    <div class="section-title">Similar Businesses</div>
    ${bizListHtml(links.similar, false)}
  </div>` : ""}

  ${links.explore.length ? `
  <!-- Explore -->
  <div class="section">
    <div class="section-title">Explore the Directory</div>
    ${linkListHtml(links.explore)}
  </div>` : ""}

  <!-- CTA -->
  <div class="cta">
    <p>Find more underrepresented businesses like this one in the directory.</p>
    <a class="btn btn-primary" href="${directoryLink}">View in Directory</a>
    <a class="btn" href="${SITE_URL}/">Browse All Businesses</a>
  </div>

</div>

${FOOTER_HTML}

</body>
</html>`;
}

// ── Generate sitemap ──────────────────────────────────────────────────────────

// The date a file last changed. Pages are only rewritten when their content changes
// (see main), so a page's mtime is the date its content last changed. The sitemap used
// to stamp TODAY on every URL on every run, telling Google all 2,300 pages had changed
// even when none had -- search engines learn to ignore a lastmod that is always "today".
function lastModified(file) {
  try { return fs.statSync(file).mtime.toISOString().split("T")[0]; }
  catch { return new Date().toISOString().split("T")[0]; }
}

function buildSitemap(businesses, dirPages) {
  const urls = businesses.map(b => `
  <url>
    <loc>${SITE_URL}/businesses/${b._slug}</loc>
    <lastmod>${lastModified(path.join(OUT_DIR, `${b._slug}.html`))}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>`).join("");
  const dirUrls = dirPages.map(d => `
  <url>
    <loc>${SITE_URL}/directory/${d.slug}</loc>
    <lastmod>${lastModified(path.join(DIR_DIR, `${d.slug}.html`))}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>${d.kind === "city" || d.kind === "industry" || d.kind === "ownership" ? "0.8" : "0.6"}</priority>
  </url>`).join("");

  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>${SITE_URL}/</loc>
    <lastmod>${lastModified(path.join(__dirname, "index.html"))}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>${SITE_URL}/about</loc>
    <lastmod>${lastModified(path.join(__dirname, "about.html"))}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>
  <url>
    <loc>${SITE_URL}/directory/</loc>
    <lastmod>${lastModified(path.join(DIR_DIR, "index.html"))}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>${dirUrls}${urls}
</urlset>`;
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log("Fetching businesses from Supabase...");
  const businesses = await fetchAllBusinesses();
  console.log(`  ${businesses.length} businesses fetched.`);

  // Must run before any page, filename, or sitemap entry is produced -- all three read
  // biz._slug and must agree.
  assignSlugs(businesses);
  const dir = buildDirectory(businesses);

  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR);

  let written = 0;
  let skipped = 0;
  let unchanged = 0;
  for (const biz of businesses) {
    if (!biz.business_name || !biz.business_name.trim()) { skipped++; continue; }
    const slug = biz._slug;
    const html = buildBusinessPage(biz, dir);
    const dest = path.join(OUT_DIR, `${slug}.html`);
    // Only write when the content actually differs. Rewriting an identical file still
    // updates its mtime and, more to the point, git sees every file as modified: a run
    // that changed 623 businesses produced 649 modified files and a commit touching all
    // of them. Comparing first keeps the diff honest about what changed.
    // Line endings are normalised before comparing: with core.autocrlf on Windows, git
    // checks these files out with CRLF while this script writes LF, so a raw comparison
    // called every page "changed" after any checkout and rewrote it -- resetting its
    // sitemap lastmod to today for no real change.
    let existing = null;
    try { existing = fs.readFileSync(dest, "utf8").replace(/\r\n/g, "\n"); } catch { /* new file */ }
    if (existing === html) { unchanged++; continue; }
    fs.writeFileSync(dest, html, "utf8");
    written++;
  }
  console.log(`  ${written} business page(s) written, ${unchanged} unchanged.`);
  if (skipped) console.log(`  ${skipped} records skipped (no business name).`);

  // Remove pages for businesses that are no longer listed publicly (purged, merged by
  // dedupe_live, deleted in admin, or hidden for lacking ownership evidence -- the
  // publishable key cannot read those, see hide_unevidenced_listings.sql; a hidden
  // listing's page comes back by itself once it is tagged). This script used to only ever add files, so a deleted
  // business kept a live, indexed page -- and stayed in search results -- indefinitely.
  // Removed pages are git-tracked, so `git checkout` brings one back if needed.
  // Guard: if the fetch came back much smaller than the folder, something is wrong with
  // the fetch, not with the folder, so remove nothing.
  const current = new Set(businesses.filter(b => b._slug).map(b => `${b._slug}.html`));
  const onDisk = fs.readdirSync(OUT_DIR).filter(f => f.endsWith(".html"));
  const orphans = onDisk.filter(f => !current.has(f));
  if (orphans.length && current.size < onDisk.length * 0.9) {
    console.log(`  !! ${orphans.length} page(s) have no matching business, but only ${current.size} businesses ` +
                `were fetched for ${onDisk.length} pages -- NOT removing anything. Check the fetch.`);
  } else if (orphans.length) {
    for (const f of orphans) fs.unlinkSync(path.join(OUT_DIR, f));
    console.log(`  ${orphans.length} page(s) removed for businesses no longer listed (deleted or hidden).`);
  }

  // Directory pages: city, category, ownership and their pairings, plus /directory/.
  // Rebuilt from scratch each run; pages that fall under MIN_DIR_PAGE are removed.
  if (!fs.existsSync(DIR_DIR)) fs.mkdirSync(DIR_DIR);
  let dirWritten = 0;
  if (writeIfChanged(path.join(DIR_DIR, "index.html"), buildDirectoryIndex(dir))) dirWritten++;
  for (const p of dir.pages) if (writeIfChanged(path.join(DIR_DIR, `${p.slug}.html`), buildDirectoryPage(p, dir))) dirWritten++;
  const dirKeep = new Set(["index.html", ...dir.pages.map(p => `${p.slug}.html`)]);
  const dirStale = fs.readdirSync(DIR_DIR).filter(f => f.endsWith(".html") && !dirKeep.has(f));
  for (const f of dirStale) fs.unlinkSync(path.join(DIR_DIR, f));
  console.log(`  ${dir.pages.length} directory page(s): ${dirWritten} written, ${dirStale.length} removed.`);

  const sitemap = buildSitemap(businesses.filter(b => b.business_name && b.business_name.trim()), dir.pages);
  const sitemapPath = path.join(OUT_DIR, "sitemap.xml");
  let oldSitemap = null;
  try { oldSitemap = fs.readFileSync(sitemapPath, "utf8").replace(/\r\n/g, "\n"); } catch { /* first run */ }
  if (oldSitemap === sitemap) {
    console.log("  sitemap.xml unchanged.");
  } else {
    fs.writeFileSync(sitemapPath, sitemap, "utf8");
    console.log("  sitemap.xml written to /businesses/");
  }

  // id -> slug for every page written, so the directory cards in index.html can link to
  // the business's page. The slug cannot be recomputed in the browser: a name shared by
  // two businesses gets a city suffix, and only this script sees every name at once.
  const slugMap = {};
  for (const b of [...businesses].sort((a, c) => a.id - c.id)) if (b._slug) slugMap[b.id] = b._slug;
  const slugsJson = JSON.stringify(slugMap) + "\n";
  const slugsPath = path.join(OUT_DIR, "slugs.json");
  let oldSlugs = null;
  try { oldSlugs = fs.readFileSync(slugsPath, "utf8").replace(/\r\n/g, "\n"); } catch { /* first run */ }
  if (oldSlugs !== slugsJson) {
    fs.writeFileSync(slugsPath, slugsJson, "utf8");
    console.log(`  slugs.json written (${Object.keys(slugMap).length} card links).`);
  }

  console.log("\nDone. Next steps:");
  console.log("  git add businesses/ directory/");
  console.log('  git commit -m "Generate business SEO pages"');
  console.log("  git push");
  console.log("\nThen submit your sitemap to Google Search Console:");
  console.log(`  ${SITE_URL}/businesses/sitemap.xml`);
  console.log("  https://search.google.com/search-console");
}

main().catch(err => {
  console.error("Error:", err.message);
  process.exit(1);
});
