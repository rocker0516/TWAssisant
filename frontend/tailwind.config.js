/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 台股配色：紅漲綠跌（與美股相反）
        up: "#e11d48",
        down: "#16a34a",
        bg: "#0f1115",
        panel: "#181b22",
        panel2: "#1f232c",
        edge: "#2a2f3a",
        muted: "#8b93a7",
      },
    },
  },
  plugins: [],
};
