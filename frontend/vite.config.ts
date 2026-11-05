/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies /api to the backend so the UI and API stay independently runnable.
const backend = process.env.FORGE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: backend, changeOrigin: true } } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test-setup.ts"], css: false },
});
