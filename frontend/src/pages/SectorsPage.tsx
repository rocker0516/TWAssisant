import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useSectors, type SectorItem } from "../api/client";
import { SectorHeatmap } from "../components/SectorHeatmap";
import { changeColor, fmtNum, fmtPct, scoreColor, trendColor } from "../lib/format";

// 資金焦點：集中在哪（佔比前3）／正移入哪（佔比升幅前3）／法人買哪（5日買超前3）
function FundFocusBar({ items, onSelect }: { items: SectorItem[]; onSelect: (id: number) => void }) {
  const focus = useMemo(() => {
    const byShare = [...items]
      .filter((s) => s.turnover_share != null)
      .sort((a, b) => (b.turnover_share ?? 0) - (a.turnover_share ?? 0))
      .slice(0, 3);
    const byInflow = [...items]
      .filter((s) => (s.turnover_chg5 ?? 0) > 0.05)
      .sort((a, b) => (b.turnover_chg5 ?? 0) - (a.turnover_chg5 ?? 0))
      .slice(0, 3);
    const byInst = [...items]
      .filter((s) => (s.foreign_net ?? 0) > 0)
      .sort((a, b) => (b.foreign_net ?? 0) - (a.foreign_net ?? 0))
      .slice(0, 3);
    return { byShare, byInflow, byInst };
  }, [items]);

  const chip = (s: SectorItem, detail: string, detailCls?: string) => (
    <button key={s.id} onClick={() => onSelect(s.id)}
      className="rounded-full bg-panel2 px-2.5 py-1 text-xs text-gray-200 hover:bg-sky-900/50">
      {s.name} <span className={detailCls ?? "text-muted"}>{detail}</span>
    </button>
  );

  return (
    <div className="mb-4 rounded-xl border border-edge bg-panel p-3">
      <div className="flex flex-col gap-2 text-sm">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 shrink-0 text-xs text-muted">💰 資金集中</span>
          {focus.byShare.map((s) =>
            chip(s, `${fmtNum(s.turnover_share)}%${
              s.turnover_chg5 != null ? (s.turnover_chg5 > 0.05 ? " ↑" : s.turnover_chg5 < -0.05 ? " ↓" : "") : ""
            }`, s.turnover_chg5 != null && s.turnover_chg5 > 0.05 ? "text-red-300" : undefined))}
          <span className="ml-1 text-xs text-muted">（成交佔比＝市場的錢在哪；↑↓＝相對前5日均）</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 shrink-0 text-xs text-muted">🔀 正在移入</span>
          {focus.byInflow.length
            ? focus.byInflow.map((s) => chip(s, `+${s.turnover_chg5?.toFixed(2)}pp`, "text-red-300"))
            : <span className="text-xs text-muted">無明顯移入（佔比分布與前5日相近）</span>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 shrink-0 text-xs text-muted">🏦 法人在買</span>
          {focus.byInst.length
            ? focus.byInst.map((s) => chip(s, `${fmtNum(s.foreign_net, 0)} 張`, "text-red-300"))
            : <span className="text-xs text-muted">近5日無類股獲法人淨買超</span>}
        </div>
      </div>
    </div>
  );
}

export default function SectorsPage() {
  const { data, isLoading } = useSectors();
  const navigate = useNavigate();
  const go = (id: number) => navigate(`/sectors/${id}`);
  // 佔比前 3＝🔥 資金集中處（表格列標記用）
  const hotIds = useMemo(() => {
    const xs = [...(data?.items ?? [])]
      .filter((s) => s.turnover_share != null)
      .sort((a, b) => (b.turnover_share ?? 0) - (a.turnover_share ?? 0))
      .slice(0, 3);
    return new Set(xs.map((s) => s.id));
  }, [data]);

  return (
    <div className="w-full px-6 py-6">
      <div className="mb-4">
        <h1 className="text-xl font-bold">類股行情</h1>
        <p className="text-sm text-muted">盤後資料：{data?.date ?? "—"}　顏色＝短波段方向、大小＝成交佔比</p>
      </div>

      {isLoading && <p className="text-muted">載入中…</p>}

      {data && (
        <>
          <FundFocusBar items={data.items} onSelect={go} />
          <div className="mb-6 rounded-xl border border-edge bg-panel p-3">
            <SectorHeatmap items={data.items} onSelect={go} />
            <div className="mt-2 flex gap-4 px-1 text-xs text-muted">
              <span><span className="text-up">■</span> 偏多</span>
              <span><span className="text-down">■</span> 偏空</span>
              <span className="text-muted">■ 中性</span>
              <span className="ml-auto">濃淡＝強弱、面積＝成交佔比</span>
            </div>
          </div>

          <div className="overflow-hidden rounded-xl border border-edge">
            <table className="w-full text-sm">
              <thead className="bg-panel2 text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 text-left">類股</th>
                  <th className="px-3 py-2 text-left">強弱</th>
                  <th className="px-3 py-2 text-center">短波段</th>
                  <th className="px-3 py-2 text-center">中長期</th>
                  <th className="px-3 py-2 text-center">輪動階段</th>
                  <th className="px-3 py-2 text-right">5日</th>
                  <th className="px-3 py-2 text-right">20日</th>
                  <th className="px-3 py-2 text-right">法人(張)</th>
                  <th className="px-3 py-2 text-right">占比</th>
                </tr>
              </thead>
              <tbody>
                {(data.items as SectorItem[]).map((s) => (
                  <tr key={s.id} onClick={() => go(s.id)} className="cursor-pointer border-t border-edge hover:bg-panel/60">
                    <td className="px-3 py-2 font-medium">
                      {s.name}
                      {hotIds.has(s.id) && <span className="ml-1" title="資金集中（成交佔比前3）">🔥</span>}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className={`w-8 tabular-nums ${scoreColor(s.strength_score)}`}>{s.strength_score?.toFixed(0)}</span>
                        <div className="h-1.5 w-20 overflow-hidden rounded-full bg-panel2">
                          <div className="h-full rounded-full bg-sky-500" style={{ width: `${Math.min(100, s.strength_score ?? 0)}%` }} />
                        </div>
                      </div>
                    </td>
                    <td className={`px-3 py-2 text-center ${trendColor(s.trend_short)}`}>{s.trend_short}</td>
                    <td className={`px-3 py-2 text-center ${trendColor(s.trend_long)}`}>{s.trend_long}</td>
                    <td className="px-3 py-2 text-center text-gray-300">{s.rotation_stage}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.momentum_5)}`}>{fmtPct(s.momentum_5)}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.momentum_20)}`}>{fmtPct(s.momentum_20)}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.foreign_net)}`}>{fmtNum(s.foreign_net, 0)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted">
                      {fmtNum(s.turnover_share)}%
                      {s.turnover_chg5 != null && Math.abs(s.turnover_chg5) > 0.05 && (
                        <span className={`ml-1 text-xs ${s.turnover_chg5 > 0 ? "text-red-300" : "text-emerald-300"}`}
                          title={`成交佔比 vs 前5日均 ${s.turnover_chg5 > 0 ? "+" : ""}${s.turnover_chg5.toFixed(2)} 個百分點`}>
                          {s.turnover_chg5 > 0 ? "▲" : "▼"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
