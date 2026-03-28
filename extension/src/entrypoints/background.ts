/**
 * Background service worker — prefetches latest data.json on extension startup.
 */

import { loadData } from "../lib/data";

export default defineBackground(() => {
  // Prefetch data on install and startup
  chrome.runtime.onInstalled.addListener(async () => {
    console.log("[Byline Card] Extension installed — prefetching data...");
    try {
      const data = await loadData();
      console.log(
        `[Byline Card] Data loaded: ${Object.keys(data.journalists).length} journalists, version ${data.version}`,
      );
    } catch (e) {
      console.error("[Byline Card] Failed to prefetch data:", e);
    }
  });

  // Periodic refresh — check for new data every 6 hours
  chrome.alarms.create("refresh-data", { periodInMinutes: 360 });
  chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name === "refresh-data") {
      try {
        await loadData();
        console.log("[Byline Card] Data refreshed.");
      } catch (e) {
        console.error("[Byline Card] Data refresh failed:", e);
      }
    }
  });
});
