import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/health": "http://localhost:8000",
      "/review": "http://localhost:8000",
      "/reviews": "http://localhost:8000",
      "/dashboard": "http://localhost:8000",
      "/repositories": "http://localhost:8000",
      "/github": "http://localhost:8000",
    },
  },
});
