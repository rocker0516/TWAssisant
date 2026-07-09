import { useState } from "react";
import { Link } from "react-router-dom";
import type { RecommendationItem } from "../api/client";
import { changeColor, consolidationMeta, entryTimingMeta, fmtNum, fmtPct, positionMeta, rangePositionMeta, TRACK_LABELS } from "../lib/format";
import { hasNegative, marketSegments, type NarrativeTone } from "../lib/narrative";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { EvidencePanel } from "./EvidencePanel";
import { ReasonChips } from "./ReasonChips";
import { ScoreDisplay } from "./ScoreDisplay";
import { Sparkline } from "./Sparkline";

function toneClass(tone: NarrativeTone): string {
  if (tone === "neg") return "text-amber-400";
  if (tone === "pos") return "text-gray-300";
  return "text-gray-500";
}

export function RecommendationCard({
  item,
  sparkDays,
}: {
  item: RecommendationItem;
  sparkDays?: number; // 走勢取近幾個交易日（由推薦頁切換；不傳＝全部）
}) {
  const [open, setOpen] = useState(false);
  const hasDetails = (item.details?.length ?? 0) > 0;
  const segments = marketSegments(item);
  const narrativeHasNeg = hasNegative(segments);
  const spark = sparkDays && item.spark ? item.spark.slice(-sparkDays) : item.spark;
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-edge bg-panel p-4 transition hover:border-sky-700">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded bg-sky-900/60 px-1.5 py-0.5 text-xs font-medium text-sky-300">
            {TRACK_LABELS[item.track]}
          </span>
          <Link to={`/stocks/${item.stock_id}`} className="hover:underline">
            <span className="font-semibold">{item.name}</span>
            <span className="ml-1.5 text-sm text-muted">{item.stock_id}</span>
          </Link>
        </div>
        <div className="text-right">
          <div className="font-semibold tabular-nums">{fmtNum(item.close)}</div>
          <div className={`text-xs tabular-nums ${changeColor(item.change_pct)}`}>{fmtPct(item.change_pct)}</div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-y-1">
        <ScoreDisplay total={item.total_score} subScores={item.sub_scores} />
        <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1">
          {(() => {
            const tm = entryTimingMeta(item.sub_scores?.entry_timing);
            return tm ? (
              <span className={`whitespace-nowrap rounded bg-rose-950/40 px-1.5 py-0.5 text-xs font-medium ${tm.color}`}>
                {tm.label}
              </span>
            ) : null;
          })()}
          {(() => {
            const cm = consolidationMeta(item.sub_scores?.consolidation);
            return cm ? (
              <span className={`whitespace-nowrap rounded bg-emerald-950/50 px-1.5 py-0.5 text-xs font-medium ${cm.color}`}>
                {cm.label}
              </span>
            ) : null;
          })()}
          {(() => {
            // 位階依走勢視窗算（spark 不足時退回後端 20 日綜合分）
            const pm = rangePositionMeta(item.spark, sparkDays) ?? positionMeta(item.sub_scores?.position);
            return pm ? (
              <span className="whitespace-nowrap text-xs">
                <span className="text-muted">位階 </span>
                <span className={`font-medium ${pm.color}`}>{pm.label}</span>
              </span>
            ) : null;
          })()}
          <ConfidenceBadge confidence={item.confidence} coverage={item.coverage} stability={item.stability} />
        </div>
      </div>

      <Sparkline data={spark} />

      {item.review && (
        <div
          className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${
            item.review.hit_pop
              ? "border-rose-700/60 bg-rose-950/30"
              : "border-edge bg-bg/40"
          }`}
        >
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {item.review.hit_pop ? (
              <span className="font-medium text-rose-300">
                ✅ 已噴 +10%（第 {item.review.days_to_pop} 個交易日）
              </span>
            ) : (
              <span className="font-medium text-gray-400">⏸ 還沒噴</span>
            )}
            <span className="text-gray-300">
              至今 <span className={changeColor(item.review.return_pct)}>{fmtPct(item.review.return_pct)}</span>
            </span>
            <span className="text-muted">
              期間 <span className="text-up">{fmtPct(item.review.mfe_pct)}</span>
              <span className="mx-0.5">/</span>
              <span className="text-down">{fmtPct(item.review.mae_pct)}</span>
            </span>
            <span className="text-muted">已過 {item.review.days_elapsed} 個交易日</span>
          </div>
        </div>
      )}

      {!item.review && segments.length > 0 && (
        <div
          className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${
            narrativeHasNeg ? "border-amber-700/50 bg-amber-950/20" : "border-edge bg-bg/40"
          }`}
        >
          <span className="font-medium text-gray-400">行情　</span>
          {segments.map((seg, i) => (
            <span key={seg.category}>
              <span className={toneClass(seg.tone)}>{seg.text}</span>
              {i < segments.length - 1 && <span className="text-gray-500">；</span>}
            </span>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <div className="text-xs text-muted">買進區間</div>
          <div className="tabular-nums">
            {fmtNum(item.buy_low)} ~ {fmtNum(item.buy_high)}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">參考停損</div>
          <div className="tabular-nums">
            {fmtNum(item.stop_loss)}{" "}
            <span className="text-down">({fmtPct(item.loss_pct)})</span>
          </div>
        </div>
      </div>

      <ReasonChips reasons={item.reasons} />

      {hasDetails && (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="self-start text-xs text-sky-400 hover:underline"
            aria-expanded={open}
          >
            {open ? "收合 ▲" : "展開詳情 ▼"}
          </button>
          {open && <EvidencePanel item={item} />}
        </>
      )}

      <div className="flex items-center justify-between border-t border-edge pt-2 text-xs text-muted">
        <span>{item.sector_name ?? "—"}</span>
        <Link to={`/stocks/${item.stock_id}`} className="text-sky-400 hover:underline">
          詳情 →
        </Link>
      </div>
    </div>
  );
}
