"""Tag discovered_urls with author_name and journalist_id.

Stores author_name for EVERY URL (future-proofing for new journalists).
Also sets journalist_id when the author matches one of our tracked journalists.

Strategy per outlet:
  - Newstalk ZB: Author in URL structure — use adapter's sitemap parsing
  - Stuff: Story API returns author JSON — no HTML needed
  - NZ Herald / RNZ / Spinoff / Newsroom: Fetch HTML, extract via trafilatura

For HTML-fetch outlets, processes in rate-limited batches.

Run: python -m pipeline.tag_authors [--outlet OUTLET] [--batch-size N] [--max-batches N]
"""

import asyncio
import logging
import re
import sys
from datetime import datetime, timezone

import aiohttp
import trafilatura

from pipeline.db import get_connection, init_db, migrate_db

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)

# Stuff story API
STUFF_STORY_API = "https://www.stuff.co.nz/api/v1.0/stuff/story/{story_id}"
ARTICLE_ID_RE = re.compile(r"/(\d{9,})/")


def _build_name_lookup(conn) -> dict[str, int]:
    """Build normalized-name → journalist_id lookup for our tracked journalists."""
    rows = conn.execute("SELECT id, name, slug FROM journalists").fetchall()
    lookup: dict[str, int] = {}
    for r in rows:
        name = r["name"].strip()
        jid = r["id"]

        # Exact lowercase
        lookup[name.lower()] = jid
        # Hyphenated
        lookup[name.lower().replace(" ", "-")] = jid
        # "Last, First"
        parts = name.split()
        if len(parts) >= 2:
            lookup[f"{parts[-1].lower()}, {parts[0].lower()}"] = jid

        # Slug without outlet suffix
        slug = r["slug"]
        for suffix in ["-nzherald", "-newstalkzb", "-rnz", "-stuff", "-1news", "-tvnz", "-thespinoff", "-newsroom"]:
            if slug.endswith(suffix):
                slug = slug[: -len(suffix)]
                break
        lookup[slug] = jid

    return lookup


def _match_author(author_name: str, lookup: dict[str, int]) -> int | None:
    """Match an author name to a journalist_id. Returns None if not tracked."""
    if not author_name:
        return None
    clean = author_name.strip().lower()
    if clean in lookup:
        return lookup[clean]
    if clean.replace(" ", "-") in lookup:
        return lookup[clean.replace(" ", "-")]
    for prefix in ["by ", "written by ", "opinion: "]:
        if clean.startswith(prefix):
            stripped = clean[len(prefix):]
            if stripped in lookup:
                return lookup[stripped]
    return None


# ─── NEWSTALK ZB ───────────────────────────────────────────────────────────

async def tag_newstalkzb(conn, lookup: dict[str, int], **kwargs) -> int:
    """Tag Newstalk ZB URLs using the adapter's sitemap-based author extraction."""
    from pipeline.sites.newstalkzb import NewstalkZBAdapter, SLUG_TO_NAME

    adapter = NewstalkZBAdapter()
    author_urls = await adapter.get_all_urls_by_author()

    tagged = 0
    for author_slug, urls in author_urls.items():
        if author_slug.startswith("_"):
            continue

        # Get display name for this author
        display_name = SLUG_TO_NAME.get(author_slug, author_slug.replace("-", " ").title())
        jid = _match_author(display_name, lookup)

        for url in urls:
            conn.execute(
                "UPDATE discovered_urls SET author_name = ?, journalist_id = ? WHERE url = ? AND author_name IS NULL",
                (display_name, jid, url),
            )
            if jid:
                tagged += 1

    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    log.info(f"Newstalk ZB: tagged {tagged} URLs to tracked journalists")
    return tagged


# ─── STUFF ─────────────────────────────────────────────────────────────────

