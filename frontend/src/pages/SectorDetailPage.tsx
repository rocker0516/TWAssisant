import { Link, useParams } from "react-router-dom";
import { useSectorDetail } from "../api/client";
import { changeColor, fmtNum, fmtPct, scoreColor, trendColor } from "../lib/format";

function DirCard({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="rounded-lg border border-edge bg-panel2 p-3 text-center">
      <div className="text-xs text-muted">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${trendColor(value)}`}>{value ?? "—"}</div>
    </div>
  );
}

function DimBar({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-10 text-muted">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-panel2">
        <div className="h-full rounded-full bg-sky-500" style={{ width: `${Math.min(100, value ?? 0)}%` }} />
      </div>
      <span className="w-8 text-right tabular-nums">{value?.toFixed(0) ?? "—"}</span>
    </div>
  );
}

export default function SectorDetailPage() {
  const { id } = useParams();
  const { data, isLoading, isError } = useSectorDetail(id);

  if (isLoading) return <div className="p-6 text-muted">載入中…</div>;
  if (isError || !data) return <div className="p-6 text-down">找不到類股</div>;
  const s = data.sector;

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <Link to="/sectors" className="text-sm text-sky-400 hover:underline">← 類股行情</Link>

      <div className="mb-5 mt-3 flex items-center gap-3">
        <h1 className="text-2xl font-bold">{s.name}</h1>
        <span className={`text-lg font-semibold ${scoreColor(s.strength_score)}`}>強弱 {s.strength_score?.toFixed(0)}</span>
        <span className="rounded bg-panel2 px-2 py-0.5 text-sm text-gray-300">{s.rotation_stage}</span>
      </div>

      {/* 方向總結卡 */}
      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-3 text-sm font-semibold">方向判讀</div>
          <div className="grid grid-cols-3 gap-3">
            <DirCard label="短波段方向" value={s.trend_short} />
            <DirCard label="中長期方向" value={s.trend_long} />
            <DirCard label="輪動階段" value={s.rotation_stage} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-6 text-sm text-muted">
            <div>近5日動能 <span className={changeColor(s.momentum_5)}>{fmtPct(s.momentum_5)}</span></div>
            <div>近20日動能 <span className={changeColor(s.momentum_20)}>{fmtPct(s.momentum_20)}</span></div>
            <div>法人5日 <span className={changeColor(s.foreign_net)}>{fmtNum(s.foreign_net, 0)} 張</span></div>
            <div>成交佔比 {fmtNum(s.turnover_share)}%</div>
          </div>
        </div>
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-3 text-sm font-semibold">強弱三維度</div>
          <div className="flex flex-col gap-3">
            <DimBar label="動能" value={s.dim_momentum} />
            <DimBar label="資金" value={s.dim_fund} />
            <DimBar label="技術" value={s.dim_tech} />
          </div>
          <p className="mt-3 text-xs text-muted">方向與輪動為趨勢判讀，非預測保證。</p>
        </div>
      </div>

      {/* 成分股（領漲排序、★已推薦）*/}
      <div className="overflow-hidden rounded-xl border border-edge">
        <div className="bg-panel2 px-3 py-2 text-sm font-semibold">成分股（{data.constituents.length}）· 領漲排序</div>
        <table className="w-full text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th className="px-3 py-2 text-left">股票</th>
              <th className="px-3 py-2 text-right">現價漲跌</th>
              <th className="px-3 py-2 text-right">波段</th>
              <th className="px-3 py-2 text-right">長線</th>
              <th className="px-3 py-2 text-center">推薦</th>
            </tr>
          </thead>
          <tbody>
            {data.constituents.map((c) => (
              <tr key={c.stock_id} className="border-t border-edge hover:bg-panel/60">
                <td className="px-3 py-2">
                  <Link to={`/stocks/${c.stock_id}`} className="hover:underline">
                    {c.name} <span className="text-xs text-muted">{c.stock_id}</span>
                  </Link>
                </td>
                <td className={`px-3 py-2 text-right tabular-nums ${changeColor(c.change_pct)}`}>{fmtPct(c.change_pct)}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${scoreColor(c.wave_score)}`}>{c.wave_score?.toFixed(0) ?? "—"}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${scoreColor(c.long_score)}`}>{c.long_score?.toFixed(0) ?? "—"}</td>
                <td className="px-3 py-2 text-center">{c.recommended ? "★" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
