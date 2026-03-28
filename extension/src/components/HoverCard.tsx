import { type FC, useState } from "react";
import type { JournalistData } from "../lib/match";
import { DistributionBar } from "./DistributionBar";
import { ConnectionsList } from "./ConnectionsList";

const CONFIDENCE_COLORS: Record<string, { bg: string; text: string }> = {
  low: { bg: "rgba(245, 158, 11, 0.1)", text: "#b45309" },
  medium: { bg: "rgba(107, 114, 128, 0.1)", text: "#4b5563" },
  high: { bg: "rgba(16, 185, 129, 0.1)", text: "#047857" },
};

export const HoverCard: FC<{
  journalist: JournalistData;
  version: string;
  onClose: () => void;
}> = ({ journalist, version, onClose }) => {
  const [pinned, setPinned] = useState(false);
  const conf = CONFIDENCE_COLORS[journalist.confidence] || CONFIDENCE_COLORS.low;

  return (
    <div
      role="dialog"
      aria-label={`Journalist profile: ${journalist.name}`}
      onClick={(e) => {
        e.stopPropagation();
        setPinned(true);
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
      tabIndex={-1}
      style={{
        width: "340px",
        maxHeight: "480px",
        overflowY: "auto",
        backgroundColor: "#ffffff",
        border: "1px solid #e5e5e5",
        borderRadius: "8px",
        boxShadow: "0 4px 12px rgba(0,0,0,0.08), 0 1px 3px rgba(0,0,0,0.04)",
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
        zIndex: 2147483647,
        animation: "bylinecard-fadein 150ms ease-out",
      }}
    >
      {/* Header zone */}
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid #f0f0f0",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
          }}
        >
          <div>
            <div style={{ fontSize: "15px", fontWeight: 600, color: "#1a1a1a" }}>
              {journalist.name}
            </div>
            <div style={{ fontSize: "13px", color: "#666", marginTop: "2px" }}>
              {journalist.beat ? `${journalist.beat} · ` : ""}
              {journalist.outlet}
            </div>
          </div>
          <span
            style={{
              fontSize: "10px",
              color: "#6b7280",
              fontWeight: 500,
              whiteSpace: "nowrap" as const,
            }}
          >
            Byline Card
          </span>
        </div>
        <div style={{ display: "flex", gap: "8px", marginTop: "6px", alignItems: "center" }}>
          <span style={{ fontSize: "12px", color: "#666" }}>
            {journalist.article_count} articles
          </span>
          <span
            style={{
              fontSize: "11px",
              fontWeight: 500,
              padding: "2px 6px",
              borderRadius: "4px",
              backgroundColor: conf.bg,
              color: conf.text,
            }}
          >
            {journalist.confidence} confidence
          </span>
        </div>
        {journalist.confidence === "low" && (
          <div
            style={{
              fontSize: "11px",
              color: "#b45309",
              marginTop: "4px",
              padding: "4px 8px",
              backgroundColor: "rgba(245, 158, 11, 0.06)",
              borderRadius: "4px",
            }}
          >
            Limited data ({journalist.article_count} articles) — scores may shift as more are analyzed
          </div>
        )}
      </div>

      {/* Primary zone — distribution chart */}
      <div style={{ padding: "12px 16px", borderBottom: "1px solid #f0f0f0" }}>
        <DistributionBar
          distribution={journalist.distribution}
          articleCount={journalist.article_count}
        />
      </div>

      {/* Secondary zone — connections + facts */}
      {(journalist.connections.length > 0 || journalist.facts.length > 0) && (
        <div style={{ padding: "12px 16px", borderBottom: "1px solid #f0f0f0" }}>
          <ConnectionsList
            connections={journalist.connections}
            facts={journalist.facts}
          />
        </div>
      )}

      {/* Footer — utility */}
      <div
        style={{
          padding: "8px 16px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: "11px",
          color: "#999",
        }}
      >
        <span>AI-scored · Updated {version}</span>
        <a
          href="https://github.com/ferguswatts/byline-card/wiki/Methodology"
          target="_blank"
          rel="noopener noreferrer"
          style={{
            color: "#2563eb",
            textDecoration: "none",
            minHeight: "44px",
            display: "flex",
            alignItems: "center",
          }}
          onMouseOver={(e) => (e.currentTarget.style.textDecoration = "underline")}
          onMouseOut={(e) => (e.currentTarget.style.textDecoration = "none")}
        >
          About
        </a>
      </div>

      <style>{`
        @keyframes bylinecard-fadein {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @media (prefers-reduced-motion: reduce) {
          @keyframes bylinecard-fadein {
            from { opacity: 1; }
            to { opacity: 1; }
          }
        }
      `}</style>
    </div>
  );
};
