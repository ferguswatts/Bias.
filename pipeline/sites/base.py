"""Base adapter interface for NZ news site scrapers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
    url: str
    title: str
    author: str
    publish_date: str  # ISO format
    outlet: str
    text: str


class SiteAdapter(ABC):
    """Abstract base class for per-site article scrapers."""

    name: str  # e.g., "nzherald"
    domain: str  # e.g., "nzherald.co.nz"
    needs_playwright: bool = False

    @abstractmethod
    async def get_article_urls(self, since_date: str | None = None) -> list[str]:
        """Get URLs of recent articles. Uses RSS or sitemap."""
        ...

    @abstractmethod
    async def extract_article(self, url: str) -> Article | None:
        """Extract article text, title, and author from a URL."""
        ...
