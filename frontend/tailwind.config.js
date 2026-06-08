/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 台股配色：紅漲綠跌（與美股相反，兩主題一致）
        up: "#e11d48",
        down: "#16a34a",
        // 主題色走 CSS 變數，支援深/淺色切換
        bg: "rgb(var(--c-bg) / <alpha-value>)",
        panel: "rgb(var(--c-panel) / <alpha-value>)",
        panel2: "rgb(var(--c-panel2) / <alpha-value>)",
        edge: "rgb(var(--c-edge) / <alpha-value>)",
        muted: "rgb(var(--c-muted) / <alpha-value>)",
      },
    },
  },
  plugins: [],
};
