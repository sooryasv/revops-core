import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In dev the SPA runs on :5173 and proxies API calls to the FastAPI app, so cookies stay same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/.well-known": "http://localhost:8000",
    },
  },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
