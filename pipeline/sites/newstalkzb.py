"""Newstalk ZB adapter — author query pages + RSS with Trafilatura extraction.

Author pages: https://www.newstalkzb.co.nz/author/?Author={Name}
RSS feed: https://www.newstalkzb.co.nz/news/rss (no author info)
Articles under /news/{category}/{slug}/ and /opinion/{slug}/
NZME-owned but uses Umbraco CMS (not Arc like NZ Herald).
"""

import re
import logging
from urllib.parse import quote
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://www.newstalkzb.co.nz"
AUTHOR_PAGE_URL = "https://www.newstalkzb.co.nz/author/?Author={name}"
RSS_URL = "https://www.newstalkzb.co.nz/news/rss"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)

# Article URL patterns
ARTICLE_RE = re.compile(r'href="(/(?:news|opinion)/[a-z0-9-]+/[a-z0-9-]+/?)"')

# Slug-to-display-name mapping for known journalists
SLUG_TO_NAME = {
    "barry-soper": "Barry Soper",
    "heather-du-plessis-allan": "Heather du Plessis-Allan",
}


class NewstalkZBAdapter(SiteAdapter):
    name = "newstalkzb"
    domain = "newstalkzb.co.nz"
    needs_playwright = False

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        urls: list[str] = []

        if author_slug:
            author_urls = await self._get_author_page_urls(author_slug)
            urls.extend(author_urls)

        # Supplement with RSS (not author-specific, but catches recent articles)
        if not urls:
            rss_urls = await self._get_rss_urls()
            urls.extend(rss_urls)

        # Deduplicate
        seen: set[str] = set()
        deduped: list[str] = []
        for u in urls:
            u = u.rstrip("/")
            if u not in seen:
                seen.add(u)
                deduped.append(u)

        return deduped

    async def _get_author_page_urls(self, author_slug: str) -> list[str]:
        # Convert slug to display name
        display_name = SLUG_TO_NAME.get(author_slug)
        if not display_name:
            # Fallback: convert slug to title case
            display_name = author_slug.replace("-", " ").title()

        author_url = AUTHOR_PAGE_URL.format(name=quote(display_name))
        log.debug(f"NewstalkZB: fetching author page {author_url}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(author_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"NewstalkZB author page {author_url} returned {resp.status}")
                        return []
                    html = await resp.text()
            except Exception as e:
                log.warning(f"NewstalkZB author page fetch failed: {e}")
                return []

        raw = ARTICLE_RE.findall(html)
        # Also match opinion pieces at top level
        raw += re.findall(r'href="(/opinion/[a-z0-9-]+/?)"', html)

        seen: set[str] = set()
        urls: list[str] = []
        for path in raw:
            full = BASE_URL + path.rstrip("/")
            if full not in seen:
                seen.add(full)
                urls.append(full)

        log.info(f"NewstalkZB: found {len(urls)} URLs on author page for {author_slug} ({display_name})")
        return urls

    async def _get_rss_urls(self) -> list[str]:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(RSS_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    xml = await resp.text()
            except Exception:
                return []

        urls = re.findall(r'<link>(https?://www\.newstalkzb\.co\.nz/(?:news|opinion)/[^<]+)</link>', xml)
        if not urls:
            urls = re.findall(r'<guid[^>]*>(https?://www\.newstalkzb\.co\.nz/(?:news|opinion)/[^<]+)</guid>', xml)
        return [u.rstrip("/") for u in urls]

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            except Exception as e:
                log.warning(f"NewstalkZB: failed to fetch {url}: {e}")
                return None

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not extracted:
            return None

        metadata = trafilatura.extract_metadata(html)
        author = metadata.author if metadata else ""

        # Fallback: try JSON-LD for author
        if not author:
            ld_match = re.search(r'"@type":\s*"Person"[^}]*"name":\s*"([^"]+)"', html)
            if ld_match:
                author = ld_match.group(1)

        if not author:
            return None

        return Article(
            url=url,
            title=metadata.title if metadata else "",
            author=author,
            publish_date=metadata.date if metadata else "",
            outlet="Newstalk ZB",
            text=extracted,
        )
