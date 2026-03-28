import { defineConfig } from "wxt";

export default defineConfig({
  modules: ["@wxt-dev/module-react"],
  manifest: {
    name: "Byline Card — NZ Journalist Transparency",
    description:
      "See the political lean and connections of the journalist writing the article you're reading",
    permissions: ["storage"],
    host_permissions: ["https://raw.githubusercontent.com/*"],
  },
});
