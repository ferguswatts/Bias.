/**
 * Content script — injected into NZ news sites.
 * Detects journalist bylines and shows hover cards.
 * Pure DOM rendering — no React dependency (keeps bundle small).
 */

import { detectByline } from "../src/lib/detect";
import { matchJournalist, type JournalistData, type DataFile } from "../src/lib/match";
import { loadData } from "../src/lib/data";

const NZ_NEWS_DOMAINS = [
  "nzherald.co.nz",
  "stuff.co.nz",
  "thepost.co.nz",
  "rnz.co.nz",
  "1news.co.nz",
  "tvnz.co.nz",
  "newsroom.co.nz",
  "thespinoff.co.nz",
  "interest.co.nz",
];

export default defineContentScript({
  matches: [
    "*://*.nzherald.co.nz/*",
    "*://*.stuff.co.nz/*",
    "*://*.thepost.co.nz/*",
    "*://*.rnz.co.nz/*",
    "*://*.1news.co.nz/*",
    "*://*.tvnz.co.nz/*",
    "*://*.newsroom.co.nz/*",
    "*://*.thespinoff.co.nz/*",
    "*://*.interest.co.nz/*",
  ],
  runAt: "document_idle",

  async main() {
    const hostname = window.location.hostname.replace("www.", "");
    if (!NZ_NEWS_DOMAINS.some((d) => hostname.includes(d))) return;

    let data: DataFile;
    try {
      data = await loadData();
    } catch (e) {
      console.error("[Bias] Failed to load data:", e);
      return;
    }

    if (!data.journalists || Object.keys(data.journalists).length === 0) {
      console.log("[Bias] No journalist data loaded.");
      return;
    }

    // Track what we've already set up to avoid duplicates
    let currentBylineEl: HTMLElement | null = null;

    async function scanForByline() {
      let byline = detectByline(document, data.sites || {});
      if (!byline) {
        byline = await waitForByline(data.sites || {}, 5000);
        if (!byline) return;
      }

      console.log(`[Bias] Byline detected: ${byline.names.join(", ")} @ ${byline.outlet}`);

      // Try to match any of the byline authors against our database
      let match: ReturnType<typeof matchJournalist> = null;
      let matchedName = byline.name;
      for (const name of byline.names) {
        match = matchJournalist(name, byline.outlet, data);
        if (match) {
          matchedName = name;
          break;
        }
      }

      if (!match) {
        console.log(`[Bias] No match for: ${byline.names.join(", ")}`);
        return;
      }

      console.log(`[Bias] Matched: ${match.journalist.name}`);

      const bylineEl = findBylineElement(matchedName);
      if (!bylineEl) {
        console.log("[Bias] Could not find byline DOM element.");
        return;
      }

      // Skip if we already set up this exact element
      if (bylineEl === currentBylineEl) return;
      currentBylineEl = bylineEl;

      // Add visual indicator
      bylineEl.style.borderBottom = "2px dotted #3b82f6";
      bylineEl.style.cursor = "pointer";
      bylineEl.title = "Bias — hover to see journalist profile";

      let cardEl: HTMLDivElement | null = null;
      let isCardHovered = false;
      let isPinned = false;
      let hoverTimeout: ReturnType<typeof setTimeout> | null = null;

      function showCard() {
        if (cardEl) return;

        cardEl = document.createElement("div");
        cardEl.style.position = "absolute";
        cardEl.style.zIndex = "2147483647";

        const rect = bylineEl!.getBoundingClientRect();
        cardEl.style.left = `${rect.left + window.scrollX}px`;

        if (window.innerHeight - rect.bottom < 400) {
          cardEl.style.top = `${rect.top + window.scrollY - 8}px`;
          cardEl.style.transform = "translateY(-100%)";
        } else {
          cardEl.style.top = `${rect.bottom + window.scrollY + 8}px`;
        }

        const shadow = cardEl.attachShadow({ mode: "open" });
        shadow.innerHTML = buildCardHTML(match!.slug, match!.journalist, data.version);

        // Wire up year slider if the journalist has year data
        const mj = match!.journalist;
        if (mj.articles_by_year && mj.articles_by_year.length > 0 && mj.years) {
          const yearKeys = Object.keys(mj.years).map(Number).sort();
          if (yearKeys.length > 1) {
            setupYearSlider(shadow, mj.articles_by_year, yearKeys[0], yearKeys[yearKeys.length - 1]);
          }
        }

        document.body.appendChild(cardEl);

        cardEl.addEventListener("mouseenter", () => {
          isCardHovered = true;
          if (hoverTimeout) { clearTimeout(hoverTimeout); hoverTimeout = null; }
        });
        cardEl.addEventListener("mouseleave", () => {
          isCardHovered = false;
          if (!isPinned) scheduleHide();
        });
        cardEl.addEventListener("click", () => { isPinned = true; });
      }

      function hideCard() {
        if (isPinned) return;
        if (cardEl) { cardEl.remove(); cardEl = null; }
        isCardHovered = false;
      }

      function scheduleHide() {
        if (hoverTimeout) clearTimeout(hoverTimeout);
        hoverTimeout = setTimeout(() => {
          if (!isCardHovered && !isPinned) hideCard();
        }, 200);
      }

      let showTimeout: ReturnType<typeof setTimeout> | null = null;
      bylineEl.addEventListener("mouseenter", () => {
        showTimeout = setTimeout(showCard, 300);
      });
      bylineEl.addEventListener("mouseleave", () => {
        if (showTimeout) { clearTimeout(showTimeout); showTimeout = null; }
        scheduleHide();
      });

      bylineEl.setAttribute("tabindex", "0");
      bylineEl.addEventListener("focus", showCard);
      bylineEl.addEventListener("blur", () => {
        setTimeout(() => { if (!isCardHovered && !isPinned) hideCard(); }, 200);
      });

      document.addEventListener("click", (e) => {
        if (isPinned && cardEl && !cardEl.contains(e.target as Node)) {
          isPinned = false; hideCard();
        }
      });
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && cardEl) { isPinned = false; hideCard(); }
      });
    }

    // Initial scan
    await scanForByline();

    // Re-scan on soft navigation (SPA sites like Stuff)
    let lastUrl = location.href;
    const observer = new MutationObserver(() => {
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        currentBylineEl = null;
        console.log("[Bias] URL changed (soft navigation), re-scanning...");
        scanForByline();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  },
});

