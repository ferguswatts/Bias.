"""NZ Herald adapter — requires Playwright (React SPA)."""

import re
from .base import SiteAdapter, Article

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

import trafilatura


class NZHeraldAdapter(SiteAdapter):
    name = "nzherald"
    domain = "nzherald.co.nz"
    needs_playwright = True
    RSS_URL = "https://www.nzherald.co.nz/arc/outboundfeeds/rss/section/nz/politics/"

    async def get_article_urls(self, since_date: str | None = None) -> list[str]:
        """Parse RSS feed for recent article URLs."""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(self.RSS_URL) as resp:
                if resp.status != 200:
                    return []
                xml = await resp.text()

        # Simple RSS URL extraction
        urls = re.findall(r"<link>(https://www\.nzherald\.co\.nz/[^<]+)</link>", xml)
        return [u for u in urls if "/nz/" in u or "/politics/" in u]

    async def extract_article(self, url: str) -> Article | None:
        """Use Playwright to render the JS SPA, then Trafilatura to extract text."""
        if not async_playwright:
            return None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=15000)
                html = await page.content()
            except Exception:
                await browser.close()
                return None
            await browser.close()

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not extracted:
            return None

        # Extract metadata from JSON-LD
        metadata = trafilatura.extract_metadata(html)
        title = metadata.title if metadata else ""
        author = metadata.author if metadata else ""
        date = metadata.date if metadata else ""

        if not author:
            return None

        return Article(
            url=url,
            title=title or "",
            author=author,
            publish_date=date or "",
            outlet="NZ Herald",
            text=extracted,
        )
