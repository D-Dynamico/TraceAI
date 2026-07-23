/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy /api to the FastAPI backend during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  // Vitest — the frontend's first test infra (Phase 9). jsdom gives the
  // component tests a DOM; the setup file wires jest-dom matchers and resets
  // the fetch stub between tests. No test hits the network: client.js talks to
  // a stubbed global.fetch, exactly as the backend suite stubs safe_get.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.js",
    css: false,
  },
});
