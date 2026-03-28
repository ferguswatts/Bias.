"""SQLite database schema and helpers for the Byline Card pipeline."""

import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "bylinecard.db"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS journalists (
            id INTEGER PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            aliases TEXT DEFAULT '[]',
            outlet TEXT NOT NULL,
            beat TEXT,
            photo_url TEXT,
            article_count INTEGER DEFAULT 0,
            confidence_tier TEXT DEFAULT 'low',
            last_scored_at TEXT
        );

        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY,
            journalist_id INTEGER REFERENCES journalists(id),
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            publish_date TEXT,
            outlet TEXT,
            text_hash TEXT,
            score_claude REAL,
            score_gpt REAL,
            score_grok REAL,
            median_score REAL,
            bucket TEXT,
            scored_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connections (
            id INTEGER PRIMARY KEY,
            journalist_id INTEGER REFERENCES journalists(id),
            type TEXT NOT NULL,
            target_name TEXT NOT NULL,
            target_role TEXT,
            source_url TEXT NOT NULL,
            verified_at TEXT
        );

        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY,
            journalist_id INTEGER REFERENCES journalists(id),
            fact_text TEXT NOT NULL,
            source_url TEXT NOT NULL,
            added_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_articles_journalist ON articles(journalist_id);
        CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);
        CREATE INDEX IF NOT EXISTS idx_connections_journalist ON connections(journalist_id);
    """)


def load_journalists_from_csv(conn: sqlite3.Connection, csv_path: Path) -> int:
    """Load journalist seed data from CSV. Returns count of new journalists added."""
    import csv
    count = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            slug = row["slug"]
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO journalists (slug, name, aliases, outlet, beat) VALUES (?, ?, ?, ?, ?)",
                    (slug, row["name"], row.get("aliases", "[]"), row["outlet"], row.get("beat", "")),
                )
                if conn.total_changes:
                    count += 1
            except sqlite3.IntegrityError:
                pass
    conn.commit()
    return count


def load_connections_from_csv(conn: sqlite3.Connection, csv_path: Path) -> int:
    """Load connections from CSV. Returns count of new connections added."""
    import csv
    count = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            journalist = conn.execute(
                "SELECT id FROM journalists WHERE slug = ?", (row["journalist_slug"],)
            ).fetchone()
            if not journalist:
                continue
            conn.execute(
                "INSERT INTO connections (journalist_id, type, target_name, target_role, source_url, verified_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (journalist["id"], row["type"], row["target_name"], row.get("target_role", ""), row["source_url"]),
            )
            count += 1
    conn.commit()
    return count


def get_journalist_by_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM journalists WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def get_articles_for_journalist(conn: sqlite3.Connection, journalist_id: int, max_age_months: int = 24) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM articles
           WHERE journalist_id = ?
           AND publish_date >= date('now', ?)
           ORDER BY publish_date DESC""",
        (journalist_id, f"-{max_age_months} months"),
    ).fetchall()
    return [dict(r) for r in rows]


def get_connections_for_journalist(conn: sqlite3.Connection, journalist_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM connections WHERE journalist_id = ?", (journalist_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_facts_for_journalist(conn: sqlite3.Connection, journalist_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM facts WHERE journalist_id = ?", (journalist_id,)
    ).fetchall()
    return [dict(r) for r in rows]
