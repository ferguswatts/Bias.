"""1News (TVNZ) adapter — politics section + RSS feed + Google News historical backfill.

1News has no public author pages. URL discovery strategy (layered):

  1. Politics section: GET /news/politics                  — ~95 recent articles (SSR)
     Arc global RSS:   GET /arc/outboundfeeds/rss/         — ~77 recent articles
     Combined + deduped → ~100 unique URLs per run.
     The orchestrator filters by author name to find the journalist's articles.

  2. Historical backfill (--backfill flag only):
     a. Fetch Google News RSS: news.google.com/rss/search?q={name}+site:1news.co.nz
        → 100 results with titles and publish dates
     b. Reconstruct likely 1News URL from title slug + date
        (1News URLs: /YYYY/MM/DD/article-slug-from-title)
     c. Verify each candidate URL with a HEAD request (parallel, 10 concurrent)
     d. Returns ~70-80 verified historical article URLs

Individual articles are SSR-rendered (Next.js) so Trafilatura extracts author reliably.
"""

import re
import asyncio
import logging
from email.utils import parsedate_to_datetime
from .base import SiteAdapter, Article

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

BASE_URL = "https://www.1news.co.nz"
POLITICS_URL = "https://www.1news.co.nz/news/politics"
RSS_URL = "https://www.1news.co.nz/arc/outboundfeeds/rss/?outputType=xml"

