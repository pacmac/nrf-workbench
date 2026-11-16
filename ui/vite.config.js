import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// base "./": every asset URL is relative, so the dashboard works on any port and behind any path
// (docs/specs/dashboard.md: "the dashboard makes no assumption about its own address").
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../nrf_workbench/dashboard/static",
    emptyOutDir: true,
    assetsDir: "assets",
  },
});
