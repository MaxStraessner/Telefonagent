import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify("0.1.0") },
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  test: {
    environment: "jsdom",
    setupFiles: "./tests/setup.ts",
    css: true
  }
});
