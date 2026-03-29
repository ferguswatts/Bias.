"""Main orchestrator for the Byline Card pipeline.

Usage:
    python -m pipeline.run                         # Full run (cap: 20 articles/journalist)
    python -m pipeline.run --journalists 5         # Limit to first 5 journalists
    python -m pipeline.run --backfill              # Historical backfill (cap: 200 articles/journalist)
    python -m pipeline.run --cap 50               # Custom cap
    python -m pipeline.run --dry-run              # Scrape + score but don't export
    python -m pipeline.run --export-only          # Just regenerate JSON from existing data
"""

import argparse
import asyncio
import hashlib
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from .db import (
    get_connection, init_db, migrate_db, load_journalists_from_csv,
    load_connections_from_csv, load_facts_from_csv,
    get_articles_needing_text, get_articles_needing_rescore,
)
from .scorer import score_article_claude, score_to_bucket, PROMPT_VERSION
from .aggregator import update_journalist_stats
from .exporter import export_to_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
EXTENSION_DATA = Path(__file__).parent.parent / "extension" / "public" / "data.json"

# Rate limiting: max concurrent LLM calls
SCORING_SEMAPHORE = asyncio.Semaphore(5)
SCRAPING_SEMAPHORE = asyncio.Semaphore(3)


async def scrape_and_score_journalist(conn, journalist: dict, adapters: dict, cap: int = 20) -> int:
    """Scrape new articles for a journalist and score them. Returns count of new articles scored."""
    outlet_key = journalist["outlet"].lower().replace(" ", "")

    # Map outlet names to adapter keys
    outlet_map = {
        "nzherald": "nzherald",
        "stuff": "stuff",
        "rnz": "rnz",
        "1news": "1news",
        "newsroom": "newsroom",
        "thespinoff": "thespinoff",
        "newstalkzb": "newstalkzb",
    }

    adapter_key = outlet_map.get(outlet_key)
    if not adapter_key or adapter_key not in adapters:
        log.warning(f"No adapter for outlet: {journalist['outlet']}")
        return 0

    adapter = adapters[adapter_key]
    scored = 0

    # Extract author slug from journalist slug (e.g., "thomas-coughlan-nzherald" → "thomas-coughlan")
    author_slug = "-".join(journalist["slug"].split("-")[:-1])

    async with SCRAPING_SEMAPHORE:
        try:
            # Pass backfill=True to adapters that support historical URL discovery
            get_urls_kwargs: dict = {"author_slug": author_slug}
            if cap > 20:  # backfill mode
                get_urls_kwargs["backfill"] = True
            urls = await adapter.get_article_urls(**get_urls_kwargs)
        except Exception as e:
            log.error(f"Failed to get URLs from {adapter.name}: {e}")
            return 0

    log.info(f"  Found {len(urls)} article URLs for {journalist['name']}")

    for url in urls[:cap]:
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
               (journalist_id, url, title, publish_date, outlet, text_body, text_hash,
                score_claude, median_score, bucket, score_prompt_version, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                journalist["id"], url, article.title, article.publish_date,
                article.outlet, article.text, text_hash, result.score, result.score,
                result.bucket, PROMPT_VERSION,
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
    parser.add_argument("--cap", type=int, default=20, help="Max articles to score per journalist per run (default: 20)")
    parser.add_argument("--backfill", action="store_true", help="Backfill mode: raise cap to 200 to collect historical articles")
    parser.add_argument("--refetch", action="store_true", help="Re-fetch text for articles missing text_body")
    parser.add_argument("--rescore", action="store_true", help="Re-score articles that have text but were scored with an older prompt")
    args = parser.parse_args()

    article_cap = 200 if args.backfill else args.cap

    conn = get_connection()
    init_db(conn)
    migrate_db(conn)

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

    facts_csv = DATA_DIR / "facts.csv"
    if facts_csv.exists():
        added = load_facts_from_csv(conn, facts_csv)
        if added:
            log.info(f"Loaded {added} new facts from CSV")

    if args.export_only:
        count = export_to_json(conn, EXTENSION_DATA)
        log.info(f"Exported {count} journalists to {EXTENSION_DATA}")
        conn.close()
        return

    # Initialize adapters (needed for refetch too)
    from .sites.nzherald import NZHeraldAdapter
    from .sites.stuff import StuffAdapter
    from .sites.rnz import RNZAdapter
    from .sites.onenews import OneNewsAdapter
    from .sites.newsroom import NewsroomAdapter
    from .sites.spinoff import SpinoffAdapter
    from .sites.newstalkzb import NewstalkZBAdapter

    adapters = {
        "nzherald": NZHeraldAdapter(),
        "stuff": StuffAdapter(),
        "rnz": RNZAdapter(),
        "1news": OneNewsAdapter(),
        "newsroom": NewsroomAdapter(),
        "thespinoff": SpinoffAdapter(),
        "newstalkzb": NewstalkZBAdapter(),
    }

    # --- REFETCH MODE: re-fetch text for articles that don't have text_body stored ---
    if args.refetch:
        articles = get_articles_needing_text(conn)
        log.info(f"REFETCH: {len(articles)} articles need text re-fetched")

        # Map outlet to adapter
        outlet_adapter_map = {
            "NZ Herald": adapters.get("nzherald"),
            "Stuff": adapters.get("stuff"),
            "The Post": adapters.get("stuff"),  # Same CMS
            "RNZ": adapters.get("rnz"),
            "1News": adapters.get("1news"),
        }

        fetched = 0
        for art in articles:
            adapter = outlet_adapter_map.get(art["outlet"])
            if not adapter:
                continue
            async with SCRAPING_SEMAPHORE:
                try:
                    extracted = await adapter.extract_article(art["url"])
                except Exception as e:
                    log.debug(f"Refetch failed for {art['url']}: {e}")
                    continue

            if extracted and extracted.text and len(extracted.text) > 100:
                text_hash = hashlib.sha256(extracted.text.encode()).hexdigest()
                conn.execute(
                    "UPDATE articles SET text_body = ?, text_hash = ? WHERE id = ?",
                    (extracted.text, text_hash, art["id"]),
                )
                conn.commit()
                fetched += 1
                if fetched % 20 == 0:
                    log.info(f"  Refetched {fetched}/{len(articles)} articles...")

        log.info(f"REFETCH complete: {fetched}/{len(articles)} articles got text stored")

        if not args.rescore:
            conn.close()
            return

    # --- RESCORE MODE: re-score articles that have text but old/missing prompt version ---
    if args.rescore:
        articles = get_articles_needing_rescore(conn, PROMPT_VERSION)
        log.info(f"RESCORE: {len(articles)} articles need re-scoring (prompt version: {PROMPT_VERSION})")

        rescored = 0
        for art in articles:
            async with SCORING_SEMAPHORE:
                result = await score_article_claude(art["text_body"])

            if not result:
                log.warning(f"Rescore failed: {art['url']}")
                continue

            conn.execute(
                """UPDATE articles SET
                   score_claude = ?, median_score = ?, bucket = ?,
                   score_prompt_version = ?, scored_at = datetime('now')
                   WHERE id = ?""",
                (result.score, result.score, result.bucket, PROMPT_VERSION, art["id"]),
            )
            conn.commit()
            rescored += 1
            if rescored % 10 == 0:
                log.info(f"  Re-scored {rescored}/{len(articles)}...")

        log.info(f"RESCORE complete: {rescored}/{len(articles)} articles re-scored")

        # Update all journalist stats
        journalist_ids = set(art["journalist_id"] for art in articles)
        for jid in journalist_ids:
            update_journalist_stats(conn, jid)

        if not args.dry_run:
            count = export_to_json(conn, EXTENSION_DATA)
            log.info(f"Exported {count} journalists to {EXTENSION_DATA}")

        conn.close()
        return

    # Get journalists to process
    journalists = conn.execute("SELECT * FROM journalists ORDER BY name").fetchall()
    journalists = [dict(j) for j in journalists]

    if args.journalists > 0:
        journalists = journalists[:args.journalists]

    log.info(f"Processing {len(journalists)} journalists with {len(adapters)} site adapters")

    if args.backfill:
        log.info(f"BACKFILL MODE — cap raised to {article_cap} articles per journalist")

    total_scored = 0
    for journalist in journalists:
        scored = await scrape_and_score_journalist(conn, journalist, adapters, cap=article_cap)
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
