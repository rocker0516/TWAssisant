import { useState } from "react";
import { Link } from "react-router-dom";
import { bestTagLift, type RecommendationItem, type TagComboStats } from "../api/client";
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

// 長線畢業條件配色：回測毒性單調 → 黃/紅為「重新審視」訊號（非停損）
function fishAgeClass(n: number): string {
  if (n > 18) return "bg-red-500/15 text-red-300";
  if (n > 12) return "bg-amber-500/15 text-amber-300";
  return "bg-emerald-500/10 text-emerald-300";
}

function mom12Class(pct: number): string {
  if (pct > 200) return "bg-red-500/15 text-red-300";
  if (pct > 80) return "bg-amber-500/15 text-amber-300";
  return "bg-gray-500/10 text-gray-400";
}

function toneClass(tone: NarrativeTone): string {
  if (tone === "neg") return "text-amber-400";
  if (tone === "pos") return "text-gray-300";
  return "text-gray-500";
}

// 量能共振徽章：條件與門檻不寫死，由 bestTagLift 從 tag_combo_stats 的雙段檢定決定，
// 撐不起來的組合（重驗後 lift ≤4pp）自動消失——避免徽章繼續宣稱過期的命中率。
const LIFT_BADGE: Record<string, { label: string; cls: string; ok: (volRatio: number) => boolean; how: string }> = {
  volup: { label: "⚡量增共振", cls: "bg-red-500/20 text-red-200",
    ok: (v) => v > 1.5, how: "5日均量/20日均量 >1.5" },
  voldn: { label: "🤫量縮惜售", cls: "bg-violet-500/20 text-violet-200",
    ok: (v) => v < 0.8, how: "5日均量/20日均量 <0.8（籌碼惜售）" },
};

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
  tagStats?: TagComboStats["stats"]; // 量能共振徽章的雙段實證來源（機率本身走達標機率查表）
}) {
  const tags = TAG_ORDER.filter(
    (t) => (t === "pop" ? popQualified : item.passed_styles?.includes(t)),
  ).filter((t) => STYLE_TAGS[t]);
  const volLifts = item.vol_ratio == null ? [] : tags.flatMap((tag) => {
    const lift = bestTagLift(tagStats, tag);
    const badge = lift ? LIFT_BADGE[lift.cond] : undefined;
    return lift && badge && badge.ok(item.vol_ratio!) ? [{ tag, lift, badge }] : [];
  });
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
          {/* 量能共振徽章：只在該標籤的加成條件通過雙段檢定（10 日窗，挖掘窗＋holdout
              皆 >4pp）時才掛，數字一律從 tag_combo_stats 讀，不手抄 */}
          {item.vol_ratio != null && volLifts.map(({ tag, lift, badge }) => (
            <span key={`${tag}|${lift.cond}`}
              title={`${STYLE_TAGS[tag].label}＋${badge.how}（現值 ${item.vol_ratio}）：`
                + `雙段命中 ${lift.hitTr}%/${lift.hitHo}%，比${STYLE_TAGS[tag].label}整體 `
                + `+${lift.liftTr.toFixed(1)}/+${lift.liftHo.toFixed(1)}pp（n=${lift.n.toLocaleString()}）`}
              className={`rounded px-1.5 py-0.5 text-xs font-medium ${badge.cls}`}>
              {badge.label}
            </span>
          ))}
          {/* 注意/處置動能徽章（2026-08 判官驗證，train/holdout 雙段 + ATR桶控波動）：
              處置後10日 命中61%/71%、控波動+16~19pp；注意×上升結構 +5~7pp。回檔亦深（MAE -19%），非無風險 */}
          {item.ml_consensus === true && (
            <span title="ML 共識：46 特徵 GBM 模型也將其排入硬篩內前 20%——四因子×ML 交集實證 holdout 命中 ~34%（單獨 ~30-31%）"
              className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-xs font-medium text-emerald-300">
              🤝共識
            </span>
          )}
          {item.attention === "punish" && (
            <span title="處置公告後10日內/執行中：實證命中 61%/71%（控波動後 +16pp）——分盤交易壓不住動能；但平均最深回檔 -19%，波動極大"
              className="rounded bg-fuchsia-500/20 px-1.5 py-0.5 text-xs font-medium text-fuchsia-300">
              🔥處置動能
            </span>
          )}
          {item.attention === "notice" && (
            <span title="近5日列注意股：熱錢聚集；上升結構下實證控波動後 +5~7pp，惟典型回檔亦放大"
              className="rounded bg-amber-500/20 px-1.5 py-0.5 text-xs font-medium text-amber-300">
              ⚡注意動能
            </span>
          )}
        </div>
        <div className="text-right">
          <div className="font-semibold tabular-nums">{fmtNum(item.close)}</div>
          <div className={`text-xs tabular-nums ${changeColor(item.change_pct)}`}>{fmtPct(item.change_pct)}</div>
        </div>
      </div>

      {/* 機率主區塊（波段軌）：唯一的機率數字＝同條件五年查表；分數不再顯示（活在後端與展開區） */}
      {item.prob_hit != null ? (
        <div
          className="flex items-baseline gap-3"
          title={`同條件（${item.prob_cond ?? ""}）2021 起歷史：隔日最高價進場、10 交易日內碰到 +10% 的比率，n=${(item.prob_n ?? 0).toLocaleString()}。歷史條件機率，非保證`}
        >
          <span
            className={`text-3xl font-bold tabular-nums leading-none ${
              item.prob_hit >= 65 ? "text-up" : item.prob_hit >= 55 ? "text-amber-300" : "text-gray-400"
            }`}
          >
            ~{Math.round(item.prob_hit)}%
          </span>
          <div className="min-w-0 text-xs leading-snug">
            <div className="text-gray-300">10 日內碰到 +10% 的歷史機率</div>
            <div className="text-muted">
              {item.prob_cond}　·　n={(item.prob_n ?? 0).toLocaleString()}
              {item.prob_mae != null && (
                <>
                  　·　<span className="text-down">同條件平均最深回撤 {item.prob_mae}%</span>
                </>
              )}
            </div>
          </div>
        </div>
      ) : item.target_zone ? (
        /* 目標主區塊（長線軌）：基準錨上漲空間；區間細節在下方「目標區間」欄 */
        <div
          className="flex items-baseline gap-3"
          title={
            item.target_zone.basis === "analyst"
              ? "FactSet 法人共識目標價中位相對現價的空間。法人評等在循環頂點最樂觀（落後指標），搭配下方畢業條件一起看；非保證"
              : "無法人報告，改以 PE 河流中位帶（估值回到歷史常態）推算。估值推算非保證"
          }
        >
          <span
            className={`text-3xl font-bold tabular-nums leading-none ${
              (item.target_zone.upside_pct ?? 0) >= 30
                ? "text-up"
                : (item.target_zone.upside_pct ?? 0) >= 10
                  ? "text-amber-300"
                  : "text-gray-400"
            }`}
          >
            {item.target_zone.upside_pct != null ? fmtPct(item.target_zone.upside_pct) : "—"}
          </span>
          <div className="min-w-0 text-xs leading-snug">
            <div className="text-gray-300">
              目標 {fmtNum(item.target_zone.base)}（
              {item.target_zone.basis === "analyst" ? "法人目標價中位" : "估值推算"}）的空間
            </div>
            <div className="text-muted">
              {item.target_zone.basis === "analyst" && item.target_zone.analyst_count != null
                ? `${item.target_zone.analyst_count} 位分析師　·　`
                : item.target_zone.basis === "pe_river"
                  ? "PE 河流中位帶　·　"
                  : ""}
              參考期間 12 個月
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-y-1">
          <ScoreDisplay total={item.total_score} subScores={item.sub_scores} />
        </div>
      )}

      {/* 畢業條件（長線軌）：不設停損，這排是「該重新審視這條魚了嗎」 */}
      {item.graduation && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted">畢業條件</span>
          {item.graduation.hit_target && (
            <span
              className="rounded bg-rose-500/15 px-1.5 py-0.5 font-medium text-rose-300"
              title="現價/期間高點已觸及基準目標——這條魚釣到了，重新評估是否續抱"
            >
              🎣 已達標
            </span>
          )}
          {item.graduation.streak_months != null && (
            <span
              className={`rounded px-1.5 py-0.5 font-medium ${fishAgeClass(item.graduation.streak_months)}`}
              title="連續營收年增為正的月數。回測：12 月見頂、19 月以上相對宇宙 −5.5pp（老魚是毒）"
            >
              🐟 魚齡 {item.graduation.streak_months} 月
            </span>
          )}
          {item.graduation.mom12_pct != null && (
            <span
              className={`rounded px-1.5 py-0.5 font-medium ${mom12Class(item.graduation.mom12_pct)}`}
              title="近 12 月漲幅。回測：漲幅 80~200% 相對宇宙 −8.2pp、200%+ −20.9pp（魚已被釣走）"
            >
              📈 已漲 {fmtPct(item.graduation.mom12_pct)}
            </span>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-y-1">
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
        {item.track === "long" ? (
          <div title="保守=PE 河流中位帶（估值回常態）、樂觀=上緣帶（估值走到歷史高檔）。長線軌不設停損，以畢業條件檢視">
            <div className="text-xs text-muted">目標區間（保守~樂觀）</div>
            <div className="tabular-nums">
              {item.target_zone?.low != null && item.target_zone?.high != null
                ? `${fmtNum(item.target_zone.low)} ~ ${fmtNum(item.target_zone.high)}`
                : "—"}
            </div>
          </div>
        ) : (
          <div>
            <div className="text-xs text-muted">參考停損</div>
            <div className="tabular-nums">
              {fmtNum(item.stop_loss)}{" "}
              <span className="text-down">({fmtPct(item.loss_pct)})</span>
            </div>
          </div>
        )}
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
