"""Score articles from discovered_urls that are already tagged to journalists.

Pulls URLs from discovered_urls (already tagged with journalist_id),
fetches article text, scores with Claude, and inserts into articles table.

For paywalled articles (NZ Herald especially), falls back to archive.is
to retrieve cached full-text versions.

Usage:
    python -m pipeline.score_discovered                    # All journalists
    python -m pipeline.score_discovered --cap 50           # Cap per journalist
    python -m pipeline.score_discovered --journalist "Mike Hosking"  # One journalist
    python -m pipeline.score_discovered --dry-run          # Count only, no API calls
    python -m pipeline.score_discovered --retry-failed     # Retry previously failed URLs
"""

import argparse
import asyncio
import hashlib
import logging
import os
import re
import signal
import sys
from pathlib import Path
from urllib.parse import quote as urlquote

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from .db import get_connection, migrate_db
from .scorer import score_article_claude, PROMPT_VERSION
from .aggregator import update_journalist_stats
from .exporter import export_to_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("score-discovered")

EXTENSION_DATA = Path(__file__).parent.parent / "extension" / "public" / "data.json"

# Concurrency limits
FETCH_SEM = asyncio.Semaphore(10)
ARCHIVE_SEM = asyncio.Semaphore(3)  # Be gentle with archive.is
SCORE_SEM = asyncio.Semaphore(5)

# Graceful shutdown
_shutdown = False

def _handle_sigint(sig, frame):
    global _shutdown
    if _shutdown:
        log.warning("Force quit")
        sys.exit(1)
    _shutdown = True
    log.info("Ctrl+C received — finishing current batch then stopping...")

signal.signal(signal.SIGINT, _handle_sigint)


def _ensure_fetch_failures_table(conn):
    """Create fetch_failures table if it doesn't exist."""
    for _attempt in range(5):
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fetch_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    journalist_id INTEGER,
                    outlet TEXT,
                    status_code INTEGER,
                    reason TEXT,
                    attempted_at TEXT DEFAULT (datetime('now')),
                    retry_count INTEGER DEFAULT 0,
                    resolved INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            return
        except Exception:
            import time; time.sleep(1 + _attempt)


def _record_failure(conn, url, journalist_id, outlet, status_code, reason):
    """Record a fetch failure for later retry."""
    for _attempt in range(5):
        try:
            conn.execute("""
                INSERT INTO fetch_failures (url, journalist_id, outlet, status_code, reason)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    retry_count = retry_count + 1,
                    status_code = excluded.status_code,
                    reason = excluded.reason,
                    attempted_at = datetime('now')
            """, (url, journalist_id, outlet, status_code, reason))
            return
        except Exception:
            import time; time.sleep(1 + _attempt)


async def fetch_from_archive(session, url: str) -> str | None:
    """Try to fetch full article text from archive.is/archive.today."""
    import trafilatura

    async with ARCHIVE_SEM:
        try:
            import aiohttp
            # archive.is search for the URL
            archive_url = f"https://archive.is/newest/{url}"
            timeout = aiohttp.ClientTimeout(total=45)
            async with session.get(archive_url, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None
                # Check we actually got an archive page (not a "no results" page)
                final_url = str(resp.url)
                if "archive.is/newest/" in final_url:
                    # No archived version found
                    return None
                html = await resp.text()
        except Exception as e:
            log.debug(f"Archive fetch failed {url}: {e}")
            return None

    try:
        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            output_format="txt",
        )
        if extracted and len(extracted) > 200:
            return extracted
        return None
    except Exception:
        return None


async def fetch_stuff_api(session, url: str) -> tuple[str, str, str, int] | None:
    """Fetch Stuff article via their internal JSON API. Returns (title, date, text, 200) or None."""
    # Extract story ID from URL: stuff.co.nz/section/360961102/slug
    m = re.search(r'/(\d{6,})', url)
    if not m:
        return None

    story_id = m.group(1)
    api_url = f"https://www.stuff.co.nz/api/v1.0/stuff/story/{story_id}"

    async with FETCH_SEM:
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=15)
            async with session.get(api_url, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://www.stuff.co.nz/",
                "Origin": "https://www.stuff.co.nz",
                "Accept": "application/json",
            }) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception:
            return None

    # Extract content from API response
    content = data.get("content", {})
    if isinstance(content, dict):
        title = content.get("title", "")
        intro = content.get("intro", "")
        body_blocks = content.get("contentBody", {}).get("body", "") or content.get("body", "")
    else:
        title = ""
        intro = ""
        body_blocks = ""

    # Clean HTML from body
    if body_blocks:
        text = re.sub(r'<[^>]+>', '', str(body_blocks)).strip()
        if intro:
            text = re.sub(r'<[^>]+>', '', intro).strip() + "\n\n" + text
    else:
        return None

    if len(text) < 200:
        return None

    date = data.get("publishedDate", "")
    if date:
        date = date[:10]  # "2026-04-07T..." -> "2026-04-07"

    return (title, date, text, 200)


