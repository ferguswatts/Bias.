import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock chrome.storage.local and chrome.runtime.getURL
const mockStorage: Record<string, unknown> = {};
vi.stubGlobal("chrome", {
  storage: {
    local: {
      get: vi.fn(async (key: string) => ({ [key]: mockStorage[key] })),
      set: vi.fn(async (obj: Record<string, unknown>) => {
        Object.assign(mockStorage, obj);
      }),
    },
  },
  runtime: {
    getURL: vi.fn((path: string) => `chrome-extension://test/${path}`),
  },
});

// Mock fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

describe("loadData", () => {
  let loadData: typeof import("../../extension/src/lib/data").loadData;

  beforeEach(async () => {
    // Reset mocks
    Object.keys(mockStorage).forEach((k) => delete mockStorage[k]);
    mockFetch.mockReset();

    const mod = await import("../../extension/src/lib/data");
    loadData = mod.loadData;
  });

  it("returns cached data if fresh", async () => {
    const testData = { version: "2026-03-29", journalists: {}, sites: {} };
    mockStorage["bylinecard_data"] = {
      data: testData,
      fetchedAt: Date.now() - 1000, // 1 second ago
    };

    const result = await loadData();
    expect(result.version).toBe("2026-03-29");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("fetches from GitHub when cache is stale", async () => {
    const freshData = { version: "2026-03-29", journalists: {}, sites: {} };
    mockStorage["bylinecard_data"] = {
      data: { version: "old", journalists: {}, sites: {} },
      fetchedAt: Date.now() - 25 * 60 * 60 * 1000, // 25 hours ago
    };

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => freshData,
    });

    const result = await loadData();
    expect(result.version).toBe("2026-03-29");
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("falls back to stale cache on network error", async () => {
    const staleData = { version: "stale", journalists: {}, sites: {} };
    mockStorage["bylinecard_data"] = {
      data: staleData,
      fetchedAt: Date.now() - 48 * 60 * 60 * 1000, // 2 days ago
    };

    mockFetch.mockRejectedValueOnce(new Error("Network error"));

    const result = await loadData();
    expect(result.version).toBe("stale");
  });

  it("falls back to bundled data when cache empty and network fails", async () => {
    const bundledData = { version: "bundled", journalists: {}, sites: {} };

    mockFetch
      .mockRejectedValueOnce(new Error("Network error")) // GitHub fetch fails
      .mockResolvedValueOnce({
        ok: true,
        json: async () => bundledData,
      }); // Bundled fetch succeeds

    const result = await loadData();
    expect(result.version).toBe("bundled");
  });
});
