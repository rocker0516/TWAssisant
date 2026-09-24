import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 驗證用配置：使用者常自跑 5173/8000，驗證走 5174→8001（同 backend-verify 哲學）。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
});
