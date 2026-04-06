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

      console.log(`[Bias] Byline detected: ${byline.name} @ ${byline.outlet}`);

      const match = matchJournalist(byline.name, byline.outlet, data);
      if (!match) {
        console.log(`[Bias] No match for: ${byline.name}`);
        return;
      }

      console.log(`[Bias] Matched: ${match.journalist.name}`);

      const bylineEl = findBylineElement(byline.name);
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

  const bucketColors: Record<string, string> = {
    left: "#ef4444", centre_left: "#f97316", centre: "#6b7280",
    centre_right: "#3b82f6", right: "#1d4ed8",
  };

  const barRow = (label: string, pct: number, key: string) =>
    `<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">
      <span style="width:80px;font-size:11px;font-weight:500;color:#555;text-align:right;flex-shrink:0">${label}</span>
      <div style="flex:1;height:6px;background:#f3f4f6;border-radius:3px;overflow:hidden">
        <div style="width:${pct}%;height:100%;background:${bucketColors[key]};border-radius:3px;min-width:2px"></div>
      </div>
      <span style="width:52px;font-size:11px;color:#555;flex-shrink:0">${pct}%</span>
    </div>`;

  // Spectrum position & lean text
  const spectrumPos = ((score + 1) / 2) * 100;
  const leanPct = Math.abs(Math.round(score * 100));
  let leanText: string;
  let leanColor: string;
  if (leanPct <= 2) {
    leanText = "Centre";
    leanColor = "#6b7280";
  } else if (score < 0) {
    leanText = `${leanPct}% left leaning`;
    leanColor = "#d97706";
  } else {
    leanText = `${leanPct}% right leaning`;
    leanColor = "#3b82f6";
  }

  // Avatar
  const initials = j.name.split(" ").map(p => p[0]).join("").slice(0, 2).toUpperCase();
  const hue = j.name.split("").reduce((s, c) => s + c.charCodeAt(0), 0) % 360;
  const avatarHTML = j.photo_url
    ? `<img src="${j.photo_url}" alt="${j.name}" style="width:42px;height:42px;border-radius:50%;object-fit:cover;flex-shrink:0">`
    : `<div style="width:42px;height:42px;border-radius:50%;background:hsl(${hue},45%,62%);display:flex;align-items:center;justify-content:center;flex-shrink:0;color:#fff;font-weight:600;font-size:15px">${initials}</div>`;

  const connectionsHTML = (j.connections.length > 0 || j.facts.length > 0)
    ? `<div style="padding:10px 16px;border-top:1px solid #f3f4f6">
        <div style="font-size:11px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px">Connections</div>
        ${j.connections.map((c) =>
          `<div style="font-size:12px;color:#444;margin-bottom:4px;line-height:1.4">
            <span style="color:#888;font-size:11px;font-weight:500;text-transform:capitalize">${c.type}</span>
            <span style="font-weight:600;color:#1a1a1a">${c.target}</span>${c.role ? ` — ${c.role}` : ""}
            ${c.source ? `<a href="${c.source}" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none;font-size:11px;margin-left:6px">source</a>` : ""}
          </div>`
        ).join("")}
        ${j.facts.length > 0 ? `
          <div style="font-size:11px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.4px;margin:8px 0 6px">Key facts</div>
          ${j.facts.map((f) =>
            `<div style="font-size:12px;color:#444;margin-bottom:4px;line-height:1.4">
              ${f.text}
              ${f.source ? `<a href="${f.source}" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none;font-size:11px;margin-left:6px">source</a>` : ""}
            </div>`
          ).join("")}
        ` : ""}
      </div>`
    : "";

  return `
    <style>
      :host { all: initial; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
      @keyframes fadein { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
      @media (prefers-reduced-motion: reduce) { @keyframes fadein { from { opacity:1; } to { opacity:1; } } }
      a:hover { text-decoration: underline !important; }
    </style>
    <div role="dialog" aria-label="Journalist profile: ${j.name}" style="
      width:380px;max-height:520px;overflow-y:auto;background:#fff;border:1px solid #e5e7eb;
      border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.08),0 1px 3px rgba(0,0,0,0.04);
      animation:fadein 150ms ease-out;
    ">
      <!-- Header -->
      <div style="padding:14px 16px;display:flex;align-items:center;gap:12px">
        ${avatarHTML}
        <div style="flex:1;min-width:0">
          <div style="font-size:15px;font-weight:600;color:#1a1a1a">${j.name}</div>
          <div style="font-size:12px;color:#888;margin-top:2px">${j.outlet} · ${j.beat || "Politics"}</div>
          ${j.formerly ? `<div style="font-size:11px;color:#aaa;margin-top:1px">Formerly: ${j.formerly}</div>` : ""}
        </div>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" style="width:28px;height:28px;flex-shrink:0">
          <rect width="512" height="512" rx="80" fill="#1a1a2e"/>
          <text x="80" y="390" font-family="'Helvetica Neue', Helvetica, Arial, sans-serif" font-size="420" font-weight="700" fill="white">B</text>
          <line x1="340" y1="60" x2="280" y2="450" stroke="#e63946" stroke-width="36" stroke-linecap="round"/>
          <line x1="60" y1="440" x2="452" y2="440" stroke="#e63946" stroke-width="10" stroke-linecap="round"/>
        </svg>
      </div>

      <!-- Spectrum bar -->
      <div style="padding:4px 16px 10px;display:flex;align-items:center;gap:8px">
        <span style="font-size:10px;font-weight:700;color:#dc2626">Left</span>
        <div style="flex:1;position:relative;height:24px">
          <div style="position:absolute;top:8px;left:0;right:0;height:8px;border-radius:4px;background:linear-gradient(to right,#dc2626,#f97316 25%,#d1d5db 50%,#3b82f6 75%,#1d4ed8)"></div>
          <div style="position:absolute;top:1px;width:4px;height:22px;border-radius:2px;background:#1a1a1a;box-shadow:0 1px 4px rgba(0,0,0,0.4);left:${spectrumPos.toFixed(1)}%;transform:translateX(-50%)"></div>
        </div>
        <span style="font-size:10px;font-weight:700;color:#1d4ed8">Right</span>
        <span style="font-size:12px;font-weight:600;color:${leanColor};white-space:nowrap;margin-left:4px">${leanText}</span>
      </div>

      <!-- Article count -->
      <div style="padding:0 16px 10px;font-size:12px;color:#888">${j.article_count} articles</div>

      <!-- Distribution chart -->
      <div style="padding:10px 16px;border-top:1px solid #f3f4f6">
        ${barRow("Left", dist.left, "left")}
        ${barRow("Centre-Left", dist.centre_left, "centre_left")}
        ${barRow("Centre", dist.centre, "centre")}
        ${barRow("Centre-Right", dist.centre_right, "centre_right")}
        ${barRow("Right", dist.right, "right")}
      </div>

      <!-- Connections -->
      ${connectionsHTML}

      <!-- Background -->
      ${j.bio ? `
      <div style="padding:10px 16px;border-top:1px solid #f3f4f6">
        <div style="font-size:11px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px">Background</div>
        <div style="font-size:12px;color:#555;line-height:1.6">${j.bio.length > 300 ? j.bio.slice(0, 300) + "…" : j.bio}</div>
      </div>
      ` : ""}

      <!-- Footer -->
      <div style="padding:8px 16px;display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#999;border-top:1px solid #f3f4f6">
        <span>AI-scored · Updated ${version}</span>
        <div style="display:flex;gap:12px">
          <a href="https://ferguswatts.github.io/Bias./#${slug}" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none">View articles</a>
          <a href="https://ferguswatts.github.io/Bias./" target="_blank" rel="noopener" style="color:#2563eb;text-decoration:none">All journalists</a>
        </div>
      </div>
    </div>
  `;
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
