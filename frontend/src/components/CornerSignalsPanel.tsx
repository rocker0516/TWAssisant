import { useState } from "react";
import { Link } from "react-router-dom";
import { useCornerReview, useCornerSignals, type CornerFired } from "../api/client";

/** 高確信角落影子軌（實驗）：30 個反推挖掘角落的當日亮燈區。
 *
 * 純觀察層——與會噴排序無關；多數日子全空屬正常（低波動期＝空手）。
 * 家族：深崩期（大盤崩跌時的超賣高波動）/ 回檔期（大盤 20 日跌 4%+）/ 全天候。
 */

const FAMILY_STYLE: Record<CornerFired["family"], string> = {
  crash: "border-red-700/60 bg-red-950/40 text-red-200",
  dip: "border-amber-700/60 bg-amber-950/40 text-amber-200",
  allweather: "border-violet-700/60 bg-violet-950/40 text-violet-200",
};

function yearBand(c: CornerFired): string {
  const hits = Object.values(c.per_year)
    .map((y) => y.hit)
    .filter((h): h is number => h != null);
  if (!hits.length) return "";
  return `${Math.round(Math.min(...hits))}~${Math.round(Math.max(...hits))}%`;
}

export function CornerSignalsPanel() {
  const { data } = useCornerSignals();
  const [open, setOpen] = useState(true);
  const [showReview, setShowReview] = useState(false);
  const { data: review } = useCornerReview(showReview);
  if (!data) return null;

  const fired = data.fired;
  const stockCount = new Set(fired.flatMap((c) => c.stocks.map((s) => s.stock_id))).size;

  return (
    <div className="mb-4 rounded-lg border border-violet-800/50 bg-violet-950/20 px-3.5 py-2.5 text-sm">
      <button
        onClick={() => setOpen((s) => !s)}
        className="flex w-full items-center gap-2 text-left"
      >
        <span className="text-violet-300">◆ 高確信角落</span>
        <span className="rounded border border-violet-700/50 px-1.5 py-0.5 text-[10px] text-violet-300">
          實驗中
        </span>
        {fired.length > 0 ? (
          <span className="text-xs text-violet-200">
            今日 <b>{fired.length}</b> 個角落亮燈 · <b>{stockCount}</b> 檔
          </span>
        ) : (
          <span className="text-xs text-muted">今日無訊號（多數日子空手屬正常）</span>
        )}
        <span className="ml-auto text-xs text-muted">{open ? "收起" : "展開"}</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2">
          {fired.map((c) => (
            <div key={c.id} className={`rounded-md border px-3 py-2 ${FAMILY_STYLE[c.family]}`}>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <b>{c.family_label}</b>
                <span className="opacity-80">{c.atoms.join(" ∧ ")}</span>
                <span className="ml-auto whitespace-nowrap">
                  歷史分年 {yearBand(c)}（地板 {Math.round(c.floor)}%）
                </span>
              </div>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {c.stocks.map((s) => (
                  <Link
                    key={s.stock_id}
                    to={`/stocks/${s.stock_id}`}
                    className="rounded-md border border-white/15 bg-black/20 px-2 py-0.5 text-xs hover:underline"
                  >
                    {s.name} <span className="opacity-60">{s.stock_id}</span>
                    {s.close != null ? <span className="ml-1 opacity-60">（{s.close} 元）</span> : null}
                  </Link>
                ))}
              </div>
            </div>
          ))}
          <div className="text-[11px] leading-relaxed text-muted">
            {data.note}
            {data.recent.length > 0 && (
              <span className="ml-2">
                近期訊號日：
                {data.recent.slice(0, 8).map((r) => (
                  <span key={r.date} className="ml-1.5 whitespace-nowrap">
                    {r.date.slice(5)}（{r.signals}筆）
                  </span>
                ))}
              </span>
            )}
          </div>

          {/* 影子期回看：滿窗口徑結算（滿30交易日才進命中率，避免贏家提早結算灌水） */}
          <button
            onClick={() => setShowReview((s) => !s)}
            className="rounded-md border border-violet-700/50 px-2 py-1 text-xs text-violet-300 hover:bg-violet-900/30"
          >
            {showReview ? "收起回看" : "▶ 影子期回看（實際命中結算）"}
          </button>
          {showReview && review && (
            <div className="space-y-2 text-xs">
              <div className="rounded-md border border-edge bg-panel px-3 py-2 leading-relaxed">
                整體（去重同股同日）：滿窗 <b>{review.overall_unique.matured}</b> 筆、命中{" "}
                <b>{review.overall_unique.hits}</b>
                {review.overall_unique.hit_rate != null && (
                  <>（<b>{review.overall_unique.hit_rate}%</b>）</>
                )}
                　·　未滿窗 {review.overall_unique.pending} 筆
                （其中 <b className="text-up">{review.overall_unique.early_hits}</b> 筆已先摸到 +10%）
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-[11px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="py-1 pr-2">角落</th>
                      <th className="py-1 pr-2">家族</th>
                      <th className="py-1 pr-2">歷史地板</th>
                      <th className="py-1 pr-2">滿窗命中</th>
                      <th className="py-1 pr-2">未滿窗（已先摸到）</th>
                    </tr>
                  </thead>
                  <tbody>
                    {review.by_corner
                      .filter((r) => r.matured > 0 || r.pending > 0)
                      .map((r) => (
                        <tr key={r.id} className="border-t border-edge/60">
                          <td className="py-1 pr-2">{r.atoms.join(" ∧ ")}</td>
                          <td className="py-1 pr-2">{r.family_label}</td>
                          <td className="py-1 pr-2">{Math.round(r.floor)}%</td>
                          <td className="py-1 pr-2">
                            {r.matured > 0 ? (
                              <span className={r.hit_rate != null && r.hit_rate >= r.floor - 5 ? "text-up" : "text-amber-300"}>
                                {r.hits}/{r.matured}（{r.hit_rate}%）
                              </span>
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </td>
                          <td className="py-1 pr-2 text-muted">
                            {r.pending}
                            {r.early_hits > 0 && <span className="text-up">（{r.early_hits}）</span>}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              <div className="leading-relaxed text-muted">{review.note}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