async def tag_stuff(conn, lookup: dict[str, int], batch_size: int = 50, max_batches: int = 0, **kwargs) -> int:
    """Tag Stuff URLs using their story API (author in JSON, no HTML)."""
    rows = conn.execute(
        "SELECT id, url FROM discovered_urls WHERE outlet = 'Stuff' AND author_name IS NULL"
    ).fetchall()
    log.info(f"Stuff: {len(rows)} untagged URLs to process via API")

    if not rows:
        return 0

    tagged = 0
    authors_found = 0
    checked = 0
    batches_done = 0

    async with aiohttp.ClientSession() as session:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            tasks = [_stuff_get_author(session, r["url"]) for r in batch]
            results = await asyncio.gather(*tasks)

            for row, author_name in zip(batch, results):
                checked += 1
                if author_name:
                    authors_found += 1
                    jid = _match_author(author_name, lookup)
                    conn.execute(
                        "UPDATE discovered_urls SET author_name = ?, journalist_id = ? WHERE id = ?",
                        (author_name, jid, row["id"]),
                    )
                    if jid:
                        tagged += 1

            conn.commit()
            batches_done += 1
            if batches_done % 20 == 0:
                log.info(f"Stuff: checked {checked}/{len(rows)}, authors found {authors_found}, tracked {tagged}")
            if max_batches and batches_done >= max_batches:
                break

            await asyncio.sleep(0.2)

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    log.info(f"Stuff: checked {checked}, authors found {authors_found}, tracked journalists {tagged}")
    return tagged


