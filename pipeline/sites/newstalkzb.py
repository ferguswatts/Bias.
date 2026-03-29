"""Newstalk ZB adapter — sitemap-based discovery with Trafilatura extraction.

Sitemaps:
  https://www.newstalkzb.co.nz/sitemaps/opinion.xml  (1000+ articles, 2018-2025, author in URL)
  https://www.newstalkzb.co.nz/sitemaps/on-air.xml   (500+ segments, rolling 2 weeks)
  https://www.newstalkzb.co.nz/sitemaps/news.xml     (500+ articles, rolling 4 weeks)

Author pages: https://www.newstalkzb.co.nz/author/?Author={Name} (fallback)
NZME-owned, Umbraco CMS.
"""

import re
import logging
from urllib.parse import quote
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://www.newstalkzb.co.nz"
SITEMAP_URLS = [
    "https://www.newstalkzb.co.nz/sitemaps/opinion.xml",
    "https://www.newstalkzb.co.nz/sitemaps/on-air.xml",
    "https://www.newstalkzb.co.nz/sitemaps/news.xml",
]
AUTHOR_PAGE_URL = "https://www.newstalkzb.co.nz/author/?Author={name}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=30)

# Slug-to-display-name mapping for known journalists
SLUG_TO_NAME = {
    "barry-soper": "Barry Soper",
    "heather-du-plessis-allan": "Heather du Plessis-Allan",
    "mike-hosking": "Mike Hosking",
}

# Show-name to author-slug mapping for on-air content
SHOW_TO_SLUG = {
    "mike-hosking-breakfast": "mike-hosking",
    "heather-du-plessis-allan-drive": "heather-du-plessis-allan",
    "barry-soper": "barry-soper",
    "marcus-lush-nights": "marcus-lush",
    "early-edition-with-ryan-bridge": "ryan-bridge",
    "saturday-morning-with-jack-tame": "jack-tame",
    "kerre-woodham-mornings": "kerre-woodham",
}


class NewstalkZBAdapter(SiteAdapter):
    name = "newstalkzb"
    domain = "newstalkzb.co.nz"
    needs_playwright = False

    _sitemap_cache: dict[str, list[str]] | None = None

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        # Load all sitemap URLs (cached across calls)
        if self._sitemap_cache is None:
            self._sitemap_cache = await self._load_all_sitemaps()

        if not author_slug:
            # Return all URLs
            all_urls = []
            for urls in self._sitemap_cache.values():
                all_urls.extend(urls)
            return list(set(all_urls))

        # Filter URLs for this author
        matched = self._sitemap_cache.get(author_slug, [])

        # Also try author page for additional coverage
        page_urls = await self._get_author_page_urls(author_slug)
        matched.extend(page_urls)

        # Deduplicate
        seen: set[str] = set()
        deduped: list[str] = []
        for u in matched:
            u = u.rstrip("/")
            if u not in seen:
                seen.add(u)
                deduped.append(u)

        log.info(f"NewstalkZB: {len(deduped)} total URLs for {author_slug}")
        return deduped

    async def get_all_urls_by_author(self) -> dict[str, list[str]]:
        """Return all sitemap URLs grouped by detected author slug.
        Useful for discovering which journalists have content."""
        if self._sitemap_cache is None:
            self._sitemap_cache = await self._load_all_sitemaps()
        return dict(self._sitemap_cache)

    async def _load_all_sitemaps(self) -> dict[str, list[str]]:
        """Fetch all sitemaps and group URLs by author slug."""
        author_urls: dict[str, list[str]] = {}

        async with aiohttp.ClientSession() as session:
            for sitemap_url in SITEMAP_URLS:
                try:
                    async with session.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                        if resp.status != 200:
                            log.warning(f"NewstalkZB sitemap {sitemap_url} returned {resp.status}")
                            continue
                        xml = await resp.text()
                except Exception as e:
                    log.warning(f"NewstalkZB sitemap fetch failed: {e}")
                    continue

                urls = re.findall(r'<loc>([^<]+)</loc>', xml)
                log.info(f"NewstalkZB: {len(urls)} URLs from {sitemap_url.split('/')[-1]}")

                for url in urls:
                    slug = self._extract_author_slug(url)
                    if slug:
                        author_urls.setdefault(slug, []).append(url)

        total = sum(len(v) for v in author_urls.values())
        log.info(f"NewstalkZB: {total} URLs across {len(author_urls)} authors from sitemaps")
        return author_urls

    def _extract_author_slug(self, url: str) -> str | None:
        """Try to extract an author slug from a URL path."""
        path = url.replace(BASE_URL, "")

        # Opinion with author subdirectory: /opinion/barry-soper/headline/
        m = re.match(r'/opinion/([a-z-]+)/[a-z0-9-]+/?$', path)
        if m:
            author = m.group(1)
            if author not in ("zb-plus-guest-opinions",):
                return author

        # Flat opinion URLs: /opinion/author-name-headline/
        # We store these as "unattributed" — the pipeline will extract
        # the real author from page metadata when it fetches the article
        if re.match(r'/opinion/[a-z0-9-]+/?$', path):
            return "_opinion_unattributed"

        # On-air opinion: /on-air/mike-hosking-breakfast/opinion/slug/
        m = re.match(r'/on-air/([a-z-]+)/opinion/', path)
        if m:
            show = m.group(1)
            return SHOW_TO_SLUG.get(show)

        # On-air audio: skip audio-only content (full show podcasts, etc.)
        # These aren't articles we can score

        return None

    async def _get_author_page_urls(self, author_slug: str) -> list[str]:
        display_name = SLUG_TO_NAME.get(author_slug)
        if not display_name:
            display_name = author_slug.replace("-", " ").title()

        author_url = AUTHOR_PAGE_URL.format(name=quote(display_name))
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(author_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()
            except Exception:
                return []

        raw = re.findall(r'href="(/(?:news|opinion)/[a-z0-9-]+/[a-z0-9-]+/?)"', html)
        raw += re.findall(r'href="(/opinion/[a-z0-9-]+/?)"', html)
        raw += re.findall(r'href="(/on-air/[a-z0-9-]+/opinion/[a-z0-9-]+/?)"', html)

        return [BASE_URL + p.rstrip("/") for p in set(raw)]

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
