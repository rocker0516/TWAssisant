import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 本機單人跑：dev 把 /api 代理到後端，省去 CORS 設定。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 後端 API 掛在真 /api 前綴下，故不再 rewrite——dev 與打包後路徑一致。
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