async def fetch_article_text(session, url: str, outlet: str) -> tuple[str, str, str, int] | None:
    """Fetch article text. Returns (title, date, text, status_code) or None.

    Uses Stuff JSON API for stuff.co.nz/thepost.co.nz, trafilatura for others.
    Falls back to archive.is for paywalled content.
    """
    # Stuff/The Post: use their internal JSON API (SPA site, trafilatura can't extract)
    if "stuff.co.nz" in url or "thepost.co.nz" in url:
        result = await fetch_stuff_api(session, url)
        if result:
            return result
        # Fall through to trafilatura as backup

    try:
        import trafilatura
    except ImportError:
        log.error("trafilatura not installed. Run: pip install trafilatura")
        return None

    status_code = 0
    html = None

    async with FETCH_SEM:
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=30)
            async with session.get(url, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }) as resp:
                status_code = resp.status
                if resp.status == 200:
                    html = await resp.text()
        except asyncio.TimeoutError:
            return None  # Will be recorded as timeout
        except Exception as e:
            log.debug(f"Fetch failed {url}: {e}")
            return None

    # Try extracting from direct fetch
    text = None
    title = ""
    date = ""

    if html:
        try:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
                output_format="txt",
            )

            # Check for paywall indicators even on 200 responses
            if text and len(text) < 300:
                # Might be a paywall stub — check for common indicators
                lower = text.lower()
                if any(w in lower for w in ["subscribe", "premium content", "sign in to read", "this content is for"]):
                    text = None  # Treat as paywalled

            if text and len(text) >= 200:
                # Extract metadata using trafilatura's metadata extractor
                meta = trafilatura.extract_metadata(html)
                if meta:
                    if meta.title and meta.title != html[:50]:
                        title = meta.title
                    if meta.date:
                        # Validate it's not today's date (generic page date)
                        from datetime import date as dt_date
                        if meta.date != dt_date.today().isoformat():
                            date = meta.date

                # Fallback: try JSON-LD for publish date
                if not date:
                    ld_m = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', html)
                    if ld_m:
                        date = ld_m.group(1)

                # Fallback: try <meta> tags
                if not date:
                    meta_m = re.search(r'<meta[^>]*(?:property|name)="(?:article:published_time|datePublished|publish[_-]?date)"[^>]*content="(\d{4}-\d{2}-\d{2})', html)
                    if meta_m:
                        date = meta_m.group(1)

                # Fallback: try <time> element
                if not date:
                    time_m = re.search(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})', html)
                    if time_m:
                        date = time_m.group(1)

                return (title, date, text, status_code)
        except Exception as e:
            log.debug(f"Extract failed {url}: {e}")

    # Direct fetch failed or was paywalled — try archive.is
    archive_text = await fetch_from_archive(session, url)
    if archive_text:
        # Try to get title from the original HTML if we have it
        if html:
            try:
                meta = trafilatura.extract(html, output_format="xmltei", include_comments=False)
                if meta:
                    title_m = re.search(r'<title[^>]*>([^<]+)</title>', meta)
                    if title_m:
                        title = title_m.group(1).strip()
                    date_m = re.search(r'when="(\d{4}-\d{2}-\d{2})"', meta)
                    if date_m:
                        date = date_m.group(1)
            except Exception:
                pass
        return (title, date, archive_text, status_code)

    # Return status code so caller can record the failure reason
    return None


