"""Export SQLite data to JSON for the Chrome extension."""

import json
import sqlite3
from datetime import date
from pathlib import Path

from .aggregator import compute_distribution
from .db import get_connections_for_journalist, get_facts_for_journalist


def export_to_json(conn: sqlite3.Connection, output_path: Path) -> int:
    """Export all journalist data to a single JSON file for the extension.

    Returns: number of journalists exported.
    """
    journalists = conn.execute("SELECT * FROM journalists ORDER BY name").fetchall()

    data = {
        "version": date.today().isoformat(),
        "journalists": {},
    }

    for j in journalists:
        dist = compute_distribution(conn, j["id"])
        if dist["article_count"] == 0:
            continue

        connections = get_connections_for_journalist(conn, j["id"])
        facts = get_facts_for_journalist(conn, j["id"])

        aliases = json.loads(j["aliases"]) if j["aliases"] else []

        data["journalists"][j["slug"]] = {
            "name": j["name"],
            "aliases": aliases,
            "outlet": j["outlet"],
            "beat": j["beat"] or "",
            "article_count": dist["article_count"],
            "confidence": dist["confidence"],
            "distribution": {
                "left": dist["left"],
                "centre_left": dist["centre_left"],
                "centre": dist["centre"],
                "centre_right": dist["centre_right"],
                "right": dist["right"],
            },
            "connections": [
                {
                    "type": c["type"],
                    "target": c["target_name"],
                    "role": c["target_role"] or "",
                    "source": c["source_url"],
                }
                for c in connections
            ],
            "facts": [
                {"text": f["fact_text"], "source": f["source_url"]}
                for f in facts
            ],
            "methodology": f"Based on {dist['article_count']} articles scored by AI",
        }

    # Load site selectors
    sites_path = Path(__file__).parent.parent / "data" / "sites.json"
    if sites_path.exists():
        with open(sites_path) as f:
            data["sites"] = json.load(f)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return len(data["journalists"])