function waitForByline(
  siteConfig: Record<string, { selectors: { byline: string } }>,
  timeout: number,
): Promise<import("../src/lib/detect").BylineResult | null> {
  return new Promise((resolve) => {
    const check = () => detectByline(document, siteConfig);
    const interval = setInterval(() => {
      const result = check();
      if (result) {
        clearInterval(interval);
        resolve(result);
      }
    }, 300);
    setTimeout(() => {
      clearInterval(interval);
      resolve(null);
    }, timeout);
  });
}

function buildCardHTML(slug: string, j: JournalistData, version: string): string {
  const dist = j.distribution;
  const score = j.bias_score || 0;
  const bc: Record<string, string> = {
    left: "#ef4444", centre_left: "#f97316", centre: "#6b7280",
    centre_right: "#3b82f6", right: "#1d4ed8",
  };

  const barRow = (label: string, pct: number, key: string, id: string) =>
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
      <span style="width:76px;font-size:11px;font-weight:500;color:#555;text-align:right;flex-shrink:0">${label}</span>
      <div style="flex:1;height:5px;background:#f3f4f6;border-radius:3px;overflow:hidden">
        <div id="${id}" style="width:${pct}%;height:100%;background:${bc[key]};border-radius:3px;min-width:1px;transition:width 0.2s ease"></div>
      </div>
      <span id="${id}-n" style="width:40px;font-size:10px;color:#888;flex-shrink:0">${pct}%</span>
    </div>`;

  // Spectrum
  const specPos = ((score + 1) / 2) * 100;
  const leanPct = Math.abs(Math.round(score * 100));
  let leanText = "Centre", leanColor = "#6b7280";
  if (leanPct > 2) {
    if (score < 0) { leanText = `${leanPct}% left leaning`; leanColor = "#d97706"; }
    else { leanText = `${leanPct}% right leaning`; leanColor = "#3b82f6"; }
  }

  // Avatar
  const initials = j.name.split(" ").map(p => p[0]).join("").slice(0, 2).toUpperCase();
  const hue = j.name.split("").reduce((s, c) => s + c.charCodeAt(0), 0) % 360;
  const avatar = j.photo_url
    ? `<img src="${j.photo_url}" alt="" style="width:40px;height:40px;border-radius:50%;object-fit:cover;flex-shrink:0">`
    : `<div style="width:40px;height:40px;border-radius:50%;background:hsl(${hue},45%,62%);display:flex;align-items:center;justify-content:center;flex-shrink:0;color:#fff;font-weight:600;font-size:14px">${initials}</div>`;

  // Social links
  const socialLinks: string[] = [];
  if (j.social?.twitter) socialLinks.push(`<a href="${j.social.twitter}" target="_blank" rel="noopener" style="font-size:10px;padding:2px 8px;border-radius:4px;background:#1a1a1a;color:#fff;text-decoration:none;font-weight:600">X</a>`);
  if (j.social?.linkedin) socialLinks.push(`<a href="${j.social.linkedin}" target="_blank" rel="noopener" style="font-size:10px;padding:2px 8px;border-radius:4px;background:#0077b5;color:#fff;text-decoration:none;font-weight:600">LinkedIn</a>`);
  const socialHTML = socialLinks.length ? `<div style="display:flex;gap:4px;margin-top:4px">${socialLinks.join("")}</div>` : "";

  // Topic pills
  let topicHTML = "";
  if (j.topics && Object.keys(j.topics).length > 0) {
    const pills = Object.entries(j.topics)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([t, p]) => `<span style="font-size:10px;padding:2px 8px;border-radius:10px;background:#f3f4f6;color:#555;white-space:nowrap">${t.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase())} ${p}%</span>`)
      .join("");
    topicHTML = `<div style="padding:8px 16px;border-top:1px solid #f3f4f6">
      <div style="font-size:10px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.3px;margin-bottom:5px">Topic Profile</div>
      <div style="display:flex;flex-wrap:wrap;gap:4px">${pills}</div>
    </div>`;
  }

  // Year slider data
  const years = j.years || {};
  const yearKeys = Object.keys(years).map(Number).sort();
  const minYear = yearKeys[0] || 2020;
  const maxYear = yearKeys[yearKeys.length - 1] || 2026;
  const articlesData = JSON.stringify(j.articles_by_year || []);
  const hasYearRange = yearKeys.length > 1;

  const yearSliderHTML = hasYearRange ? `
    <div style="padding:10px 16px;border-top:1px solid #f3f4f6" id="yr-section">
      <div style="font-size:10px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.3px;margin-bottom:6px">Filter by period</div>
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:10px;color:#999">${minYear}</span>
        <div id="yr-track" style="flex:1;position:relative;height:28px;cursor:pointer;user-select:none;touch-action:none">
          <div style="position:absolute;top:12px;left:0;right:0;height:4px;background:#e5e7eb;border-radius:2px"></div>
          <div id="yr-fill" style="position:absolute;top:12px;height:4px;background:linear-gradient(to right,#f97316,#6b7280,#3b82f6);border-radius:2px;left:0;width:100%"></div>
          <div id="yr-tmin" style="position:absolute;top:2px;width:24px;height:24px;border-radius:12px;background:#1a1a1a;cursor:grab;transform:translateX(-50%);display:flex;align-items:center;justify-content:center;left:0%;z-index:2;box-shadow:0 1px 4px rgba(0,0,0,0.2)"><span style="font-size:8px;font-weight:700;color:#fff;pointer-events:none" id="yr-lmin">'${String(minYear).slice(-2)}</span></div>
          <div id="yr-tmax" style="position:absolute;top:2px;width:24px;height:24px;border-radius:12px;background:#1a1a1a;cursor:grab;transform:translateX(-50%);display:flex;align-items:center;justify-content:center;left:100%;z-index:2;box-shadow:0 1px 4px rgba(0,0,0,0.2)"><span style="font-size:8px;font-weight:700;color:#fff;pointer-events:none" id="yr-lmax">'${String(maxYear).slice(-2)}</span></div>
        </div>
        <span style="font-size:10px;color:#999">${maxYear}</span>
      </div>
      <div id="yr-info" style="font-size:10px;color:#888;margin-top:3px">All years · ${j.article_count} articles</div>
    </div>` : "";

  // Connections
  const connHTML = (j.connections.length > 0 || j.facts.length > 0) ? `
    <div style="padding:8px 16px;border-top:1px solid #f3f4f6">
      <div style="font-size:10px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.3px;margin-bottom:5px">Connections</div>
      ${socialHTML}
      ${j.connections.map(c => `<div style="font-size:11px;color:#444;margin-bottom:3px;line-height:1.4">
        <span style="color:#888;font-size:10px;font-weight:500;text-transform:capitalize">${c.type}</span>
        <span style="font-weight:600;color:#1a1a1a">${c.target}</span>${c.role ? ` — ${c.role}` : ""}
        ${c.source ? `<a href="${c.source}" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none;font-size:10px;margin-left:4px">source</a>` : ""}
      </div>`).join("")}
      ${j.facts.length > 0 ? `
        <div style="font-size:10px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.3px;margin:6px 0 4px">Key facts</div>
        ${j.facts.map(f => `<div style="font-size:11px;color:#444;margin-bottom:3px;line-height:1.4">${f.text}
          ${f.source ? `<a href="${f.source}" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none;font-size:10px;margin-left:4px">source</a>` : ""}
        </div>`).join("")}` : ""}
    </div>` : "";

  return `
    <style>
      :host { all: initial; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
      @keyframes fadein { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
      @media (prefers-reduced-motion: reduce) { @keyframes fadein { from { opacity:1; } to { opacity:1; } } }
      a:hover { text-decoration: underline !important; }
      *::-webkit-scrollbar { width: 6px; }
      *::-webkit-scrollbar-track { background: transparent; }
      *::-webkit-scrollbar-thumb { background: #ddd; border-radius: 3px; }
    </style>
    <div role="dialog" aria-label="Journalist profile: ${j.name}" style="
      width:400px;background:#fff;border:1px solid #e5e7eb;
      border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,0.12),0 2px 8px rgba(0,0,0,0.06);
      animation:fadein 150ms ease-out;
    ">
      <!-- Header -->
      <div style="padding:14px 16px;display:flex;align-items:center;gap:10px">
        ${avatar}
        <div style="flex:1;min-width:0">
          <div style="font-size:14px;font-weight:600;color:#1a1a1a">${j.name}</div>
          <div style="font-size:11px;color:#888;margin-top:1px">${j.outlet} · ${j.beat || "Politics"}</div>
        </div>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 330 115" width="48" height="16" style="flex-shrink:0">
          <text font-family="'Helvetica Neue',Helvetica,Arial,sans-serif" font-size="100" font-weight="600" y="90">
            <tspan x="20" fill="#1a1a1a">B</tspan>
            <tspan fill="#e63946" font-style="italic">I</tspan>
            <tspan fill="#1a1a1a">AS</tspan>
          </text>
          <line x1="16" y1="102" x2="313" y2="102" stroke="#e63946" stroke-width="5" stroke-linecap="round"/>
        </svg>
      </div>

      <!-- Spectrum -->
      <div style="padding:2px 16px 8px;display:flex;align-items:center;gap:6px">
        <span style="font-size:9px;font-weight:700;color:#dc2626">Left</span>
        <div style="flex:1;position:relative;height:20px">
          <div style="position:absolute;top:7px;left:0;right:0;height:6px;border-radius:3px;background:linear-gradient(to right,#dc2626,#f97316 25%,#d1d5db 50%,#3b82f6 75%,#1d4ed8)"></div>
          <div id="spec-marker" style="position:absolute;top:1px;width:4px;height:18px;border-radius:2px;background:#1a1a1a;box-shadow:0 1px 4px rgba(0,0,0,0.4);left:${specPos.toFixed(1)}%;transform:translateX(-50%);transition:left 0.2s ease"></div>
        </div>
        <span style="font-size:9px;font-weight:700;color:#1d4ed8">Right</span>
        <span id="lean-text" style="font-size:11px;font-weight:600;color:${leanColor};white-space:nowrap;margin-left:2px">${leanText}</span>
      </div>

      <!-- Article count -->
      <div style="padding:0 16px 8px;font-size:11px;color:#888">${j.article_count} articles</div>

      <!-- Distribution -->
      <div style="padding:8px 16px;border-top:1px solid #f3f4f6" id="dist-section">
        ${barRow("Left", dist.left, "left", "db-l")}
        ${barRow("Centre-Left", dist.centre_left, "centre_left", "db-cl")}
        ${barRow("Centre", dist.centre, "centre", "db-c")}
        ${barRow("Centre-Right", dist.centre_right, "centre_right", "db-cr")}
        ${barRow("Right", dist.right, "right", "db-r")}
      </div>

      <!-- Year slider -->
      ${yearSliderHTML}

      <!-- Topics -->
      ${topicHTML}

      <!-- Connections -->
      ${connHTML}

      <!-- Bio -->
      ${j.bio ? `
      <div style="padding:8px 16px;border-top:1px solid #f3f4f6">
        <div style="font-size:10px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.3px;margin-bottom:4px">Background</div>
        <div style="font-size:11px;color:#555;line-height:1.5">${j.bio.length > 350 ? j.bio.slice(0, 350) + "…" : j.bio}</div>
      </div>` : ""}

      <!-- Footer -->
      <div style="padding:8px 16px;display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#999;border-top:1px solid #f3f4f6">
        <span>AI-scored · ${version}</span>
        <div style="display:flex;gap:10px">
          <a href="https://ferguswatts.github.io/Bias./#${slug}" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none">Full profile</a>
          <a href="https://ferguswatts.github.io/Bias./" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none">All journalists</a>
        </div>
      </div>
    </div>

    ${hasYearRange ? `<!-- slider-data:${articlesData.replace(/"/g, '&quot;')}:${minYear}:${maxYear} -->` : ""}
  `;
}

function setupYearSlider(root: ShadowRoot, articles: Array<{y:number;b:string;s:number}>, rMin: number, rMax: number) {
  const track = root.getElementById('yr-track');
  const fill = root.getElementById('yr-fill');
  const tmin = root.getElementById('yr-tmin');
  const tmax = root.getElementById('yr-tmax');
  const lmin = root.getElementById('yr-lmin');
  const lmax = root.getElementById('yr-lmax');
  const info = root.getElementById('yr-info');
  const marker = root.getElementById('spec-marker');
  const leanEl = root.getElementById('lean-text');
  if (!track || !tmin || !tmax) return;

  let curMin = rMin, curMax = rMax;
  const v2p = (v: number) => ((v - rMin) / (rMax - rMin || 1)) * 100;
  const p2v = (p: number) => Math.round(rMin + (p / 100) * (rMax - rMin));

  function render() {
    const lp = v2p(curMin), rp = v2p(curMax);
    tmin.style.left = lp + '%';
    tmax!.style.left = rp + '%';
    if (fill) { fill.style.left = lp + '%'; fill.style.width = (rp - lp) + '%'; }
    if (lmin) lmin.textContent = "'" + String(curMin).slice(-2);
    if (lmax) lmax.textContent = "'" + String(curMax).slice(-2);

    const f = articles.filter(a => a.y >= curMin && a.y <= curMax);
    const bk: Record<string, number> = {left:0,'centre-left':0,centre:0,'centre-right':0,right:0};
    const scores: number[] = [];
    f.forEach(a => { if (a.b in bk) bk[a.b]++; scores.push(a.s); });
    const total = f.length;

    ([['l','left'],['cl','centre-left'],['c','centre'],['cr','centre-right'],['r','right']] as const).forEach(([id, b]) => {
      const bar = root.getElementById('db-' + id);
      const num = root.getElementById('db-' + id + '-n');
      const count = bk[b] || 0;
      const pct = total > 0 ? Math.round((count/total)*100) : 0;
      if (bar) bar.style.width = pct + '%';
      if (num) num.textContent = pct + '%';
    });

    scores.sort((a,b) => a - b);
    const n = scores.length;
    const med = n > 0 ? (n%2===1 ? scores[Math.floor(n/2)] : (scores[Math.floor(n/2)-1]+scores[Math.floor(n/2)])/2) : 0;
    if (marker) marker.style.left = (((med+1)/2)*100).toFixed(1) + '%';
    if (leanEl) {
      const pct = Math.abs(Math.round(med * 100));
      if (pct <= 2) { leanEl.textContent = 'Centre'; leanEl.style.color = '#6b7280'; }
      else if (med < 0) { leanEl.textContent = pct + '% left leaning'; leanEl.style.color = '#d97706'; }
      else { leanEl.textContent = pct + '% right leaning'; leanEl.style.color = '#3b82f6'; }
    }

    let gov = '<span style="font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;background:#f3f4f6;color:#555">Mixed</span>';
    if (curMin >= 2023) gov = '<span style="font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;background:#eff6ff;color:#1d4ed8">National</span>';
    else if (curMax <= 2017) gov = '<span style="font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;background:#eff6ff;color:#1d4ed8">National</span>';
    else if (curMin >= 2017 && curMax <= 2023) gov = '<span style="font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;background:#fef2f2;color:#dc2626">Labour</span>';
    if (info) info.innerHTML = curMin + '–' + curMax + ' ' + gov + ' · ' + total + ' articles';
  }

  function drag(thumb: HTMLElement, isMin: boolean) {
    return function(e: MouseEvent | TouchEvent) {
      e.preventDefault();
      thumb.style.cursor = 'grabbing';
      const rect = track!.getBoundingClientRect();
      function onMove(e2: MouseEvent | TouchEvent) {
        const x = ('touches' in e2 ? e2.touches[0].clientX : e2.clientX) - rect.left;
        const pct = Math.max(0, Math.min(100, (x / rect.width) * 100));
        const val = p2v(pct);
        if (isMin) curMin = Math.min(val, curMax);
        else curMax = Math.max(val, curMin);
        render();
      }
      function onUp() {
        thumb.style.cursor = 'grab';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        document.removeEventListener('touchmove', onMove);
        document.removeEventListener('touchend', onUp);
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      document.addEventListener('touchmove', onMove, {passive: false});
      document.addEventListener('touchend', onUp);
    };
  }

  tmin.addEventListener('mousedown', drag(tmin, true));
  tmin.addEventListener('touchstart', drag(tmin, true), {passive: false});
  tmax.addEventListener('mousedown', drag(tmax, false));
  tmax.addEventListener('touchstart', drag(tmax, false), {passive: false});
  track.addEventListener('mousedown', (e) => {
    if (e.target === tmin || e.target === tmax) return;
    const x = e.clientX - track!.getBoundingClientRect().left;
    const pct = (x / track!.getBoundingClientRect().width) * 100;
    const minD = Math.abs(pct - v2p(curMin)), maxD = Math.abs(pct - v2p(curMax));
    drag(minD <= maxD ? tmin : tmax, minD <= maxD)(e);
  });
}


function findBylineElement(name: string): HTMLElement | null {
  const nameLower = name.toLowerCase();

  const authorLinks = document.querySelectorAll('a[rel="author"], .author-name, .byline a, a[href*="/authors/"], a[href*="/reporter/"]');
  for (const el of authorLinks) {
    if (el.textContent?.toLowerCase().includes(nameLower)) {
      return el as HTMLElement;
    }
  }

  const candidates = document.querySelectorAll(
    "article header *, .article-header *, .story-header *, .byline, [class*='author'], [class*='byline']",
  );
  for (const el of candidates) {
    const text = el.textContent?.trim() || "";
    if (text.toLowerCase().includes(nameLower) && text.length < 200) {
      return el as HTMLElement;
    }
  }

  return null;
}
