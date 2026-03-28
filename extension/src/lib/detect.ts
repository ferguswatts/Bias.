/**
 * Layered byline detection: JSON-LD → meta tag → CSS selector.
 * Returns the detected author name and outlet domain, or null.
 */

export interface BylineResult {
  name: string;
  outlet: string;
}

/** Try JSON-LD structured data (most reliable — sites maintain this for SEO). */
function tryJsonLd(doc: Document): string | null {
  const scripts = doc.querySelectorAll('script[type="application/ld+json"]');
  for (const script of scripts) {
    try {
      const data = JSON.parse(script.textContent || "");
      // Handle both single object and array
      const items = Array.isArray(data) ? data : [data];
      for (const item of items) {
        if (
          item["@type"] === "NewsArticle" ||
          item["@type"] === "Article" ||
          item["@type"] === "ReportageNewsArticle"
        ) {
          const author = item.author;
          if (typeof author === "string") return author;
          if (Array.isArray(author) && author[0]?.name) return author[0].name;
          if (author?.name) return author.name;
        }
      }
    } catch {
      continue;
    }
  }
  return null;
}

/** Try <meta name="author"> tag. */
function tryMetaTag(doc: Document): string | null {
  const meta = doc.querySelector('meta[name="author"]');
  if (meta) {
    const content = meta.getAttribute("content")?.trim();
    if (content && content.length > 1 && content.length < 100) {
      return content;
    }
  }
  return null;
}

/** Try CSS selectors from site config (last resort). */
function tryCssSelector(
  doc: Document,
  siteConfig: Record<string, { selectors: { byline: string } }>,
): string | null {
  const hostname = window.location.hostname.replace("www.", "");
  const config = siteConfig[hostname];
  if (!config?.selectors?.byline) return null;

  const el = doc.querySelector(config.selectors.byline);
  if (el) {
    const text = el.textContent?.trim();
    if (text && text.length > 1 && text.length < 100) {
      return text;
    }
  }
  return null;
}

/**
 * Detect the journalist byline on the current page.
 * Layered: JSON-LD → meta → CSS selector.
 */
export function detectByline(
  doc: Document,
  siteConfig: Record<string, { selectors: { byline: string } }> = {},
): BylineResult | null {
  const outlet = window.location.hostname.replace("www.", "");

  const name = tryJsonLd(doc) ?? tryMetaTag(doc) ?? tryCssSelector(doc, siteConfig);

  if (!name) return null;

  // Clean up common prefixes
  const cleaned = name
    .replace(/^by\s+/i, "")
    .replace(/,.*$/, "") // Remove "Katie Bradford, Chief Political Editor"
    .trim();

  if (!cleaned || cleaned.length < 2) return null;

  return { name: cleaned, outlet };
}
