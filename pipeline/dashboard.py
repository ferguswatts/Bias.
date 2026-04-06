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
            <div class="journalist-card empty" data-outlet="{j['outlet']}" data-name="{j['name'].lower()}" data-score="999">
                <div class="j-header" onclick="toggleDetails('{j['slug']}')">
                    {avatar_html}
                    <div class="j-left">
                        <div class="j-name">{j['name']}</div>
                        <div class="j-meta">{j['outlet']} · {j['beat'] or 'No beat set'}</div>
                    </div>
                </div>
                <div class="accordion-body" id="details-{j['slug']}">
                    {connections_html}
                    {bio_html}
                </div>
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

        # Article rows — grouped by year/month, most recent first
        from collections import OrderedDict
        month_groups: OrderedDict[str, list] = OrderedDict()
        sorted_articles = sorted(articles, key=lambda a: a["publish_date"] or a["scored_at"] or "", reverse=True)
        for a in sorted_articles:
            date_str = (a["publish_date"] or a["scored_at"] or "")[:10]
            if len(date_str) >= 7:
                ym = date_str[:7]  # "2026-03"
            else:
                ym = "Unknown"
            month_groups.setdefault(ym, []).append(a)

        article_rows = ""
        is_first_month = True
        for ym, group in month_groups.items():
            if ym != "Unknown":
                try:
                    from datetime import datetime
                    dt = datetime.strptime(ym, "%Y-%m")
                    month_label = dt.strftime("%B %Y")
                except ValueError:
                    month_label = ym
            else:
                month_label = "Unknown date"
            group_id = f"month-{j['slug']}-{ym}"
            collapsed = "" if is_first_month else " style=\"display:none\""
            arrow_class = "month-arrow toggle-icon open" if is_first_month else "month-arrow toggle-icon"
            article_rows += f"""
                <tr class="month-header" onclick="document.querySelectorAll('.{group_id}').forEach(r=>r.style.display=r.style.display==='none'?'':'none');this.querySelector('.month-arrow').classList.toggle('open')">
                    <td colspan="4" style="background:#f9fafb;font-weight:600;font-size:12px;color:#374151;padding:8px 12px;cursor:pointer;user-select:none">
                        <span class="{arrow_class}">▼</span> {month_label} <span style="font-weight:400;color:#9ca3af">({len(group)} articles)</span>
                    </td>
                </tr>"""
            for a in group:
                score = a["median_score"] or 0
                bucket = a["bucket"] or "centre"
                color = BUCKET_COLORS.get(bucket, "#6b7280")
                date = (a["publish_date"] or a["scored_at"] or "")[:10]
                title = (a["title"] or "Untitled")[:90]
                url = a["url"] or "#"
                article_rows += f"""
                <tr class="{group_id}"{collapsed}>
                    <td class="art-title"><a href="{url}" target="_blank" rel="noopener">{title}</a></td>
                    <td class="art-date">{date}</td>
                    <td><span class="bucket-badge" style="background:{color}20;color:{color};border:1px solid {color}40">{bucket}</span></td>
                    <td class="art-score" style="color:{color}">{score:+.2f}</td>
                </tr>"""
            is_first_month = False

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
        <div class="journalist-card" data-outlet="{j['outlet']}" data-name="{j['name'].lower()}" data-score="{avg_score:.4f}">
            <div class="j-header" onclick="toggleDetails('{j['slug']}')">
                {avatar_html}
                <div class="j-left">
                    <div class="j-name">{j['name']}</div>
                    <div class="j-meta">{j['outlet']} · {j['beat'] or 'Politics'}</div>
                </div>
                <div class="j-right">
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
                </div>
            </div>
            <div class="accordion-body" id="details-{j['slug']}">
                <div class="dist-section">
                    {dist_bars}
                </div>
                {connections_html}
                {facts_html}
                {bio_html}
                <div class="articles-section">
                    <div class="articles-toggle" onclick="toggleArticles('{j['slug']}')">
                        <span>Articles ({total})</span>
                        <span class="toggle-icon toggle-articles">▼</span>
                    </div>
                    <div class="accordion-body" id="articles-content-{j['slug']}">
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
                </div>
            </div>
        </div>""")

    # Split cards into 3 fixed columns (round-robin) so expanding
    # a card in one column doesn't affect the others
    cols = [[], [], []]
    for i, s in enumerate(journalist_sections):
        cols[i % 3].append(s)
    sections_html = (
        '<div class="card-column">' + "\n".join(cols[0]) + '</div>'
        '<div class="card-column">' + "\n".join(cols[1]) + '</div>'
        '<div class="card-column">' + "\n".join(cols[2]) + '</div>'
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build outlet filter buttons
    outlets = sorted(set(j['outlet'] for j in journalists if j['outlet']))
    outlet_buttons = ''.join(
        f'<button class="filter-btn outlet-btn" onclick="filterOutlet(\'{o}\', this)">{o}</button>'
        for o in outlets
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bias — NZ Journalist Transparency</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; background: #f8f9fa; color: #1a1a1a; -webkit-font-smoothing: antialiased; }}

  /* ── Header ── */
  header {{ background: #1a1a1a; color: #fff; padding: 20px 32px; display: flex; justify-content: space-between; align-items: center; }}
  header h1 {{ font-size: clamp(16px, 2vw, 18px); font-weight: 600; }}
  header .subtitle {{ font-size: 12px; color: #999; margin-top: 2px; }}
  .stats {{ display: flex; gap: 24px; }}
  .stat {{ text-align: right; }}
  .stat-value {{ font-size: clamp(18px, 2.5vw, 22px); font-weight: 700; color: #fff; }}
  .stat-label {{ font-size: 11px; color: #888; }}

  /* ── Layout ── */
  .container {{ max-width: 1600px; margin: 0 auto; padding: 24px 16px; }}
  .card-grid {{ display: flex; gap: 8px; align-items: flex-start; }}
  .card-column {{ flex: 1; min-width: 0; }}

  /* ── Cards ── */
  .journalist-card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.04); transition: box-shadow 0.2s; }}
  .journalist-card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}

  .j-header {{ padding: 10px 16px; display: flex; align-items: center; gap: 12px; cursor: pointer; user-select: none; min-height: 44px; flex-wrap: wrap; transition: background 0.15s; }}
  .j-header:hover {{ background: #f9fafb; }}
  .j-avatar {{ width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0; overflow: hidden; }}
  .j-avatar img {{ width: 100%; height: 100%; object-fit: cover; }}
  .j-avatar svg {{ display: block; }}
  .j-left {{ flex: 1; min-width: 0; }}
  .j-name {{ font-size: 15px; font-weight: 600; color: #1a1a1a; }}
  .j-meta {{ font-size: 12px; color: #888; margin-top: 2px; }}
  .j-right {{ display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}

  /* ── Spectrum widget ── */
  .spectrum-wrap {{ display: flex; align-items: center; gap: 4px; width: 140px; flex-shrink: 0; }}
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

  /* ── Accordion sections (Pretext-animated) ── */
  .accordion-body {{ height: 0; overflow: clip; transition: height 250ms ease; }}
  .accordion-body.open {{ /* height set by JS */ }}

  /* ── Distribution ── */
  .dist-section {{ padding: 10px 18px 12px; border-top: 1px solid #f3f4f6; }}
  .dist-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 5px; }}
  .dist-label {{ width: 88px; font-size: 11px; font-weight: 500; color: #555; text-align: right; flex-shrink: 0; }}
  .dist-bar-wrap {{ flex: 1; height: 6px; background: #f3f4f6; border-radius: 3px; overflow: hidden; }}
  .dist-bar {{ height: 100%; border-radius: 3px; transition: width 0.3s; min-width: 2px; }}
  .dist-count {{ width: 72px; font-size: 11px; color: #555; flex-shrink: 0; }}
  .dist-pct {{ color: #aaa; }}

  .no-data {{ padding: 10px 18px; font-size: 12px; color: #aaa; }}

  /* ── Methodology Section ── */
  .methodology-section {{ background: #fff; border-top: 1px solid #e5e7eb; margin-top: 48px; padding: 64px 0; }}
  .methodology-inner {{ max-width: 960px; margin: 0 auto; padding: 0 24px; }}
  .methodology-title {{ font-size: 28px; font-weight: 700; color: #1a1a1a; margin-bottom: 8px; }}
  .methodology-subtitle {{ font-size: 15px; color: #666; margin-bottom: 40px; }}

  .methodology-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin-bottom: 48px; }}
  .methodology-card {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 24px; position: relative; }}
  .methodology-step {{ position: absolute; top: -12px; left: 20px; background: #1a1a1a; color: #fff; width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }}
  .methodology-card h3 {{ font-size: 16px; font-weight: 600; margin-bottom: 8px; color: #1a1a1a; }}
  .methodology-card p {{ font-size: 14px; color: #555; line-height: 1.6; }}
  .methodology-card a {{ color: #2563eb; text-decoration: none; }}
  .methodology-card a:hover {{ text-decoration: underline; }}
  .methodology-detail {{ margin-top: 12px; padding-top: 12px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #888; }}
  .methodology-detail strong {{ color: #555; }}

  .scoring-dimensions {{ margin-bottom: 48px; }}
  .scoring-dimensions h3 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
  .scoring-dimensions > p {{ font-size: 14px; color: #666; margin-bottom: 20px; }}
  .dimensions-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }}
  .dimension {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; text-align: center; }}
  .dimension-icon {{ font-size: 20px; color: #888; margin-bottom: 8px; }}
  .dimension strong {{ display: block; font-size: 13px; margin-bottom: 6px; color: #1a1a1a; }}
  .dimension p {{ font-size: 12px; color: #666; line-height: 1.5; }}

  .scoring-example {{ margin-bottom: 48px; }}
  .scoring-example h3 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
  .scoring-example > p {{ font-size: 14px; color: #666; margin-bottom: 20px; }}
  .example-pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .example-card {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }}
  .example-left {{ border-top: 3px solid #dc2626; }}
  .example-right {{ border-top: 3px solid #1d4ed8; }}
  .example-badge {{ display: inline-block; font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 4px; text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 10px; }}
  .badge-left {{ background: #fef2f2; color: #dc2626; }}
  .badge-right {{ background: #eff6ff; color: #1d4ed8; }}
  .example-header {{ padding: 20px 24px 16px; }}
  .example-article-title {{ font-size: 15px; font-weight: 600; color: #1a1a1a; margin-bottom: 4px; }}
  .example-meta {{ font-size: 13px; color: #888; }}
  .example-meta strong {{ color: #1a1a1a; }}
  .example-reasoning {{ padding: 16px 24px; border-top: 1px solid #e5e7eb; }}
  .example-label {{ font-size: 11px; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
  .example-reasoning p {{ font-size: 13px; color: #555; line-height: 1.7; }}
  .example-dimensions {{ padding: 14px 24px; border-top: 1px solid #e5e7eb; display: flex; gap: 10px; flex-wrap: wrap; }}
  .example-dim {{ display: flex; align-items: center; gap: 6px; }}
  .dim-name {{ font-size: 11px; color: #888; }}
  .dim-score {{ font-size: 12px; font-weight: 700; color: #1a1a1a; background: #e5e7eb; padding: 2px 8px; border-radius: 4px; }}

  .scoring-prompt-section {{ margin-bottom: 48px; }}
  .scoring-prompt-section h3 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
  .scoring-prompt-section > p {{ font-size: 14px; color: #666; margin-bottom: 16px; }}
  .scoring-prompt-section a {{ color: #2563eb; text-decoration: none; }}
  .scoring-prompt-section a:hover {{ text-decoration: underline; }}
  .prompt-details {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; }}
  .prompt-details summary {{ padding: 14px 20px; cursor: pointer; font-size: 14px; font-weight: 500; color: #555; }}
  .prompt-details summary:hover {{ background: #f3f4f6; }}
  .prompt-text {{ padding: 20px; font-size: 13px; line-height: 1.7; color: #555; white-space: pre-wrap; font-family: "SF Mono", "Fira Code", monospace; background: #1a1a1a; color: #d4d4d4; border-radius: 0 0 8px 8px; overflow-x: auto; }}

  .methodology-caveats {{ margin-bottom: 0; }}
  .methodology-caveats h3 {{ font-size: 20px; font-weight: 600; margin-bottom: 16px; }}
  .methodology-caveats ul {{ list-style: none; padding: 0; }}
  .methodology-caveats li {{ font-size: 14px; color: #555; line-height: 1.7; padding: 12px 0; border-bottom: 1px solid #f3f4f6; }}
  .methodology-caveats li:last-child {{ border-bottom: none; }}
  .methodology-caveats li strong {{ color: #1a1a1a; }}

  @media (max-width: 768px) {{
    .methodology-grid {{ grid-template-columns: 1fr; }}
    .dimensions-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .example-pair {{ grid-template-columns: 1fr; }}
    .example-dimensions {{ flex-direction: column; gap: 8px; }}
    .methodology-title {{ font-size: 22px; }}
  }}
  @media (max-width: 480px) {{
    .dimensions-grid {{ grid-template-columns: 1fr; }}
  }}
  .conf-badge {{ font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px; white-space: nowrap; }}

  /* ── Connections & bio ── */
  .card-connections {{ padding: 10px 18px; border-top: 1px solid #f3f4f6; }}
  .card-section-label {{ font-size: 11px; font-weight: 500; color: #888; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 6px; }}
  .conn-row {{ font-size: 12px; color: #444; margin-bottom: 4px; line-height: 1.4; }}
  .conn-type {{ color: #888; font-size: 11px; font-weight: 500; text-transform: capitalize; }}
  .conn-target {{ font-weight: 600; color: #1a1a1a; }}
  .conn-source {{ color: #2563eb; text-decoration: none; font-size: 11px; margin-left: 6px; }}
  .conn-source:hover {{ text-decoration: underline; }}
  .social-row {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }}
  .social-link {{ font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 4px; text-decoration: none; transition: opacity 0.15s; }}
  .social-link:hover {{ opacity: 0.8; }}
  .social-x {{ background: #0f1419; color: #fff; }}
  .social-bsky {{ background: #0085ff; color: #fff; }}
  .social-li {{ background: #0a66c2; color: #fff; }}
  .social-fb {{ background: #1877f2; color: #fff; }}
  .social-sub {{ background: #ff6719; color: #fff; }}

  .card-methodology {{ padding: 6px 18px 10px; font-size: 11px; color: #aaa; border-top: 1px solid #f3f4f6; }}
  .card-bio {{ padding: 10px 18px; border-top: 1px solid #f3f4f6; }}
  .bio-text {{ font-size: 12px; color: #555; line-height: 1.6; margin: 0; }}

  /* ── Articles ── */
  .details-section {{ }}
  .articles-toggle {{ padding: 10px 18px; border-top: 1px solid #f3f4f6; display: flex; justify-content: space-between; align-items: center; cursor: pointer; user-select: none; font-size: 12px; font-weight: 500; color: #888; text-transform: uppercase; letter-spacing: 0.4px; min-height: 44px; transition: background 0.15s; }}
  .articles-toggle:hover {{ background: #f9fafb; }}
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
  .month-header td {{ position: sticky; top: 0; z-index: 1; }}
  .month-header:hover td {{ background: #f3f4f6 !important; }}
  .month-arrow {{ display: inline-block; transition: transform 0.2s; font-size: 10px; margin-right: 6px; }}
  .month-arrow.open {{ transform: rotate(0deg); }}
  .month-arrow:not(.open) {{ transform: rotate(-90deg); }}

  /* ── Filter bar ── */
  .filter-bar {{ display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; position: sticky; top: 0; z-index: 10; background: #f8f9fa; padding: 8px 0; }}
  .filter-btn {{ padding: 8px 14px; border-radius: 20px; border: 1px solid #e5e7eb; background: #fff; font-size: 12px; cursor: pointer; color: #555; min-height: 36px; transition: all 0.15s; }}
  .filter-btn:hover, .filter-btn.active {{ background: #1a1a1a; color: #fff; border-color: #1a1a1a; }}
  .filter-label {{ font-size: 12px; color: #888; margin-right: 4px; }}
  .search-input {{ padding: 8px 14px; border-radius: 20px; border: 1px solid #e5e7eb; font-size: 12px; outline: none; width: 180px; margin-left: auto; min-height: 36px; transition: border-color 0.15s; }}
  .search-input:focus {{ border-color: #1a1a1a; }}

  footer {{ text-align: center; font-size: 11px; color: #bbb; padding: 24px; }}
  footer a {{ transition: color 0.15s; }}

  /* ── Responsive: 3-col → 2-col ── */
  @media (max-width: 1200px) {{
    .card-grid {{ flex-wrap: wrap; }}
    .card-column {{ flex: 0 0 calc(50% - 4px); }}
  }}

  /* ── Responsive: 2-col → 1-col ── */
  @media (max-width: 1024px) {{
    .card-column {{ flex: 0 0 100%; }}
  }}

  /* ── Responsive: tablet ── */
  @media (max-width: 768px) {{
    header {{ padding: 16px; flex-direction: column; align-items: flex-start; gap: 12px; }}
    .stats {{ width: 100%; }}
    .search-input {{ width: 100%; margin-left: 0; }}
    .filter-bar {{ gap: 6px; }}
    .filter-btn {{ padding: 8px 12px; font-size: 11px; }}
    .art-table thead th:nth-child(4),
    .art-table tbody td:nth-child(4) {{ display: none; }}
  }}

  /* ── Responsive: phone ── */
  @media (max-width: 480px) {{
    .container {{ padding: 12px 8px; }}
    .j-header {{ flex-wrap: wrap; padding: 10px 12px; }}
    .j-right {{ width: 100%; margin-top: 6px; padding-left: 52px; }}
    .spectrum-wrap {{ width: 100%; }}
    .art-table thead th:nth-child(2),
    .art-table tbody td:nth-child(2) {{ display: none; }}
    .art-table tbody td {{ padding: 8px 10px; }}
    .dist-section {{ padding: 10px 12px; }}
    .card-connections {{ padding: 10px 12px; }}
    .card-bio {{ padding: 10px 12px; }}
    .articles-toggle {{ padding: 10px 12px; }}
  }}

  /* ── Reduced motion ── */
  @media (prefers-reduced-motion: reduce) {{
    .accordion-body, .toggle-icon, .month-arrow, .dist-bar, .journalist-card, .filter-btn, .social-link {{ transition: none !important; }}
  }}
</style>
</head>
<body>


<div class="container">
  <div class="filter-bar">
    <span class="filter-label">Show:</span>
    <button class="filter-btn active" onclick="filterCards('all', this)">All ({len(journalists)})</button>
    <button class="filter-btn" onclick="filterCards('scored', this)">Scored ({scored_journalists})</button>
    <button class="filter-btn" onclick="filterCards('empty', this)">No data ({len(journalists) - scored_journalists})</button>
    <span class="filter-label" style="margin-left:auto">Sort:</span>
    <button class="filter-btn sort-btn" onclick="sortCards('alpha', this)">A–Z</button>
    <button class="filter-btn sort-btn" onclick="sortCards('left', this)">Most Left</button>
    <button class="filter-btn sort-btn" onclick="sortCards('right', this)">Most Right</button>
    <button class="filter-btn sort-btn" onclick="sortCards('centre', this)">Most Centre</button>
    <button class="filter-btn" onclick="expandAll()">Expand all</button>
    <button class="filter-btn" onclick="collapseAll()">Collapse all</button>
  </div>
  <div class="filter-bar">
    <span class="filter-label">Outlet:</span>
    <button class="filter-btn outlet-btn active" onclick="filterOutlet('all', this)">All</button>
    {outlet_buttons}
    <input type="text" class="search-input" placeholder="Search by name…" oninput="searchCards(this.value)">
  </div>

  <div class="card-grid">
  {sections_html}
  </div>
</div>

<!-- ── Methodology Section ── -->
<div class="methodology-section" id="methodology">
  <div class="methodology-inner">
    <h2 class="methodology-title">How We Score</h2>
    <p class="methodology-subtitle">Complete transparency in our methodology — every step is open source</p>

    <div class="methodology-grid">
      <div class="methodology-card">
        <div class="methodology-step">1</div>
        <h3>Article Discovery</h3>
        <p>We crawl sitemaps from NZ's major news outlets to build a comprehensive index of every published article. For each URL, we extract the author name using outlet-specific strategies — APIs, structured data, or streaming HTML.</p>
        <div class="methodology-detail">
          <strong>Outlets:</strong> NZ Herald, Stuff, RNZ, The Spinoff, Newstalk ZB, 1News, Newsroom
        </div>
      </div>

      <div class="methodology-card">
        <div class="methodology-step">2</div>
        <h3>Text Extraction</h3>
        <p>Full article text is extracted using <a href="https://trafilatura.readthedocs.io/" target="_blank" rel="noopener">trafilatura</a>, an open-source library designed for web content extraction. The extracted text is cleaned of navigation, ads, and boilerplate before scoring.</p>
        <div class="methodology-detail">
          <strong>Minimum threshold:</strong> Articles under 200 characters are excluded as likely stubs or fragments
        </div>
      </div>

      <div class="methodology-card">
        <div class="methodology-step">3</div>
        <h3>AI Scoring</h3>
        <p>Each article is scored by <strong>Claude Sonnet 4.5</strong> (Anthropic) on a scale from <strong>-1.0</strong> (hard left) to <strong>+1.0</strong> (hard right). The AI evaluates five dimensions of bias for each article and applies the "Cui Bono" principle — asking who benefits from the story being published.</p>
        <div class="methodology-detail">
          <strong>Model:</strong> claude-sonnet-4-5 &nbsp;|&nbsp; <strong>Prompt version:</strong> v2-cuibono
        </div>
      </div>

      <div class="methodology-card">
        <div class="methodology-step">4</div>
        <h3>Aggregation</h3>
        <p>Individual article scores are aggregated into five buckets for each journalist. The spectrum position shown on each card is the median score across all their articles from the past 24 months. Confidence ratings (low/medium/high) reflect the number of articles scored.</p>
        <div class="methodology-detail">
          <strong>Buckets:</strong> Left (&lt;-0.6) · Centre-Left (-0.6 to -0.2) · Centre (-0.2 to 0.2) · Centre-Right (0.2 to 0.6) · Right (&gt;0.6)
        </div>
      </div>
    </div>

    <div class="scoring-dimensions">
      <h3>The Five Dimensions of Bias</h3>
      <p>Every article is evaluated across these five dimensions, each scored independently:</p>
      <div class="dimensions-grid">
        <div class="dimension">
          <div class="dimension-icon">&#9664;</div>
          <strong>Story Selection</strong>
          <p>The editorial choice of what to cover. Publishing a story about one party's scandal while ignoring the other's is itself a signal — even before a word is written.</p>
        </div>
        <div class="dimension">
          <div class="dimension-icon">&#9632;</div>
          <strong>Framing</strong>
          <p>How is the topic presented? Who is positioned as protagonist or antagonist? The frame shapes reader perception before they process the facts.</p>
        </div>
        <div class="dimension">
          <div class="dimension-icon">&#9654;</div>
          <strong>Source Selection</strong>
          <p>Which voices are quoted? Are opposing views included? Over-representing one side's experts or spokespeople creates an imbalanced narrative.</p>
        </div>
        <div class="dimension">
          <div class="dimension-icon">&#9650;</div>
          <strong>Language</strong>
          <p>Loaded words like "slammed", "controversial", "radical", or "common sense" inject editorial judgement into ostensibly neutral reporting.</p>
        </div>
        <div class="dimension">
          <div class="dimension-icon">&#9660;</div>
          <strong>Omission</strong>
          <p>What relevant context is missing? Profiling a politician's controversial views while omitting their actual policy record is bias by absence.</p>
        </div>
      </div>
    </div>

    <div class="scoring-example">
      <h3>Worked Examples</h3>
      <p>Two fictional articles showing how the same scoring framework produces different results:</p>

      <div class="example-pair">
        <div class="example-card example-left">
          <div class="example-header">
            <div class="example-badge badge-left">Left-leaning example</div>
            <div class="example-article-title">"Government's housing selloff leaves thousands in limbo"</div>
            <div class="example-meta">Fictional Author · Score: <strong>-0.45</strong> · Bucket: <strong>Centre-Left</strong></div>
          </div>
          <div class="example-reasoning">
            <div class="example-label">AI Reasoning</div>
            <p>The article frames government housing policy as a "selloff" — loaded language implying reckless disposal rather than neutral "reform" or "restructuring". Sources are predominantly tenant advocacy groups and opposition MPs, with only a single paragraph quoting the Housing Minister. The cui bono test: this story benefits parties advocating for more state housing, which skews left. The omission of waiting list reduction data under the policy further tilts the framing.</p>
          </div>
          <div class="example-dimensions">
            <div class="example-dim"><span class="dim-name">Story Selection</span><span class="dim-score">-0.3</span></div>
            <div class="example-dim"><span class="dim-name">Framing</span><span class="dim-score">-0.5</span></div>
            <div class="example-dim"><span class="dim-name">Source Selection</span><span class="dim-score">-0.5</span></div>
            <div class="example-dim"><span class="dim-name">Language</span><span class="dim-score">-0.6</span></div>
            <div class="example-dim"><span class="dim-name">Omission</span><span class="dim-score">-0.4</span></div>
          </div>
        </div>

        <div class="example-card example-right">
          <div class="example-header">
            <div class="example-badge badge-right">Right-leaning example</div>
            <div class="example-article-title">"Taxpayers foot the bill as council's pet project blows budget"</div>
            <div class="example-meta">Fictional Author · Score: <strong>+0.40</strong> · Bucket: <strong>Centre-Right</strong></div>
          </div>
          <div class="example-reasoning">
            <div class="example-label">AI Reasoning</div>
            <p>The article frames a public infrastructure project as wasteful government spending — "pet project" and "foot the bill" are loaded phrases that prime the reader to see public investment negatively. Sources are dominated by a taxpayer advocacy group and business owners, with the council's justification confined to a brief quote. The cui bono test: this story benefits parties advocating for reduced government spending, which skews right. The omission of the project's employment and economic impact data tilts the narrative further.</p>
          </div>
          <div class="example-dimensions">
            <div class="example-dim"><span class="dim-name">Story Selection</span><span class="dim-score">+0.3</span></div>
            <div class="example-dim"><span class="dim-name">Framing</span><span class="dim-score">+0.5</span></div>
            <div class="example-dim"><span class="dim-name">Source Selection</span><span class="dim-score">+0.4</span></div>
            <div class="example-dim"><span class="dim-name">Language</span><span class="dim-score">+0.5</span></div>
            <div class="example-dim"><span class="dim-name">Omission</span><span class="dim-score">+0.3</span></div>
          </div>
        </div>
      </div>
    </div>

    <div class="scoring-prompt-section">
      <h3>The Exact Prompt</h3>
      <p>In the spirit of full transparency, this is the exact prompt sent to the AI for every article. Nothing is hidden. <a href="https://github.com/ferguswatts/Bias./blob/main/pipeline/scorer.py" target="_blank" rel="noopener">View source on GitHub →</a></p>
      <details class="prompt-details">
        <summary>Click to expand the full scoring prompt</summary>
        <pre class="prompt-text">You are a political bias analyst for New Zealand media. Score the following
news article on a scale from -1.0 (hard left) to +1.0 (hard right).

Consider these dimensions:
- FRAMING: How is the topic presented? Who is the protagonist/antagonist?
- SOURCE SELECTION: Which politicians, experts, or voices are quoted?
- LANGUAGE: Is loaded or emotive language used?
- TOPIC EMPHASIS: What aspects are highlighted vs downplayed?
- OMISSION: What relevant context or perspectives are missing?

CRITICAL — ask "CUI BONO?" (who benefits from this story being published?):
- An article exposing a right-wing party's failures BENEFITS THE LEFT
- An article exposing a left-wing party's problems BENEFITS THE RIGHT
- The editorial choice of what to write about is itself a signal of lean
- "Both sides quoted" does NOT mean neutral

NZ political context:
- Left = Labour, Greens, Te Pati Maori
- Right = National, ACT
- Centre = NZ First (varies by issue)

Returns: score (-1.0 to 1.0), confidence, reasoning, and per-dimension scores.</pre>
      </details>
    </div>

    <div class="methodology-caveats">
      <h3>Limitations &amp; Caveats</h3>
      <ul>
        <li><strong>AI is not infallible.</strong> Large language models can misinterpret sarcasm, cultural context, or NZ-specific political nuance. Individual article scores should be taken with a grain of salt — the aggregate pattern across hundreds of articles is what matters.</li>
        <li><strong>Bias is multidimensional.</strong> A single left-right spectrum is a simplification. Journalists may be progressive on social issues but conservative on economic policy. We use five sub-dimensions to partially address this.</li>
        <li><strong>Article availability varies.</strong> Not all articles are accessible for scoring. Some may be unavailable, removed, or behind access restrictions, which can affect representation.</li>
        <li><strong>Correlation is not intent.</strong> A journalist whose work scores left-leaning may be accurately reporting on a left-leaning government's policies. Context matters.</li>
        <li><strong>This is a starting point, not a verdict.</strong> Bias exists to surface patterns and encourage critical reading — not to label journalists as biased.</li>
      </ul>
    </div>
  </div>
</div>

<footer>Bias · Open source at <a href="https://github.com/ferguswatts/Bias." style="color:#999">GitHub</a></footer>

<script>
/* ── Accordion toggle — smooth height animation ── */
function toggleAccordion(el) {{
  if (el.classList.contains('open')) {{
    // Collapse: set explicit height first, then animate to 0
    el.style.height = el.scrollHeight + 'px';
    requestAnimationFrame(() => {{ el.style.height = '0'; }});
    el.classList.remove('open');
  }} else {{
    // Expand: set height to scrollHeight, then clear after transition
    el.style.height = el.scrollHeight + 'px';
    el.classList.add('open');
    const onEnd = () => {{ el.style.height = 'auto'; el.removeEventListener('transitionend', onEnd); }};
    el.addEventListener('transitionend', onEnd);
  }}
}}

function toggleDetails(slug) {{
  const el = document.getElementById('details-' + slug);
  if (!el) return;
  toggleAccordion(el);

  // If collapsing, also collapse inner articles
  if (!el.classList.contains('open')) {{
    const artContent = document.getElementById('articles-content-' + slug);
    if (artContent && artContent.classList.contains('open')) {{
      artContent.style.height = '0';
      artContent.classList.remove('open');
    }}
    const artIcon = el.querySelector('.toggle-articles');
    if (artIcon) artIcon.classList.remove('open');
  }}
}}

function toggleArticles(slug) {{
  const el = document.getElementById('articles-content-' + slug);
  if (!el) return;
  const icon = el.closest('.articles-section').querySelector('.toggle-articles');
  toggleAccordion(el);
  if (icon) icon.classList.toggle('open');
}}

/* ── Filters ── */
let activeShowFilter = 'all';
let activeOutletFilter = 'all';
let activeSearchTerm = '';

function applyFilters() {{
  document.querySelectorAll('.journalist-card').forEach(card => {{
    let show = true;
    if (activeShowFilter === 'scored' && card.classList.contains('empty')) show = false;
    if (activeShowFilter === 'empty' && !card.classList.contains('empty')) show = false;
    if (activeOutletFilter !== 'all' && card.dataset.outlet !== activeOutletFilter) show = false;
    if (activeSearchTerm && !card.dataset.name.includes(activeSearchTerm)) show = false;
    card.style.display = show ? '' : 'none';
  }});
}}

function filterCards(type, btn) {{
  btn.closest('.filter-bar').querySelectorAll('.filter-btn:not(.outlet-btn)').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeShowFilter = type;
  applyFilters();
}}

function filterOutlet(outlet, btn) {{
  btn.closest('.filter-bar').querySelectorAll('.outlet-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeOutletFilter = outlet;
  applyFilters();
}}

function searchCards(term) {{
  activeSearchTerm = term.toLowerCase();
  applyFilters();
}}

/* ── Expand / Collapse all ── */
function expandAll() {{
  document.querySelectorAll('.accordion-body:not(.open)').forEach(el => {{
    el.style.height = 'auto';
    el.classList.add('open');
  }});
  document.querySelectorAll('.toggle-icon').forEach(el => el.classList.add('open'));
}}

function collapseAll() {{
  document.querySelectorAll('.accordion-body.open').forEach(el => {{
    el.style.height = '0';
    el.classList.remove('open');
  }});
  document.querySelectorAll('.toggle-icon').forEach(el => el.classList.remove('open'));
}}

/* ── Sort ── */
function sortCards(mode, btn) {{
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const columns = document.querySelectorAll('.card-column');
  const cards = [];
  columns.forEach(col => {{
    col.querySelectorAll('.journalist-card').forEach(c => cards.push(c));
  }});
  cards.sort((a, b) => {{
    const sa = parseFloat(a.dataset.score);
    const sb = parseFloat(b.dataset.score);
    if (mode === 'left') return sa - sb;
    if (mode === 'right') return sb - sa;
    if (mode === 'centre') return Math.abs(sa) - Math.abs(sb);
    return a.dataset.name.localeCompare(b.dataset.name);
  }});
  columns.forEach(col => {{ while (col.firstChild) col.removeChild(col.firstChild); }});
  cards.forEach((c, i) => columns[i % columns.length].appendChild(c));
}}

/* ── Deep linking via URL hash ── */
function openFromHash() {{
  const slug = window.location.hash.replace('#', '');
  if (!slug) return;
  const el = document.getElementById('details-' + slug);
  if (!el) return;
  // Expand card
  el.style.height = 'auto';
  el.classList.add('open');
  // Also expand articles if present
  const artContent = document.getElementById('articles-content-' + slug);
  if (artContent) {{
    artContent.style.height = 'auto';
    artContent.classList.add('open');
    const artIcon = el.querySelector('.toggle-articles');
    if (artIcon) artIcon.classList.add('open');
  }}
  // Scroll into view
  const card = el.closest('.journalist-card');
  setTimeout(() => card.scrollIntoView({{ behavior: 'smooth', block: 'start' }}), 100);
}}
window.addEventListener('DOMContentLoaded', openFromHash);
window.addEventListener('hashchange', openFromHash);
</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate Bias dashboard")
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
