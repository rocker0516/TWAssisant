import { useState } from "react";
import type { TargetPriceResponse } from "../api/client";
import { changeColor, fmtNum } from "../lib/format";

const DIR_LABEL: Record<string, string> = { up: "調升", down: "調降", flat: "維持", new: "新增" };
const DIR_COLOR: Record<string, string> = { up: "text-up", down: "text-down", flat: "text-muted", new: "text-muted" };

export function TargetPriceCard({
  data,
  lineOn,
  onToggleLine,
}: {
  data: TargetPriceResponse;
  lineOn: boolean;
  onToggleLine: (on: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const tp = data.latest;
  return (
    <div className="rounded-xl border border-edge bg-panel p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">法人目標價（FactSet 共識）</span>
        {tp && (
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={lineOn} onChange={(e) => onToggleLine(e.target.checked)} className="accent-pink-400" />
            K線顯示
          </label>
        )}
      </div>
      {!tp ? (
        <p className="text-sm text-muted">無 FactSet 共識目標價（多為中小型股未被外資覆蓋）。</p>
      ) : (
        <>
          <div className="flex items-end justify-between">
            <div>
              <span className="text-2xl font-bold tabular-nums">{fmtNum(tp.target_price)}</span>
              {tp.upside_pct != null && (
                <span className={`ml-2 text-sm tabular-nums ${changeColor(tp.upside_pct)}`}>
                  隱含 {tp.upside_pct > 0 ? "+" : ""}{tp.upside_pct}%
                </span>
              )}
            </div>
            {tp.hit ? (
              <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-xs text-amber-400">已達標 {tp.hit_date}</span>
            ) : (
              <span className="rounded bg-panel2 px-1.5 py-0.5 text-xs text-muted">未達標</span>
            )}
          </div>
          <div className="mt-2 flex flex-col gap-1 text-sm">
            {tp.target_low != null && tp.target_high != null && (
              <div className="flex justify-between">
                <span className="text-muted">估值區間</span>
                <span className="tabular-nums">{fmtNum(tp.target_low)} ~ {fmtNum(tp.target_high)}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-muted">分析師</span>
              <span>{tp.analyst_count ?? "—"} 位</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">最近調整</span>
              <span className={DIR_COLOR[tp.direction]}>
                {tp.date} {DIR_LABEL[tp.direction]}{tp.prev_target != null ? `（${fmtNum(tp.prev_target)}→${fmtNum(tp.target_price)}）` : ""}
              </span>
            </div>
          </div>
          {tp.rating_bull != null && tp.rating_neutral != null && tp.rating_bear != null && (
            <div className="mt-3">
              <div className="mb-1 flex justify-between text-xs text-muted">
                <span>樂觀 {tp.rating_bull}</span>
                <span>中立 {tp.rating_neutral}</span>
                <span>悲觀 {tp.rating_bear}</span>
              </div>
              <div className="flex h-1.5 overflow-hidden rounded bg-panel2">
                <div className="bg-up" style={{ flexGrow: tp.rating_bull }} />
                <div className="bg-gray-500" style={{ flexGrow: tp.rating_neutral }} />
                <div className="bg-down" style={{ flexGrow: tp.rating_bear }} />
              </div>
            </div>
          )}
          {data.history.length > 1 && (
            <div className="mt-3">
              <button onClick={() => setExpanded(!expanded)} className="text-xs text-sky-400 hover:underline">
                歷次調整（{data.history.length}）{expanded ? "▲" : "▼"}
              </button>
              {expanded && (
                <div className="mt-1.5 flex flex-col gap-1">
                  {data.history.map((h) => (
                    <div key={h.date} className="flex items-center justify-between text-xs">
                      <span className="text-muted">{h.date}</span>
                      <span className={DIR_COLOR[h.direction]}>
                        {DIR_LABEL[h.direction]}{h.prev_target != null ? ` ${fmtNum(h.prev_target)}→` : " "}{fmtNum(h.target_price)}
                      </span>
                      <span className={h.hit ? "text-amber-400" : "text-muted"}>{h.hit ? "✓達標" : "未達"}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          <p className="mt-3 text-[11px] text-muted">來源：鉅亨網 FactSet 調查，僅供參考非投資建議。</p>
        </>
      )}
    </div>
  );
}
