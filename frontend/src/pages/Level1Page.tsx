import { useState } from "react";
import { Link } from "react-router-dom";
import { useLevel1Board, useLevel1Performance } from "../api/client";

// Level 1 ML 推薦軌（FRS §8）：純預測排序展示，Score/Rank 不是買賣指令。
// K 不固定——Ledger 存全排名，這裡只是視圖。5D 為主軌（2026-08-28 定案）。

const HORIZONS: { key: number; label: string; note: string }[] = [
  { key: 1, label: "1 日", note: "隔日相對強弱" },
  { key: 5, label: "5 日", note: "主軌" },
  { key: 10, label: "10 日", note: "雙週相對強弱" },
];
const K_OPTIONS = [20, 50, 100];

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export default function Level1Page() {
  const [horizon, setHorizon] = useState(5);
  const [k, setK] = useState(20);
  const { data: board, isLoading } = useLevel1Board(horizon, k);
  const { data: perf } = useLevel1Performance(horizon, k);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">ML 排序（Level 1）</h1>
        {board?.date && (
          <span className="text-sm text-gray-400">
            {board.date}・{board.model_version}・Universe {board.universe_size} 檔
          </span>
        )}
      </div>

      <p className="text-xs text-gray-500 leading-relaxed">
        模型每日收盤後預測「未來 N 日相對全市場的強弱排名」，此處只做排序展示，
        分數不是買進指令；進出場、部位與風控屬後續交易層。
      </p>

      <div className="flex flex-wrap items-center gap-2">
        {HORIZONS.map((h) => (
          <button
            key={h.key}
            onClick={() => setHorizon(h.key)}
            className={`rounded-full px-3 py-1 text-sm ${
              horizon === h.key
                ? "bg-sky-600 text-white"
                : "bg-gray-800 text-gray-300 hover:bg-gray-700"
            }`}
            title={h.note}
          >
            {h.label}
            {h.key === 5 && <span className="ml-1 text-[10px] opacity-75">主軌</span>}
          </button>
        ))}
        <span className="mx-2 text-gray-600">|</span>
        {K_OPTIONS.map((n) => (
          <button
            key={n}
            onClick={() => setK(n)}
            className={`rounded px-2 py-1 text-xs ${
              k === n ? "bg-gray-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            }`}
          >
            Top {n}
          </button>
        ))}
      </div>

      {/* 已成熟實績（Ledger 可驗證戰績；資料成熟前顯示等待狀態） */}
      <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 text-sm">
        {perf && perf.n_days > 0 ? (
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            <span>
              已成熟 <b>{perf.n_days}</b> 個預測日
            </span>
            <span>
              Top{k} 平均超額{" "}
              <b className={perf.mean_excess! >= 0 ? "text-rose-300" : "text-emerald-300"}>
                {pct(perf.mean_excess, 2)}
              </b>
            </span>
            <span>
              日勝率 <b>{pct(perf.day_win_rate)}</b>
            </span>
            <span title="0.5 = 無預測力；越高代表 Top-K 實際排名越前">
              平均實際百分位 <b>{perf.mean_actual_pct?.toFixed(3) ?? "—"}</b>
            </span>
          </div>
        ) : (
          <span className="text-gray-500">
            實績待累積：預測需等 N 個交易日後成熟回填，屆時此處顯示 Top-K 的可驗證戰績。
          </span>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead className="bg-gray-900 text-left text-xs text-gray-400">
            <tr>
              <th className="px-3 py-2">#</th>
              <th className="px-3 py-2">股票</th>
              <th className="px-3 py-2 text-right">收盤</th>
              <th className="px-3 py-2 text-right" title="模型預測的未來相對強弱（越高越強）">
                預測分位
              </th>
              <th className="px-3 py-2 text-right" title="成熟後回填的實際 N 日報酬">
                實際報酬
              </th>
              <th className="px-3 py-2 text-right" title="實際落在全市場的百分位">
                實際分位
              </th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-gray-500">
                  載入中…
                </td>
              </tr>
            )}
            {!isLoading && (!board || board.items.length === 0) && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-gray-500">
                  尚無預測資料——每日盤後 pipeline 執行後產生。
                </td>
              </tr>
            )}
            {board?.items.map((it) => (
              <tr key={it.stock_id} className="border-t border-gray-800/60 hover:bg-gray-800/40">
                <td className="px-3 py-2 text-gray-400">{it.rank}</td>
                <td className="px-3 py-2">
                  <Link to={`/stocks/${it.stock_id}`} className="text-sky-300 hover:underline">
                    {it.stock_id} {it.name ?? ""}
                  </Link>
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {it.close?.toFixed(2) ?? "—"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{it.pct_rank.toFixed(3)}</td>
                <td
                  className={`px-3 py-2 text-right tabular-nums ${
                    it.actual_return == null
                      ? "text-gray-600"
                      : it.actual_return >= 0
                        ? "text-rose-300"
                        : "text-emerald-300"
                  }`}
                >
                  {it.actual_return == null ? "未成熟" : pct(it.actual_return, 2)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-400">
                  {it.actual_pct?.toFixed(3) ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
