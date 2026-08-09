import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { SectorRotationResponse } from "../api/client";

// 資金輪動象限圖（RRG 風格）：
//   X=資金強度（強度模式=法人淨買超佔成交比%；絕對模式=淨買超張數）
//   Y=資金加速度（近5日流速 − 近20日流速）
//   點大小=資金量（20日成交張）  尾巴=近N週軌跡  顏色=象限
// 四象限：右上 主流(強且加速) / 右下 高檔鈍化(強但退燒) / 左下 冷區 / 左上 轉機
const Q = {
  主流: "#e11d48",
  高檔鈍化: "#f59e0b",
  冷區: "#16a34a",
  轉機: "#3b82f6",
};

export type FlowMetric = "strength" | "absolute";

function quadrant(x: number, y: number): keyof typeof Q {
  if (x >= 0) return y >= 0 ? "主流" : "高檔鈍化";
  return y >= 0 ? "轉機" : "冷區";
}

function xy(p: { net20: number; net5: number; turnover20: number; turnover5: number }, metric: FlowMetric) {
  if (metric === "strength") {
    const s20 = p.turnover20 ? (p.net20 / p.turnover20) * 100 : 0;
    const s5 = p.turnover5 ? (p.net5 / p.turnover5) * 100 : 0;
    return { x: s20, y: s5 - s20 };
  }
  return { x: p.net20, y: p.net5 / 5 - p.net20 / 20 };
}

