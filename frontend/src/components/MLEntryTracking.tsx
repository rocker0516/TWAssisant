import { Link } from "react-router-dom";
import type { MLEntryTracking, MLEntryTrackingItem } from "../api/client";

// 追蹤中（spec §2.3，mockup tracking B）：近 10 交易日推薦的即時路徑。
// 量尺 −5%～0～+10%：白棒＝目前報酬；灰帶＝期間 [MAE, MFE]。報酬以 barrier 基準（推薦隔日開盤）起算。

const LO = -0.05, HI = 0.10;
const pos = (v: number) => `${((Math.min(Math.max(v, LO), HI) - LO) / (HI - LO)) * 100}%`;
const pct = (v: number | null, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);

const CHIP: Record<string, { text: (it: MLEntryTrackingItem) => string; cls: string }> = {
  PENDING_ENTRY: { text: () => "待進場", cls: "bg-gray-800 text-gray-300" },
  LIVE: { text: () => "進行中", cls: "bg-gray-800 text-gray-200" },
  TARGET: { text: (it) => `TARGET D${it.hit_day ?? "?"}`, cls: "bg-rose-950 text-rose-300" },
  STOP: { text: (it) => `STOP D${it.hit_day ?? "?"}`, cls: "bg-emerald-950 text-emerald-300" },
  STOP_AMBIGUOUS: { text: (it) => `同日雙觸 D${it.hit_day ?? "?"}`, cls: "bg-amber-950 text-amber-300" },
  TIMEOUT: { text: () => "TIMEOUT", cls: "bg-gray-800 text-gray-400" },
  NOT_ENTERED: { text: () => "無法進場", cls: "bg-gray-800 text-gray-500" },
  DATA_MISSING: { text: () => "資料缺漏", cls: "bg-gray-800 text-gray-500" },
};

function Gauge({ it }: { it: MLEntryTrackingItem }) {
  if (it.ret_now == null || it.mfe == null || it.mae == null) {
    return <span className="text-xs text-gray-500">{it.status === "PENDING_ENTRY" ? "待明日開盤" : "—"}</span>;
  }
  const tip = `MFE ${pct(it.mfe)} ／ MAE ${pct(it.mae)} ／ 距目標 ${((HI - it.ret_now) * 100).toFixed(1)}pp ／ 距停損 ${((it.ret_now - LO) * 100).toFixed(1)}pp`;
  return (
    <div title={tip} className="relative h-2.5 w-36 rounded-full"
         style={{ background: "linear-gradient(90deg, rgb(6 78 59 / .7) 0, rgb(31 41 55) 33.3%, rgb(31 41 55) 33.3%, rgb(76 5 25 / .7) 100%)" }}>
      <div className="absolute top-[3px] h-1 rounded bg-gray-400/40" style={{ left: pos(it.mae), width: `calc(${pos(it.mfe)} - ${pos(it.mae)})` }} />
      <div className="absolute -top-0.5 -bottom-0.5 w-px bg-gray-500" style={{ left: pos(0) }} />
      <div className="absolute -top-1 h-[18px] w-1 -translate-x-1/2 rounded-sm bg-gray-50" style={{ left: pos(it.ret_now) }} />
    </div>
  );
}

export function MLEntryTrackingView({ data, isLoading }: { data?: MLEntryTracking; isLoading: boolean }) {
  if (isLoading) return <div className="py-4 text-center text-sm text-gray-500">載入中…</div>;
  const s = data?.summary;
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-sm font-semibold text-gray-300">追蹤中</span>
        {s && s.n > 0 && (
          <span className="text-xs text-gray-500">
            近 10 日 {s.n} 檔 · <span className="text-rose-300">TARGET {s.target}</span> · <span className="text-emerald-300">STOP {s.stop}</span>
            {" "}· 進行中 {s.live}{s.pending ? ` · 待進場 ${s.pending}` : ""}{s.timeout ? ` · TIMEOUT ${s.timeout}` : ""}
          </span>
        )}
      </div>
      {!data || data.items.length === 0 ? (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 text-sm text-gray-500">近 10 個交易日沒有推薦。</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr>
                <th className="px-3 py-2">推薦日</th><th className="px-3 py-2">股票</th>
                <th className="px-3 py-2 text-right">天數</th><th className="px-3 py-2 text-right">目前報酬</th>
                <th className="px-3 py-2">−5% ─ 0 ── +10%</th><th className="px-3 py-2">狀態</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => {
                const chip = CHIP[it.status];
                return (
                  <tr key={`${it.signal_date}-${it.stock_id}`} className="border-t border-gray-800/60">
                    <td className="px-3 py-2 tabular-nums text-gray-400">{it.signal_date.slice(5)}</td>
                    <td className="px-3 py-2">
                      <Link to={`/stocks/${it.stock_id}`} className="text-sky-300 hover:underline">{it.stock_id} {it.name ?? ""}</Link>
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-gray-400">{it.day_index}/{it.horizon}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${
                      it.ret_now == null ? "text-gray-500" : it.ret_now >= 0 ? "text-rose-300" : "text-emerald-300"}`}>
                      {pct(it.ret_now)}
                    </td>
                    <td className="px-3 py-2"><Gauge it={it} /></td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-1.5 py-0.5 text-[11px] ${chip.cls}`}>{chip.text(it)}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="border-t border-gray-800/60 px-3 py-1.5 text-[11px] text-gray-500">
            白棒＝目前報酬；灰帶＝期間最低～最高（MAE～MFE）。報酬自推薦隔日開盤起算，與模型 barrier 同基準。游標移上看精確距離。
          </div>
        </div>
      )}
    </section>
  );
}
