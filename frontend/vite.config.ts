import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 本機單人跑：dev 把 /api 代理到後端，省去 CORS 設定。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
