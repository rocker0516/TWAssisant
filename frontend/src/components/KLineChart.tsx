import { useEffect, useRef } from "react";
import { createChart, type IChartApi, type Time } from "lightweight-charts";
import type { Candle } from "../api/client";

const UP = "#e11d48"; // 紅漲
const DOWN = "#16a34a"; // 綠跌
const MA_COLORS: Record<string, string> = { ma5: "#eab308", ma20: "#38bdf8", ma60: "#a855f7" };

export function KLineChart({ candles }: { candles: Candle[] }) {
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

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles]);

  return <div ref={ref} className="h-[420px] w-full" />;
}
