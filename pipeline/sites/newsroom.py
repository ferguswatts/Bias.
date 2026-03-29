"""Newsroom adapter — WordPress author pages with Trafilatura extraction.

Author pages: https://newsroom.co.nz/author/{slug}/
WordPress CMS — articles at /YYYY/MM/DD/slug/ or legacy /slug format.
"""

import re
import logging
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://newsroom.co.nz"
AUTHOR_PAGE_URL = "https://newsroom.co.nz/author/{slug}/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)

# Paths that are not articles
RESERVED_PATHS = {"author", "category", "home", "opinion", "about-newsroom", "feed", "wp-json", "wp-content", "wp-admin", "tag", "page"}


class NewsroomAdapter(SiteAdapter):
    name = "newsroom"
    domain = "newsroom.co.nz"
    needs_playwright = False

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        if author_slug:
            urls = await self._get_author_page_urls(author_slug)
            if urls:
                return urls
        return []

    async def _get_author_page_urls(self, author_slug: str) -> list[str]:
        author_url = AUTHOR_PAGE_URL.format(slug=author_slug)
        log.debug(f"Newsroom: fetching author page {author_url}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(author_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"Newsroom author page {author_url} returned {resp.status}")
                        return []
                    html = await resp.text()
            except Exception as e:
                log.warning(f"Newsroom author page fetch failed: {e}")
                return []

        # Match dated articles: /YYYY/MM/DD/slug/
        dated = re.findall(r'href="(/?(?:https?://(?:www\.)?newsroom\.co\.nz)?/\d{4}/\d{2}/\d{2}/[a-z0-9][a-z0-9-]+[a-z0-9]/?)"', html)
        # Match legacy articles: /slug (exclude reserved paths)
        legacy = re.findall(r'href="(/?(?:https?://(?:www\.)?newsroom\.co\.nz)?/([a-z0-9][a-z0-9-]+[a-z0-9])/?)"', html)

        seen: set[str] = set()
        urls: list[str] = []

        for path in dated:
            path = path.rstrip("/")
            if path.startswith("http"):
                full = path
            elif path.startswith("/"):
                full = BASE_URL + path
            else:
                full = BASE_URL + "/" + path
            if full not in seen:
                seen.add(full)
                urls.append(full)

        for full_match, slug_part in legacy:
            if slug_part in RESERVED_PATHS:
                continue
            path = full_match.rstrip("/")
            if path.startswith("http"):
                full = path
            elif path.startswith("/"):
                full = BASE_URL + path
            else:
                full = BASE_URL + "/" + path
            if full not in seen:
                seen.add(full)
                urls.append(full)

        log.info(f"Newsroom: found {len(urls)} URLs on author page for {author_slug}")
        return urls

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            except Exception as e:
                log.warning(f"Newsroom: failed to fetch {url}: {e}")
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
            outlet="Newsroom",
            text=extracted,
        )
