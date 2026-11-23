import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const port = process.env.NOVELISTA_PORT || "8000";

export default defineConfig({
  plugins: [react()],
  base: "/",
  resolve: { alias: { "@": "/src" } },
  server: { proxy: { "/api": `http://127.0.0.1:${port}` } },
  build: { outDir: "dist" },
});
