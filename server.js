import express from "express";
import * as cheerio from "cheerio";

const app = express();
const PORT = process.env.PORT ? Number(process.env.PORT) : 5177;

app.use(express.static(process.cwd(), { extensions: ["html"] }));

function parseIntFromHits(text) {
  const n = Number(String(text).replace(/[^\d]/g, ""));
  return Number.isFinite(n) ? n : null;
}

async function fetchText(url) {
  // Node 18+ has global fetch.
  const res = await fetch(url, {
    headers: {
      // Basic UA to avoid some bot blocks; still not guaranteed.
      "user-agent":
        "ultimate-guitar-stats-onepager/1.0 (+local dev; educational)",
      "accept":
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Upstream fetch failed: ${res.status} ${res.statusText}\n${body.slice(0, 300)}`);
  }
  return await res.text();
}

function parseTopTabs(html) {
  const $ = cheerio.load(html);

  // The page is a table-like list; the most stable approach is:
  // - Find the header row containing Artist / Song / Hits / Type (Song may be absent in some renders)
  // - Then walk subsequent rows.
  const allText = $("body").text();
  if (!allText || allText.length < 100) return [];

  const rows = [];

  // Primary: parse anchor links to artists along with nearby hits/type text.
  // We look for artist links in the main content area; UG uses /artist/ in href.
  const artistLinks = $("a[href*=\"/artist/\"]");
  artistLinks.each((_, el) => {
    const a = $(el);
    const artist = a.text().trim();
    const artistUrl = a.attr("href")?.trim() || null;
    if (!artist) return;

    // Try to read the next few text nodes after the anchor within the same parent/row.
    // We climb to a reasonable container: closest 'tr' if present, otherwise parent.
    const container = a.closest("tr").length ? a.closest("tr") : a.parent();
    const containerText = container.text().replace(/\s+/g, " ").trim();

    // Heuristic: hits is a big number with commas; type is one of known strings.
    const hitsMatch = containerText.match(/(\d[\d,]{3,})/);
    const typeMatch = containerText.match(/\b(chords|tab|tabs|bass|ukulele|power|guitar pro|pro|drums)\b/i);

    const hits = hitsMatch ? parseIntFromHits(hitsMatch[1]) : null;
    const typeRaw = typeMatch ? typeMatch[1] : null;
    const type =
      typeRaw?.toLowerCase() === "tabs" ? "tab" : typeRaw?.toLowerCase() || null;

    if (hits == null || !type) return;

    // Avoid duplicates: the same artist link can appear in header/footer.
    // Keep only plausible "top list" entries with large hits.
    if (hits < 100000) return;

    rows.push({ artist, artistUrl, hits, type });
  });

  // Deduplicate by (artist,hits,type)
  const seen = new Set();
  const deduped = [];
  for (const r of rows) {
    const key = `${r.artist}__${r.hits}__${r.type}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(r);
  }

  // Sort descending by hits.
  deduped.sort((a, b) => b.hits - a.hits);

  return deduped;
}

app.get("/api/top-tabs", async (req, res) => {
  const url =
    req.query.url?.toString() ||
    "https://www.ultimate-guitar.com/top/tabs?order=hitstotal_desc&type=all";

  try {
    const html = await fetchText(url);
    const items = parseTopTabs(html);
    res.json({
      source: url,
      fetchedAt: new Date().toISOString(),
      count: items.length,
      items
    });
  } catch (e) {
    res.status(502).json({
      error: "Failed to fetch/parse upstream page",
      details: e instanceof Error ? e.message : String(e)
    });
  }
});

app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
  console.log(`Open: http://localhost:${PORT}/index.html`);
});

