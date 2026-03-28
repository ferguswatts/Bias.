"""Tests for journalist score aggregation."""

import sqlite3
import pytest
from pipeline.db import init_db
from pipeline.aggregator import compute_distribution, update_journalist_stats


@pytest.fixture
def conn():
    """In-memory SQLite for testing."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


@pytest.fixture
def journalist_id(conn):
    conn.execute(
        "INSERT INTO journalists (slug, name, outlet) VALUES (?, ?, ?)",
        ("test-journalist-nzherald", "Test Journalist", "NZ Herald"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_articles(conn, journalist_id, buckets):
    for i, bucket in enumerate(buckets):
        conn.execute(
            """INSERT INTO articles (journalist_id, url, publish_date, bucket, scored_at)
               VALUES (?, ?, date('now', ?), ?, datetime('now'))""",
            (journalist_id, f"https://example.com/{i}", f"-{i} days", bucket),
        )
    conn.commit()


class TestComputeDistribution:
    def test_empty_no_articles(self, conn, journalist_id):
        dist = compute_distribution(conn, journalist_id)
        assert dist["article_count"] == 0
        assert dist["confidence"] == "low"

    def test_single_article(self, conn, journalist_id):
        _add_articles(conn, journalist_id, ["left"])
        dist = compute_distribution(conn, journalist_id)
        assert dist["left"] == 100
        assert dist["article_count"] == 1
        assert dist["confidence"] == "low"

    def test_mixed_distribution(self, conn, journalist_id):
        _add_articles(conn, journalist_id, ["left"] * 6 + ["centre-left"] * 2 + ["centre"] * 2)
        dist = compute_distribution(conn, journalist_id)
        assert dist["left"] == 60
        assert dist["centre_left"] == 20
        assert dist["centre"] == 20
        assert dist["article_count"] == 10
        assert dist["confidence"] == "low"

    def test_medium_confidence(self, conn, journalist_id):
        _add_articles(conn, journalist_id, ["centre"] * 25)
        dist = compute_distribution(conn, journalist_id)
        assert dist["confidence"] == "medium"

    def test_high_confidence(self, conn, journalist_id):
        _add_articles(conn, journalist_id, ["centre"] * 55)
        dist = compute_distribution(conn, journalist_id)
        assert dist["confidence"] == "high"

    def test_old_articles_excluded(self, conn, journalist_id):
        # Add article 30 months ago — should be excluded
        conn.execute(
            """INSERT INTO articles (journalist_id, url, publish_date, bucket, scored_at)
               VALUES (?, ?, date('now', '-30 months'), 'left', datetime('now'))""",
            (journalist_id, "https://example.com/old"),
        )
        conn.commit()
        dist = compute_distribution(conn, journalist_id)
        assert dist["article_count"] == 0


class TestUpdateJournalistStats:
    def test_updates_article_count(self, conn, journalist_id):
        _add_articles(conn, journalist_id, ["left"] * 5)
        dist = update_journalist_stats(conn, journalist_id)
        assert dist["article_count"] == 5

        row = conn.execute("SELECT article_count, confidence_tier FROM journalists WHERE id = ?", (journalist_id,)).fetchone()
        assert row["article_count"] == 5
        assert row["confidence_tier"] == "low"
