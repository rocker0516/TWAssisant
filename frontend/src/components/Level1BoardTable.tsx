import { Link } from "react-router-dom";
import type { Level1Board } from "../api/client";

// 榜單表（設計 §3.2）：score 取代 degenerate 的預測分位；adv20 是倉位脈絡非過濾。

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function Level1BoardTable({ board, isLoading }: {
  board: Level1Board | undefined;
  isLoading: boolean;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="w-full text-sm">
        <thead className="bg-gray-900 text-left text-xs text-gray-400">
          <tr>
            <th className="px-3 py-2">#</th>
            <th className="px-3 py-2">股票</th>
            <th className="px-3 py-2 text-right">收盤</th>
            <th className="px-3 py-2 text-right"
                title="近 20 個交易日平均成交值——倉位規模脈絡">
              20日均成交值
            </th>
            <th className="px-3 py-2 text-right"
                title="模型原始分數，僅供同日同 horizon 內比較，跨日不可比">
              模型分數
            </th>
            <th className="px-3 py-2 text-right" title="成熟後回填的實際 N 日報酬">
              實際報酬
            </th>
            <th className="px-3 py-2 text-right"
                title="實際落在池內的百分位；0.5＝無資訊">
              實際分位
            </th>
          </tr>
        </thead>
        <tbody>
          {isLoading && (
            <tr>
              <td colSpan={7} className="px-3 py-6 text-center text-gray-500">
                載入中…
              </td>
            </tr>
          )}
          {!isLoading && (!board || board.items.length === 0) && (
            <tr>
              <td colSpan={7} className="px-3 py-6 text-center text-gray-500">
                尚無預測資料——每日盤後 pipeline 執行後產生。
              </td>
            </tr>
          )}
          {board?.items.map((it) => (
            <tr key={it.stock_id}
                className="border-t border-gray-800/60 hover:bg-gray-800/40">
              <td className="px-3 py-2 text-gray-400">{it.rank}</td>
              <td className="px-3 py-2">
                <Link to={`/stocks/${it.stock_id}`}
                      className="text-sky-300 hover:underline">
                  {it.stock_id} {it.name ?? ""}
                </Link>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {it.close?.toFixed(2) ?? "—"}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-gray-400">
                {it.adv20 != null ? `${(it.adv20 / 1e8).toFixed(1)} 億` : "—"}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {it.score.toFixed(4)}
              </td>
              <td className={`px-3 py-2 text-right tabular-nums ${
                it.actual_return == null
                  ? "text-gray-600"
                  : it.actual_return >= 0
                    ? "text-rose-300"
                    : "text-emerald-300"
              }`}>
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
  );
}
