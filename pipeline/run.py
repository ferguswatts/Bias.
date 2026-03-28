"""Main orchestrator for the Byline Card pipeline.

Usage:
    python -m pipeline.run                    # Full run
    python -m pipeline.run --journalists 5    # Limit to first 5 journalists
    python -m pipeline.run --dry-run          # Scrape + score but don't export
    python -m pipeline.run --export-only      # Just regenerate JSON from existing data
"""

import argparse
import asyncio
import hashlib
import logging
from pathlib import Path

from .db import get_connection, init_db, load_journalists_from_csv, load_connections_from_csv
from .scorer import score_article_claude, score_to_bucket
from .aggregator import update_journalist_stats
from .exporter import export_to_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
EXTENSION_DATA = Path(__file__).parent.parent / "extension" / "public" / "data.json"

# Rate limiting: max concurrent LLM calls
SCORING_SEMAPHORE = asyncio.Semaphore(5)
SCRAPING_SEMAPHORE = asyncio.Semaphore(3)


async def scrape_and_score_journalist(conn, journalist: dict, adapters: dict) -> int:
    """Scrape new articles for a journalist and score them. Returns count of new articles scored."""
    outlet_key = journalist["outlet"].lower().replace(" ", "")

    # Map outlet names to adapter keys
    outlet_map = {
        "nzherald": "nzherald",
        "stuff": "stuff",
        "rnz": "rnz",
    }

    adapter_key = outlet_map.get(outlet_key)
    if not adapter_key or adapter_key not in adapters:
        log.warning(f"No adapter for outlet: {journalist['outlet']}")
        return 0

    adapter = adapters[adapter_key]
    scored = 0

    async with SCRAPING_SEMAPHORE:
        try:
            urls = await adapter.get_article_urls()
        except Exception as e:
            log.error(f"Failed to get URLs from {adapter.name}: {e}")
            return 0

    for url in urls[:20]:  # Cap per journalist per run
        # Skip if already in database
        existing = conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()
        if existing:
            continue

        async with SCRAPING_SEMAPHORE:
            try:
                article = await adapter.extract_article(url)
            except Exception as e:
                log.error(f"Failed to extract {url}: {e}")
                continue

        if not article or not article.text:
            continue

        # Check if this journalist wrote it
        author_lower = article.author.lower()
        journalist_name_lower = journalist["name"].lower()
        if journalist_name_lower not in author_lower:
            continue

        # Score the article
        async with SCORING_SEMAPHORE:
            result = await score_article_claude(article.text)

        if not result:
            log.warning(f"Failed to score: {url}")
            continue

        text_hash = hashlib.sha256(article.text.encode()).hexdigest()

        conn.execute(
            """INSERT OR IGNORE INTO articles
               (journalist_id, url, title, publish_date, outlet, text_hash,
                score_claude, median_score, bucket, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                journalist["id"], url, article.title, article.publish_date,
                article.outlet, text_hash, result.score, result.score,
                result.bucket,
            ),
        )
        conn.commit()
        scored += 1
        log.info(f"Scored: {article.title[:60]} → {result.bucket} ({result.score:.2f})")

    return scored


async def main():
    parser = argparse.ArgumentParser(description="Byline Card scoring pipeline")
    parser.add_argument("--journalists", type=int, default=0, help="Limit to first N journalists")
    parser.add_argument("--dry-run", action="store_true", help="Scrape + score but don't export")
    parser.add_argument("--export-only", action="store_true", help="Just regenerate JSON")
    args = parser.parse_args()

    conn = get_connection()
    init_db(conn)

    # Load seed data
    journalists_csv = DATA_DIR / "journalists.csv"
    connections_csv = DATA_DIR / "connections.csv"

    if journalists_csv.exists():
        added = load_journalists_from_csv(conn, journalists_csv)
        if added:
            log.info(f"Loaded {added} new journalists from CSV")

    if connections_csv.exists():
        added = load_connections_from_csv(conn, connections_csv)
        if added:
            log.info(f"Loaded {added} new connections from CSV")

    if args.export_only:
        count = export_to_json(conn, EXTENSION_DATA)
        log.info(f"Exported {count} journalists to {EXTENSION_DATA}")
        conn.close()
        return

    # Initialize adapters
    from .sites.nzherald import NZHeraldAdapter
    from .sites.stuff import StuffAdapter

    adapters = {
        "nzherald": NZHeraldAdapter(),
        "stuff": StuffAdapter(),
    }

    # Get journalists to process
    journalists = conn.execute("SELECT * FROM journalists ORDER BY name").fetchall()
    journalists = [dict(j) for j in journalists]

    if args.journalists > 0:
        journalists = journalists[:args.journalists]

    log.info(f"Processing {len(journalists)} journalists with {len(adapters)} site adapters")

    total_scored = 0
    for journalist in journalists:
        scored = await scrape_and_score_journalist(conn, journalist, adapters)
        if scored:
            update_journalist_stats(conn, journalist["id"])
            total_scored += scored
            log.info(f"{journalist['name']}: {scored} new articles scored")

    log.info(f"Total: {total_scored} articles scored across {len(journalists)} journalists")

    if not args.dry_run:
        count = export_to_json(conn, EXTENSION_DATA)
        log.info(f"Exported {count} journalists to {EXTENSION_DATA}")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
