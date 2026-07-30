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

// 風格標籤（2026-07-28 標籤制）：各自獨立回測驗證過的訊號，越多標籤=越多共振
const STYLE_TAGS: Record<string, { label: string; cls: string; title: string }> = {
  pop: { label: "會噴", cls: "bg-sky-500/15 text-sky-300",
    title: "硬篩＋四因子排名達嚴格度橫桿" },
  explosive: { label: "爆發", cls: "bg-amber-500/15 text-amber-300",
    title: "日均波幅>7%＋上揚月線（回測命中~72%，回撤深）" },
  strong: { label: "強勢延伸", cls: "bg-rose-500/15 text-rose-300",
    title: "52週高檔＋月線上方23%+（回測命中~66%，回撤深~−19%）" },
  story: { label: "故事股", cls: "bg-violet-500/15 text-violet-300",
    title: "高PB＋高PE＋高波動（回測命中~68%）" },
  crash: { label: "深跌反攻", cls: "bg-red-500/15 text-red-300",
    title: "大盤崩跌日限定：逆勢強勢/故事股＋波幅>6%（回測命中~68%）" },
};

function toneClass(tone: NarrativeTone): string {
  if (tone === "neg") return "text-amber-400";
  if (tone === "pos") return "text-gray-300";
  return "text-gray-500";
}

type TagStat = { n: number; hit: number; avg_mae: number };

const TAG_ORDER = ["pop", "explosive", "strong", "story", "crash"];

export function RecommendationCard({
  item,
  sparkDays,
  popQualified,
  tagStats,
}: {
  item: RecommendationItem;
  sparkDays?: number; // 走勢取近幾個交易日（由推薦頁切換；不傳＝全部）
  popQualified?: boolean; // 會噴標籤（過硬篩且分數達橫桿；由推薦頁依橫桿算）
  tagStats?: Record<string, TagStat>; // 標籤組合五年實證（useTagComboStats）
}) {
  const tags = TAG_ORDER.filter(
    (t) => (t === "pop" ? popQualified : item.passed_styles?.includes(t)),
  ).filter((t) => STYLE_TAGS[t]);
  // 同條件實證：先查精確組合，樣本太薄(n<150)退回單標籤邊際(取樣本最大者)
  let combo: TagStat | undefined;
  let comboLabel = "";
  if (tagStats && tags.length > 0) {
    const exact = tagStats[tags.join("+")];
    if (exact && exact.n >= 150) {
      combo = exact;
      comboLabel = tags.map((t) => STYLE_TAGS[t].label).join("＋");
    } else {
      const best = tags
        .map((t) => [t, tagStats[`any:${t}`]] as const)
        .filter(([, v]) => v)
        .sort((a, b) => (b[1]!.n ?? 0) - (a[1]!.n ?? 0))[0];
      if (best) {
        combo = best[1]!;
        comboLabel = STYLE_TAGS[best[0]].label;
      }
    }
  }
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
          {tags.map((t) => (
            <span key={t} title={STYLE_TAGS[t].title}
              className={`rounded px-1.5 py-0.5 text-xs font-medium ${STYLE_TAGS[t].cls}`}>
              {STYLE_TAGS[t].label}
            </span>
          ))}
        </div>
        <div className="text-right">
          <div className="font-semibold tabular-nums">{fmtNum(item.close)}</div>
          <div className={`text-xs tabular-nums ${changeColor(item.change_pct)}`}>{fmtPct(item.change_pct)}</div>
        </div>
      </div>

      {combo && (
        <div className="text-xs text-muted" title="forward_labels 全市場標記：同標籤組合、隔天最高價進場、30 交易日內摸 +10% 的歷史比率">
          {comboLabel} 同條件五年實證：命中 <b className="text-gray-300">{combo.hit}%</b>
          　·　平均最深回撤 {combo.avg_mae}%　·　n={combo.n.toLocaleString()}
        </div>
      )}

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
