"""The Spinoff adapter — Atom feed + author pages with Trafilatura extraction.

Atom feed: https://thespinoff.co.nz/feed
Author pages: https://thespinoff.co.nz/author/{slug}
Articles: /{category}/{DD-MM-YYYY}/{slug}
"""

import re
import logging
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://thespinoff.co.nz"
FEED_URL = "https://thespinoff.co.nz/feed"
AUTHOR_PAGE_URL = "https://thespinoff.co.nz/author/{slug}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)

# Article URL pattern: /{category}/{DD-MM-YYYY}/{slug}
ARTICLE_RE = re.compile(r'https?://thespinoff\.co\.nz/[a-z-]+/\d{2}-\d{2}-\d{4}/[a-z0-9-]+')


class SpinoffAdapter(SiteAdapter):
    name = "thespinoff"
    domain = "thespinoff.co.nz"
    needs_playwright = False

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        urls: list[str] = []

        # Try Atom feed first — it includes author info
        if author_slug:
            feed_urls = await self._get_feed_urls_for_author(author_slug)
            if feed_urls:
                urls.extend(feed_urls)

        # Also scrape author page for more coverage
        if author_slug:
            page_urls = await self._get_author_page_urls(author_slug)
            urls.extend(page_urls)

        # Deduplicate
        seen: set[str] = set()
        deduped: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)

        return deduped

    async def _get_feed_urls_for_author(self, author_slug: str) -> list[str]:
        """Parse Atom feed and filter entries by author slug."""
        log.debug(f"Spinoff: fetching Atom feed")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(FEED_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"Spinoff feed returned {resp.status}")
                        return []
                    xml = await resp.text()
            except Exception as e:
                log.warning(f"Spinoff feed fetch failed: {e}")
                return []

        # Find entries whose author URI contains the author slug
        # Atom format: <entry>...<author><uri>https://thespinoff.co.nz/authors/{slug}</uri></author>...<link href="..."/>...</entry>
        urls: list[str] = []
        entries = re.findall(r'<entry>(.*?)</entry>', xml, re.DOTALL)
        for entry in entries:
            author_uris = re.findall(r'<uri>([^<]+)</uri>', entry)
            if any(author_slug in uri for uri in author_uris):
                links = re.findall(r'<link[^>]*href="([^"]+)"', entry)
                for link in links:
                    if ARTICLE_RE.match(link):
                        urls.append(link)

        log.info(f"Spinoff: found {len(urls)} feed URLs for {author_slug}")
        return urls

    async def _get_author_page_urls(self, author_slug: str) -> list[str]:
        author_url = AUTHOR_PAGE_URL.format(slug=author_slug)
        log.debug(f"Spinoff: fetching author page {author_url}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(author_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"Spinoff author page {author_url} returned {resp.status}")
                        return []
                    html = await resp.text()
            except Exception as e:
                log.warning(f"Spinoff author page fetch failed: {e}")
                return []

        urls = ARTICLE_RE.findall(html)
        log.info(f"Spinoff: found {len(urls)} URLs on author page for {author_slug}")
        return urls

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            except Exception as e:
                log.warning(f"Spinoff: failed to fetch {url}: {e}")
                return None

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not extracted:
            return None

        metadata = trafilatura.extract_metadata(html)
        author = metadata.author if metadata else ""
        if not author:
            # Try JSON-LD
            ld_match = re.search(r'"author":\s*\{[^}]*"name":\s*"([^"]+)"', html)
            if ld_match:
                author = ld_match.group(1)

        if not author:
            return None

        return Article(
            url=url,
            title=metadata.title if metadata else "",
            author=author,
            publish_date=metadata.date if metadata else "",
            outlet="The Spinoff",
            text=extracted,
        )
