import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000; Vite proxies /api so the browser sees one origin.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
