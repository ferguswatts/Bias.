"""1News (TVNZ) adapter — politics section + RSS feed, author filtered.

1News has no public author pages. Instead:
  - Fetches the SSR-rendered politics section (/news/politics) — ~95 recent articles
  - Fetches the Arc RSS feed                                    — ~76 recent articles
  - Deduplicates and returns combined URL list (~120-150 unique)
  - The orchestrator filters results to the target journalist by author name.

Individual articles are SSR-rendered (Next.js) so Trafilatura extracts author reliably.
"""

import re
import logging
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://www.1news.co.nz"
POLITICS_URL = "https://www.1news.co.nz/news/politics"
RSS_URL = "https://www.1news.co.nz/arc/outboundfeeds/rss/?outputType=xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-NZ,en;q=0.9",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)

# 1News article URLs: /YYYY/MM/DD/article-slug or /YYYY/article-slug
ARTICLE_URL_RE = re.compile(r'^https://www\.1news\.co\.nz/\d{4}/\S+')


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for u in urls:
        clean = u.split("?")[0].rstrip("/")
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


class OneNewsAdapter(SiteAdapter):
    name = "1news"
    domain = "1news.co.nz"
    needs_playwright = False

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None) -> list[str]:
        """Combine politics section page (SSR) + RSS feed for maximum article coverage."""
        section_urls = await self._get_section_urls()
        rss_urls = await self._get_rss_urls()
        combined = _dedupe(section_urls + rss_urls)
        log.info(f"1News: {len(combined)} unique URLs from section + RSS")
        return combined

    async def _get_section_urls(self) -> list[str]:
        """Fetch the politics section page — Next.js SSR means full HTML available."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(POLITICS_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"1News: politics page returned {resp.status}")
                        return []
                    html = await resp.text()
            except Exception as e:
                log.warning(f"1News: failed to fetch politics page: {e}")
                return []

        # Match absolute and relative article URLs
        raw = re.findall(r'href="(/\d{4}/[^"#?]+)"', html)
        raw += re.findall(r'href="(https://www\.1news\.co\.nz/\d{4}/[^"#?]+)"', html)
        urls = []
        for u in raw:
            full = BASE_URL + u if u.startswith("/") else u
            if ARTICLE_URL_RE.match(full) and full.count("/") >= 4:
                urls.append(full)
        return urls

    async def _get_rss_urls(self) -> list[str]:
        """Fetch the Arc RSS feed."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(RSS_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    xml = await resp.text()
            except Exception:
                return []

        links = re.findall(r'<link>(https://www\.1news\.co\.nz/\d{4}/[^<]+)</link>', xml)
        # Arc RSS also uses <guid>
        links += re.findall(r'<guid[^>]*>(https://www\.1news\.co\.nz/\d{4}/[^<]+)</guid>', xml)
        return [u.rstrip("/") for u in links]

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            except Exception as e:
                log.warning(f"1News: failed to fetch {url}: {e}")
                return None

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not extracted:
            return None

        metadata = trafilatura.extract_metadata(html)
        author = metadata.author if metadata else ""
        if not author:
            return None

        return Article(
            url=url,
            title=metadata.title if metadata else "",
            author=author,
            publish_date=metadata.date if metadata else "",
            outlet="1News",
            text=extracted,
        )
