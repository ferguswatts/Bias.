"""RNZ adapter — author pages with Trafilatura extraction.

Author pages: https://www.rnz.co.nz/authors/{slug}
Falls back to politics RSS if author page returns no results.
"""

import re
import logging
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://www.rnz.co.nz"
AUTHOR_PAGE_URL = "https://www.rnz.co.nz/authors/{slug}"
POLITICS_RSS_URL = "https://www.rnz.co.nz/rss/political.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)


class RNZAdapter(SiteAdapter):
    name = "rnz"
    domain = "rnz.co.nz"
    needs_playwright = False

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        if author_slug:
            urls = await self._get_author_page_urls(author_slug)
            if urls:
                return urls

        # Fallback: politics RSS (not author-specific)
        return await self._get_rss_urls()

    async def _get_author_page_urls(self, author_slug: str) -> list[str]:
        author_url = AUTHOR_PAGE_URL.format(slug=author_slug)
        log.debug(f"RNZ: fetching author page {author_url}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(author_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"RNZ author page {author_url} returned {resp.status}")
                        return []
                    html = await resp.text()
            except Exception as e:
                log.warning(f"RNZ author page fetch failed: {e}")
                return []

        # RNZ article URLs: /news/{category}/{numeric-id}/{slug}
        # e.g. /news/political/512345/story-title
        raw = re.findall(r'href="(/news/[a-z-]+/\d{5,}/[^"#?]+)"', html)
        raw += re.findall(r'href="(/national/\d{5,}/[^"#?]+)"', html)

        seen: set[str] = set()
        urls: list[str] = []
        for path in raw:
            full = BASE_URL + path
            if full not in seen:
                seen.add(full)
                urls.append(full)

        log.info(f"RNZ: found {len(urls)} URLs on author page for {author_slug}")
        return urls

    async def _get_rss_urls(self) -> list[str]:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(POLITICS_RSS_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    xml = await resp.text()
            except Exception:
                return []
        return re.findall(r"<link>(https://www\.rnz\.co\.nz/news/[^<]+)</link>", xml)

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            except Exception as e:
                log.warning(f"RNZ: failed to fetch {url}: {e}")
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
            outlet="RNZ",
            text=extracted,
        )
