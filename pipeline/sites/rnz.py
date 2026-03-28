"""RNZ adapter — static HTML, Trafilatura handles directly."""

import re
from .base import SiteAdapter, Article

import aiohttp
import trafilatura


class RNZAdapter(SiteAdapter):
    name = "rnz"
    domain = "rnz.co.nz"
    needs_playwright = False
    RSS_URL = "https://www.rnz.co.nz/rss/political.xml"

    async def get_article_urls(self, since_date: str | None = None) -> list[str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(self.RSS_URL) as resp:
                if resp.status != 200:
                    return []
                xml = await resp.text()
        urls = re.findall(r"<link>(https://www\.rnz\.co\.nz/news/[^<]+)</link>", xml)
        return urls

    async def extract_article(self, url: str) -> Article | None:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
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
            outlet="RNZ",
            text=extracted,
        )
