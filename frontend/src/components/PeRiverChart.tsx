import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { PeRiverResponse } from "../api/client";

// 本益比河流圖：歷史 PE 分位數（10/30/50/70/90%）× 隱含 EPS = 價格帶，疊收盤價。
// 股價貼下緣＝歷史便宜區、貼上緣＝歷史昂貴區（僅相對自身歷史，非買賣建議）。
const CLOSE = "#e5e7eb";
// 河流帶由下（便宜）到上（貴）：綠 → 紅
const BAND_FILLS = ["rgba(22,163,74,0.28)", "rgba(132,204,22,0.22)", "rgba(245,158,11,0.20)", "rgba(225,29,72,0.22)"];

export function PeRiverChart({ data }: { data: PeRiverResponse }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (data.points.length < 2 || !ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const dates = data.points.map((p) => p.date);
    const nBands = data.pe_levels.length;

    // 帶狀：band[i] 為基準線（透明），band[i+1]-band[i] 疊 stack 填色
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const series: any[] = [];
    for (let i = 0; i < nBands - 1; i++) {
      series.push({
        name: `_base${i}`, type: "line", stack: `b${i}`, silent: true,
        data: data.points.map((p) => p.bands[i]),
        symbol: "none", lineStyle: { width: 0 }, tooltip: { show: false },
      });
      series.push({
        name: `${data.pe_levels[i]}~${data.pe_levels[i + 1]}x`, type: "line", stack: `b${i}`, silent: true,
        data: data.points.map((p) => (p.bands[i + 1] != null && p.bands[i] != null ? p.bands[i + 1]! - p.bands[i]! : null)),
        symbol: "none", lineStyle: { width: 0 },
        areaStyle: { color: BAND_FILLS[i % BAND_FILLS.length] }, tooltip: { show: false },
      });
    }
    series.push({
      name: "收盤價", type: "line", data: data.points.map((p) => p.close),
      symbol: "none", lineStyle: { color: CLOSE, width: 1.8 }, itemStyle: { color: CLOSE }, z: 10,
    });

    chart.setOption({
      backgroundColor: "transparent",
      grid: { top: 12, right: 16, bottom: 20, left: 56 },
      tooltip: {
        trigger: "axis", backgroundColor: "#1f2430", borderColor: "#374151",
        textStyle: { color: "#e5e7eb", fontSize: 12 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (ps: any[]) => {
          const i = ps[0].dataIndex;
          const p = data.points[i];
          const bandTxt = data.pe_levels
            .map((lv, j) => `${lv}x：${p.bands[j]?.toLocaleString("zh-TW") ?? "—"}`)
            .join("<br/>");
          return `${p.date}<br/>收盤：<b>${p.close?.toLocaleString("zh-TW") ?? "—"}</b><br/>${bandTxt}`;
        },
      },
      xAxis: {
        type: "category", data: dates,
        axisLabel: { color: "#6b7280", fontSize: 10 }, axisLine: { lineStyle: { color: "#374151" } },
      },
      yAxis: {
        type: "value", scale: true,
        axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } },
      },
      series,
    });

    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [data]);

  if (data.points.length < 2) return null;
  return <div ref={ref} className="h-64 w-full" />;
}
