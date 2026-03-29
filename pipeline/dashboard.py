"""Dev dashboard generator — outputs a self-contained HTML file from the SQLite database.

Usage:
    python -m pipeline.dashboard
    python -m pipeline.dashboard --open    # auto-open in browser
"""

import argparse
import csv
import json
import webbrowser
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from .db import get_connection


DATA_JSON_PATH = Path(__file__).parent.parent / "extension" / "public" / "data.json"
CONNECTIONS_CSV_PATH = Path(__file__).parent.parent / "data" / "connections.csv"


def load_card_data() -> dict:
    """Load baseball card data from data.json keyed by slug."""
    if DATA_JSON_PATH.exists():
        with open(DATA_JSON_PATH) as f:
            return json.load(f).get("journalists", {})
    return {}


def load_connections() -> dict:
    """Load connections from CSV, grouped by journalist slug."""
    conns: dict = {}
    if CONNECTIONS_CSV_PATH.exists():
        with open(CONNECTIONS_CSV_PATH) as f:
            for row in csv.DictReader(f):
                slug = row["journalist_slug"]
                conns.setdefault(slug, []).append({
                    "type": row["type"],
                    "target": row["target_name"],
                    "role": row["target_role"],
                    "source": row["source_url"],
                })
    return conns


def initials_avatar(name: str, size: int = 48) -> str:
    """Generate an SVG data URI with the journalist's initials."""
    parts = name.split()
    initials = (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else name[0].upper()
    # Deterministic color from name
    hue = sum(ord(c) for c in name) % 360
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<rect width="{size}" height="{size}" rx="{size // 2}" fill="hsl({hue},45%,62%)"/>'
        f'<text x="50%" y="50%" dy=".35em" text-anchor="middle" fill="#fff" '
        f'font-family="-apple-system,BlinkMacSystemFont,sans-serif" font-size="{size // 2 - 2}" font-weight="600">'
        f'{initials}</text></svg>'
    )


BUCKET_COLORS = {
    "left":         "#ef4444",
    "centre-left":  "#f97316",
    "centre":       "#6b7280",
    "centre-right": "#3b82f6",
    "right":        "#1d4ed8",
}

BUCKET_ORDER = ["left", "centre-left", "centre", "centre-right", "right"]


def score_to_color(score: float) -> str:
    if score <= -0.6:   return "#dc2626"   # red — Left
    if score <= -0.4:   return "#ef4444"   # lighter red — Leans Left
    if score <= -0.2:   return "#f97316"   # orange-red — Leans Centre-Left
    if score <= -0.05:  return "#d97706"   # warm amber — Centre (leans left)
    if score <=  0.05:  return "#6b7280"   # grey — Centre
    if score <=  0.2:   return "#6366f1"   # indigo — Centre (leans right)
    if score <=  0.4:   return "#3b82f6"   # blue — Leans Centre-Right
    if score <=  0.6:   return "#2563eb"   # deeper blue — Leans Right
    return "#1d4ed8"                       # dark blue — Right


def score_to_label(score: float) -> str:
    if score <= -0.6:   return "Left"
    if score <= -0.4:   return "Leans Left"
    if score <= -0.2:   return "Leans Centre-Left"
    if score <= -0.05:  return "Centre (leans left)"
    if score <=  0.05:  return "Centre"
    if score <=  0.2:   return "Centre (leans right)"
    if score <=  0.4:   return "Leans Centre-Right"
    if score <=  0.6:   return "Leans Right"
    return "Right"


