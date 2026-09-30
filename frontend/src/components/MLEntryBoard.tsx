import { Link } from "react-router-dom";
import type { MLEntryBoard as BoardT, MLEntryItem } from "../api/client";

// 今日榜單（附錄 C）：shadow recommendation 卡片 + 通過 Gate 但未入 Top-K 的候選。
// 機率為 calibrated + horizon 投影後的值；Alpha profile = 同日同 ATR 十分位內的百分位（vn）。
// 漲紅跌綠慣例：Target 用紅系、Stop 用綠系。

const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);

function Prob({ label, v, base, tone }: { label: string; v: number | null; base: number | null; tone: "t" | "s" }) {
  const cls = tone === "t" ? "text-rose-300" : "text-emerald-300";
  const ratio = v != null && base ? v / base : null;
  return (
    <div className="text-xs">
      <div className="text-gray-500">{label}</div>
      <div className={`tabular-nums ${cls}`}>{pct(v)}</div>
      {ratio != null && <div className="tabular-nums text-gray-500">{ratio.toFixed(2)}× 基率</div>}
    </div>
  );
}

function Card({ it, base }: { it: MLEntryItem; base: BoardT["market_base"] }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-3">
          <span className="text-2xl font-bold tabular-nums text-gray-300">#{it.rank ?? "—"}</span>
          <Link to={`/stocks/${it.stock_id}`} className="text-lg text-sky-300 hover:underline">
            {it.stock_id} {it.name ?? ""}
          </Link>
          <span className="text-sm tabular-nums text-gray-400">收 {it.close?.toFixed(2) ?? "—"}</span>
        </div>
        <div className="text-right text-xs text-gray-400">
          Score <span className="tabular-nums text-gray-200">{it.recommendation_score?.toFixed(3) ?? "—"}</span>
          <span className="ml-3">Gate {it.gate_pass ? <span className="text-sky-300">通過</span> : "未過"}</span>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-7">
        <Prob label="P Target 3D" v={it.p_target_3d} base={null} tone="t" />
        <Prob label="P Target 5D" v={it.p_target_5d} base={null} tone="t" />
        <Prob label="P Target 10D" v={it.p_target_10d} base={base.market_target_rate} tone="t" />
        <Prob label="P Stop 3D" v={it.p_stop_3d} base={null} tone="s" />
        <Prob label="P Stop 5D" v={it.p_stop_5d} base={null} tone="s" />
        <Prob label="P Stop 10D" v={it.p_stop_10d} base={base.market_stop_rate} tone="s" />
        <div className="text-xs">
          <div className="text-gray-500">Pred MFE 10D</div>
          <div className="tabular-nums text-gray-200">{pct(it.pred_mfe_10d)}</div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-gray-400">
        <span title="同日同 ATR 十分位內的 Target 百分位（波動度中性化）">
          Alpha profile：Target vn <span className="tabular-nums text-gray-200">{it.p_target_vn?.toFixed(2) ?? "—"}</span>
          ・Stop vn <span className="tabular-nums text-gray-200">{it.p_stop_vn?.toFixed(2) ?? "—"}</span>
        </span>
        <span>ATR% <span className="tabular-nums">{pct(it.atr_pct)}</span></span>
        <span>P executable <span className="tabular-nums">{pct(it.p_executable)}</span></span>
      </div>
    </div>
  );
}

export function MLEntryBoardView({ board, isLoading }: { board: BoardT | undefined; isLoading: boolean }) {
  if (isLoading) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  if (!board?.run) return <div className="py-8 text-center text-gray-500">尚無 run——每日盤後 MLEntryDailyStep 執行後產生。</div>;
  const run = board.run;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm text-gray-400">
        <span>{run.signal_date}</span>
        <span>Universe {run.universe_count} 檔</span>
        <span>通過 Gate {run.qualified_count} 檔</span>
        <span>{board.stack.recommendation_label} {run.recommendation_count} 檔（K ≤ 5）</span>
        {board.market_base.market_target_rate != null && (
          <span title="Frozen validation 期間的全市場 10D 命中／停損基率">
            基率 Target {pct(board.market_base.market_target_rate)}・Stop {pct(board.market_base.market_stop_rate)}
          </span>
        )}
      </div>
      {run.no_trade && (
        <div className={`rounded-lg border px-4 py-3 text-sm ${
          run.status === "SYSTEM_NO_TRADE" ? "border-rose-800 bg-rose-950/30 text-rose-200" : "border-gray-700 bg-gray-900 text-gray-300"}`}>
          <div className="font-semibold">{run.status === "SYSTEM_NO_TRADE" ? "暫停出單（系統 fail-closed）" : "今日 NO_TRADE"}</div>
          <div className="text-xs">{run.no_trade_reason}：{run.no_trade_text ?? ""}</div>
        </div>
      )}
      <div className="grid gap-3">
        {board.items.map((it) => <Card key={it.stock_id} it={it} base={board.market_base} />)}
      </div>
      {board.candidates.length > 0 && (
        <details className="rounded-lg border border-gray-800">
          <summary className="cursor-pointer px-4 py-2 text-sm text-gray-300">
            通過 Gate 但未入 Top-K（{board.candidates.length}）
          </summary>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-900 text-left text-xs text-gray-400">
                <tr><th className="px-3 py-2">#</th><th className="px-3 py-2">股票</th><th className="px-3 py-2 text-right">Score</th>
                    <th className="px-3 py-2 text-right">P Target 10D</th><th className="px-3 py-2 text-right">P Stop 10D</th></tr>
              </thead>
              <tbody>
                {board.candidates.map((c) => (
                  <tr key={c.stock_id} className="border-t border-gray-800/60">
                    <td className="px-3 py-2 text-gray-400">{c.rank ?? "—"}</td>
                    <td className="px-3 py-2"><Link to={`/stocks/${c.stock_id}`} className="text-sky-300 hover:underline">{c.stock_id} {c.name ?? ""}</Link></td>
                    <td className="px-3 py-2 text-right tabular-nums">{c.recommendation_score?.toFixed(3) ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-rose-300">{pct(c.p_target_10d)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-emerald-300">{pct(c.p_stop_10d)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
