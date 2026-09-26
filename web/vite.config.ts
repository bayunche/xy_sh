import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8790",
      "/status": "http://127.0.0.1:8790",
      "/events": "http://127.0.0.1:8790",
      "/history": "http://127.0.0.1:8790",
      "/items": "http://127.0.0.1:8790",
      "/capability": "http://127.0.0.1:8790",
      "/floor": "http://127.0.0.1:8790",
      "/orders": "http://127.0.0.1:8790",
      "/order": "http://127.0.0.1:8790",
      "/confirm": "http://127.0.0.1:8790",
      "/snipe": "http://127.0.0.1:8790",
      "/audit": "http://127.0.0.1:8790",
      "/search": "http://127.0.0.1:8790",
      "/send": "http://127.0.0.1:8790",
      "/reprice": "http://127.0.0.1:8790",
      "/offline": "http://127.0.0.1:8790",
      "/health": "http://127.0.0.1:8790",
    },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1024 },
});
