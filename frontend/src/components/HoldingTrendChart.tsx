import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { HoldingPoint } from "../api/client";

// 集保股權分散趨勢（ECharts 折線）：大戶/千張大戶（左軸，高占比）vs 散戶（右軸，低占比）。
// 重點看「變化」——y 軸 scale:true 自動縮放到資料區間，放大週與週之間的增減幅度。
// 台股語意配色：大戶集中＝紅（偏多）、散戶增加＝綠。
const BIG = "#e11d48"; // 大戶（≥400張）
const OVER1000 = "#f59e0b"; // 千張大戶（≥1000張）
const SMALL = "#16a34a"; // 散戶（<10張）

export function HoldingTrendChart({ points }: { points: HoldingPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || points.length < 2) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const dates = points.map((p) => p.date);
    const big = points.map((p) => p.big_pct);
    const over1000 = points.map((p) => p.over1000_pct);
    const small = points.map((p) => p.small_pct);

    chart.setOption({
      backgroundColor: "transparent",
      grid: { top: 30, right: 48, bottom: 24, left: 48 },
      legend: {
        top: 0,
        textStyle: { color: "#9ca3af", fontSize: 11 },
        data: ["大戶 ≥400張", "千張大戶", "散戶 <10張"],
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1f2430",
        borderColor: "#374151",
        textStyle: { color: "#e5e7eb", fontSize: 12 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (ps: any[]) => {
          const head = `${ps[0].axisValue}`;
          const rows = ps
            .map((p) => `${p.marker}${p.seriesName}：<b>${p.value ?? "—"}%</b>`)
            .join("<br/>");
          return `${head}<br/>${rows}`;
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { color: "#6b7280", fontSize: 10 },
        axisLine: { lineStyle: { color: "#374151" } },
      },
      yAxis: [
        {
          type: "value",
          scale: true,
          axisLabel: { color: "#6b7280", fontSize: 10, formatter: "{value}%" },
          splitLine: { lineStyle: { color: "#1f2430" } },
        },
        {
          type: "value",
          scale: true,
          axisLabel: { color: "#6b7280", fontSize: 10, formatter: "{value}%" },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "大戶 ≥400張",
          type: "line",
          data: big,
          yAxisIndex: 0,
          smooth: true,
          symbol: "none",
          lineStyle: { color: BIG, width: 2 },
          itemStyle: { color: BIG },
        },
        {
          name: "千張大戶",
          type: "line",
          data: over1000,
          yAxisIndex: 0,
          smooth: true,
          symbol: "none",
          lineStyle: { color: OVER1000, width: 1.5, type: "dashed" },
          itemStyle: { color: OVER1000 },
        },
        {
          name: "散戶 <10張",
          type: "line",
          data: small,
          yAxisIndex: 1,
          smooth: true,
          symbol: "none",
          lineStyle: { color: SMALL, width: 2 },
          itemStyle: { color: SMALL },
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
    return <div className="py-6 text-center text-xs text-muted">集保歷史資料不足，無法繪製趨勢</div>;
  }
  return <div ref={ref} className="h-56 w-full" />;
}
