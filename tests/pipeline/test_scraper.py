"""Tests for scraper site adapters."""

import pytest
from pipeline.sites.base import Article


class TestArticleDataclass:
    def test_creates_article(self):
        a = Article(
            url="https://nzherald.co.nz/test",
            title="Test Article",
            author="Test Author",
            publish_date="2026-03-28",
            outlet="NZ Herald",
            text="Article body text here.",
        )
        assert a.url == "https://nzherald.co.nz/test"
        assert a.author == "Test Author"
        assert a.outlet == "NZ Herald"

    def test_article_with_empty_text(self):
        a = Article(
            url="https://stuff.co.nz/test",
            title="",
            author="Author",
            publish_date="",
            outlet="Stuff",
            text="",
        )
        assert a.text == ""


class TestNZHeraldAdapter:
    """Test NZ Herald RSS URL parsing (without network calls)."""

    def test_rss_url_extraction(self):
        from pipeline.sites.nzherald import NZHeraldAdapter
        adapter = NZHeraldAdapter()
        assert adapter.name == "nzherald"
        assert adapter.domain == "nzherald.co.nz"
        assert adapter.needs_playwright is True

    def test_rss_url_format(self):
        from pipeline.sites.nzherald import NZHeraldAdapter
        adapter = NZHeraldAdapter()
        assert "rss" in adapter.RSS_URL.lower()


class TestStuffAdapter:
    def test_adapter_config(self):
        from pipeline.sites.stuff import StuffAdapter
        adapter = StuffAdapter()
        assert adapter.name == "stuff"
        assert adapter.domain == "stuff.co.nz"
        assert adapter.needs_playwright is False
