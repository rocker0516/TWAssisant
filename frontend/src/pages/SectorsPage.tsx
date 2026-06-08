import { useNavigate } from "react-router-dom";
import { useSectors } from "../api/client";
import { SectorHeatmap } from "../components/SectorHeatmap";
import { changeColor, fmtNum, fmtPct, scoreColor, trendColor } from "../lib/format";

export default function SectorsPage() {
  const { data, isLoading } = useSectors();
  const navigate = useNavigate();
  const go = (id: number) => navigate(`/sectors/${id}`);

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <div className="mb-4">
        <h1 className="text-xl font-bold">類股行情</h1>
        <p className="text-sm text-muted">盤後資料：{data?.date ?? "—"}　顏色＝短波段方向、大小＝成交佔比</p>
      </div>

      {isLoading && <p className="text-muted">載入中…</p>}

      {data && (
        <>
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
                {data.items.map((s) => (
                  <tr key={s.id} onClick={() => go(s.id)} className="cursor-pointer border-t border-edge hover:bg-panel/60">
                    <td className="px-3 py-2 font-medium">{s.name}</td>
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
                    <td className="px-3 py-2 text-right tabular-nums text-muted">{fmtNum(s.turnover_share)}%</td>
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