async def process_batch(conn, session, rows, lookup_name, total_for_journalist, stats):
    """Process a batch of URLs: fetch text, score, insert into articles."""
    for row in rows:
        if _shutdown:
            return

        url = row["url"]

        # Skip if already in articles table
        existing = conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()
        if existing:
            stats["skipped"] += 1
            continue

        # Skip if already recorded as a resolved failure
        existing_fail = conn.execute(
            "SELECT id FROM fetch_failures WHERE url = ? AND resolved = 0 AND retry_count >= 2",
            (url,)
        ).fetchone()
        if existing_fail:
            stats["skipped"] += 1
            continue

        # Fetch article text
        result = await fetch_article_text(session, url, row["outlet"])
        if not result:
            stats["fetch_failed"] += 1
            _record_failure(conn, url, row["journalist_id"], row["outlet"], 0, "no_text")
            continue

        title, date, text, status_code = result
        stats["fetched"] += 1

        if "archive.is" not in url and status_code != 200:
            stats["archive_rescued"] = stats.get("archive_rescued", 0) + 1

        # Score with Claude
        async with SCORE_SEM:
            score_result = await score_article_claude(text)

        if not score_result:
            stats["score_failed"] += 1
            _record_failure(conn, url, row["journalist_id"], row["outlet"], status_code, "score_failed")
            continue

        text_hash = hashlib.sha256(text.encode()).hexdigest()

        for _attempt in range(20):
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO articles
                       (journalist_id, url, title, publish_date, outlet, text_body, text_hash,
                        score_claude, median_score, bucket, score_prompt_version, scored_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    (
                        row["journalist_id"], url, title, date or None,
                        row["outlet"], text, text_hash, score_result.score, score_result.score,
                        score_result.bucket, PROMPT_VERSION,
                    ),
                )
                conn.execute("UPDATE fetch_failures SET resolved = 1 WHERE url = ?", (url,))
                break
            except Exception as e:
                if "locked" in str(e):
                    import time; time.sleep(0.5 + _attempt * 0.5)
                    continue
                raise

        stats["scored"] += 1

        if stats["scored"] % 10 == 0:
            for _attempt in range(10):
                try:
                    conn.commit()
                    break
                except Exception:
                    import time; time.sleep(1)
            archive_str = f" | {stats.get('archive_rescued', 0)} from archive" if stats.get("archive_rescued") else ""
            log.info(
                f"  {lookup_name}: {stats['scored']}/{total_for_journalist} scored | "
                f"{stats['fetch_failed']} fetch fails | {stats['score_failed']} score fails{archive_str}"
            )

    for _attempt in range(10):
        try:
            conn.commit()
            break
        except Exception:
            import time; time.sleep(1)


