import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { FundamentalHistoryResponse } from "../api/client";

// 基本面趨勢（ECharts）：
//   上圖 月營收 — 營收 bars + YoY 折線（右軸），一眼看成長動能
//   下圖 季獲利 — 單季 EPS bars + 毛利/營益/淨利率折線（右軸），看利潤率趨勢
const REV = "#38bdf8"; // 營收 bar
const YOY = "#f59e0b"; // YoY 線
const EPS = "#38bdf8"; // EPS bar
const GM = "#e11d48"; // 毛利率
const OM = "#f59e0b"; // 營益率
const NM = "#a855f7"; // 淨利率

const AXIS = {
  axisLabel: { color: "#6b7280", fontSize: 10 },
  axisLine: { lineStyle: { color: "#374151" } },
};
const TOOLTIP = {
  trigger: "axis" as const,
  backgroundColor: "#1f2430",
  borderColor: "#374151",
  textStyle: { color: "#e5e7eb", fontSize: 12 },
};

function num(v: number | null | undefined, digits = 1) {
  return v == null ? "—" : v.toLocaleString("zh-TW", { maximumFractionDigits: digits });
}

export function FundamentalTrendChart({ data }: { data: FundamentalHistoryResponse }) {
  const revRef = useRef<HTMLDivElement>(null);
  const finRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const revs = data.revenues;
    const qs = data.quarters;

    // ── 上圖：月營收 + YoY ──
    const rchart = revs.length >= 2 && revRef.current
      ? echarts.init(revRef.current, undefined, { renderer: "canvas" }) : null;
    rchart?.setOption({
      backgroundColor: "transparent",
      grid: { top: 28, right: 48, bottom: 20, left: 56 },
      legend: { top: 0, textStyle: { color: "#9ca3af", fontSize: 11 }, data: ["月營收", "YoY"] },
      tooltip: {
        ...TOOLTIP,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (ps: any[]) => {
          const p = revs[ps[0].dataIndex];
          return `${p.ym}<br/>營收：<b>${num(p.revenue)}</b> 億<br/>YoY：<b>${num(p.yoy)}%</b>　MoM：<b>${num(p.mom)}%</b>`;
        },
      },
      xAxis: { type: "category", data: revs.map((p) => p.ym), ...AXIS },
      yAxis: [
        { type: "value", name: "營收(億)", nameTextStyle: { color: "#6b7280", fontSize: 10 },
          axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } } },
        { type: "value", name: "YoY(%)", nameTextStyle: { color: "#6b7280", fontSize: 10 },
          axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { show: false } },
      ],
      series: [
        { name: "月營收", type: "bar", data: revs.map((p) => p.revenue), yAxisIndex: 0,
          barWidth: "60%", itemStyle: { color: REV, opacity: 0.75 } },
        { name: "YoY", type: "line", data: revs.map((p) => p.yoy), yAxisIndex: 1, smooth: true,
          symbol: "none", lineStyle: { color: YOY, width: 2 }, itemStyle: { color: YOY } },
      ],
    });

    // ── 下圖：單季 EPS + 三率 ──
    const fchart = qs.length >= 2 && finRef.current
      ? echarts.init(finRef.current, undefined, { renderer: "canvas" }) : null;
    fchart?.setOption({
      backgroundColor: "transparent",
      grid: { top: 28, right: 48, bottom: 20, left: 56 },
      legend: { top: 0, textStyle: { color: "#9ca3af", fontSize: 11 }, data: ["單季EPS", "毛利率", "營益率", "淨利率"] },
      tooltip: {
        ...TOOLTIP,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (ps: any[]) => {
          const q = qs[ps[0].dataIndex];
          return (
            `${q.label}<br/>單季 EPS：<b>${num(q.eps, 2)}</b>　營收：<b>${num(q.revenue)}</b> 億<br/>` +
            `毛利率：<b>${num(q.gross_margin)}%</b>　營益率：<b>${num(q.op_margin)}%</b>　淨利率：<b>${num(q.net_margin)}%</b>`
          );
        },
      },
      xAxis: { type: "category", data: qs.map((q) => q.label), ...AXIS },
      yAxis: [
        { type: "value", name: "EPS(元)", nameTextStyle: { color: "#6b7280", fontSize: 10 },
          axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } } },
        { type: "value", name: "%", nameTextStyle: { color: "#6b7280", fontSize: 10 },
          axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { show: false } },
      ],
      series: [
        { name: "單季EPS", type: "bar", data: qs.map((q) => q.eps), yAxisIndex: 0,
          barWidth: "50%", itemStyle: { color: EPS, opacity: 0.75 } },
        { name: "毛利率", type: "line", data: qs.map((q) => q.gross_margin), yAxisIndex: 1,
          smooth: true, symbol: "none", lineStyle: { color: GM, width: 2 }, itemStyle: { color: GM } },
        { name: "營益率", type: "line", data: qs.map((q) => q.op_margin), yAxisIndex: 1,
          smooth: true, symbol: "none", lineStyle: { color: OM, width: 1.5 }, itemStyle: { color: OM } },
        { name: "淨利率", type: "line", data: qs.map((q) => q.net_margin), yAxisIndex: 1,
          smooth: true, symbol: "none", lineStyle: { color: NM, width: 1.5 }, itemStyle: { color: NM } },
      ],
    });

    const onResize = () => { rchart?.resize(); fchart?.resize(); };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      rchart?.dispose();
      fchart?.dispose();
    };
  }, [data]);

  if (data.revenues.length < 2 && data.quarters.length < 2) {
    return <div className="py-6 text-center text-xs text-muted">基本面歷史資料不足</div>;
  }
  return (
    <div className="flex flex-col gap-2">
      {data.revenues.length >= 2 && <div ref={revRef} className="h-52 w-full" />}
      {data.quarters.length >= 2 && <div ref={finRef} className="h-52 w-full" />}
    </div>
  );
}
