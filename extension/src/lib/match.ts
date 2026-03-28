/**
 * Journalist matching — resolve a detected byline name to a journalist in the database.
 */

export interface JournalistData {
  name: string;
  aliases?: string[];
  outlet: string;
  beat: string;
  article_count: number;
  confidence: string;
  distribution: {
    left: number;
    centre_left: number;
    centre: number;
    centre_right: number;
    right: number;
  };
  connections: Array<{
    type: string;
    target: string;
    role: string;
    source: string;
  }>;
  facts: Array<{
    text: string;
    source: string;
  }>;
  methodology: string;
}

export interface DataFile {
  version: string;
  journalists: Record<string, JournalistData>;
  sites?: Record<string, { selectors: { byline: string } }>;
}

const SKIP_BYLINES = ["staff", "newsroom", "wire", "ap", "reuters", "nzme", "stuff"];

/** Map common outlet hostnames to slug keys. */
const OUTLET_MAP: Record<string, string> = {
  "nzherald.co.nz": "nzherald",
  "stuff.co.nz": "stuff",
  "rnz.co.nz": "rnz",
  "1news.co.nz": "1news",
  "newsroom.co.nz": "newsroom",
  "thespinoff.co.nz": "spinoff",
  "interest.co.nz": "interest",
};

function normalize(name: string): string {
  return name
    .toLowerCase()
    .replace(/^by\s+/i, "")
    .replace(/[^a-z\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

function outletKey(hostname: string): string {
  const host = hostname.replace("www.", "");
  return OUTLET_MAP[host] || host.split(".")[0];
}

/**
 * Match a detected byline to a journalist in the data.
 * Returns null if no match, ambiguous, or should be skipped.
 */
export function matchJournalist(
  name: string,
  outlet: string,
  data: DataFile,
): { slug: string; journalist: JournalistData } | null {
  const normalizedName = normalize(name);

  // Skip known non-journalist bylines
  if (SKIP_BYLINES.some((s) => normalizedName.includes(s))) {
    return null;
  }

  // Handle joint bylines — match first author only
  const firstAuthor = name.includes(" and ")
    ? name.split(" and ")[0].trim()
    : name;
  const normalizedFirst = normalize(firstAuthor);

  const outKey = outletKey(outlet);

  // 1. Exact slug match
  const slug = `${normalizedFirst}-${outKey}`;
  if (data.journalists[slug]) {
    return { slug, journalist: data.journalists[slug] };
  }

  // 2. Try matching just by name across all outlets
  const matches: Array<{ slug: string; journalist: JournalistData }> = [];
  for (const [s, j] of Object.entries(data.journalists)) {
    const jNormalized = normalize(j.name);
    if (jNormalized === normalizedFirst) {
      matches.push({ slug: s, journalist: j });
    }
    // 3. Alias match
    if (j.aliases) {
      for (const alias of j.aliases) {
        if (normalize(alias) === normalizedFirst) {
          matches.push({ slug: s, journalist: j });
          break;
        }
      }
    }
  }

  // Multiple matches = ambiguous, return null (false negative > false positive)
  if (matches.length === 1) {
    return matches[0];
  }

  return null;
}