async def main():
    parser = argparse.ArgumentParser(description="Score articles from discovered_urls")
    parser.add_argument("--cap", type=int, default=0, help="Max articles per journalist across all rounds (0=unlimited)")
    parser.add_argument("--per-round", type=int, default=0, help="Articles per journalist per round (0=do all in one pass)")
    parser.add_argument("--journalist", type=str, default="", help="Process only this journalist name")
    parser.add_argument("--dry-run", action="store_true", help="Count articles only, no fetching/scoring")
    parser.add_argument("--batch-size", type=int, default=50, help="Batch size for processing")
    parser.add_argument("--retry-failed", action="store_true", help="Retry previously failed URLs")
    parser.add_argument("--priority", type=str, default="", help="Comma-separated journalist names to process first, in order")
    parser.add_argument("--only", type=str, default="", help="Comma-separated journalist names — ONLY process these (for parallel workers)")
    args = parser.parse_args()

    import sqlite3 as _sqlite3
    conn = get_connection()
    conn.execute("PRAGMA busy_timeout = 30000")  # Wait up to 30s for locks
    migrate_db(conn)
    _ensure_fetch_failures_table(conn)

    if args.retry_failed:
        # Reset unresolved failures for retry
        reset = conn.execute(
            "UPDATE fetch_failures SET retry_count = 0 WHERE resolved = 0"
        ).rowcount
        log.info(f"Reset {reset} failed URLs for retry")

    # Get journalists with unscored discovered_urls
    if args.journalist:
        journalists = conn.execute(
            "SELECT * FROM journalists WHERE name = ?", (args.journalist,)
        ).fetchall()
    elif args.only:
        only_names = [n.strip() for n in args.only.split(",")]
        placeholders = ",".join("?" * len(only_names))
        journalists = conn.execute(
            f"SELECT * FROM journalists WHERE name IN ({placeholders})", only_names
        ).fetchall()
    else:
        journalists = conn.execute("SELECT * FROM journalists ORDER BY name").fetchall()

    # For each journalist, count how many discovered_urls are NOT in articles table
    # and not already permanently failed
    work = []
    for j in journalists:
        count = conn.execute(
            """SELECT COUNT(*) FROM discovered_urls d
               WHERE d.journalist_id = ?
               AND d.url NOT IN (SELECT url FROM articles)
               AND d.url NOT IN (SELECT url FROM fetch_failures WHERE resolved = 0 AND retry_count >= 2)""",
            (j["id"],)
        ).fetchone()[0]
        if count > 0:
            work.append((dict(j), count))

    # Sort by priority list first, then by unscored count descending
    if args.priority:
        priority_names = [n.strip() for n in args.priority.split(",")]
        priority_map = {name: i for i, name in enumerate(priority_names)}

        def sort_key(item):
            j, count = item
            if j["name"] in priority_map:
                return (0, priority_map[j["name"]])
            return (1, -count)

        work.sort(key=sort_key)
    else:
        work.sort(key=lambda x: x[1], reverse=True)

    total_to_score = sum(c for _, c in work)
    log.info(f"Found {total_to_score:,} unscored articles across {len(work)} journalists")

    if args.dry_run:
        for j, count in work:
            cap_str = f" (capped to {args.cap})" if args.cap and count > args.cap else ""
            log.info(f"  {j['name']}: {count:,} articles{cap_str}")
        estimated_cost = total_to_score * 0.011
        log.info(f"Estimated cost at Sonnet 4.5: ${estimated_cost:,.0f}")

        # Show failure stats
        fail_count = conn.execute("SELECT COUNT(*) FROM fetch_failures WHERE resolved = 0").fetchone()[0]
        if fail_count:
            log.info(f"\nPrevious failures: {fail_count:,} URLs failed (use --retry-failed to retry)")
            top_reasons = conn.execute("""
                SELECT reason, COUNT(*) as cnt FROM fetch_failures
                WHERE resolved = 0 GROUP BY reason ORDER BY cnt DESC LIMIT 5
            """).fetchall()
            for r in top_reasons:
                log.info(f"  {r[0]}: {r[1]:,}")

        conn.close()
        return

    import aiohttp
    per_round = args.per_round
    overall_cap = args.cap

    # Track how many we've scored per journalist across rounds
    scored_per_journalist: dict[str, int] = {}

    async with aiohttp.ClientSession() as session:
        grand_total = 0
        grand_archive = 0
        grand_failed = 0
        round_num = 0

        while not _shutdown:
            round_num += 1
            round_scored = 0

            if per_round:
                log.info(f"\n{'#'*60}")
                log.info(f"ROUND {round_num} — scoring up to {per_round} per journalist")

            for j, total_unscored in work:
                if _shutdown:
                    break

                jname = j["name"]
                already_scored = scored_per_journalist.get(jname, 0)

                # Check overall cap
                if overall_cap and already_scored >= overall_cap:
                    continue

                # How many to score this round
                if per_round:
                    this_round = per_round
                    if overall_cap:
                        this_round = min(this_round, overall_cap - already_scored)
                else:
                    this_round = min(total_unscored, overall_cap) if overall_cap else total_unscored

                # Get unscored URLs — newest first
                rows = conn.execute(
                    """SELECT d.url, d.outlet, d.journalist_id
                       FROM discovered_urls d
                       LEFT JOIN articles a ON a.url = d.url
                       WHERE d.journalist_id = ?
                       AND a.id IS NULL
                       AND d.url NOT IN (SELECT url FROM fetch_failures WHERE resolved = 0 AND retry_count >= 2)
                       ORDER BY d.id DESC
                       LIMIT ?""",
                    (j["id"], this_round)
                ).fetchall()

                if not rows:
                    continue

                log.info(f"\n{'='*60}")
                log.info(f"{jname} ({j['outlet']}): scoring {len(rows)} (round {round_num}, {already_scored} already done)")

                stats = {"scored": 0, "skipped": 0, "fetched": 0, "fetch_failed": 0, "score_failed": 0, "archive_rescued": 0}

                for i in range(0, len(rows), args.batch_size):
                    if _shutdown:
                        break
                    batch = rows[i:i + args.batch_size]
                    await process_batch(conn, session, batch, jname, len(rows), stats)

                # Update stats
                update_journalist_stats(conn, j["id"])
                scored_per_journalist[jname] = already_scored + stats["scored"]
                grand_total += stats["scored"]
                grand_archive += stats.get("archive_rescued", 0)
                grand_failed += stats["fetch_failed"]
                round_scored += stats["scored"]

                archive_str = f", {stats.get('archive_rescued', 0)} from archive.is" if stats.get("archive_rescued") else ""
                log.info(
                    f"{jname}: {stats['scored']} scored this round{archive_str}, "
                    f"{scored_per_journalist[jname]} total, "
                    f"{stats['fetch_failed']} fetch fails"
                )

                # Checkpoint
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

            if not per_round or round_scored == 0:
                # Single pass mode, or nothing left to score
                break

            log.info(f"\nRound {round_num} complete: {round_scored} articles scored")

        log.info(f"\n{'='*60}")
        log.info(f"COMPLETE: {grand_total:,} articles scored ({grand_archive:,} rescued from archive.is)")
        log.info(f"FAILED: {grand_failed:,} articles could not be fetched")

        # Summary of failures
        fail_stats = conn.execute("""
            SELECT reason, COUNT(*) as cnt FROM fetch_failures
            WHERE resolved = 0 GROUP BY reason ORDER BY cnt DESC
        """).fetchall()
        if fail_stats:
            log.info("Failure breakdown:")
            for r in fail_stats:
                log.info(f"  {r[0]}: {r[1]:,}")

    # Export
    count = export_to_json(conn, EXTENSION_DATA)
    log.info(f"Exported {count} journalists to data.json")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