async def _stuff_get_author(session: aiohttp.ClientSession, url: str) -> str | None:
    """Get author name from Stuff story API."""
    match = ARTICLE_ID_RE.search(url)
    if not match:
        return None

    story_id = match.group(1)
    api_url = STUFF_STORY_API.format(story_id=story_id)

    try:
        async with session.get(api_url, headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception:
        return None

    authors = data.get("author", [])
    return authors[0].get("name", "") if authors else None


# ─── HTML-BASED TAGGING (Herald, RNZ, Spinoff, Newsroom) ──────────────────

async def tag_via_html(
    conn,
    lookup: dict[str, int],
    outlet: str,
    batch_size: int = 20,
    max_batches: int = 0,
    concurrency: int = 5,
    **kwargs,
) -> int:
    """Tag URLs by fetching HTML and extracting author metadata."""
    rows = conn.execute(
        "SELECT id, url FROM discovered_urls WHERE outlet = ? AND author_name IS NULL",
        (outlet,),
    ).fetchall()
    log.info(f"{outlet}: {len(rows)} untagged URLs to process via HTML")

    if not rows:
        return 0

    tagged = 0
    authors_found = 0
    checked = 0
    batches_done = 0
    semaphore = asyncio.Semaphore(concurrency)

    async with aiohttp.ClientSession() as session:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            tasks = [_html_get_author(session, semaphore, r["url"]) for r in batch]
            results = await asyncio.gather(*tasks)

            for row, author_name in zip(batch, results):
                checked += 1
                if author_name:
                    authors_found += 1
                    jid = _match_author(author_name, lookup)
                    conn.execute(
                        "UPDATE discovered_urls SET author_name = ?, journalist_id = ? WHERE id = ?",
                        (author_name, jid, row["id"]),
                    )
                    if jid:
                        tagged += 1

            conn.commit()
            batches_done += 1
            if batches_done % 5 == 0:
                log.info(
                    f"{outlet}: checked {checked}/{len(rows)}, "
                    f"authors found {authors_found}, tracked {tagged}"
                )
            if max_batches and batches_done >= max_batches:
                break

            await asyncio.sleep(0.5)

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    log.info(f"{outlet}: checked {checked}, authors found {authors_found}, tracked journalists {tagged}")
    return tagged


async def _html_get_author(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    url: str,
) -> str | None:
    """Fetch a URL and extract author name from HTML metadata."""
    async with semaphore:
        try:
            async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
        except Exception:
            return None

    # Try trafilatura metadata first (fastest)
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata and metadata.author:
            return metadata.author
    except Exception:
        pass

    # Fallback: JSON-LD
    ld_match = re.search(r'"author"[^}]*?"name"\s*:\s*"([^"]+)"', html)
    if ld_match:
        return ld_match.group(1)

    # Fallback: meta tag
    meta_match = re.search(r'<meta[^>]*name="author"[^>]*content="([^"]+)"', html)
    if meta_match:
        return meta_match.group(1)

    return None


# ─── MAIN ──────────────────────────────────────────────────────────────────

OUTLETS = {
    "newstalkzb": tag_newstalkzb,
    "stuff": tag_stuff,
    "1news": lambda conn, lookup, **kw: tag_via_html(conn, lookup, "1News", **kw),
    "nzherald": lambda conn, lookup, **kw: tag_via_html(conn, lookup, "NZ Herald", **kw),
    "rnz": lambda conn, lookup, **kw: tag_via_html(conn, lookup, "RNZ", **kw),
    "spinoff": lambda conn, lookup, **kw: tag_via_html(conn, lookup, "The Spinoff", **kw),
    "newsroom": lambda conn, lookup, **kw: tag_via_html(conn, lookup, "Newsroom", **kw),
}


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    conn = get_connection()
    init_db(conn)
    migrate_db(conn)

    lookup = _build_name_lookup(conn)
    log.info(f"Built name lookup with {len(lookup)} entries for {len(set(lookup.values()))} journalists")

    # Parse args
    outlet_filter = None
    batch_size = 50
    max_batches = 0

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--outlet" and i + 1 < len(args):
            outlet_filter = args[i + 1].lower()
        elif arg == "--batch-size" and i + 1 < len(args):
            batch_size = int(args[i + 1])
        elif arg == "--max-batches" and i + 1 < len(args):
            max_batches = int(args[i + 1])

    outlets_to_run = OUTLETS if not outlet_filter else {outlet_filter: OUTLETS[outlet_filter]}

    for name, tag_fn in outlets_to_run.items():
        log.info(f"=== Tagging {name} ===")
        try:
            await tag_fn(conn, lookup, batch_size=batch_size, max_batches=max_batches)
        except Exception as e:
            log.error(f"Tagging failed for {name}: {e}")
            import traceback
            traceback.print_exc()

    # Summary
    rows = conn.execute("""
        SELECT outlet,
               COUNT(*) as total,
               SUM(CASE WHEN author_name IS NOT NULL THEN 1 ELSE 0 END) as with_author,
               SUM(CASE WHEN journalist_id IS NOT NULL THEN 1 ELSE 0 END) as tracked,
               COUNT(DISTINCT author_name) as unique_authors
        FROM discovered_urls GROUP BY outlet ORDER BY total DESC
    """).fetchall()

    log.info("=== TAGGING SUMMARY ===")
    for r in rows:
        log.info(
            f"  {r['outlet']}: {r['with_author']:,} / {r['total']:,} have author "
            f"({r['unique_authors']} unique), {r['tracked']:,} tracked"
        )

    # Per-journalist breakdown
    j_rows = conn.execute("""
        SELECT j.name, j.outlet, COUNT(d.id) as urls
        FROM journalists j
        JOIN discovered_urls d ON d.journalist_id = j.id
        GROUP BY j.id
        ORDER BY urls DESC
    """).fetchall()
    if j_rows:
        log.info("=== PER-JOURNALIST ===")
        for r in j_rows:
            log.info(f"  {r['name']} ({r['outlet']}): {r['urls']:,} URLs")

    # Top non-tracked authors
    top_rows = conn.execute("""
        SELECT author_name, COUNT(*) as cnt
        FROM discovered_urls
        WHERE author_name IS NOT NULL AND journalist_id IS NULL
        GROUP BY author_name
        ORDER BY cnt DESC
        LIMIT 20
    """).fetchall()
    if top_rows:
        log.info("=== TOP UNTRACKED AUTHORS (candidates for adding) ===")
        for r in top_rows:
            log.info(f"  {r['author_name']}: {r['cnt']:,} articles")

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
