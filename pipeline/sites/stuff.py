"""Stuff.co.nz adapter — uses Stuff's internal JSON API directly.

Stuff is a React SPA (7KB static HTML). Their internal API provides:
  - Author pages:   GET /api/v1.0/stuff/page?path=authors/{slug}
  - Article data:   GET /api/v1.0/stuff/story/{article_id}

Article body is returned as HTML in content.contentBody.body — no Playwright needed.
The article ID is always embedded in the URL: /section/{numeric_id}/{slug}.

Falls back to the politics section API if author page returns no results.
"""

import re
import logging
import html as html_lib
from .base import SiteAdapter, Article

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://www.stuff.co.nz"
PAGE_API = "https://www.stuff.co.nz/api/v1.0/stuff/page?path={path}"
STORY_API = "https://www.stuff.co.nz/api/v1.0/stuff/story/{story_id}"

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


def _stories_to_urls(data: dict) -> list[str]:
    """Extract article URLs from a Stuff page API response."""
    urls = []
    seen: set[str] = set()
    for section in data.get("data", []):
        for story in section.get("stories", []):
            content_url = story.get("content", {}).get("url", "")
            if not content_url:
                continue
            full = BASE_URL + content_url if content_url.startswith("/") else content_url
            if full not in seen:
                seen.add(full)
                urls.append(full)
    return urls


class StuffAdapter(SiteAdapter):
    name = "stuff"
    domain = "stuff.co.nz"
    needs_playwright = False  # API-based, no browser rendering needed

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None) -> list[str]:
        if author_slug:
            urls = await self._get_author_urls(author_slug)
            if urls:
                return urls

        # Fallback: politics section
        return await self._get_section_urls("politics")

    async def _get_author_urls(self, author_slug: str) -> list[str]:
        api_url = PAGE_API.format(path=f"authors/{author_slug}")
        log.debug(f"Stuff: hitting author API {api_url}")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url, headers=HEADERS, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        log.warning(f"Stuff: author API returned {resp.status} for {author_slug}")
                        return []
                    data = await resp.json()
            except Exception as e:
                log.warning(f"Stuff: author API failed for {author_slug}: {e}")
                return []

        urls = _stories_to_urls(data)
        log.info(f"Stuff: found {len(urls)} articles for {author_slug}")
        return urls

    async def _get_section_urls(self, section: str) -> list[str]:
        api_url = PAGE_API.format(path=section)
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
        """Extract article content via the Stuff story API (no Playwright needed)."""
        # Extract the numeric article ID from the URL
        match = ARTICLE_ID_RE.search(url)
        if not match:
            log.debug(f"Stuff: no article ID in URL {url}")
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
                log.warning(f"Stuff: story API failed for {url}: {e}")
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
        # Normalize date to YYYY-MM-DD
        if publish_date:
            publish_date = publish_date[:10]

        return Article(
            url=url,
            title=title,
            author=author,
            publish_date=publish_date,
            outlet="Stuff",
            text=text,
        )
