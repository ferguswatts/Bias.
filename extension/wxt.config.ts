import { defineConfig } from "wxt";

export default defineConfig({
  modules: [],
  manifest: {
    name: "Bias — NZ Journalist Transparency",
    description:
      "See the political lean and connections of the journalist writing the article you're reading",
    icons: {
      16: "icon-16.png",
      48: "icon-48.png",
      128: "icon-128.png",
    },
    permissions: ["storage"],
    host_permissions: ["https://raw.githubusercontent.com/*"],
    browser_specific_settings: {
      gecko: {
        data_collection_permissions: {
          required: ["none"],
          optional: [],
        },
      },
    },
    web_accessible_resources: [
      {
        resources: ["data.json", "dashboard.html"],
        matches: ["*://*.nzherald.co.nz/*", "*://*.stuff.co.nz/*", "*://*.thepost.co.nz/*", "*://*.rnz.co.nz/*", "*://*.1news.co.nz/*", "*://*.tvnz.co.nz/*", "*://*.newsroom.co.nz/*", "*://*.thespinoff.co.nz/*", "*://*.interest.co.nz/*"],
      },
    ],
  },
});
