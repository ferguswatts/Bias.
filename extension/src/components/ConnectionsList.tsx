import type { FC } from "react";

interface Connection {
  type: string;
  target: string;
  role: string;
  source: string;
}

interface Fact {
  text: string;
  source: string;
}

const TYPE_LABELS: Record<string, string> = {
  family: "Family",
  spouse: "Spouse",
  employer: "Employer",
  board: "Board",
  political: "Political",
};

export const ConnectionsList: FC<{
  connections: Connection[];
  facts: Fact[];
}> = ({ connections, facts }) => {
  if (connections.length === 0 && facts.length === 0) return null;

  return (
    <div>
      {connections.length > 0 && (
        <>
          <div
            style={{
              fontSize: "11px",
              fontWeight: 500,
              color: "#888",
              textTransform: "uppercase" as const,
              letterSpacing: "0.5px",
              marginBottom: "6px",
            }}
          >
            Documented connections
          </div>
          {connections.map((c, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: "4px",
                fontSize: "13px",
                lineHeight: "1.4",
              }}
            >
              <span style={{ color: "#1a1a1a" }}>
                <span style={{ color: "#888", fontSize: "12px" }}>
                  {TYPE_LABELS[c.type] || c.type}:
                </span>{" "}
                {c.target}
                {c.role && (
                  <span style={{ color: "#666" }}>, {c.role}</span>
                )}
              </span>
              <a
                href={c.source}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  color: "#2563eb",
                  textDecoration: "none",
                  fontSize: "11px",
                  marginLeft: "8px",
                  flexShrink: 0,
                  minHeight: "44px",
                  display: "flex",
                  alignItems: "center",
                }}
                onMouseOver={(e) =>
                  (e.currentTarget.style.textDecoration = "underline")
                }
                onMouseOut={(e) =>
                  (e.currentTarget.style.textDecoration = "none")
                }
              >
                source
              </a>
            </div>
          ))}
        </>
      )}
      {facts.length > 0 && (
        <div style={{ marginTop: connections.length > 0 ? "8px" : "0" }}>
          {facts.map((f, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: "4px",
                fontSize: "12px",
                color: "#444",
              }}
            >
              <span>{f.text}</span>
              <a
                href={f.source}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  color: "#2563eb",
                  textDecoration: "none",
                  fontSize: "11px",
                  marginLeft: "8px",
                  flexShrink: 0,
                  minHeight: "44px",
                  display: "flex",
                  alignItems: "center",
                }}
                onMouseOver={(e) =>
                  (e.currentTarget.style.textDecoration = "underline")
                }
                onMouseOut={(e) =>
                  (e.currentTarget.style.textDecoration = "none")
                }
              >
                source
              </a>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
