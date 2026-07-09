import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so the built index.html loads correctly from file:// in Electron.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
  },
});