# Google News RSS search — 100 results, no auth required
GNEWS_RSS_URL = (
    "https://news.google.com/rss/search"
    "?q={query}+site:1news.co.nz&hl=en-NZ&gl=NZ&ceid=NZ:en"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-NZ,en;q=0.9",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)
VERIFY_TIMEOUT = aiohttp.ClientTimeout(total=10)

# 1News article URLs: /YYYY/MM/DD/article-slug or /YYYY/article-slug
ARTICLE_URL_RE = re.compile(r"^https://www\.1news\.co\.nz/\d{4}/\S+")

# Max concurrent URL verifications during backfill
VERIFY_CONCURRENCY = 10


def _dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for u in urls:
        clean = u.split("?")[0].rstrip("/")
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _title_to_slug(title: str) -> str:
    """Convert an article title to a 1News-style URL slug.

    1News slugs are lowercase, hyphenated, with punctuation stripped.
    E.g. "John Campbell: What exactly has the tide brought in?" →
         "john-campbell-what-exactly-has-the-tide-brought-in"
    """
    # Strip source suffix like '- 1News'
    title = re.sub(r"\s*-\s*1News\s*$", "", title, flags=re.IGNORECASE)
    slug = title.lower()
    # Remove common punctuation
    slug = re.sub(r"['\"\u2018\u2019\u201c\u201d:;,!?.()\[\]&\u2013\u2014]", "", slug)
    # Spaces and separators → hyphens
    slug = re.sub(r"[\s_/\\]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


class OneNewsAdapter(SiteAdapter):
    name = "1news"
    domain = "1news.co.nz"
    needs_playwright = False

    async def get_article_urls(
        self,
        since_date: str | None = None,
        author_slug: str | None = None,
        backfill: bool = False,
    ) -> list[str]:
        """Collect 1News article URLs from all available sources."""
        # Layer 1: politics section + global RSS (recent articles)
        section_urls = await self._get_section_urls()
        rss_urls = await self._get_rss_urls()
        recent = _dedupe(section_urls + rss_urls)
        log.info(f"1News: {len(recent)} recent URLs from section + RSS")

        # Layer 2: historical backfill via Google News title reconstruction
        gnews_urls: list[str] = []
        if backfill and author_slug:
            # Convert slug like "john-campbell" → "John Campbell" for the search query
            author_name = " ".join(w.capitalize() for w in author_slug.split("-"))
            gnews_urls = await self._get_gnews_historical_urls(author_name)
            log.info(
                f"1News: {len(gnews_urls)} historical URLs from Google News for {author_name}"
            )

        combined = _dedupe(recent + gnews_urls)
        log.info(f"1News: {len(combined)} unique URLs total for {author_slug or 'all'}")
        return combined

    async def _get_section_urls(self) -> list[str]:
        """Fetch the politics section page — Next.js SSR means full HTML available."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    POLITICS_URL, headers=HEADERS, timeout=TIMEOUT
                ) as resp:
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
        """Fetch the Arc global RSS feed."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    RSS_URL, headers=HEADERS, timeout=TIMEOUT
                ) as resp:
                    if resp.status != 200:
                        return []
                    xml = await resp.text()
            except Exception:
                return []

        links = re.findall(
            r"<link>(https://www\.1news\.co\.nz/\d{4}/[^<]+)</link>", xml
        )
        links += re.findall(
            r"<guid[^>]*>(https://www\.1news\.co\.nz/\d{4}/[^<]+)</guid>", xml
        )
        return [u.rstrip("/") for u in links]

    async def _get_gnews_historical_urls(self, author_name: str) -> list[str]:
        """Discover historical 1News articles via Google News RSS + title-to-URL reconstruction.

        Strategy:
          1. Fetch 100 results from Google News RSS for "{author_name} site:1news.co.nz"
          2. For each result, reconstruct the likely 1News URL from article title + publish date
             (1News URL format: /YYYY/MM/DD/article-slug-derived-from-title)
          3. Verify each candidate URL with a HEAD request (10 concurrent)
          4. Return verified URLs only

        Achieves ~70-80% hit rate on reconstructed URLs. Misses come from:
          - Very long titles where 1News truncated the slug differently
          - Articles from the journalist's pre-1News career
        """
        import urllib.parse

        query = urllib.parse.quote(author_name)
        gnews_url = GNEWS_RSS_URL.format(query=query)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    gnews_url, headers=HEADERS, timeout=TIMEOUT
                ) as resp:
                    if resp.status != 200:
                        log.warning(
                            f"1News GNews: feed returned {resp.status} for {author_name}"
                        )
                        return []
                    xml = await resp.text()
            except Exception as e:
                log.warning(f"1News GNews: failed to fetch feed for {author_name}: {e}")
                return []

        # Extract items: title + pubDate pairs
        items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
        log.debug(f"1News GNews: got {len(items)} items for {author_name}")

        candidates: list[str] = []
        for item in items:
            title_m = re.search(r"<title>([^<]+)</title>", item)
            date_m = re.search(r"<pubDate>([^<]+)</pubDate>", item)
            if not (title_m and date_m):
                continue

            title = title_m.group(1)
            try:
                dt = parsedate_to_datetime(date_m.group(1))
            except Exception:
                continue

            # Only reconstruct URLs for 1News articles (not other outlets)
            publisher_m = re.search(r"<source[^>]*>([^<]*)</source>", item)
            if publisher_m and "1news" not in publisher_m.group(1).lower():
                continue

            slug = _title_to_slug(title)
            if not slug:
                continue

            candidate = f"{BASE_URL}/{dt.year}/{dt.month:02d}/{dt.day:02d}/{slug}"
            candidates.append(candidate)

        log.debug(f"1News GNews: {len(candidates)} candidate URLs to verify")

        # Verify candidates in parallel (HEAD requests)
        semaphore = asyncio.Semaphore(VERIFY_CONCURRENCY)
        async with aiohttp.ClientSession() as session:
            tasks = [
                self._verify_url(session, semaphore, url) for url in candidates
            ]
            results = await asyncio.gather(*tasks)

        verified = [url for url, ok in zip(candidates, results) if ok]
        log.info(
            f"1News GNews: {len(verified)}/{len(candidates)} URLs verified for {author_name}"
        )
        return verified

    async def _verify_url(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        url: str,
    ) -> bool:
        """HEAD request to confirm a URL exists on 1News."""
        async with semaphore:
            try:
                async with session.head(
                    url,
                    headers=HEADERS,
                    timeout=VERIFY_TIMEOUT,
                    allow_redirects=True,
                ) as resp:
                    return resp.status == 200
            except Exception:
                return False

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    url, headers=HEADERS, timeout=TIMEOUT
                ) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            except Exception as e:
                log.warning(f"1News: failed to fetch {url}: {e}")
                return None

        extracted = trafilatura.extract(
            html, include_comments=False, include_tables=False
        )
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