export function SectorRotationChart({
  data,
  metric,
  topN = 9,
}: {
  data: SectorRotationResponse;
  metric: FlowMetric;
  topN?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || data.sectors.length === 0) return;
    const sectors = data.sectors.slice(0, topN); // 後端已依資金量排序

    // 各類股原始 X(強度)/Y(加速度) + 資金量
    const raw = sectors.map((s) => {
      const head = s.points[s.points.length - 1];
      return { s, head, ...xy(head, metric) };
    });
    // RRG 風格 + 排名定位：對 X/Y 各自用「排名百分位」映射到 ±Z，保證雲均勻填滿象限、
    // 不被離群值或視窗寬壓扁。中心=各類股中位數；象限=相對同儕(高於/低於中位)。tooltip 顯示原始強度%/張。
    const Z = 2.6;
    const mapper = (vals: number[]) => {
      const sorted = [...vals].sort((a, b) => a - b);
      const n = sorted.length;
      return (v: number) => {
        if (n <= 1) return 0;
        if (v <= sorted[0]) return -Z;
        if (v >= sorted[n - 1]) return Z;
        let i = 1;
        while (i < n && sorted[i] < v) i++;
        const x0 = sorted[i - 1];
        const x1 = sorted[i];
        const frac = (i - 1 + (x1 > x0 ? (v - x0) / (x1 - x0) : 0)) / (n - 1);
        return (frac * 2 - 1) * Z;
      };
    };
    const mapX = mapper(raw.map((r) => r.x));
    const mapY = mapper(raw.map((r) => r.y));
    let maxSize = 1;
    for (const r of raw) maxSize = Math.max(maxSize, r.head.turnover20);
    const sym = (sz: number) => 10 + 26 * Math.sqrt(Math.max(0, sz) / maxSize);

    const boundX = Z + 0.3;
    const boundY = Z + 0.3;

    // 變化＝「幽靈點(6週前)→現在點」：淡空心點是起點，曲線連到實心發光的現在點。
    // 一眼看出「從哪移到哪」，連線長度＝變化幅度、箭頭＝方向。只畫起點與現在，無 6 週蛛網。
    const ghosts = raw.map((r) => {
      const f = xy(r.s.points[0], metric);
      const color = Q[quadrant(mapX(r.x), mapY(r.y))];
      return {
        value: [mapX(f.x), mapY(f.y)],
        symbolSize: 7,
        itemStyle: { color: "transparent", borderColor: color, borderWidth: 1.5, opacity: 0.5 },
      };
    });
    const connectors = raw.map((r) => {
      const f = xy(r.s.points[0], metric);
      const color = Q[quadrant(mapX(r.x), mapY(r.y))];
      return {
        coords: [
          [mapX(f.x), mapY(f.y)],
          [mapX(r.x), mapY(r.y)],
        ],
        lineStyle: { color, width: 1.6, opacity: 0.55, curveness: 0.2 },
      };
    });

    // 現在點（大小=資金量、色=象限、柔光、標籤=類股名）。value 末兩位存真實 x/y 供 tooltip。
    const heads = raw.map((r) => {
      const q = quadrant(mapX(r.x), mapY(r.y));
      return {
        name: r.s.name,
        value: [mapX(r.x), mapY(r.y), r.head.turnover20, r.head.net20, q, r.x, r.y],
        symbolSize: sym(r.head.turnover20),
        itemStyle: { color: Q[q], borderColor: "#0b0e14", borderWidth: 1.5, shadowBlur: 10, shadowColor: Q[q] },
      };
    });

    // 四象限角落大標籤（淡），讓結構一目了然
    const corners = [
      { value: [boundX * 0.58, boundY * 0.82], text: "主流", c: Q.主流 },
      { value: [boundX * 0.58, -boundY * 0.82], text: "高檔鈍化", c: Q.高檔鈍化 },
      { value: [-boundX * 0.58, boundY * 0.82], text: "轉機", c: Q.轉機 },
      { value: [-boundX * 0.58, -boundY * 0.82], text: "冷區", c: Q.冷區 },
    ].map((o) => ({
      value: o.value,
      symbolSize: 0,
      label: { show: true, formatter: o.text, color: o.c, fontSize: 13, fontWeight: "bold" as const, opacity: 0.5 },
    }));
    // 四象限柔色底（標出結構，淡到不搶點）
    const tint = (c: string, to: number[]) => [
      { coord: [0, 0], itemStyle: { color: c, opacity: 0.06 } },
      { coord: to },
    ];

    const unit = metric === "strength" ? "%" : "張";
    echarts.getInstanceByDom(ref.current)?.dispose(); // 防 StrictMode/HMR 重複 init
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "transparent",
      grid: { top: 20, right: 90, bottom: 44, left: 64 },
      tooltip: {
        backgroundColor: "#1f2430",
        borderColor: "#374151",
        textStyle: { color: "#e5e7eb", fontSize: 12 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (p: any) => {
          if (p.seriesType !== "scatter") return "";
          const [, , , net20, q, x, y] = p.value;
          return (
            `<b>${p.name}</b>（${q}）<br/>` +
            `強度 X：<b>${x.toFixed(metric === "strength" ? 2 : 0)}</b> ${unit}<br/>` +
            `加速度 Y：<b>${y.toFixed(metric === "strength" ? 2 : 0)}</b><br/>` +
            `20日淨買超：<b>${net20.toLocaleString("zh-TW")}</b> 張`
          );
        },
      },
      xAxis: {
        type: "value",
        name: (metric === "strength" ? "資金強度" : "淨買超量") + "（vs 類股平均）→",
        nameLocation: "middle",
        nameGap: 22,
        nameTextStyle: { color: "#9ca3af", fontSize: 11 },
        min: -boundX,
        max: boundX,
        axisLabel: { show: false },
        axisTick: { show: false },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: "#1a1f29" } },
      },
      yAxis: {
        type: "value",
        name: "加速度（流入增溫 ↑）",
        nameTextStyle: { color: "#9ca3af", fontSize: 11 },
        min: -boundY,
        max: boundY,
        axisLabel: { show: false },
        axisTick: { show: false },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: "#1a1f29" } },
      },
      series: [
        // 背景：象限柔色底 + 中心十字 + 角落標籤
        {
          type: "scatter",
          data: corners,
          silent: true,
          z: 0,
          markArea: {
            silent: true,
            data: [
              tint(Q.主流, [boundX, boundY]),
              tint(Q.高檔鈍化, [boundX, -boundY]),
              tint(Q.轉機, [-boundX, boundY]),
              tint(Q.冷區, [-boundX, -boundY]),
            ],
          },
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: "#475569", width: 1, opacity: 0.7 },
            label: { show: false },
            data: [{ xAxis: 0 }, { yAxis: 0 }],
          },
        },
        // 變化連線：幽靈點(起)→現在(終)，曲線+箭頭
        {
          type: "lines",
          coordinateSystem: "cartesian2d",
          data: connectors,
          silent: true,
          symbol: ["none", "arrow"],
          symbolSize: 7,
          z: 2,
        },
        // 起點幽靈空心點
        { type: "scatter", data: ghosts, silent: true, z: 3 },
        // 現在點 + 標籤
        {
          type: "scatter",
          data: heads,
          label: {
            show: true,
            formatter: "{b}",
            position: "right",
            color: "#e5e7eb",
            fontSize: 10,
            backgroundColor: "rgba(11,14,20,0.55)",
            padding: [1, 3],
            borderRadius: 3,
          },
          labelLayout: { hideOverlap: true, moveOverlap: "shiftY" },
          emphasis: { scale: 1.25 },
          z: 5,
        },
      ],
    });

    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [data, metric, topN]);

  if (data.sectors.length === 0) {
    return <div className="py-10 text-center text-sm text-muted">類股輪動資料不足。</div>;
  }
  return (
    <div className="flex flex-col gap-1">
      <div ref={ref} className="h-[28rem] w-full" />
      <div className="px-2 text-xs text-muted">
        實心點＝現在（大小＝資金量）、空心點＝{data.weeks} 週前、連線＝這段期間的移動（長＝變化大）。前 {topN} 大資金類股。
      </div>
    </div>
  );
}
