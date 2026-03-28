/**
 * Data fetching + caching for the extension.
 * Strategy: GitHub raw URL → chrome.storage.local cache → bundled fallback.
 */

import type { DataFile } from "./match";

const GITHUB_RAW_URL =
  "https://raw.githubusercontent.com/ferguswatts/byline-card/main/extension/public/data.json";
const CACHE_KEY = "bylinecard_data";
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

interface CacheEntry {
  data: DataFile;
  fetchedAt: number;
}

/** Fetch latest data from GitHub, cache locally, fallback gracefully. */
export async function loadData(): Promise<DataFile> {
  // 1. Try cache first (if fresh enough)
  try {
    const cached = await chrome.storage.local.get(CACHE_KEY);
    const entry = cached[CACHE_KEY] as CacheEntry | undefined;
    if (entry && Date.now() - entry.fetchedAt < CACHE_TTL_MS) {
      return entry.data;
    }
  } catch {
    // Cache miss or error — continue
  }

  // 2. Try fetching from GitHub raw URL
  try {
    const response = await fetch(GITHUB_RAW_URL, {
      signal: AbortSignal.timeout(5000),
    });
    if (response.ok) {
      const data: DataFile = await response.json();
      // Save to cache
      const entry: CacheEntry = { data, fetchedAt: Date.now() };
      await chrome.storage.local.set({ [CACHE_KEY]: entry });
      return data;
    }
  } catch {
    // Network error — fall through
  }

  // 3. Try stale cache (better than nothing)
  try {
    const cached = await chrome.storage.local.get(CACHE_KEY);
    const entry = cached[CACHE_KEY] as CacheEntry | undefined;
    if (entry) {
      console.log(
        `[Byline Card] Using stale cache from ${new Date(entry.fetchedAt).toISOString()}`,
      );
      return entry.data;
    }
  } catch {
    // No cache at all
  }

  // 4. Fall back to bundled data
  const bundledUrl = chrome.runtime.getURL("data.json");
  const response = await fetch(bundledUrl);
  return response.json();
}
