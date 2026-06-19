import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { ChipPoint } from "../api/client";

// 籌碼趨勢（ECharts）：
//   上圖 法人 — 每日三大法人買賣超 bars（紅買綠賣）+ 累計淨買超 line（曲線＝偷偷進/出貨）
//   下圖 信用 — 融資餘額 line（左軸）+ 融券餘額 line（右軸）
const RED = "#e11d48"; // 買超
const GREEN = "#16a34a"; // 賣超
const CUM = "#38bdf8"; // 累計線
const MARGIN = "#f59e0b"; // 融資
const SHORT = "#a855f7"; // 融券

function num(v: number | null | undefined) {
  return v == null ? "—" : v.toLocaleString("zh-TW");
}

export function ChipTrendChart({ points }: { points: ChipPoint[] }) {
  const instRef = useRef<HTMLDivElement>(null);
  const marginRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (points.length < 2) return;
    const dates = points.map((p) => p.date);

    // ── 上圖：法人買賣超 + 累計 ──
    let cum = 0;
    const cumulative = points.map((p) => (cum += p.total_net ?? 0));
    const bars = points.map((p) => ({
      value: p.total_net,
      itemStyle: { color: (p.total_net ?? 0) >= 0 ? RED : GREEN },
    }));
    const inst = instRef.current ? echarts.init(instRef.current, undefined, { renderer: "canvas" }) : null;
    inst?.setOption({
      backgroundColor: "transparent",
      grid: { top: 28, right: 56, bottom: 20, left: 56 },
      legend: { top: 0, textStyle: { color: "#9ca3af", fontSize: 11 }, data: ["每日買賣超", "累計淨買超"] },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1f2430",
        borderColor: "#374151",
        textStyle: { color: "#e5e7eb", fontSize: 12 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (ps: any[]) => {
          const i = ps[0].dataIndex;
          const p = points[i];
          return (
            `${p.date}<br/>外資：<b>${num(p.foreign_net)}</b> 張<br/>投信：<b>${num(p.trust_net)}</b> 張<br/>` +
            `自營商：<b>${num(p.dealer_net)}</b> 張<br/>合計：<b>${num(p.total_net)}</b> 張<br/>累計：<b>${num(cumulative[i])}</b> 張`
          );
        },
      },
      xAxis: {
        type: "category", data: dates,
        axisLabel: { color: "#6b7280", fontSize: 10 }, axisLine: { lineStyle: { color: "#374151" } },
      },
      yAxis: [
        { type: "value", name: "每日(張)", nameTextStyle: { color: "#6b7280", fontSize: 10 },
          axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } } },
        { type: "value", name: "累計(張)", nameTextStyle: { color: "#6b7280", fontSize: 10 },
          axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { show: false } },
      ],
      series: [
        { name: "每日買賣超", type: "bar", data: bars, yAxisIndex: 0, barWidth: "60%" },
        { name: "累計淨買超", type: "line", data: cumulative, yAxisIndex: 1, smooth: true,
          symbol: "none", lineStyle: { color: CUM, width: 2 }, itemStyle: { color: CUM } },
      ],
    });

    // ── 下圖：融資 / 融券餘額 ──
    const margin = points.map((p) => p.margin_balance);
    const short = points.map((p) => p.short_balance);
    const hasMargin = margin.some((v) => v != null);
    const mchart = hasMargin && marginRef.current
      ? echarts.init(marginRef.current, undefined, { renderer: "canvas" }) : null;
    mchartOpt(mchart, dates, margin, short);

    const onResize = () => { inst?.resize(); mchart?.resize(); };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      inst?.dispose();
      mchart?.dispose();
    };
  }, [points]);

  if (points.length < 2) {
    return <div className="py-6 text-center text-xs text-muted">籌碼歷史資料不足</div>;
  }
  return (
    <div className="flex flex-col gap-2">
      <div ref={instRef} className="h-56 w-full" />
      <div ref={marginRef} className="h-44 w-full" />
    </div>
  );
}

function mchartOpt(
  chart: echarts.ECharts | null,
  dates: string[],
  margin: (number | null | undefined)[],
  short: (number | null | undefined)[],
) {
  chart?.setOption({
    backgroundColor: "transparent",
    grid: { top: 28, right: 56, bottom: 20, left: 56 },
    legend: { top: 0, textStyle: { color: "#9ca3af", fontSize: 11 }, data: ["融資餘額", "融券餘額"] },
    tooltip: {
      trigger: "axis", backgroundColor: "#1f2430", borderColor: "#374151",
      textStyle: { color: "#e5e7eb", fontSize: 12 },
    },
    xAxis: {
      type: "category", data: dates,
      axisLabel: { color: "#6b7280", fontSize: 10 }, axisLine: { lineStyle: { color: "#374151" } },
    },
    yAxis: [
      { type: "value", scale: true, name: "融資(張)", nameTextStyle: { color: "#6b7280", fontSize: 10 },
        axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } } },
      { type: "value", scale: true, name: "融券(張)", nameTextStyle: { color: "#6b7280", fontSize: 10 },
        axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { show: false } },
    ],
    series: [
      { name: "融資餘額", type: "line", data: margin, yAxisIndex: 0, smooth: true,
        symbol: "none", lineStyle: { color: MARGIN, width: 2 }, itemStyle: { color: MARGIN } },
      { name: "融券餘額", type: "line", data: short, yAxisIndex: 1, smooth: true,
        symbol: "none", lineStyle: { color: SHORT, width: 1.5 }, itemStyle: { color: SHORT } },
    ],
  });
}
