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
            """SELECT title, url, publish_date, bucket, median_score, score_claude, scored_at, topic
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
                        {f'<div class="j-formerly">Formerly: {j["formerly"]}</div>' if j.keys().__contains__("formerly") and j["formerly"] else ""}
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

        # Build per-article year+bucket array for JS filtering
        article_buckets = []
        for a in articles:
            yr = (a["publish_date"] or "")[:4]
            b = a["bucket"] or "centre"
            if yr and yr.isdigit():
                article_buckets.append(dict(y=int(yr), b=b, s=round(a["median_score"] or 0, 3)))
        import json as _json2
        article_buckets_json = _json2.dumps(article_buckets)

        dist_bars = ""
        for bucket in BUCKET_ORDER:
            count = dist[bucket]
            pct = round((count / total) * 100) if total else 0
            color = BUCKET_COLORS[bucket]
            bucket_id = bucket.replace("-", "_")
            dist_bars += f"""
                <div class="dist-row">
                    <span class="dist-label">{bucket.title()}</span>
                    <div class="dist-bar-wrap">
                        <div class="dist-bar" id="dist-bar-{j['slug']}-{bucket_id}" style="width:{pct}%;background:{color}"></div>
                    </div>
                    <span class="dist-count" id="dist-count-{j['slug']}-{bucket_id}">{count} <span class="dist-pct">({pct}%)</span></span>
                </div>"""

        # Topic profile
        from collections import Counter
        topic_counts = Counter(a["topic"] for a in articles if a["topic"])
        topic_total = sum(topic_counts.values())
        top_topics = topic_counts.most_common(6)

        topic_pills_html = ""
        if topic_total > 0:
            topic_pills = ""
            for topic, count in top_topics:
                pct = round(count / topic_total * 100)
                if pct >= 3:  # Only show topics >= 3%
                    label = topic.replace("-", " ").title()
                    topic_pills += f'<span class="topic-pill" title="{count} articles">{label} <span class="topic-pct">{pct}%</span></span>'
            if topic_pills:
                topic_pills_html = f"""
                <div class="topic-profile">
                    <div class="card-section-label">Topic Profile</div>
                    <div class="topic-pills">{topic_pills}</div>
                </div>"""

        # Per-year score data (for year filter)
        year_data = dict()
        for a in articles:
            yr = (a["publish_date"] or "")[:4]
            if yr and yr.isdigit():
                year_data.setdefault(yr, []).append(a["median_score"] or 0)

        year_scores_json = dict()
        for yr, scores in sorted(year_data.items()):
            scores.sort()
            n_yr = len(scores)
            median_yr = scores[n_yr // 2] if n_yr % 2 == 1 else (scores[n_yr // 2 - 1] + scores[n_yr // 2]) / 2
            year_scores_json[yr] = dict(count=n_yr, median=round(median_yr, 3))

        import json as _json
        year_data_attr = _json.dumps(year_scores_json).replace('"', '&quot;')

        sorted_scores = sorted(a["median_score"] or 0 for a in articles)
        n = len(sorted_scores)
        avg_score = sorted_scores[n // 2] if n % 2 == 1 else (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2
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
        <div class="journalist-card" data-outlet="{j['outlet']}" data-name="{j['name'].lower()}" data-score="{avg_score:.4f}" data-years="{year_data_attr}">
            <div class="j-header" onclick="toggleDetails('{j['slug']}')">
                {avatar_html}
                <div class="j-left">
                    <div class="j-name">{j['name']}</div>
                    <div class="j-meta">{j['outlet']} · {j['beat'] or 'Politics'}</div>
                    {f'<div class="j-formerly">Formerly: {j["formerly"]}</div>' if j.keys().__contains__("formerly") and j["formerly"] else ""}
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
                <div class="dist-section" id="dist-section-{j['slug']}" data-articles='{article_buckets_json}'>
                    {dist_bars}
                </div>
                {f"""<div class="year-range-section" data-slug="{j['slug']}" data-years='{year_data_attr.replace("&quot;", chr(34))}' data-min="{min(int(y) for y in year_data.keys())}" data-max="{max(int(y) for y in year_data.keys())}">
                    <div class="card-section-label">Filter by period</div>
                    <div class="range-slider-wrap">
                        <span class="range-label range-label-min">{min(year_data.keys())}</span>
                        <div class="range-track" id="range-track-{j['slug']}">
                            <div class="range-rail"></div>
                            <div class="range-fill" id="range-fill-{j['slug']}" style="left:0%;width:100%"></div>
                            <div class="range-thumb" id="range-thumb-min-{j['slug']}" style="left:0%"></div>
                            <div class="range-thumb" id="range-thumb-max-{j['slug']}" style="left:100%"></div>
                        </div>
                        <span class="range-label range-label-max">{max(year_data.keys())}</span>
                    </div>
                    <div class="range-info" id="range-info-{j['slug']}">All years · {total} articles</div>
                </div>""" if year_data and len(year_data) > 1 else ""}
                {topic_pills_html}
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
  header {{ background: #1a1a2e; text-align: center; padding: 0; }}
  header img {{ width: 100%; max-width: 1400px; height: auto; max-height: 300px; object-fit: contain; display: block; margin: 0 auto; }}
  header .how-we-score {{ display: block; padding: 10px; font-size: 13px; }}
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
  .j-formerly {{ font-size: 11px; color: #aaa; margin-top: 1px; font-style: italic; }}
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

  /* ── Topic profile ── */
  .topic-profile {{ padding: 10px 18px; border-top: 1px solid #f3f4f6; }}
  .topic-pills {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .topic-pill {{ font-size: 11px; padding: 3px 10px; border-radius: 12px; background: #f3f4f6; color: #555; font-weight: 500; white-space: nowrap; }}
  .topic-pct {{ color: #999; font-weight: 400; margin-left: 2px; }}

  /* ── Year range slider (custom) ── */
  .year-range-section {{ padding: 14px 18px; border-top: 1px solid #f3f4f6; }}
  .range-slider-wrap {{ display: flex; align-items: center; gap: 12px; margin-top: 8px; }}
  .range-label {{ font-size: 12px; font-weight: 700; color: #1a1a1a; min-width: 34px; font-variant-numeric: tabular-nums; }}
  .range-label-max {{ text-align: right; }}
  .range-track {{ position: relative; flex: 1; height: 32px; cursor: pointer; user-select: none; -webkit-user-select: none; touch-action: none; }}
  .range-rail {{ position: absolute; top: 13px; left: 0; right: 0; height: 6px; background: #e5e7eb; border-radius: 3px; }}
  .range-fill {{ position: absolute; top: 13px; height: 6px; background: linear-gradient(to right, #f97316, #6b7280, #3b82f6); border-radius: 3px; pointer-events: none; }}
  .range-thumb {{ position: absolute; top: 6px; width: 20px; height: 20px; border-radius: 50%; background: #fff; border: 2px solid #1a1a1a; box-shadow: 0 1px 4px rgba(0,0,0,0.15); cursor: grab; transform: translateX(-50%); z-index: 2; transition: box-shadow 0.15s; }}
  .range-thumb:hover {{ box-shadow: 0 2px 8px rgba(0,0,0,0.25); }}
  .range-thumb.dragging {{ cursor: grabbing; box-shadow: 0 2px 10px rgba(0,0,0,0.3); background: #f9fafb; }}
  .range-info {{ font-size: 11px; color: #888; margin-top: 6px; display: flex; align-items: center; gap: 6px; }}
  .range-govt-badge {{ display: inline-block; font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 3px; }}
  .range-govt-labour {{ background: #fef2f2; color: #dc2626; }}
  .range-govt-national {{ background: #eff6ff; color: #1d4ed8; }}
  .range-govt-mixed {{ background: #f3f4f6; color: #555; }}

  /* ── Methodology Section ── */
  .methodology-section {{ background: #fff; border-top: 1px solid #e5e7eb; margin-top: 48px; padding: 64px 0; }}
  .methodology-inner {{ max-width: 960px; margin: 0 auto; padding: 0 24px; }}
  .methodology-title {{ font-size: 28px; font-weight: 700; color: #1a1a1a; margin-bottom: 8px; }}
  .methodology-subtitle {{ font-size: 15px; color: #666; margin-bottom: 40px; }}

  /* ── Scoring scale ── */
  .scoring-scale {{ margin-bottom: 48px; position: relative; }}
  .scale-labels-top {{ display: flex; justify-content: space-between; font-size: 12px; font-weight: 600; color: #888; margin-bottom: 6px; padding: 0 2px; }}
  .scale-track {{ position: relative; height: 16px; border-radius: 8px; overflow: hidden; }}
  .scale-gradient {{ width: 100%; height: 100%; background: linear-gradient(to right, #dc2626, #f97316 25%, #d1d5db 50%, #3b82f6 75%, #1d4ed8); border-radius: 8px; }}
  .scale-divider {{ position: absolute; top: 0; width: 2px; height: 100%; background: rgba(255,255,255,0.6); transform: translateX(-50%); }}
  .scale-bucket-labels {{ position: relative; height: 28px; margin-top: 8px; }}
  .scale-bucket {{ position: absolute; transform: translateX(-50%); font-size: 13px; font-weight: 600; white-space: nowrap; }}

  @media (max-width: 768px) {{
    .scale-labels-top {{ font-size: 10px; }}
    .scale-bucket {{ font-size: 11px; }}
  }}
  @media (max-width: 480px) {{
    .scale-bucket {{ font-size: 9px; }}
    .scale-labels-top {{ font-size: 9px; }}
  }}

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

<header>
  <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAABXgAAAIwCAIAAACzxZTvAAAABmJLR0QA/wD/AP+gvaeTAAAgAElEQVR4nOzdZ1gc19nG8ZnZ2aVXoQISQoWmjnrvAoFAErCyHfcax3GJE5fETuK4xrFjJ66x49hxr3EVoIa6rN4bEgg1hCpFAgECdmd33g9O8iaOZQuYPdv+vyuflEs+o53dmXPuec4zclzcYAkAAAAAAMAIirsPAAAAAAAA+A6CBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAAYBiCBgAAAAAA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" alt="Bias — Media Bias Scorer">
  <a href="#methodology" class="how-we-score" style="color:#999;text-decoration:none">How We Score →</a>
</header>

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

    <!-- Scoring scale -->
    <div class="scoring-scale">
      <div class="scale-labels-top">
        <span>-1.0</span>
        <span>-0.6</span>
        <span>-0.2</span>
        <span>0</span>
        <span>+0.2</span>
        <span>+0.6</span>
        <span>+1.0</span>
      </div>
      <div class="scale-track">
        <div class="scale-gradient"></div>
        <div class="scale-divider" style="left:20%"></div>
        <div class="scale-divider" style="left:40%"></div>
        <div class="scale-divider" style="left:50%"></div>
        <div class="scale-divider" style="left:60%"></div>
        <div class="scale-divider" style="left:80%"></div>
      </div>
      <div class="scale-bucket-labels">
        <span class="scale-bucket" style="left:10%;color:#dc2626">Left</span>
        <span class="scale-bucket" style="left:30%;color:#f97316">Centre-Left</span>
        <span class="scale-bucket" style="left:50%;color:#6b7280">Centre</span>
        <span class="scale-bucket" style="left:70%;color:#3b82f6">Centre-Right</span>
        <span class="scale-bucket" style="left:90%;color:#1d4ed8">Right</span>
      </div>
    </div>

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
          <strong>Model:</strong> claude-sonnet-4-5 &nbsp;|&nbsp; <strong>Prompt version:</strong> v3-nuance
        </div>
      </div>

      <div class="methodology-card">
        <div class="methodology-step">4</div>
        <h3>Aggregation</h3>
        <p>Individual article scores are aggregated into five buckets for each journalist. The spectrum position shown on each card is the median score across all their scored articles. Confidence ratings (low/medium/high) reflect the number of articles scored.</p>
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
- Centre-left = Labour, Greens
- Centre-right = National, ACT
- Centre/populist = NZ First (has governed with both Labour and National)
- Te Pati Maori = indigenous rights focus; often aligns left on economic/social policy

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

<footer>
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" style="width:20px;height:20px;vertical-align:middle;margin-right:6px">
    <rect width="512" height="512" rx="80" fill="#1a1a2e"/>
    <text x="80" y="390" font-family="'Helvetica Neue', Helvetica, Arial, sans-serif" font-size="420" font-weight="700" fill="white">B</text>
    <line x1="340" y1="60" x2="280" y2="450" stroke="#e63946" stroke-width="36" stroke-linecap="round"/>
    <line x1="60" y1="440" x2="452" y2="440" stroke="#e63946" stroke-width="10" stroke-linecap="round"/>
  </svg>Bias · Open source at <a href="https://github.com/ferguswatts/Bias." style="color:#999">GitHub</a>
</footer>

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

/* ── Per-journalist year range slider (custom drag) ── */
function initRangeSliders() {{
  document.querySelectorAll('.year-range-section').forEach(section => {{
    const slug = section.dataset.slug;
    const rMin = parseInt(section.dataset.min);
    const rMax = parseInt(section.dataset.max);
    const track = document.getElementById(`range-track-${{slug}}`);
    const thumbMin = document.getElementById(`range-thumb-min-${{slug}}`);
    const thumbMax = document.getElementById(`range-thumb-max-${{slug}}`);
    if (!track || !thumbMin || !thumbMax) return;

    let curMin = rMin, curMax = rMax;

    function valToPct(v) {{ return ((v - rMin) / (rMax - rMin || 1)) * 100; }}
    function pctToVal(p) {{ return Math.round(rMin + (p / 100) * (rMax - rMin)); }}

    function render() {{
      const lp = valToPct(curMin), rp = valToPct(curMax);
      thumbMin.style.left = lp + '%';
      thumbMax.style.left = rp + '%';
      const fill = document.getElementById(`range-fill-${{slug}}`);
      if (fill) {{ fill.style.left = lp + '%'; fill.style.width = (rp - lp) + '%'; }}
      section.querySelector('.range-label-min').textContent = curMin;
      section.querySelector('.range-label-max').textContent = curMax;
      // When at same position, put min on top if at right edge, max on top if at left edge
      if (curMin === curMax) {{
        if (curMin === rMax) {{ thumbMin.style.zIndex = 3; thumbMax.style.zIndex = 2; }}
        else {{ thumbMin.style.zIndex = 2; thumbMax.style.zIndex = 3; }}
      }} else {{
        thumbMin.style.zIndex = 2; thumbMax.style.zIndex = 2;
      }}
      updateDisplay(slug, curMin, curMax);
    }}

    function startDrag(thumb, isMin) {{
      return function(e) {{
        e.preventDefault();
        thumb.classList.add('dragging');
        const rect = track.getBoundingClientRect();

        function onMove(e2) {{
          const x = (e2.touches ? e2.touches[0].clientX : e2.clientX) - rect.left;
          const pct = Math.max(0, Math.min(100, (x / rect.width) * 100));
          const val = pctToVal(pct);
          if (isMin) {{ curMin = Math.min(val, curMax); }}
          else {{ curMax = Math.max(val, curMin); }}
          render();
        }}

        function onUp() {{
          thumb.classList.remove('dragging');
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          document.removeEventListener('touchmove', onMove);
          document.removeEventListener('touchend', onUp);
        }}

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.addEventListener('touchmove', onMove, {{passive: false}});
        document.addEventListener('touchend', onUp);
      }};
    }}

    // When thumbs overlap, pick the right one based on click position relative to edges
    function pickThumb(e) {{
      const x = (e.touches ? e.touches[0].clientX : e.clientX);
      const rect = track.getBoundingClientRect();
      const pct = ((x - rect.left) / rect.width) * 100;
      const minPct = valToPct(curMin);
      const maxPct = valToPct(curMax);

      // If thumbs are at same position, pick based on which edge is closer
      if (Math.abs(minPct - maxPct) < 2) {{
        // If near the left end, move min; if near right end, move max
        if (pct <= minPct) return [thumbMin, true];
        return [thumbMax, false];
      }}

      // Otherwise pick the closer thumb
      if (Math.abs(pct - minPct) <= Math.abs(pct - maxPct)) return [thumbMin, true];
      return [thumbMax, false];
    }}

    thumbMin.addEventListener('mousedown', startDrag(thumbMin, true));
    thumbMin.addEventListener('touchstart', startDrag(thumbMin, true), {{passive: false}});
    thumbMax.addEventListener('mousedown', startDrag(thumbMax, false));
    thumbMax.addEventListener('touchstart', startDrag(thumbMax, false), {{passive: false}});

    // Also allow clicking on the track itself to grab the nearest thumb
    track.addEventListener('mousedown', function(e) {{
      if (e.target === thumbMin || e.target === thumbMax) return;
      const [thumb, isMin] = pickThumb(e);
      startDrag(thumb, isMin)(e);
    }});
    track.addEventListener('touchstart', function(e) {{
      if (e.target === thumbMin || e.target === thumbMax) return;
      const [thumb, isMin] = pickThumb(e);
      startDrag(thumb, isMin)(e);
    }}, {{passive: false}});
  }});
}}

function updateDisplay(slug, minVal, maxVal) {{
  const card = document.querySelector(`.journalist-card[data-years]`);
  const section = document.querySelector(`.year-range-section[data-slug="${{slug}}"]`);
  if (!section) return;

  const distSection = document.getElementById(`dist-section-${{slug}}`);
  const marker = section.closest('.journalist-card').querySelector('.spectrum-marker');
  const leanEl = section.closest('.journalist-card').querySelector('.lean-text');
  const infoEl = document.getElementById(`range-info-${{slug}}`);

  // Government badge
  let govBadge = '';
  if (minVal >= 2023) govBadge = '<span class="range-govt-badge range-govt-national">National govt</span>';
  else if (maxVal <= 2017) govBadge = '<span class="range-govt-badge range-govt-national">National govt</span>';
  else if (minVal >= 2017 && maxVal <= 2023) govBadge = '<span class="range-govt-badge range-govt-labour">Labour govt</span>';
  else govBadge = '<span class="range-govt-badge range-govt-mixed">Mixed</span>';

  if (!distSection) return;
  const allArticles = JSON.parse(distSection.dataset.articles || '[]');
  const filtered = allArticles.filter(a => a.y >= minVal && a.y <= maxVal);
  const buckets = {{'left': 0, 'centre-left': 0, 'centre': 0, 'centre-right': 0, 'right': 0}};
  let filteredScores = [];
  filtered.forEach(a => {{ if (buckets.hasOwnProperty(a.b)) buckets[a.b]++; filteredScores.push(a.s); }});
  const total = filtered.length;

  ['left', 'centre_left', 'centre', 'centre_right', 'right'].forEach(bk => {{
    const bucket = bk.replace('_', '-');
    const count = buckets[bucket] || 0;
    const pct = total > 0 ? Math.round((count / total) * 100) : 0;
    const bar = document.getElementById(`dist-bar-${{slug}}-${{bk}}`);
    const countEl = document.getElementById(`dist-count-${{slug}}-${{bk}}`);
    if (bar) {{ bar.style.width = pct + '%'; bar.style.transition = 'width 0.2s ease'; }}
    if (countEl) countEl.innerHTML = `${{count}} <span class="dist-pct">(${{pct}}%)</span>`;
  }});

  filteredScores.sort((a, b) => a - b);
  const n = filteredScores.length;
  const median = n > 0 ? (n % 2 === 1 ? filteredScores[Math.floor(n/2)] : (filteredScores[n/2-1] + filteredScores[n/2]) / 2) : 0;

  if (n > 0 && marker && leanEl) {{
    marker.style.left = (((median + 1) / 2) * 100).toFixed(1) + '%';
    marker.style.transition = 'left 0.2s ease';
    const pct = Math.abs(Math.round(median * 100));
    if (pct <= 2) {{ leanEl.textContent = 'Centre'; leanEl.style.color = '#6b7280'; }}
    else if (median < 0) {{ leanEl.textContent = pct + '% left leaning'; leanEl.style.color = '#d97706'; }}
    else {{ leanEl.textContent = pct + '% right leaning'; leanEl.style.color = '#3b82f6'; }}
    if (infoEl) infoEl.innerHTML = `${{minVal}}–${{maxVal}} ${{govBadge}} · ${{total}} articles`;
  }} else {{
    if (leanEl) {{ leanEl.textContent = 'No data'; leanEl.style.color = '#ccc'; }}
    if (infoEl) infoEl.innerHTML = `${{minVal}}–${{maxVal}} · No articles`;
  }}
}}

window.addEventListener('DOMContentLoaded', initRangeSliders);

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
