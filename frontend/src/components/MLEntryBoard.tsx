import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import type { MLEntryBoard as BoardT, MLEntryItem } from "../api/client";

// 今日推薦（spec §2.1，mockup card-layout B）：緊湊表格＋點列展開。
// 估算價由後端依台股升降單位、保守側取整；實際 barrier 從明日開盤起算。漲紅跌綠。

const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const vn = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2).replace(/^0/, ""));
const px = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));
const ratio = (v: number | null, base: number | null) => (v != null && base ? `${(v / base).toFixed(2)}×` : "");

function Head({ withPrice }: { withPrice: boolean }) {
  return (
    <thead className="bg-gray-900 text-left text-xs text-gray-400">
      <tr>
        <th className="px-3 py-2">#</th><th className="px-3 py-2">股票</th>
        <th className="px-3 py-2 text-right">Target 10D</th><th className="px-3 py-2 text-right">Stop 10D</th>
        <th className="px-3 py-2 text-right">MFE</th><th className="px-3 py-2 text-right">vn T/S</th>
        {withPrice && <th className="px-3 py-2 text-right">目標／停損*</th>}
      </tr>
    </thead>
  );
}

function Row({ it, board, withPrice, open, onToggle }: {
  it: MLEntryItem; board: BoardT; withPrice: boolean; open: boolean; onToggle: () => void;
}) {
  const b = board.market_base; const g = board.gate_thresholds;
  return (
    <Fragment>
      <tr onClick={onToggle} className="cursor-pointer border-t border-gray-800/60 hover:bg-gray-800/30">
        <td className="px-3 py-2 tabular-nums text-gray-400">{it.rank ?? "—"}</td>
        <td className="px-3 py-2">
          <Link to={`/stocks/${it.stock_id}`} onClick={(e) => e.stopPropagation()} className="text-sky-300 hover:underline">
            {it.stock_id} {it.name ?? ""}
          </Link>
        </td>
        <td className="px-3 py-2 text-right tabular-nums text-rose-300">
          {pct(it.p_target_10d)} <span className="text-xs text-gray-500">{ratio(it.p_target_10d, b.market_target_rate)}</span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums text-emerald-300">
          {pct(it.p_stop_10d)} <span className="text-xs text-gray-500">{ratio(it.p_stop_10d, b.market_stop_rate)}</span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums">{pct(it.pred_mfe_10d)}</td>
        <td className="px-3 py-2 text-right tabular-nums"
            title={g.target_vn_min != null ? `Gate：Target vn ≥ ${g.target_vn_min}、Stop vn ≤ ${g.stop_vn_max}（同日同 ATR 十分位內百分位）` : ""}>
          {vn(it.p_target_vn)}/{vn(it.p_stop_vn)}
        </td>
        {withPrice && (
          <td className="px-3 py-2 text-right tabular-nums">
            <span className="text-rose-300">{px(it.est_target_price)}</span>
            <span className="text-gray-500"> / </span>
            <span className="text-emerald-300">{px(it.est_stop_price)}</span>
          </td>
        )}
      </tr>
      {open && (
        <tr className="bg-gray-900/70">
          <td colSpan={withPrice ? 7 : 6} className="px-3 py-2 text-xs tabular-nums text-gray-400">
            時序 T {pct(it.p_target_3d)}→{pct(it.p_target_5d)}→{pct(it.p_target_10d)}
            <span className="mx-2 text-gray-600">｜</span>
            S {pct(it.p_stop_3d)}→{pct(it.p_stop_5d)}→{pct(it.p_stop_10d)}
            <span className="mx-2 text-gray-600">｜</span>MFE {pct(it.pred_mfe_3d)}→{pct(it.pred_mfe_5d)}→{pct(it.pred_mfe_10d)}
            <span className="mx-2 text-gray-600">｜</span>ATR {pct(it.atr_pct)}
            <span className="mx-2 text-gray-600">｜</span>Score {it.recommendation_score?.toFixed(3) ?? "—"}
            <span className="mx-2 text-gray-600">｜</span>P exec {pct(it.p_executable)}
            <span className="mx-2 text-gray-600">｜</span>收 {px(it.close)}
            <Link to={`/stocks/${it.stock_id}`} className="ml-3 text-sky-300 hover:underline">個股頁 →</Link>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function MLEntryBoardView({ board, isLoading }: { board: BoardT | undefined; isLoading: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  if (isLoading) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  if (!board?.run) return <div className="py-8 text-center text-gray-500">尚無 run——每日盤後 MLEntryDailyStep 執行後產生。</div>;
  const run = board.run; const b = board.market_base;
  const toggle = (id: string) => setOpen((o) => (o === id ? null : id));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-500">
        <span className="text-sm font-semibold text-gray-300">今日推薦</span>
        {b.market_target_rate != null && (
          <span>基率 Target {pct(b.market_target_rate)}・Stop {pct(b.market_stop_rate)}（Frozen 期間）</span>
        )}
      </div>
      {!run.no_trade && board.items.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <Head withPrice />
            <tbody>
              {board.items.map((it) => (
                <Row key={it.stock_id} it={it} board={board} withPrice open={open === it.stock_id} onToggle={() => toggle(it.stock_id)} />
              ))}
            </tbody>
          </table>
          <div className="border-t border-gray-800/60 px-3 py-1.5 text-[11px] text-gray-500">
            * 以收盤估算並依升降單位取保守側；實際 barrier 從明日開盤 ×1.10／×0.95 起算。點列展開時序。
          </div>
        </div>
      )}
      {board.candidates.length > 0 && (
        <details className="rounded-lg border border-gray-800">
          <summary className="cursor-pointer px-4 py-2 text-sm text-gray-300">通過 Gate 但未入 Top-K（{board.candidates.length}）</summary>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <Head withPrice={false} />
              <tbody>
                {board.candidates.map((c) => (
                  <Row key={c.stock_id} it={c} board={board} withPrice={false} open={open === c.stock_id} onToggle={() => toggle(c.stock_id)} />
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
