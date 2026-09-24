import { useState } from "react";
import { Link } from "react-router-dom";
import { useCornerReview, useCornerSignals, type CornerFired } from "../api/client";

/** 高確信角落影子軌（實驗）：30 個反推挖掘角落的當日亮燈。
 *
 * 純觀察層——與會噴排序無關；多數日子全空屬正常（低波動期＝空手）。
 * 家族：深崩期（大盤崩跌時的超賣高波動）/ 回檔期（大盤 20 日跌 4%+）/ 全天候。
 *
 * 兩個出口：
 *   CornerSignalsStrip — 進場推薦頁主清單下方的獨立小分區，只在亮燈時渲染。
 *     角落與會噴分數是兩套正交篩子，亮燈股多半不在推薦清單裡（近期 15 筆訊號
 *     只有 2 筆落在清單上），所以獨立分區而非卡片徽章。
 *   CornerLabSection — 實驗室完整版：年帶、近期訊號日、滿窗回看結算。
 */

const FAMILY_STYLE: Record<CornerFired["family"], string> = {
  crash: "border-red-700/60 bg-red-950/40 text-red-200",
  dip: "border-amber-700/60 bg-amber-950/40 text-amber-200",
  allweather: "border-violet-700/60 bg-violet-950/40 text-violet-200",
};

/** stable_edge 用自己的外框：它的證據型態與地板紀律不同，混色會被誤讀成同一種東西。 */
const EDGE_STYLE = "border-sky-600/60 bg-sky-950/40 text-sky-200";

function cardStyle(c: CornerFired): string {
  return c.origin === "stable_edge" ? EDGE_STYLE : FAMILY_STYLE[c.family];
}

function yearBand(c: CornerFired): string {
  const hits = Object.values(c.per_year)
    .map((y) => y.hit)
    .filter((h): h is number => h != null);
  if (!hits.length) return "";
  return `${Math.round(Math.min(...hits))}~${Math.round(Math.max(...hits))}%`;
}

/** 角落的招牌數字：地板紀律看地板，超額紀律看兩窗增量（它的地板本來就不高）。 */
function Headline({ c, verbose }: { c: CornerFired; verbose?: boolean }) {
  if (c.origin !== "stable_edge") {
    return (
      <span className="ml-auto whitespace-nowrap">
        {verbose ? `歷史分年 ${yearBand(c)}（地板 ${Math.round(c.floor)}%）` : `地板 ${Math.round(c.floor)}%`}
      </span>
    );
  }
  // OOS 增量是試跑實測，與挖掘期反號時要比挖掘數字更顯眼——那才是它現在的成績
  const oos = c.oos_edge_pp;
  return (
    <span className="ml-auto whitespace-nowrap">
      同日超額 挖 <b>+{c.edge_mine_pp}</b>/後 <b>+{c.edge_holdout_pp}</b>pp
      {oos != null && (
        <span className={oos < 0 ? "ml-1 text-amber-300" : "ml-1 text-emerald-300"}>
          · OOS <b>{oos > 0 ? "+" : ""}{oos}</b>pp
        </span>
      )}
      {verbose && c.holdout_hit != null && (
        <span className="opacity-70">
          {" "}· holdout {c.holdout_hit}%（n={c.holdout_n}）· 地板 {Math.round(c.floor)}%
        </span>
      )}
    </span>
  );
}

function EdgeBadge() {
  return (
    <span className="rounded border border-sky-600/60 px-1 py-0.5 text-[10px] text-sky-300">
      超額·試跑
    </span>
  );
}

function StockChips({ c }: { c: CornerFired }) {
  return (
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
  );
}

/** 進場推薦頁：主清單下方的角落訊號分區。無訊號＝不佔版面。 */
export function CornerSignalsStrip() {
  const { data } = useCornerSignals();
  if (!data || data.fired.length === 0) return null;

  const stockCount = new Set(data.fired.flatMap((c) => c.stocks.map((s) => s.stock_id))).size;

  return (
    <div className="mt-6 rounded-lg border border-violet-800/50 bg-violet-950/20 px-3.5 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-violet-300">◆ 高確信角落</span>
        <span className="rounded border border-violet-700/50 px-1.5 py-0.5 text-[10px] text-violet-300">
          實驗中
        </span>
        <span className="text-xs text-violet-200">
          今日 <b>{data.fired.length}</b> 個角落亮燈 · <b>{stockCount}</b> 檔
        </span>
        <Link to="/lab" className="ml-auto text-xs text-muted hover:text-violet-300">
          回看結算 →
        </Link>
      </div>

      <div className="mt-2 space-y-2">
        {data.fired.map((c) => (
          <div key={c.id} className={`rounded-md border px-3 py-2 ${cardStyle(c)}`}>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <b>{c.family_label}</b>
              {c.origin === "stable_edge" && <EdgeBadge />}
              <span className="opacity-80">{c.atoms.join(" ∧ ")}</span>
              <Headline c={c} />
            </div>
            <StockChips c={c} />
          </div>
        ))}
      </div>

      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        獨立於會噴排序的觀察層——這些標的多半不在上方清單裡（兩套篩子正交）。
        forward 驗證累積中，尚未取得推薦權。
      </p>
    </div>
  );
}

