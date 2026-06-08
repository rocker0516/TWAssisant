import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { SectorItem } from "../api/client";
import { sectorTileColor } from "../lib/format";

// 熱力圖（ECharts treemap）：大小=成交佔比、顏色=短波段方向×強弱
export function SectorHeatmap({ items, onSelect }: { items: SectorItem[]; onSelect: (id: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || items.length === 0) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.setOption({
      tooltip: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (p: any) => {
          const it = p.data.raw as SectorItem;
          return `${it.name}<br/>強弱 ${it.strength_score?.toFixed(0)}　${it.trend_short}/${it.trend_long}<br/>${it.rotation_stage}　占比 ${it.turnover_share}%`;
        },
      },
      series: [
        {
          type: "treemap",
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          width: "100%",
          height: "100%",
          itemStyle: { borderColor: "#0f1115", borderWidth: 2, gapWidth: 2 },
          label: { show: true, formatter: (p: any) => {
            const it = p.data.raw as SectorItem;
            return `{name|${it.name}}\n{val|${it.strength_score?.toFixed(0)} · ${it.rotation_stage ?? ""}}`;
          }, rich: {
            name: { fontSize: 13, fontWeight: "bold", color: "#fff" },
            val: { fontSize: 10, color: "#e6e8ee" },
          } },
          data: items.map((it) => ({
            name: it.name,
            value: Math.max(it.turnover_share ?? 0.05, 0.08),
            raw: it,
            itemStyle: { color: sectorTileColor(it.trend_short, it.strength_score) },
          })),
        },
      ],
    });
    chart.on("click", (p: any) => {
      if (p.data?.raw) onSelect(p.data.raw.id);
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [items, onSelect]);

  return <div ref={ref} className="h-[440px] w-full" />;
}
