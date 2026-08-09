import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { Actor, MarketFlowResponse } from "../api/client";
import { ACTOR_LABELS } from "../api/client";

// 市場法人資金流向（ECharts）：
//   上圖 累積淨買超曲線（外資/投信/自營三條，可看背離）疊加權指數（右軸）— 看整個週期方向
//   下圖 每日三大法人買賣超堆疊柱（億元）
const FOREIGN = "#e11d48"; // 外資
const TRUST = "#3b82f6"; // 投信
const DEALER = "#a855f7"; // 自營
const INDEX = "#9ca3af"; // 加權指數
const TOTAL = "#f59e0b"; // 合計

function fmt(v: number | null | undefined) {
  return v == null ? "—" : v.toLocaleString("zh-TW", { maximumFractionDigits: 1 });
}

export function MarketFlowChart({ data, actor }: { data: MarketFlowResponse; actor: Actor }) {
  const cumRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const dates = data.dates;
    if (dates.length < 2) return;

    const cumLine = (a: Actor, color: string) => ({
      name: `${ACTOR_LABELS[a]}累積`,
      type: "line" as const,
      data: data.actors[a]?.cum ?? [],
      yAxisIndex: 0,
      smooth: true,
      symbol: "none",
      lineStyle: { color, width: a === actor ? 2.6 : 1.1, opacity: a === actor ? 1 : 0.55 },
      itemStyle: { color },
      z: a === actor ? 5 : 2,
    });

    const cum = cumRef.current ? echarts.init(cumRef.current, undefined, { renderer: "canvas" }) : null;
    cum?.setOption({
      backgroundColor: "transparent",
      grid: { top: 30, right: 60, bottom: 24, left: 64 },
      legend: {
        top: 0,
        textStyle: { color: "#9ca3af", fontSize: 11 },
        data: ["三大法人累積", "外資累積", "投信累積", "自營商累積", "加權指數"],
        selected: { 三大法人累積: actor === "total" },
      },
      tooltip: { trigger: "axis", backgroundColor: "#1f2430", borderColor: "#374151", textStyle: { color: "#e5e7eb", fontSize: 12 } },
      xAxis: { type: "category", data: dates, axisLabel: { color: "#6b7280", fontSize: 10 }, axisLine: { lineStyle: { color: "#374151" } } },
      yAxis: [
        { type: "value", name: "累積(億)", nameTextStyle: { color: "#6b7280", fontSize: 10 }, axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } } },
        { type: "value", scale: true, name: "指數", nameTextStyle: { color: "#6b7280", fontSize: 10 }, axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { show: false } },
      ],
      series: [
        cumLine("total", TOTAL),
        cumLine("foreign", FOREIGN),
        cumLine("trust", TRUST),
        cumLine("dealer", DEALER),
        { name: "加權指數", type: "line", data: data.index, yAxisIndex: 1, smooth: true, symbol: "none", lineStyle: { color: INDEX, width: 1.4, type: "dashed" }, itemStyle: { color: INDEX } },
      ],
    });

    // ── 下圖：每日三大法人買賣超堆疊柱 ──
    const bar = barRef.current ? echarts.init(barRef.current, undefined, { renderer: "canvas" }) : null;
    bar?.setOption({
      backgroundColor: "transparent",
      grid: { top: 28, right: 60, bottom: 24, left: 64 },
      legend: { top: 0, textStyle: { color: "#9ca3af", fontSize: 11 }, data: ["外資", "投信", "自營商"] },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1f2430",
        borderColor: "#374151",
        textStyle: { color: "#e5e7eb", fontSize: 12 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (ps: any[]) => {
          const i = ps[0].dataIndex;
          const t = data.actors.total?.daily?.[i];
          return (
            `${dates[i]}<br/>外資：<b>${fmt(data.actors.foreign?.daily?.[i])}</b> 億<br/>` +
            `投信：<b>${fmt(data.actors.trust?.daily?.[i])}</b> 億<br/>` +
            `自營商：<b>${fmt(data.actors.dealer?.daily?.[i])}</b> 億<br/>合計：<b>${fmt(t)}</b> 億`
          );
        },
      },
      xAxis: { type: "category", data: dates, axisLabel: { color: "#6b7280", fontSize: 10 }, axisLine: { lineStyle: { color: "#374151" } } },
      yAxis: { type: "value", name: "每日(億)", nameTextStyle: { color: "#6b7280", fontSize: 10 }, axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } } },
      series: [
        { name: "外資", type: "bar", stack: "net", data: data.actors.foreign?.daily ?? [], itemStyle: { color: FOREIGN } },
        { name: "投信", type: "bar", stack: "net", data: data.actors.trust?.daily ?? [], itemStyle: { color: TRUST } },
        { name: "自營商", type: "bar", stack: "net", data: data.actors.dealer?.daily ?? [], itemStyle: { color: DEALER } },
      ],
    });

    const onResize = () => { cum?.resize(); bar?.resize(); };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      cum?.dispose();
      bar?.dispose();
    };
  }, [data, actor]);

  if (data.dates.length < 2) {
    return <div className="py-10 text-center text-sm text-muted">市場法人資料不足，請先於設定頁載入或等待回補。</div>;
  }
  return (
    <div className="flex flex-col gap-2">
      <div ref={cumRef} className="h-72 w-full" />
      <div ref={barRef} className="h-40 w-full" />
    </div>
  );
}
