"""NZ Herald adapter — requires Playwright (React SPA).
Scrapes author archive pages instead of RSS (NZ Herald killed public RSS feeds).
Paywalled articles are retried via archive.is.
"""

import re
import logging
from .base import SiteAdapter, Article

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

import aiohttp
import trafilatura

log = logging.getLogger(__name__)

ARCHIVE_IS_URL = "https://archive.is/newest/{url}"


class NZHeraldAdapter(SiteAdapter):
    name = "nzherald"
    domain = "nzherald.co.nz"
    needs_playwright = True

    # Map journalist slugs to their NZ Herald author page URLs
    AUTHOR_URLS = {
        "thomas-coughlan": "https://www.nzherald.co.nz/author/thomas-coughlan/",
        "claire-trevett": "https://www.nzherald.co.nz/author/claire-trevett/",
        "audrey-young": "https://www.nzherald.co.nz/author/audrey-young/",
        "derek-cheng": "https://www.nzherald.co.nz/author/derek-cheng/",
        "jason-walls": "https://www.nzherald.co.nz/author/jason-walls/",
        "michael-neilson": "https://www.nzherald.co.nz/author/michael-neilson/",
        "david-farrar": "https://www.nzherald.co.nz/author/david-farrar/",
    }

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None, backfill: bool = False) -> list[str]:
        """Scrape article URLs from an author's archive page using Playwright."""
        if not async_playwright or not author_slug:
            return []

        author_url = self.AUTHOR_URLS.get(author_slug) or f"https://www.nzherald.co.nz/author/{author_slug}/"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(author_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

                links = await page.eval_on_selector_all(
                    'a[href*="/nz/"]',
                    "els => els.map(e => e.href).filter(h => h.includes('/nz/') && h.split('/').length > 5)"
                )

                more_links = await page.eval_on_selector_all(
                    'a[href*="nzherald.co.nz"]',
                    """els => els.map(e => e.href).filter(h =>
                        h.includes('nzherald.co.nz/nz/') &&
                        !h.includes('/author/') &&
                        !h.includes('/section/') &&
                        h.split('/').length > 5
                    )"""
                )

                all_links = list(set(links + more_links))
            except Exception as e:
                log.error(f"[NZHerald] Failed to scrape author page {author_url}: {e}")
                all_links = []
            finally:
                await browser.close()

        urls = []
        seen = set()
        for url in all_links:
            clean = url.split("?")[0].split("#")[0]
            if clean not in seen and "nzherald.co.nz" in clean:
                seen.add(clean)
                urls.append(clean)

        return urls

    async def _fetch_from_archive(self, url: str) -> str | None:
        """Try to fetch a paywalled article from archive.is."""
        archive_url = ARCHIVE_IS_URL.format(url=url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(archive_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
                        if extracted and len(extracted) >= 200:
                            log.info(f"[NZHerald] archive.is hit for {url}")
                            return extracted
        except Exception as e:
            log.debug(f"[NZHerald] archive.is failed for {url}: {e}")
        return None

    async def extract_article(self, url: str) -> Article | None:
        """Use Playwright to render the JS SPA, then Trafilatura to extract text.
        Falls back to archive.is if the article is paywalled."""
        if not async_playwright:
            return None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                html = await page.content()
            except Exception as e:
                log.error(f"[NZHerald] Failed to load {url}: {e}")
                await browser.close()
                return None
            await browser.close()

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)

        # If paywalled (short or no text), try archive.is
        if not extracted or len(extracted) < 200:
            log.info(f"[NZHerald] Possible paywall, trying archive.is: {url}")
            extracted = await self._fetch_from_archive(url)

        if not extracted or len(extracted) < 200:
            return None

        metadata = trafilatura.extract_metadata(html)
        title = metadata.title if metadata else ""
        author = metadata.author if metadata else ""
        date = metadata.date if metadata else ""

        if not author:
            author_match = re.search(r'"author":\s*\{[^}]*"name":\s*"([^"]+)"', html)
            if author_match:
                author = author_match.group(1)

        return Article(
            url=url,
            title=title or "",
            author=author or "Unknown",
            publish_date=date or "",
            outlet="NZ Herald",
            text=extracted,
        )
