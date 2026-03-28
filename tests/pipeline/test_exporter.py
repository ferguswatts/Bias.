"""Tests for the JSON exporter."""

import json
import sqlite3
import pytest
from pathlib import Path
from pipeline.db import init_db
from pipeline.exporter import export_to_json


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


@pytest.fixture
def tmp_json(tmp_path):
    return tmp_path / "data.json"


class TestExportToJson:
    def test_empty_database(self, conn, tmp_json):
        count = export_to_json(conn, tmp_json)
        assert count == 0
        with open(tmp_json) as f:
            data = json.load(f)
        assert data["journalists"] == {}
        assert "version" in data

    def test_journalist_with_articles(self, conn, tmp_json):
        conn.execute(
            "INSERT INTO journalists (slug, name, aliases, outlet, beat) VALUES (?, ?, ?, ?, ?)",
            ("test-nzherald", "Test Person", "[]", "NZ Herald", "Politics"),
        )
        jid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        for i in range(5):
            conn.execute(
                """INSERT INTO articles (journalist_id, url, publish_date, bucket, scored_at)
                   VALUES (?, ?, date('now', ?), ?, datetime('now'))""",
                (jid, f"https://nzherald.co.nz/{i}", f"-{i} days", "left"),
            )
        conn.commit()

        count = export_to_json(conn, tmp_json)
        assert count == 1

        with open(tmp_json) as f:
            data = json.load(f)

        j = data["journalists"]["test-nzherald"]
        assert j["name"] == "Test Person"
        assert j["outlet"] == "NZ Herald"
        assert j["distribution"]["left"] == 100
        assert j["article_count"] == 5

    def test_journalist_with_connections(self, conn, tmp_json):
        conn.execute(
            "INSERT INTO journalists (slug, name, aliases, outlet) VALUES (?, ?, ?, ?)",
            ("test-stuff", "Test Person", "[]", "Stuff"),
        )
        jid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Need at least one scored article to be exported
        conn.execute(
            """INSERT INTO articles (journalist_id, url, publish_date, bucket, scored_at)
               VALUES (?, 'https://stuff.co.nz/1', date('now'), 'centre', datetime('now'))""",
            (jid,),
        )

        conn.execute(
            "INSERT INTO connections (journalist_id, type, target_name, target_role, source_url) VALUES (?, ?, ?, ?, ?)",
            (jid, "family", "Jane Doe", "MP", "https://example.com"),
        )
        conn.commit()

        export_to_json(conn, tmp_json)
        with open(tmp_json) as f:
            data = json.load(f)

        j = data["journalists"]["test-stuff"]
        assert len(j["connections"]) == 1
        assert j["connections"][0]["target"] == "Jane Doe"
        assert j["connections"][0]["source"] == "https://example.com"

    def test_skips_journalists_with_no_articles(self, conn, tmp_json):
        conn.execute(
            "INSERT INTO journalists (slug, name, aliases, outlet) VALUES (?, ?, ?, ?)",
            ("empty-stuff", "Empty Person", "[]", "Stuff"),
        )
        conn.commit()

        count = export_to_json(conn, tmp_json)
        assert count == 0
