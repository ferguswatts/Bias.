"""Stuff.co.nz + The Post adapter — uses their shared internal JSON API.

Both Stuff and The Post (thepost.co.nz) run on the same CMS and share article IDs.
Their internal API provides:
  - Author pages:   GET /api/v1.0/stuff/page?path=authors/{slug}
  - Article data:   GET /api/v1.0/stuff/story/{article_id}

Article body is returned as HTML in content.contentBody.body — no Playwright needed.
The article ID is always embedded in the URL: /section/{numeric_id}/{slug}.

Stuff note: some authors have a numeric suffix on their slug (e.g. andrea-vance-0).
STUFF_SLUG_OVERRIDES maps the canonical CSV slug to the actual API slug.

Falls back to the politics section API if all author page lookups return no results.
"""

import re
import logging
import html as html_lib
from .base import SiteAdapter, Article

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://www.stuff.co.nz"
THEPOST_BASE_URL = "https://www.thepost.co.nz"

# Both domains share the same API structure and article IDs
STUFF_PAGE_API = "https://www.stuff.co.nz/api/v1.0/stuff/page?path={path}"
THEPOST_PAGE_API = "https://www.thepost.co.nz/api/v1.0/stuff/page?path={path}"
STORY_API = "https://www.stuff.co.nz/api/v1.0/stuff/story/{story_id}"

# Stuff CMS adds numeric suffixes to some author slugs to avoid collisions.
# Map: canonical CSV-derived slug → actual API slug
STUFF_SLUG_OVERRIDES: dict[str, str] = {
    "andrea-vance": "andrea-vance-0",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)

# Stuff article IDs are 9-digit numbers embedded in every article URL
ARTICLE_ID_RE = re.compile(r"/(\d{9,})/")

# Strip HTML tags for text extraction
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _extract_text_from_html(body_html: str) -> str:
    """Strip HTML tags and decode entities to get plain text from contentBody."""
    if not body_html:
        return ""
    text = HTML_TAG_RE.sub(" ", body_html)
    text = html_lib.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _stories_to_urls(data: dict, base_url: str = BASE_URL) -> list[str]:
    """Extract article URLs from a Stuff/ThePost page API response."""
    urls = []
    seen: set[str] = set()
    for section in data.get("data", []):
        for story in section.get("stories", []):
            content_url = story.get("content", {}).get("url", "")
            if not content_url:
                continue
            full = base_url + content_url if content_url.startswith("/") else content_url
            if full not in seen:
                seen.add(full)
                urls.append(full)
    return urls


class StuffAdapter(SiteAdapter):
    name = "stuff"
    domain = "stuff.co.nz"
    needs_playwright = False  # API-based, no browser rendering needed

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        if author_slug:
            urls = await self._get_author_urls(author_slug)
            if urls:
                return urls

        # Fallback: politics section
        return await self._get_section_urls("politics")

    async def _get_author_urls(self, author_slug: str) -> list[str]:
        """Check both Stuff and The Post author pages — they share the same CMS."""
        # Apply slug override if one exists (e.g. andrea-vance → andrea-vance-0)
        api_slug = STUFF_SLUG_OVERRIDES.get(author_slug, author_slug)

        urls: list[str] = []
        seen: set[str] = set()

        async with aiohttp.ClientSession() as session:
            for base_url, page_api in [
                (BASE_URL, STUFF_PAGE_API),
                (THEPOST_BASE_URL, THEPOST_PAGE_API),
            ]:
                api_url = page_api.format(path=f"authors/{api_slug}")
                log.debug(f"Stuff/ThePost: hitting {api_url}")
                try:
                    async with session.get(api_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                except Exception as e:
                    log.warning(f"Stuff/ThePost: API failed for {api_url}: {e}")
                    continue

                for story_url in _stories_to_urls(data, base_url=base_url):
                    if story_url not in seen:
                        seen.add(story_url)
                        urls.append(story_url)

        log.info(f"Stuff/ThePost: found {len(urls)} articles for {author_slug}")
        return urls

    async def _get_section_urls(self, section: str) -> list[str]:
        api_url = STUFF_PAGE_API.format(path=section)
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            except Exception:
                return []

        return _stories_to_urls(data)

    async def extract_article(self, url: str) -> Article | None:
        """Extract article content via the Stuff/ThePost story API (no Playwright needed).
        Works for both stuff.co.nz and thepost.co.nz URLs — they share article IDs."""
        # Extract the numeric article ID from the URL
        match = ARTICLE_ID_RE.search(url)
        if not match:
            log.debug(f"Stuff/ThePost: no article ID in URL {url}")
            return None

        story_id = match.group(1)
        api_url = STORY_API.format(story_id=story_id)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            except Exception as e:
                log.warning(f"Stuff/ThePost: story API failed for {url}: {e}")
                return None

        # Extract author name
        authors = data.get("author", [])
        author = authors[0].get("name", "") if authors else ""
        if not author:
            return None

        # Extract article text from HTML body
        body_html = data.get("content", {}).get("contentBody", {}).get("body", "")
        text = _extract_text_from_html(body_html)
        if len(text) < 100:
            return None

        title = data.get("content", {}).get("title", "") or data.get("teaser", {}).get("title", "")
        publish_date = data.get("publishedDate", "") or data.get("date", "")
        if publish_date:
            publish_date = publish_date[:10]

        # Determine outlet from URL domain
        outlet = "The Post" if "thepost.co.nz" in url else "Stuff"

        return Article(
            url=url,
            title=title,
            author=author,
            publish_date=publish_date,
            outlet=outlet,
            text=text,
        )
