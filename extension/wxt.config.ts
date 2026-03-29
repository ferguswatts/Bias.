import { defineConfig } from "wxt";

export default defineConfig({
  modules: [],
  manifest: {
    name: "Byline Card — NZ Journalist Transparency",
    description:
      "See the political lean and connections of the journalist writing the article you're reading",
    permissions: ["storage"],
    host_permissions: ["https://raw.githubusercontent.com/*"],
    web_accessible_resources: [
      {
        resources: ["data.json"],
        matches: ["*://*.nzherald.co.nz/*", "*://*.stuff.co.nz/*", "*://*.thepost.co.nz/*", "*://*.rnz.co.nz/*", "*://*.1news.co.nz/*", "*://*.newsroom.co.nz/*", "*://*.thespinoff.co.nz/*", "*://*.interest.co.nz/*"],
      },
    ],
  },
});
