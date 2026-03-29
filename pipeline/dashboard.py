"""Dev dashboard generator — outputs a self-contained HTML file from the SQLite database.

Usage:
    python -m pipeline.dashboard
    python -m pipeline.dashboard --open    # auto-open in browser
"""

import argparse
import json
import webbrowser
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from .db import get_connection


BUCKET_COLORS = {
    "left":         "#ef4444",
    "centre-left":  "#f97316",
    "centre":       "#6b7280",
    "centre-right": "#3b82f6",
    "right":        "#1d4ed8",
}

BUCKET_ORDER = ["left", "centre-left", "centre", "centre-right", "right"]


def score_to_color(score: float) -> str:
    if score <= -0.6:   return "#ef4444"
    if score <= -0.2:   return "#f97316"
    if score <=  0.2:   return "#6b7280"
    if score <=  0.6:   return "#3b82f6"
    return "#1d4ed8"


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

        if not articles:
            journalist_sections.append(f"""
            <div class="journalist-card empty">
                <div class="j-header">
                    <div class="j-name">{j['name']}</div>
                    <div class="j-meta">{j['outlet']} · {j['beat'] or 'No beat set'}</div>
                </div>
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

        journalist_sections.append(f"""
        <div class="journalist-card">
            <div class="j-header" onclick="toggleArticles('{j['slug']}')">
                <div class="j-left">
                    <div class="j-name">{j['name']}</div>
                    <div class="j-meta">{j['outlet']} · {j['beat'] or 'Politics'}</div>
                </div>
                <div class="j-right">
                    <span class="avg-score" style="color:{avg_color}">avg {avg_score:+.2f}</span>
                    <span class="article-count">{total} articles</span>
                    <span class="toggle-icon">▼</span>
                </div>
            </div>
            <div class="dist-section">
                {dist_bars}
            </div>
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
  .journalist-card.empty {{ opacity: 0.5; }}

  .j-header {{ padding: 14px 18px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; user-select: none; }}
  .j-header:hover {{ background: #f9fafb; }}
  .j-name {{ font-size: 15px; font-weight: 600; color: #1a1a1a; }}
  .j-meta {{ font-size: 12px; color: #888; margin-top: 2px; }}
  .j-right {{ display: flex; align-items: center; gap: 12px; }}
  .avg-score {{ font-size: 14px; font-weight: 700; }}
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