def generate_html(conn) -> str:
    journalists = conn.execute(
        "SELECT * FROM journalists ORDER BY name"
    ).fetchall()

    total_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    scored_journalists = conn.execute(
        "SELECT COUNT(DISTINCT journalist_id) FROM articles"
    ).fetchone()[0]

    journalist_sections = []

    for j in journalists:
        articles = conn.execute(
            """SELECT title, url, publish_date, bucket, median_score, score_claude, scored_at
               FROM articles WHERE journalist_id = ?
               ORDER BY scored_at DESC""",
            (j["id"],)
        ).fetchall()

        avatar_svg = initials_avatar(j['name'])
        photo_url = j['photo_url']
        if photo_url:
            avatar_html = f'<img class="j-avatar" src="{photo_url}" alt="{j["name"]}">'
        else:
            avatar_html = f'<div class="j-avatar">{avatar_svg}</div>'

        # Baseball card data from DB
        db_connections = conn.execute(
            """SELECT DISTINCT type, target_name, target_role, source_url
               FROM connections WHERE journalist_id = ?""",
            (j["id"],)
        ).fetchall()
        connections = [dict(c) for c in db_connections]

        db_facts = conn.execute(
            """SELECT DISTINCT fact_text, source_url
               FROM facts WHERE journalist_id = ?""",
            (j["id"],)
        ).fetchall()
        facts = [dict(f) for f in db_facts]

        confidence = j["confidence_tier"] or ""

        # Social links (shared by both empty and scored cards)
        social_links = []
        if j['twitter_url']:
            social_links.append(f'<a href="{j["twitter_url"]}" target="_blank" rel="noopener" class="social-link social-x">X</a>')
        if j['bluesky_url']:
            social_links.append(f'<a href="{j["bluesky_url"]}" target="_blank" rel="noopener" class="social-link social-bsky">Bluesky</a>')
        if j['linkedin_url']:
            social_links.append(f'<a href="{j["linkedin_url"]}" target="_blank" rel="noopener" class="social-link social-li">LinkedIn</a>')
        if j['facebook_url']:
            social_links.append(f'<a href="{j["facebook_url"]}" target="_blank" rel="noopener" class="social-link social-fb">Facebook</a>')
        if j['substack_url']:
            social_links.append(f'<a href="{j["substack_url"]}" target="_blank" rel="noopener" class="social-link social-sub">Substack</a>')
        social_html = f'<div class="social-row">{"".join(social_links)}</div>' if social_links else ""

        # Connections section (shared by both empty and scored cards)
        connections_html = ""
        if connections or social_links:
            conn_rows = ""
            for c in connections:
                c_type = c.get("type", "")
                c_target = c.get("target_name", "")
                c_role = c.get("target_role", "")
                c_source = c.get("source_url", "")
                source_link = f'<a href="{c_source}" target="_blank" rel="noopener" class="conn-source">source</a>' if c_source else ""
                conn_rows += f'<div class="conn-row"><span class="conn-type">{c_type}</span> <span class="conn-target">{c_target}</span>{f" — {c_role}" if c_role else ""} {source_link}</div>'
            connections_html = f'<div class="card-connections"><div class="card-section-label">Connections</div>{social_html}{conn_rows}</div>'

        bio_html = ""
        bio = j['bio'] if 'bio' in j.keys() else None
        if bio:
            bio_html = f'<div class="card-bio"><div class="card-section-label">Background</div><p class="bio-text">{bio}</p></div>'

        if not articles:
            journalist_sections.append(f"""
            <div class="journalist-card empty">
                <div class="j-header">
                    {avatar_html}
                    <div class="j-left">
                        <div class="j-name">{j['name']}</div>
                        <div class="j-meta">{j['outlet']} · {j['beat'] or 'No beat set'}</div>
                    </div>
                </div>
                {connections_html}
                {bio_html}
                <div class="no-data">No articles scored yet</div>
            </div>""")
            continue

        # Distribution
        dist = {b: 0 for b in BUCKET_ORDER}
        for a in articles:
            b = a["bucket"] or "centre"
            if b in dist:
                dist[b] += 1
        total = len(articles)

        dist_bars = ""
        for bucket in BUCKET_ORDER:
            count = dist[bucket]
            pct = round((count / total) * 100) if total else 0
            color = BUCKET_COLORS[bucket]
            dist_bars += f"""
                <div class="dist-row">
                    <span class="dist-label">{bucket.title()}</span>
                    <div class="dist-bar-wrap">
                        <div class="dist-bar" style="width:{pct}%;background:{color}"></div>
                    </div>
                    <span class="dist-count">{count} <span class="dist-pct">({pct}%)</span></span>
                </div>"""

        avg_score = sum(a["median_score"] or 0 for a in articles) / total
        avg_color = score_to_color(avg_score)
        avg_label = score_to_label(avg_score)
        lean_pct = abs(round(avg_score * 100))
        if lean_pct <= 2:
            lean_text = "Centre"
        elif avg_score < 0:
            lean_text = f"{lean_pct}% left leaning"
        else:
            lean_text = f"{lean_pct}% right leaning"

        # Article rows
        article_rows = ""
        for a in articles:
            score = a["median_score"] or 0
            bucket = a["bucket"] or "centre"
            color = BUCKET_COLORS.get(bucket, "#6b7280")
            date = (a["publish_date"] or a["scored_at"] or "")[:10]
            title = (a["title"] or "Untitled")[:90]
            url = a["url"] or "#"
            article_rows += f"""
                <tr>
                    <td class="art-title"><a href="{url}" target="_blank" rel="noopener">{title}</a></td>
                    <td class="art-date">{date}</td>
                    <td><span class="bucket-badge" style="background:{color}20;color:{color};border:1px solid {color}40">{bucket}</span></td>
                    <td class="art-score" style="color:{color}">{score:+.2f}</td>
                </tr>"""

        # Baseball card section
        conf_colors = {
            "low": ("#fff7ed", "#b45309"),
            "medium": ("#f3f4f6", "#4b5563"),
            "high": ("#ecfdf5", "#047857"),
        }
        conf_bg, conf_text = conf_colors.get(confidence, ("#f3f4f6", "#4b5563"))
        confidence_badge = f'<span class="conf-badge" style="background:{conf_bg};color:{conf_text}">{confidence}</span>' if confidence else ""

        facts_html = ""
        if facts:
            fact_rows = ""
            for f in facts:
                f_source = f.get("source_url", "")
                source_link = f'<a href="{f_source}" target="_blank" rel="noopener" class="conn-source">source</a>' if f_source else ""
                fact_rows += f'<div class="conn-row">{f["fact_text"]} {source_link}</div>'
            facts_html = f'<div class="card-connections"><div class="card-section-label">Key facts</div>{fact_rows}</div>'

        journalist_sections.append(f"""
        <div class="journalist-card">
            <div class="j-header" onclick="toggleArticles('{j['slug']}')">
                {avatar_html}
                <div class="j-left">
                    <div class="j-name">{j['name']}</div>
                    <div class="j-meta">{j['outlet']} · {j['beat'] or 'Politics'}</div>
                </div>
                <div class="j-right">
                    {confidence_badge}
                    <div class="spectrum-wrap" title="{avg_label} ({avg_score:+.2f})">
                        <span class="spectrum-label-l">Left</span>
                        <div class="spectrum-track">
                            <div class="spectrum-bar"></div>
                            <div class="spectrum-tick" style="left:25%"></div>
                            <div class="spectrum-tick spectrum-tick-center" style="left:50%"></div>
                            <div class="spectrum-tick" style="left:75%"></div>
                            <div class="spectrum-tick-label" style="left:0%">−</div>
                            <div class="spectrum-tick-label" style="left:50%">0</div>
                            <div class="spectrum-tick-label" style="left:100%">+</div>
                            <div class="spectrum-marker" style="left:{((avg_score + 1) / 2) * 100:.1f}%"></div>
                        </div>
                        <span class="spectrum-label-r">Right</span>
                    </div>
                    <span class="lean-text" style="color:{avg_color}">{lean_text}</span>
                    <span class="article-count">{total} articles</span>
                    <span class="toggle-icon">▼</span>
                </div>
            </div>
            <div class="dist-section">
                {dist_bars}
            </div>
            {connections_html}
            {facts_html}
            {bio_html}
            <div class="articles-section" id="articles-{j['slug']}" style="display:none">
                <table class="art-table">
                    <thead>
                        <tr>
                            <th>Article</th>
                            <th>Date</th>
                            <th>Bucket</th>
                            <th>Score</th>
                        </tr>
                    </thead>
                    <tbody>
                        {article_rows}
                    </tbody>
                </table>
            </div>
        </div>""")

    sections_html = "\n".join(journalist_sections)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Byline Card — Dev Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background: #f8f9fa; color: #1a1a1a; }}

  header {{ background: #1a1a1a; color: #fff; padding: 20px 32px; display: flex; justify-content: space-between; align-items: center; }}
  header h1 {{ font-size: 18px; font-weight: 600; }}
  header .subtitle {{ font-size: 12px; color: #999; margin-top: 2px; }}
  .stats {{ display: flex; gap: 24px; }}
  .stat {{ text-align: right; }}
  .stat-value {{ font-size: 22px; font-weight: 700; color: #fff; }}
  .stat-label {{ font-size: 11px; color: #888; }}

  .container {{ max-width: 960px; margin: 0 auto; padding: 24px 16px; }}

  .journalist-card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }}
  .journalist-card.empty {{ }}

  .j-header {{ padding: 14px 18px; display: flex; align-items: center; gap: 14px; cursor: pointer; user-select: none; }}
  .j-header:hover {{ background: #f9fafb; }}
  .j-avatar {{ width: 48px; height: 48px; border-radius: 50%; flex-shrink: 0; overflow: hidden; }}
  .j-avatar img {{ width: 100%; height: 100%; object-fit: cover; }}
  .j-avatar svg {{ display: block; }}
  .j-left {{ flex: 1; min-width: 0; }}
  .j-name {{ font-size: 15px; font-weight: 600; color: #1a1a1a; }}
  .j-meta {{ font-size: 12px; color: #888; margin-top: 2px; }}
  .j-right {{ display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
  .spectrum-wrap {{ display: flex; align-items: center; gap: 6px; width: 300px; flex-shrink: 0; }}
  .spectrum-label-l {{ font-size: 10px; font-weight: 700; color: #dc2626; }}
  .spectrum-label-r {{ font-size: 10px; font-weight: 700; color: #1d4ed8; }}
  .spectrum-track {{ flex: 1; position: relative; height: 28px; }}
  .spectrum-bar {{ position: absolute; top: 10px; left: 0; right: 0; height: 8px; border-radius: 4px; background: linear-gradient(to right, #dc2626, #f97316 25%, #d1d5db 50%, #3b82f6 75%, #1d4ed8); }}
  .spectrum-tick {{ position: absolute; top: 4px; width: 1px; height: 20px; background: rgba(0,0,0,0.15); transform: translateX(-50%); }}
  .spectrum-tick-label {{ position: absolute; top: 26px; font-size: 8px; color: #999; transform: translateX(-50%); font-weight: 500; }}
  .spectrum-tick-center {{ background: rgba(0,0,0,0.3); }}
  .spectrum-marker {{ position: absolute; top: 3px; width: 4px; height: 22px; border-radius: 2px; background: #1a1a1a; box-shadow: 0 1px 4px rgba(0,0,0,0.4); transform: translateX(-50%); }}
  .lean-text {{ font-size: 12px; font-weight: 600; white-space: nowrap; }}
  .article-count {{ font-size: 12px; color: #888; white-space: nowrap; }}
  .toggle-icon {{ font-size: 11px; color: #bbb; transition: transform 0.2s; }}
  .toggle-icon.open {{ transform: rotate(180deg); }}

  .dist-section {{ padding: 10px 18px 12px; border-top: 1px solid #f3f4f6; }}
  .dist-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 5px; }}
  .dist-label {{ width: 88px; font-size: 11px; font-weight: 500; color: #555; text-align: right; flex-shrink: 0; }}
  .dist-bar-wrap {{ flex: 1; height: 6px; background: #f3f4f6; border-radius: 3px; overflow: hidden; }}
  .dist-bar {{ height: 100%; border-radius: 3px; transition: width 0.3s; min-width: 2px; }}
  .dist-count {{ width: 72px; font-size: 11px; color: #555; flex-shrink: 0; }}
  .dist-pct {{ color: #aaa; }}

  .no-data {{ padding: 10px 18px; font-size: 12px; color: #aaa; }}

  .conf-badge {{ font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px; white-space: nowrap; }}

  .card-connections {{ padding: 10px 18px; border-top: 1px solid #f3f4f6; }}
  .card-section-label {{ font-size: 11px; font-weight: 500; color: #888; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 6px; }}
  .conn-row {{ font-size: 12px; color: #444; margin-bottom: 4px; line-height: 1.4; }}
  .conn-type {{ color: #888; font-size: 11px; font-weight: 500; text-transform: capitalize; }}
  .conn-target {{ font-weight: 600; color: #1a1a1a; }}
  .conn-source {{ color: #2563eb; text-decoration: none; font-size: 11px; margin-left: 6px; }}
  .conn-source:hover {{ text-decoration: underline; }}
  .social-row {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }}
  .social-link {{ font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 4px; text-decoration: none; }}
  .social-link:hover {{ opacity: 0.8; }}
  .social-x {{ background: #0f1419; color: #fff; }}
  .social-bsky {{ background: #0085ff; color: #fff; }}
  .social-li {{ background: #0a66c2; color: #fff; }}
  .social-fb {{ background: #1877f2; color: #fff; }}
  .social-sub {{ background: #ff6719; color: #fff; }}

  .card-methodology {{ padding: 6px 18px 10px; font-size: 11px; color: #aaa; border-top: 1px solid #f3f4f6; }}

  .card-bio {{ padding: 10px 18px; border-top: 1px solid #f3f4f6; }}
  .bio-text {{ font-size: 12px; color: #555; line-height: 1.6; margin: 0; }}

  .articles-section {{ border-top: 1px solid #f3f4f6; }}
  .art-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .art-table thead th {{ padding: 8px 18px; text-align: left; font-size: 11px; font-weight: 500; color: #888; text-transform: uppercase; letter-spacing: 0.4px; border-bottom: 1px solid #f3f4f6; background: #fafafa; }}
  .art-table tbody tr:hover {{ background: #f9fafb; }}
  .art-table tbody td {{ padding: 9px 18px; border-bottom: 1px solid #f9fafb; vertical-align: middle; }}
  .art-title a {{ color: #1a1a1a; text-decoration: none; line-height: 1.4; }}
  .art-title a:hover {{ text-decoration: underline; color: #2563eb; }}
  .art-date {{ color: #888; font-size: 12px; white-space: nowrap; }}
  .art-score {{ font-weight: 700; font-size: 13px; text-align: right; white-space: nowrap; }}
  .bucket-badge {{ font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 4px; white-space: nowrap; }}

  .filter-bar {{ display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }}
  .filter-btn {{ padding: 5px 12px; border-radius: 20px; border: 1px solid #e5e7eb; background: #fff; font-size: 12px; cursor: pointer; color: #555; }}
  .filter-btn:hover, .filter-btn.active {{ background: #1a1a1a; color: #fff; border-color: #1a1a1a; }}
  .filter-label {{ font-size: 12px; color: #888; margin-right: 4px; }}

  footer {{ text-align: center; font-size: 11px; color: #bbb; padding: 24px; }}
</style>
</head>
<body>

<header>
  <div>
    <h1>Byline Card · Dev Dashboard</h1>
    <div class="subtitle">Generated {generated_at}</div>
  </div>
  <div class="stats">
    <div class="stat">
      <div class="stat-value">{total_articles}</div>
      <div class="stat-label">Articles Scored</div>
    </div>
    <div class="stat">
      <div class="stat-value">{scored_journalists} / {len(journalists)}</div>
      <div class="stat-label">Journalists Active</div>
    </div>
  </div>
</header>

<div class="container">
  <div class="filter-bar">
    <span class="filter-label">Show:</span>
    <button class="filter-btn active" onclick="filterCards('all', this)">All ({len(journalists)})</button>
    <button class="filter-btn" onclick="filterCards('scored', this)">Scored ({scored_journalists})</button>
    <button class="filter-btn" onclick="filterCards('empty', this)">No data ({len(journalists) - scored_journalists})</button>
    <button class="filter-btn" style="margin-left:auto" onclick="expandAll()">Expand all</button>
    <button class="filter-btn" onclick="collapseAll()">Collapse all</button>
  </div>

  {sections_html}
</div>

<footer>Byline Card pipeline · SQLite data · Refresh by running <code>python -m pipeline.dashboard --open</code></footer>

<script>
function toggleArticles(slug) {{
  const el = document.getElementById('articles-' + slug);
  const icon = el.closest('.journalist-card').querySelector('.toggle-icon');
  if (el.style.display === 'none') {{
    el.style.display = 'block';
    icon.classList.add('open');
  }} else {{
    el.style.display = 'none';
    icon.classList.remove('open');
  }}
}}

function filterCards(type, btn) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.journalist-card').forEach(card => {{
    if (type === 'all') card.style.display = '';
    else if (type === 'scored') card.style.display = card.classList.contains('empty') ? 'none' : '';
    else if (type === 'empty') card.style.display = card.classList.contains('empty') ? '' : 'none';
  }});
}}

function expandAll() {{
  document.querySelectorAll('.articles-section').forEach(el => el.style.display = 'block');
  document.querySelectorAll('.toggle-icon').forEach(el => el.classList.add('open'));
}}

function collapseAll() {{
  document.querySelectorAll('.articles-section').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.toggle-icon').forEach(el => el.classList.remove('open'));
}}
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate Byline Card dev dashboard")
    parser.add_argument("--open", action="store_true", help="Open in browser after generating")
    parser.add_argument("--output", type=str, default="dashboard.html", help="Output file path")
    args = parser.parse_args()

    conn = get_connection()
    html = generate_html(conn)
    conn.close()

    output_path = Path(__file__).parent.parent / args.output
    output_path.write_text(html)
    print(f"Dashboard written to {output_path}")

    if args.open:
        webbrowser.open(f"file://{output_path.resolve()}")


if __name__ == "__main__":
    main()
