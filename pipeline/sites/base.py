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
    async def get_article_urls(
        self,
        since_date: str | None = None,
        author_slug: str | None = None,
        backfill: bool = False,
    ) -> list[str]:
        """Get URLs of recent articles. Uses RSS, sitemap, or author page.

        Args:
            since_date: ISO date string — skip articles older than this (optional).
            author_slug: Journalist slug (e.g., "john-campbell") for author-scoped fetches.
            backfill: If True, fetch historical articles beyond the recent pool.
        """
        ...

    @abstractmethod
    async def extract_article(self, url: str) -> Article | None:
        """Extract article text, title, and author from a URL."""
        ...
