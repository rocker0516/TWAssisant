import { useEffect, useRef } from "react";
import { createChart, LineStyle, type IChartApi, type SeriesMarker, type Time } from "lightweight-charts";
import type { Candle, LevelDTO, MarkDTO } from "../api/client";

const UP = "#e11d48"; // 紅漲
const DOWN = "#16a34a"; // 綠跌
const MA_COLORS: Record<string, string> = { ma5: "#eab308", ma20: "#38bdf8", ma60: "#a855f7" };
const SUPPORT_COLOR = "#16a34a"; // 支撐：綠
const RESIST_COLOR = "#e11d48"; // 壓力：紅

export function KLineChart({ candles, levels = [], marks = [] }: { candles: Candle[]; levels?: LevelDTO[]; marks?: MarkDTO[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || candles.length === 0) return;
    const chart: IChartApi = createChart(ref.current, {
      layout: { background: { color: "transparent" }, textColor: "#8b93a7" },
      grid: { vertLines: { color: "#1f232c" }, horzLines: { color: "#1f232c" } },
      rightPriceScale: { borderColor: "#2a2f3a" },
      timeScale: { borderColor: "#2a2f3a", rightOffset: 4 },
      crosshair: { mode: 0 },
      autoSize: true,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN,
    });
    candleSeries.setData(
      candles
        .filter((c) => c.close != null)
        .map((c) => ({
          time: c.date as Time,
          open: c.open!, high: c.high!, low: c.low!, close: c.close!,
        })),
    );

    for (const key of ["ma5", "ma20", "ma60"] as const) {
      const line = chart.addLineSeries({ color: MA_COLORS[key], lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      line.setData(
        candles
          .filter((c) => c[key] != null)
          .map((c) => ({ time: c.date as Time, value: c[key]! })),
      );
    }

    const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      candles
        .filter((c) => c.volume != null)
        .map((c) => ({
          time: c.date as Time,
          value: c.volume!,
          color: (c.close ?? 0) >= (c.open ?? 0) ? "#e11d4855" : "#16a34a55",
        })),
    );

    // 支撐/壓力水平線（強度越高線越粗；虛線標價）
    for (const lv of levels) {
      if (lv.price == null) continue;
      const isSup = lv.kind === "support";
      candleSeries.createPriceLine({
        price: lv.price,
        color: isSup ? SUPPORT_COLOR : RESIST_COLOR,
        lineWidth: lv.strength >= 70 ? 2 : 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${isSup ? "支撐" : "壓力"} ${lv.strength}`,
      });
    }

    // 推薦標記：金✓=30日內達標、灰✗=未達標、藍…=評估中（口徑同回看）
    if (marks.length > 0) {
      const times = new Set(candles.map((c) => c.date));
      candleSeries.setMarkers(
        marks
          .filter((m) => times.has(m.date))
          .map((m): SeriesMarker<Time> => ({
            time: m.date as Time,
            position: "belowBar",
            shape: m.status === "hit" ? "arrowUp" : "circle",
            color: m.status === "hit" ? "#f59e0b" : m.status === "miss" ? "#6b7280" : "#38bdf8",
            text: m.status === "hit" ? `推薦✓${m.ret_pct != null ? ` +${m.ret_pct}%` : ""}` : m.status === "miss" ? "推薦✗" : "推薦…",
          })),
      );
    }

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, levels, marks]);

  return <div ref={ref} className="h-[420px] w-full" />;
}
