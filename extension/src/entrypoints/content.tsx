/**
 * Content script — injected into NZ news sites.
 * Detects journalist bylines and shows hover cards.
 */

import { createRoot } from "react-dom/client";
import { detectByline } from "../lib/detect";
import { matchJournalist, type DataFile } from "../lib/match";
import { loadData } from "../lib/data";
import { HoverCard } from "../components/HoverCard";

// NZ news site domains where the extension activates
const NZ_NEWS_DOMAINS = [
  "nzherald.co.nz",
  "stuff.co.nz",
  "rnz.co.nz",
  "1news.co.nz",
  "newsroom.co.nz",
  "thespinoff.co.nz",
  "interest.co.nz",
];

export default defineContentScript({
  matches: NZ_NEWS_DOMAINS.map((d) => `*://*.${d}/*`),
  cssInjectionMode: "ui",

  async main(ctx) {
    const hostname = window.location.hostname.replace("www.", "");
    if (!NZ_NEWS_DOMAINS.some((d) => hostname.includes(d))) return;

    // Load journalist data
    let data: DataFile;
    try {
      data = await loadData();
    } catch (e) {
      console.error("[Byline Card] Failed to load data:", e);
      return;
    }

    // Detect byline
    const byline = detectByline(document, data.sites || {});
    if (!byline) return;

    // Match journalist
    const match = matchJournalist(byline.name, byline.outlet, data);
    if (!match) return;

    // Find the byline element to anchor the card
    const bylineEl = findBylineElement(byline.name);
    if (!bylineEl) return;

    // Create the hover card UI
    let cardContainer: HTMLDivElement | null = null;
    let root: ReturnType<typeof createRoot> | null = null;
    let hoverTimeout: ReturnType<typeof setTimeout> | null = null;
    let isCardHovered = false;
    let isPinned = false;

    // Add visual indicator to byline
    bylineEl.style.borderBottom = "2px dotted #3b82f6";
    bylineEl.style.cursor = "pointer";
    bylineEl.title = "Byline Card available — hover to see journalist profile";

    function showCard() {
      if (cardContainer) return;

      cardContainer = document.createElement("div");
      cardContainer.style.position = "absolute";
      cardContainer.style.zIndex = "2147483647";

      // Position below the byline element
      const rect = bylineEl!.getBoundingClientRect();
      const scrollTop = window.scrollY;
      const scrollLeft = window.scrollX;

      cardContainer.style.left = `${rect.left + scrollLeft}px`;

      // Flip above if too close to bottom
      const spaceBelow = window.innerHeight - rect.bottom;
      if (spaceBelow < 300) {
        cardContainer.style.bottom = `${window.innerHeight - rect.top - scrollTop + 8}px`;
      } else {
        cardContainer.style.top = `${rect.bottom + scrollTop + 8}px`;
      }

      document.body.appendChild(cardContainer);

      // Card hover tracking
      cardContainer.addEventListener("mouseenter", () => {
        isCardHovered = true;
        if (hoverTimeout) {
          clearTimeout(hoverTimeout);
          hoverTimeout = null;
        }
      });

      cardContainer.addEventListener("mouseleave", () => {
        isCardHovered = false;
        if (!isPinned) {
          scheduleHide();
        }
      });

      // Render React component
      const shadow = cardContainer.attachShadow({ mode: "open" });
      const mountPoint = document.createElement("div");
      shadow.appendChild(mountPoint);

      root = createRoot(mountPoint);
      root.render(
        <HoverCard
          journalist={match!.journalist}
          version={data.version}
          onClose={hideCard}
        />,
      );
    }

    function hideCard() {
      if (isPinned) return;
      if (root) {
        root.unmount();
        root = null;
      }
      if (cardContainer) {
        cardContainer.remove();
        cardContainer = null;
      }
      isCardHovered = false;
    }

    function scheduleHide() {
      if (hoverTimeout) clearTimeout(hoverTimeout);
      hoverTimeout = setTimeout(() => {
        if (!isCardHovered && !isPinned) {
          hideCard();
        }
      }, 200);
    }

    // Hover behavior: 300ms delay to show, stays when mouse enters card
    let showTimeout: ReturnType<typeof setTimeout> | null = null;

    bylineEl.addEventListener("mouseenter", () => {
      showTimeout = setTimeout(showCard, 300);
    });

    bylineEl.addEventListener("mouseleave", () => {
      if (showTimeout) {
        clearTimeout(showTimeout);
        showTimeout = null;
      }
      scheduleHide();
    });

    // Keyboard: focus triggers card
    bylineEl.setAttribute("tabindex", "0");
    bylineEl.addEventListener("focus", showCard);
    bylineEl.addEventListener("blur", () => {
      setTimeout(() => {
        if (!isCardHovered && !isPinned) hideCard();
      }, 200);
    });

    // Click outside to dismiss pinned card
    document.addEventListener("click", (e) => {
      if (isPinned && cardContainer && !cardContainer.contains(e.target as Node)) {
        isPinned = false;
        hideCard();
      }
    });

    // Escape to dismiss
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && cardContainer) {
        isPinned = false;
        hideCard();
      }
    });
  },
});

/** Find the DOM element containing the journalist's byline name. */
function findBylineElement(name: string): HTMLElement | null {
  // Try JSON-LD author link first
  const authorLinks = document.querySelectorAll('a[rel="author"], .author-name, .byline a');
  for (const el of authorLinks) {
    if (el.textContent?.toLowerCase().includes(name.toLowerCase())) {
      return el as HTMLElement;
    }
  }

  // Try any element containing the author name near the top of the article
  const candidates = document.querySelectorAll(
    "article header *, .article-header *, .story-header *, .byline, [class*='author'], [class*='byline']",
  );
  for (const el of candidates) {
    const text = el.textContent?.trim() || "";
    if (text.toLowerCase().includes(name.toLowerCase()) && text.length < 200) {
      return el as HTMLElement;
    }
  }

  return null;
}