/** 實驗室：角落完整檢視（年帶、近期訊號日、滿窗回看結算）。 */
export function CornerLabSection() {
  const { data } = useCornerSignals();
  const [showReview, setShowReview] = useState(false);
  const { data: review } = useCornerReview(showReview);
  if (!data) return null;

  const fired = data.fired;
  const stockCount = new Set(fired.flatMap((c) => c.stocks.map((s) => s.stock_id))).size;

  return (
    <section className="mb-8">
      <h2 className="mb-2 flex flex-wrap items-center gap-2 text-base font-semibold">
        ◆ 高確信角落影子軌
        <span className="rounded border border-violet-700/50 px-1.5 py-0.5 text-[10px] font-normal text-violet-300">
          實驗中
        </span>
        <span className="text-xs font-normal text-muted">
          {fired.length > 0
            ? `今日 ${fired.length} 個角落亮燈 · ${stockCount} 檔`
            : "今日無訊號（多數日子空手屬正常）"}
        </span>
      </h2>

      <div className="rounded-xl border border-edge bg-panel px-3.5 py-3 text-sm">
        <div className="space-y-2">
          {fired.map((c) => (
            <div key={c.id} className={`rounded-md border px-3 py-2 ${cardStyle(c)}`}>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <b>{c.family_label}</b>
                {c.origin === "stable_edge" && <EdgeBadge />}
                <span className="opacity-80">{c.atoms.join(" ∧ ")}</span>
                <Headline c={c} verbose />
              </div>
              {c.caveat && (
                <p className="mt-1 text-[11px] leading-relaxed text-amber-300/90">⚠ {c.caveat}</p>
              )}
              <StockChips c={c} />
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

          {/* 影子期回看：滿窗口徑結算（滿 10 交易日才進命中率，避免贏家提早結算灌水） */}
          <button
            onClick={() => setShowReview((s) => !s)}
            className="rounded-md border border-violet-700/50 px-2 py-1 text-xs text-violet-300 hover:bg-violet-900/30"
          >
            {showReview ? "收起回看" : "▶ 影子期回看（實際命中結算）"}
          </button>
          {showReview && review && (
            <div className="space-y-2 text-xs">
              <div className="rounded-md border border-edge bg-panel2 px-3 py-2 leading-relaxed">
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
                      <th className="py-1 pr-2">及格線</th>
                      <th className="py-1 pr-2">滿窗命中</th>
                      <th className="py-1 pr-2">未滿窗（已先摸到）</th>
                    </tr>
                  </thead>
                  <tbody>
                    {review.by_corner
                      .filter((r) => r.matured > 0 || r.pending > 0)
                      .map((r) => (
                        <tr key={r.id} className="border-t border-edge/60">
                          <td className="py-1 pr-2">
                            {r.origin === "stable_edge" && (
                              <span className="mr-1 text-sky-300">超額·試跑</span>
                            )}
                            {r.atoms.join(" ∧ ")}
                          </td>
                          <td className="py-1 pr-2">{r.family_label}</td>
                          {/* 及格線依紀律而異：地板紀律比地板，超額紀律比它自己的 holdout 命中 */}
                          <td className="py-1 pr-2">
                            {Math.round(r.benchmark)}%
                            <span className="ml-1 opacity-50">
                              {r.origin === "stable_edge" ? "holdout" : "地板"}
                            </span>
                          </td>
                          <td className="py-1 pr-2">
                            {r.matured > 0 ? (
                              <span className={r.hit_rate != null && r.hit_rate >= r.benchmark - 5 ? "text-up" : "text-amber-300"}>
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
      </div>
    </section>
  );
}
