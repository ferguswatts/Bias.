/**
 * Data fetching + caching for the extension.
 * Content scripts have limited Chrome API access, so we use
 * a simple approach: fetch bundled JSON via extension URL.
 */

import type { DataFile } from "./match";

const GITHUB_RAW_URL =
  "https://raw.githubusercontent.com/ferguswatts/Bias./main/extension/public/data.json";

/** Fetch journalist data. Tries GitHub first, falls back to bundled. */
export async function loadData(): Promise<DataFile> {
  // 1. Try fetching latest from GitHub (works from content scripts — it's just a fetch)
  try {
    const response = await fetch(GITHUB_RAW_URL, {
      signal: AbortSignal.timeout(3000),
    });
    if (response.ok) {
      const data: DataFile = await response.json();
      if (data.journalists && Object.keys(data.journalists).length > 0) {
        console.log(`[Bias] Loaded ${Object.keys(data.journalists).length} journalists from GitHub`);
        return data;
      }
    }
  } catch {
    // Network error or timeout — fall through to bundled
  }

  // 2. Fall back to bundled data via extension URL
  try {
    // In content scripts, we need to get the extension's URL for bundled resources
    const bundledUrl = (typeof browser !== "undefined" ? browser : chrome).runtime.getURL("data.json");
    console.log(`[Bias] Fetching bundled data from: ${bundledUrl}`);
    const response = await fetch(bundledUrl);
    if (response.ok) {
      const data: DataFile = await response.json();
      console.log(`[Bias] Loaded ${Object.keys(data.journalists).length} journalists from bundle`);
      return data;
    }
  } catch (e) {
    console.error("[Bias] Failed to load bundled data:", e);
  }

  // 3. Return empty data as last resort
  console.warn("[Bias] All data sources failed — returning empty data");
  return { version: "unknown", journalists: {}, sites: {} };
}
