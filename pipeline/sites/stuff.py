"""Stuff.co.nz adapter — static HTML, Trafilatura handles directly."""

import re
from .base import SiteAdapter, Article

import aiohttp
import trafilatura


class StuffAdapter(SiteAdapter):
    name = "stuff"
    domain = "stuff.co.nz"
    needs_playwright = False
    RSS_URL = "https://www.stuff.co.nz/rss/national/politics"

    # Map journalist slugs to their Stuff author page paths
    AUTHOR_PATHS = {
        "henry-cooke": "/henry-cooke",
        "andrea-vance": "/andrea-vance",
        "stacey-kirk": "/stacey-kirk",
        "charlie-mitchell": "/charlie-mitchell",
        "lisa-owen": "/lisa-owen",
        "stuff-reporter": None,
    }

    async def get_article_urls(self, since_date: str | None = None, author_slug: str | None = None) -> list[str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # Try author search page first if we have a slug
        if author_slug:
            author_name = author_slug.replace("-", " ")
            search_url = f"https://www.stuff.co.nz/search?q={author_name.replace(' ', '+')}&contenttype=NEWS"
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(search_url, headers=headers) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            # Extract article links from search results
                            urls = re.findall(r'href="(https://www\.stuff\.co\.nz/[^"]+/\d{9,}[^"]*)"', html)
                            urls += re.findall(r'href="(/[a-z-]+/\d{9,}[^"]*)"', html)
                            full_urls = []
                            for u in urls:
                                if u.startswith("/"):
                                    u = "https://www.stuff.co.nz" + u
                                if u not in full_urls:
                                    full_urls.append(u)
                            if full_urls:
                                return full_urls[:30]
                except Exception:
                    pass

        # Fall back to RSS
        async with aiohttp.ClientSession() as session:
            async with session.get(self.RSS_URL, headers=headers) as resp:
                if resp.status != 200:
                    return []
                xml = await resp.text()
        urls = re.findall(r"<link>(https://www\.stuff\.co\.nz/[^<]+)</link>", xml)
        return urls

    async def extract_article(self, url: str) -> Article | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

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
            outlet="Stuff",
            text=extracted,
        )
