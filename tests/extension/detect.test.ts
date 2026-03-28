import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock window.location
const mockLocation = { hostname: "www.nzherald.co.nz" };
vi.stubGlobal("window", { location: mockLocation });

// We test the detection logic by simulating DOM structures
function makeDoc(html: string): Document {
  const parser = new DOMParser();
  return parser.parseFromString(html, "text/html");
}

describe("detectByline", () => {
  // Import after mocks are set up
  let detectByline: typeof import("../../extension/src/lib/detect").detectByline;

  beforeEach(async () => {
    const mod = await import("../../extension/src/lib/detect");
    detectByline = mod.detectByline;
  });

  it("extracts author from JSON-LD NewsArticle", () => {
    const doc = makeDoc(`
      <html><head>
        <script type="application/ld+json">
          {"@type": "NewsArticle", "author": {"name": "Katie Bradford"}}
        </script>
      </head><body></body></html>
    `);
    const result = detectByline(doc);
    expect(result).toEqual({ name: "Katie Bradford", outlet: "nzherald.co.nz" });
  });

  it("extracts author from JSON-LD with array author", () => {
    const doc = makeDoc(`
      <html><head>
        <script type="application/ld+json">
          {"@type": "NewsArticle", "author": [{"name": "Thomas Coughlan"}]}
        </script>
      </head><body></body></html>
    `);
    const result = detectByline(doc);
    expect(result).toEqual({ name: "Thomas Coughlan", outlet: "nzherald.co.nz" });
  });

  it("falls back to meta tag when no JSON-LD", () => {
    const doc = makeDoc(`
      <html><head>
        <meta name="author" content="Claire Trevett">
      </head><body></body></html>
    `);
    const result = detectByline(doc);
    expect(result).toEqual({ name: "Claire Trevett", outlet: "nzherald.co.nz" });
  });

  it("falls back to CSS selector when no JSON-LD or meta", () => {
    const doc = makeDoc(`
      <html><head></head><body>
        <span class="author-name">Derek Cheng</span>
      </body></html>
    `);
    const siteConfig = {
      "nzherald.co.nz": { selectors: { byline: ".author-name" } },
    };
    const result = detectByline(doc, siteConfig);
    expect(result).toEqual({ name: "Derek Cheng", outlet: "nzherald.co.nz" });
  });

  it("returns null when no byline found", () => {
    const doc = makeDoc("<html><head></head><body><p>No author here</p></body></html>");
    const result = detectByline(doc);
    expect(result).toBeNull();
  });

  it("strips 'By ' prefix from author name", () => {
    const doc = makeDoc(`
      <html><head>
        <meta name="author" content="By Jason Walls">
      </head><body></body></html>
    `);
    const result = detectByline(doc);
    expect(result?.name).toBe("Jason Walls");
  });

  it("strips role suffix from author name", () => {
    const doc = makeDoc(`
      <html><head>
        <meta name="author" content="Audrey Young, Political Editor">
      </head><body></body></html>
    `);
    const result = detectByline(doc);
    expect(result?.name).toBe("Audrey Young");
  });
});
