// The People's Ledger — Business SEO Page Generator
// Run from repo root: node generate-business-pages.js
// Requires Node 18+ (native fetch). Older Node: npm install node-fetch and require it.
//
// What this does:
//   1. Queries Supabase for all businesses (paginated — handles 1,191+ records)
//   2. Writes a static HTML file to /businesses/{slug}.html for each business
//   3. Writes /businesses/sitemap.xml listing every business page
//
// After running:
//   git add businesses/
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

// Ensure URL has protocol for hrefs
function fullUrl(url) {
  if (!url) return null;
  return url.startsWith("http") ? url : "https://" + url;
}

// ── Fetch all businesses ──────────────────────────────────────────────────────

async function fetchAllBusinesses() {
  const fields = [
    "id", "business_name", "address", "phone", "website",
    "services_products", "minority_type", "industry",
    "status", "kentucky_based", "certification_type"
  ].join(",");

  let all    = [];
  let offset = 0;
  const limit = 1000;

  while (true) {
    const url = `${SUPABASE_URL}/rest/v1/businesses?select=${fields}&order=business_name.asc&limit=${limit}&offset=${offset}`;
    const res = await fetch(url, {
      headers: {
        apikey:        SUPABASE_KEY,
        Authorization: `Bearer ${SUPABASE_KEY}`,
      },
    });
    if (!res.ok) throw new Error(`Supabase error: ${res.status} ${await res.text()}`);
    const batch = await res.json();
    all = all.concat(batch);
    if (batch.length < limit) break;
    offset += limit;
  }

  return all;
}

// ── Generate individual business HTML ─────────────────────────────────────────

function buildBusinessPage(biz) {
  const {
    id, business_name, address, phone, website,
    services_products, minority_type, industry,
    status, kentucky_based, certification_type
  } = biz;

  const slug         = slugify(business_name);
  const minorityList = splitField(minority_type);
  const certList     = splitField(certification_type);
  const phoneDisplay = formatPhone(phone);
  const websiteDisplay = displayUrl(website);
  const websiteHref    = fullUrl(website);
  const faviconUrl     = website
    ? `https://www.google.com/s2/favicons?domain=${encodeURIComponent(websiteHref)}&sz=32`
    : null;

  const isActive  = status === "Active";
  const hasWebsite = status !== "No Website" && website;

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

  // Description for meta tags
  const ownershipStr = minorityList.length ? minorityList.join(", ") + " business" : "underrepresented business";
  const industryStr  = industry ? ` in ${industry}` : "";
  const locationStr  = address ? ` located in Kentucky` : " in Kentucky";
  const description  = `${business_name} is a ${ownershipStr}${industryStr}${locationStr}. Find contact info, services, and certification details on The People's Ledger.`;

  // Directory back-link — links to index with business name pre-filled in search
  const directoryLink = `${SITE_URL}/index.html?search=${encodeURIComponent(business_name)}`;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${business_name} | The People's Ledger — Kentucky Underrepresented Business Directory</title>
  <meta name="description" content="${description}" />
  <meta property="og:title" content="${business_name} | The People's Ledger" />
  <meta property="og:description" content="${description}" />
  <meta property="og:url" content="${SITE_URL}/businesses/${slug}.html" />
  <meta property="og:type" content="website" />
  <link rel="canonical" href="${SITE_URL}/businesses/${slug}.html" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Michroma&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

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
    }
  </style>
</head>
<body>

<nav class="nav">
  <a class="nav-brand" href="${SITE_URL}/index.html">The People's Ledger</a>
  <div class="nav-spacer"></div>
  <a class="nav-link" href="${SITE_URL}/index.html">← Directory</a>
  <a class="nav-link" href="${SITE_URL}/about.html">About</a>
</nav>

<div class="container">

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
  ${(minorityTags || certTags) ? `
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
  </div>` : ""}

  <!-- Contact Info -->
  <div class="section">
    <div class="section-title">Contact</div>
    ${address ? `
    <div class="detail-row">
      <div class="detail-label">Address</div>
      <div class="detail-value">
        <a href="https://maps.google.com/?q=${encodeURIComponent(address)}" target="_blank" rel="noopener">${address}</a>
      </div>
    </div>` : ""}
    ${phoneDisplay ? `
    <div class="detail-row">
      <div class="detail-label">Phone</div>
      <div class="detail-value"><a href="tel:${phone}">${phoneDisplay}</a></div>
    </div>` : ""}
    ${hasWebsite ? `
    <div class="detail-row">
      <div class="detail-label">Website</div>
      <div class="detail-value">
        <a href="${websiteHref}" target="_blank" rel="noopener">${websiteDisplay}</a>
      </div>
    </div>` : ""}
    ${kentucky_based === "Yes" ? `
    <div class="detail-row">
      <div class="detail-label">Location</div>
      <div class="detail-value">Kentucky-based</div>
    </div>` : ""}
  </div>

  <!-- Services -->
  ${services_products ? `
  <div class="section">
    <div class="section-title">Services &amp; Products</div>
    <div class="services-text">${services_products}</div>
  </div>` : ""}

  <!-- CTA -->
  <div class="cta">
    <p>Find more underrepresented businesses like this one in the directory.</p>
    <a class="btn btn-primary" href="${directoryLink}">View in Directory</a>
    <a class="btn" href="${SITE_URL}/index.html">Browse All Businesses</a>
  </div>

</div>

<footer class="footer">
  &copy; 2025 The People's Ledger &nbsp;|&nbsp; Operated by Education to Action LLC &nbsp;|&nbsp;
  <a href="${SITE_URL}/about.html">About</a>
  <br /><br />
  Money Talks. Spend Where It Counts.
</footer>

</body>
</html>`;
}

// ── Generate sitemap ──────────────────────────────────────────────────────────

function buildSitemap(businesses) {
  const today = new Date().toISOString().split("T")[0];
  const urls = businesses.map(b => `
  <url>
    <loc>${SITE_URL}/businesses/${slugify(b.business_name)}.html</loc>
    <lastmod>${today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>`).join("");

  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>${SITE_URL}/index.html</loc>
    <lastmod>${today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>${SITE_URL}/about.html</loc>
    <lastmod>${today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>${urls}
</urlset>`;
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log("Fetching businesses from Supabase...");
  const businesses = await fetchAllBusinesses();
  console.log(`  ${businesses.length} businesses fetched.`);

  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR);

  let written = 0;
  let skipped = 0;
  for (const biz of businesses) {
    if (!biz.business_name || !biz.business_name.trim()) { skipped++; continue; }
    const slug = slugify(biz.business_name);
    const html = buildBusinessPage(biz);
    fs.writeFileSync(path.join(OUT_DIR, `${slug}.html`), html, "utf8");
    written++;
  }
  console.log(`  ${written} business pages written to /businesses/`);
  if (skipped) console.log(`  ${skipped} records skipped (no business name).`);

  const sitemap = buildSitemap(businesses.filter(b => b.business_name && b.business_name.trim()));
  fs.writeFileSync(path.join(OUT_DIR, "sitemap.xml"), sitemap, "utf8");
  console.log("  sitemap.xml written to /businesses/");

  console.log("\nDone. Next steps:");
  console.log("  git add businesses/");
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
