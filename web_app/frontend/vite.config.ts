import { resolve } from "node:path";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/",
  plugins: [vue()],
  build: {
    outDir: "../static",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: resolve(__dirname, "index.html"),
        resources: resolve(__dirname, "resources.html"),
        replay: resolve(__dirname, "replay.html"),
        syncGuide: resolve(__dirname, "sync-guide.html"),
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
