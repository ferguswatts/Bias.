import type { FC } from "react";

interface Distribution {
  left: number;
  centre_left: number;
  centre: number;
  centre_right: number;
  right: number;
}

const LABELS: Array<{ key: keyof Distribution; label: string }> = [
  { key: "left", label: "Left" },
  { key: "centre_left", label: "Centre-Left" },
  { key: "centre", label: "Centre" },
  { key: "centre_right", label: "Centre-Right" },
  { key: "right", label: "Right" },
];

export const DistributionBar: FC<{ distribution: Distribution; articleCount: number }> = ({
  distribution,
  articleCount,
}) => {
  const maxPct = Math.max(
    distribution.left,
    distribution.centre_left,
    distribution.centre,
    distribution.centre_right,
    distribution.right,
    1,
  );

  return (
    <div style={{ padding: "0" }}>
      <div
        style={{
          fontSize: "11px",
          fontWeight: 500,
          color: "#888",
          textTransform: "uppercase" as const,
          letterSpacing: "0.5px",
          marginBottom: "8px",
        }}
      >
        Coverage distribution
      </div>
      {LABELS.map(({ key, label }) => {
        const pct = distribution[key];
        return (
          <div
            key={key}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              marginBottom: "6px",
            }}
            role="img"
            aria-label={`${pct}% ${label}`}
          >
            <span
              style={{
                width: "80px",
                fontSize: "12px",
                fontWeight: 500,
                color: "#444",
                textAlign: "right" as const,
                flexShrink: 0,
              }}
            >
              {label}
            </span>
            <div
              style={{
                flex: 1,
                height: "8px",
                backgroundColor: "#f3f4f6",
                borderRadius: "4px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${(pct / maxPct) * 100}%`,
                  height: "100%",
                  backgroundColor: "#3b82f6",
                  borderRadius: "4px",
                  transition: "width 0.3s ease",
                }}
              />
            </div>
            <span
              style={{
                width: "32px",
                fontSize: "13px",
                fontWeight: 600,
                color: "#1a1a1a",
                textAlign: "right" as const,
                flexShrink: 0,
              }}
            >
              {pct}%
            </span>
          </div>
        );
      })}
    </div>
  );
};
