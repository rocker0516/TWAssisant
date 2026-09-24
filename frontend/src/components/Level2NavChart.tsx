import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { Level2NavPoint } from "../api/client";

// Level 2 模擬帳戶淨值曲線 vs 大盤（同起點指數化，診斷欄——FRS v1.1 §14）。
const NAV = "#38bdf8"; // 帳戶
const BENCH = "#6b7280"; // 大盤（診斷）

export function Level2NavChart({ points }: { points: Level2NavPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || points.length < 2) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "transparent",
      grid: { top: 30, right: 24, bottom: 24, left: 64 },
      legend: {
        top: 0,
        textStyle: { color: "#9ca3af", fontSize: 11 },
        data: ["帳戶 NAV", "大盤（同起點，診斷）"],
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1f2430",
        borderColor: "#374151",
        textStyle: { color: "#e5e7eb", fontSize: 12 },
      },
      xAxis: {
        type: "category",
        data: points.map((p) => p.date),
        axisLabel: { color: "#6b7280", fontSize: 10 },
        axisLine: { lineStyle: { color: "#374151" } },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: "#6b7280", fontSize: 10 },
        splitLine: { lineStyle: { color: "#1f2430" } },
      },
      series: [
        {
          name: "帳戶 NAV",
          type: "line",
          data: points.map((p) => p.nav),
          smooth: false,
          symbol: "none",
          lineStyle: { color: NAV, width: 2 },
          itemStyle: { color: NAV },
        },
        {
          name: "大盤（同起點，診斷）",
          type: "line",
          data: points.map((p) => p.benchmark),
          smooth: false,
          symbol: "none",
          lineStyle: { color: BENCH, width: 1.5, type: "dashed" },
          itemStyle: { color: BENCH },
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [points]);

  if (points.length < 2) {
    return (
      <div className="py-6 text-center text-xs text-muted">
        淨值序列不足兩日，曲線待帳戶累積
      </div>
    );
  }
  return <div ref={ref} className="h-64 w-full" />;
}
