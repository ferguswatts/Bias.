"""Aggregate per-article scores into journalist-level distributions."""

import sqlite3
from collections import Counter
from .scorer import score_to_bucket


def compute_distribution(conn: sqlite3.Connection, journalist_id: int) -> dict:
    """Compute the 5-bucket distribution for a journalist's articles.

    Returns: {
        "left": int (percentage),
        "centre_left": int,
        "centre": int,
        "centre_right": int,
        "right": int,
        "article_count": int,
        "confidence": str ("low"|"medium"|"high")
    }
    """
    rows = conn.execute(
        """SELECT bucket FROM articles
           WHERE journalist_id = ?
           AND publish_date >= date('now', '-24 months')
           AND bucket IS NOT NULL""",
        (journalist_id,),
    ).fetchall()

    total = len(rows)
    if total == 0:
        return {
            "left": 0, "centre_left": 0, "centre": 0,
            "centre_right": 0, "right": 0,
            "article_count": 0, "confidence": "low",
        }

    counts = Counter(row[0] for row in rows)

    distribution = {
        "left": round(counts.get("left", 0) / total * 100),
        "centre_left": round(counts.get("centre-left", 0) / total * 100),
        "centre": round(counts.get("centre", 0) / total * 100),
        "centre_right": round(counts.get("centre-right", 0) / total * 100),
        "right": round(counts.get("right", 0) / total * 100),
        "article_count": total,
    }

    # Confidence tiers
    if total >= 50:
        distribution["confidence"] = "high"
    elif total >= 20:
        distribution["confidence"] = "medium"
    else:
        distribution["confidence"] = "low"

    return distribution


def update_journalist_stats(conn: sqlite3.Connection, journalist_id: int) -> dict:
    """Recompute and store journalist aggregate stats. Returns the distribution."""
    dist = compute_distribution(conn, journalist_id)

    conn.execute(
        """UPDATE journalists
           SET article_count = ?,
               confidence_tier = ?,
               last_scored_at = datetime('now')
           WHERE id = ?""",
        (dist["article_count"], dist["confidence"], journalist_id),
    )
    conn.commit()
    return dist
