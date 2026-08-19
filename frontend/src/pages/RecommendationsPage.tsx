import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  bestTagLift,
  comboKeyOf,
  COMBO_FILTER_KEY,
  readComboFilters,
  useActiveStrategyDaily,
  useRecommendations,
  useRecommendationsLookback,
  useRecommendationsLookbackCalendar,
  type RecommendationItem,
  type TagStatEntry,
  type Track,
  type WaveStyle,
  useTagComboStats,
} from "../api/client";
import { RecommendationCard } from "../components/RecommendationCard";
import { CornerSignalsStrip } from "../components/CornerSignalsPanel";
import { LookbackCalendar } from "../components/LookbackCalendar";
import { changeColor, consolidationMeta, fmtPct, positionMeta, rangePositionMeta, TRACK_LABELS } from "../lib/format";

type SortKey = "score" | "prob" | "entry_timing" | "change" | "lookback_return" | "lookback_mfe";

// 精確組合篩選 chip 顯示（鍵 "punish+notice" → 中文）
const COMBO_LABELS: Record<string, string> = {
  pop: "會噴", explosive: "爆發", strong: "強勢延伸", story: "故事股", crash: "深跌反攻",
  punish: "處置", notice: "注意",
};
const comboLabelOf = (k: string) => k.split("+").map((t) => COMBO_LABELS[t] ?? t).join("+");

// 標籤開關列：命中率與量能加成一律讀 tag_combo_stats（10 日窗實證，
// scripts/build_tag_combo_stats.py 重跑即更新），完全不手抄。
const TAG_TOGGLES: [string, string][] = [
  ["pop", "會噴"],
  ["explosive", "爆發"],
  ["strong", "強勢延伸"],
  ["story", "故事股"],
  ["crash", "深跌反攻"],
];

// 雙段/單段標註：深跌反攻要大盤深崩才會出現，切點之後沒再崩過＝holdout 無樣本，
// 它的命中率只是挖掘窗內的單段實證，與其他標籤性質不同，得講清楚。
function segNote(st: TagStatEntry | undefined, cut?: string): string {
  if (!st || st.hit_tr == null) return "";
  if (st.hit_ho != null) return `雙段 ${st.hit_tr}%/${st.hit_ho}%（${cut ?? ""} 前/後）`;
  return `⚠ 單段實證：樣本全在 ${cut ?? "切點"} 前（該市況近期未再出現），未經 holdout 驗證`;
}

// 位階/買點篩選（個人偏好，不影響會噴分數）。"all" 不篩；"低位盤整"＝相對低 且 波動收斂打底。
type PosFilter = "all" | "相對低" | "中性" | "偏高" | "低位盤整";
const POS_FILTERS: [PosFilter, string][] = [
  ["all", "全部"], ["相對低", "相對低"], ["中性", "中性"], ["偏高", "偏高"], ["低位盤整", "低位盤整"],
];

// 注意：排序只改顯示順序，不改清單成員（成員由會噴分數 cutoff 決定）。「進場時機」＝在已選出
// 的會噴清單『內部』把法人剛進場的往前排——研究實證清單內高時機半比低時機半多噴 +2.6pp。
function compareBy(key: SortKey): (a: RecommendationItem, b: RecommendationItem) => number {
  return (a, b) => {
    switch (key) {
      case "score":
        return (b.total_score ?? 0) - (a.total_score ?? 0);
      case "prob":
        return (b.prob_hit ?? -1) - (a.prob_hit ?? -1);
      case "entry_timing":
        return (b.sub_scores?.entry_timing ?? 0) - (a.sub_scores?.entry_timing ?? 0);
      case "lookback_return":
        return (b.review?.return_pct ?? -999) - (a.review?.return_pct ?? -999);
      case "lookback_mfe":
        return (b.review?.mfe_pct ?? -999) - (a.review?.mfe_pct ?? -999);
      case "change":
      default:
        return (b.change_pct ?? -999) - (a.change_pct ?? -999);
    }
  };
}

function sortItems(items: RecommendationItem[], key: SortKey): RecommendationItem[] {
  return [...items].sort(compareBy(key));
}

export default function RecommendationsPage() {
  const [track, setTrack] = useState<Track | "custom">("wave");
  const { data: custom } = useActiveStrategyDaily();
  // custom 頁籤無啟用策略時 fallback 渲染 wave
  const displayTrack = track === "custom" && !custom?.strategy ? "wave" : track;
  const [sort, setSort] = useState<SortKey>("prob"); // 波段預設按達標機率
  const [showExtra, setShowExtra] = useState(false);
  const [posFilter, setPosFilter] = useState<PosFilter>("all");
  const [probMin, setProbMin] = useState(0); // 達標機率門檻（%；0＝全部）
  // 拉桿連續變動 → 後端查詢（月曆/回看）用 300ms debounce 後的值，避免拖曳狂打 API；
  // 今日清單是前端就地篩，用即時值不受影響。
  const [probMinDebounced, setProbMinDebounced] = useState(0);
  useEffect(() => {
    const t = setTimeout(() => setProbMinDebounced(probMin), 300);
    return () => clearTimeout(t);
  }, [probMin]);
  const [minPrice, setMinPrice] = useState(""); // 股價下限（元，空＝不限）
  const [maxPrice, setMaxPrice] = useState(""); // 股價上限（元，空＝不限）
  const [sparkDays, setSparkDays] = useState(60); // 走勢視窗：近 N 個交易日（預設近3月）
  const [showCalendar, setShowCalendar] = useState(false); // 月曆折疊
  const [showDefenseList, setShowDefenseList] = useState(false); // 大盤防禦期仍要查看清單
  const [selectedLookbackDate, setSelectedLookbackDate] = useState<string | null>(null); // null=今天；否則=月曆點選的推薦日
  // 風格改標籤制（2026-07-28）：不再分頁切換，清單=會噴候選∪風格股，
  // 每檔卡片顯示標籤（會噴/爆發/強勢延伸/故事股/深跌反攻），標籤越多排越前。
  // 回看只支援波段軌；切到長線軌時自動回到今天；displayTrack 已處理 custom fallback
  const effTrack: Track = selectedLookbackDate ? "wave" : (displayTrack as Track);
  const effStyle: WaveStyle = "pop";
  const { data, isLoading, isError, error } = useRecommendations(effTrack, effStyle);
  const { data: tagStats } = useTagComboStats();

  // 波段(會噴)軌：API 回全部過硬篩股，前端用「前 N%」橫桿就地切（分數=百分位，前N% = 分數≥100−N）
  const topPct = data?.top_pct ?? 20; // 會噴標籤門檻（設定頁 top_pct；橫桿已由機率門檻取代）
  const cutoff = 100 - topPct;

  // 大盤 regime 閘門（僅波段軌）：防禦期(收盤跌破季線逾2%未站回)清單命中率實證較低
  // (39.5% vs 47.4%，walk-forward 三段皆成立)，預設收起清單、可手動展開。
  const regime = data?.regime ?? null;
  const inDefense = displayTrack === "wave" && selectedLookbackDate == null && regime?.state === "defense";
  const gateClosed = inDefense && !showDefenseList;

  // 月曆摘要（命中率）：機率口徑（成員=標籤制∩當日PIT機率≥門檻），隨機率門檻/風格切換
  const calendar = useRecommendationsLookbackCalendar(probMinDebounced, effStyle);

  // 回看：指定推薦日的清單（含 review + 當日 PIT 機率），同樣隨機率門檻/風格
  const lookback = useRecommendationsLookback({
    date: selectedLookbackDate,
    probMin: selectedLookbackDate ? probMinDebounced : undefined,
    style: effStyle,
  });

  // 位階/買點篩選（僅波段軌、個人偏好）：比對卡片同一套標籤，缺料則濾掉。
  // 相對低/中性/偏高＝依「走勢」視窗算的區間位階（與卡片徽章同一套 rangePositionMeta，隨 1/3/6 月連動）。
  // 「低位盤整」＝低檔盤整打底（盤整分≥50，低位已內建在分數裡；此為後端短線打底訊號，不隨走勢視窗變）。
  const matchPos = useMemo(
    () =>
      displayTrack !== "wave" || posFilter === "all"
        ? () => true
        : posFilter === "低位盤整"
        ? (it: RecommendationItem) => consolidationMeta(it.sub_scores?.consolidation) !== null
        : (it: RecommendationItem) =>
            (rangePositionMeta(it.spark, sparkDays) ?? positionMeta(it.sub_scores?.position))?.label === posFilter,
    [displayTrack, posFilter, sparkDays],
  );

  // 股價上下限（個人偏好，與會噴分數無關）：缺現價的標的在有設限時濾掉。
  const matchPrice = useMemo(() => {
    const lo = minPrice.trim() === "" ? null : Number(minPrice);
    const hi = maxPrice.trim() === "" ? null : Number(maxPrice);
    const loOk = lo != null && !Number.isNaN(lo);
    const hiOk = hi != null && !Number.isNaN(hi);
    if (!loOk && !hiOk) return () => true;
    return (it: RecommendationItem) => {
      if (it.close == null) return false;
      if (loOk && it.close < lo) return false;
      if (hiOk && it.close > hi) return false;
      return true;
    };
  }, [minPrice, maxPrice]);

  // 回看模式：後端已套用 top_pct 切清單並附 review；前端只做排序+股價篩選
  const isLookback = selectedLookbackDate != null;
  const baseItems = isLookback ? lookback.data?.items ?? [] : data?.items ?? [];
  const sorted = useMemo(
    () =>
      isLookback
        ? sortItems(baseItems, sort).filter(matchPrice) // 位階篩選不適用回看（鎖在當日）
        : sortItems(baseItems, sort)
            .filter(matchPos)
            .filter(matchPrice)
            .filter((it) => probMin <= 0 || (it.prob_hit ?? 0) >= probMin),
    [isLookback, baseItems, sort, matchPos, matchPrice, probMin],
  );
  // 標籤顯示開關（低命中標籤可關掉；localStorage 持久化）
  const [hiddenTags, setHiddenTags] = useState<Set<string>>(() => {
    try {
      return new Set<string>(JSON.parse(localStorage.getItem("hiddenStyleTags") ?? "[]"));
    } catch {
      return new Set<string>();
    }
  });
  const toggleTag = (t: string) => {
    setHiddenTags((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      localStorage.setItem("hiddenStyleTags", JSON.stringify([...next]));
      return next;
    });
  };
  // 標籤數：會噴(過硬篩且分數達橫桿) + 各純門檻風格；標籤越多=越多獨立驗證的訊號共振。
  // 只計「開啟中」的標籤 → 關掉的標籤既不顯示也不讓該股靠它進主清單。
  const tagCountOf = useMemo(
    () => (it: RecommendationItem) =>
      (it.passed_styles?.filter((t) => !hiddenTags.has(t)).length ?? 0)
      + (!hiddenTags.has("pop") && it.passed_filter && (it.total_score ?? 0) >= cutoff ? 1 : 0),
    [cutoff, hiddenTags],
  );
  // 精確組合篩選（策略室全枚舉表勾選 → localStorage）：勾了才生效，全不勾＝不過濾。
  // 組合鍵口徑與策略室一致：pop(過硬篩且達橫桿) + passed_styles + 注意/處置旗標。
  const [comboFilters, setComboFilters] = useState<string[]>(readComboFilters);
  const clearComboFilters = () => {
    setComboFilters([]);
    localStorage.setItem(COMBO_FILTER_KEY, "[]");
  };
  const comboOf = useMemo(
    () => (it: RecommendationItem) =>
      comboKeyOf([
        ...(it.passed_styles ?? []),
        ...(it.passed_filter && (it.total_score ?? 0) >= cutoff ? ["pop"] : []),
        ...(it.attention_tags ?? []),
      ]),
    [cutoff],
  );
  // 波段軌：主清單=至少一個標籤；排序主鍵=使用者所選鍵（預設達標機率），
  // 次鍵=標籤數（同分時標籤多者在前，越多獨立驗證的訊號共振越前）；
  // 觀察區=0標籤（未達橫桿又無風格）；長線軌/回看沿用原邏輯
  const mainCompare = useMemo(
    () => (a: RecommendationItem, b: RecommendationItem) =>
      compareBy(sort)(a, b) || tagCountOf(b) - tagCountOf(a),
    [sort, tagCountOf],
  );
  const main = useMemo(() => {
    const base = isLookback
      ? [...sorted].sort(mainCompare)
      : displayTrack === "wave"
      ? sorted.filter((it) => tagCountOf(it) > 0).sort(mainCompare)
      : sorted;
    // 組合篩選只作用於波段當日主清單（回看/長線不套，避免誤解歷史口徑）
    if (!isLookback && displayTrack === "wave" && comboFilters.length > 0) {
      return base.filter((it) => comboFilters.includes(comboOf(it)));
    }
    return base;
  }, [isLookback, displayTrack, sorted, tagCountOf, mainCompare, comboFilters, comboOf]);
  const extra = useMemo(
    () =>
      isLookback
        ? []
        : displayTrack === "wave"
        ? sorted.filter((it) => tagCountOf(it) === 0)
        : sortItems(data?.near ?? [], sort).filter(matchPrice),
    [isLookback, displayTrack, sorted, tagCountOf, data, sort, matchPrice],
  );
  const extraLabel = displayTrack === "wave" ? `未達前 ${topPct}% 觀察區` : "接近門檻觀察區";

  // 回看摘要
  const lbSummary = lookback.data?.summary;
  const lbHitRate = lbSummary?.hit_rate != null ? Math.round(lbSummary.hit_rate * 100) : null;


  return (
    <div className="w-full px-6 py-6">
      <div className="mb-4 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">進場推薦</h1>
          <p className="text-sm text-muted">
            {isLookback ? (
              <>
                回看：{lookback.data?.lookback_date ?? "—"} 推薦清單　·　今 {lookback.data?.today_date ?? "—"}
              </>
            ) : (
              <>
                盤後資料：{data?.date ?? "—"}
                {displayTrack === "wave"
                  ? `　會噴前 ${topPct}%＋風格標籤（依所選排序，同序看標籤數）`
                  : displayTrack === "custom"
                  ? `　自訂策略清單`
                  : `　門檻 ≥ ${data?.threshold ?? 70} 分`}
              </>
            )}
          </p>
        </div>
      </div>

      {/* 回看月曆：波段軌專屬。切到回看時長線軌會自動切回波段；custom 軌不支援回看 */}
      {displayTrack !== "custom" && (
      <div className="mb-3">
        <button
          onClick={() => setShowCalendar((s) => !s)}
          className="mb-2 flex items-center gap-1.5 text-sm text-muted hover:text-gray-200"
        >
          <span>{showCalendar ? "▼" : "▶"}</span>
          <span>回看月曆</span>
          {isLookback && (
            <span className="text-sky-300">· 選中 {selectedLookbackDate}</span>
          )}
          {calendar.data && !isLookback && (
            <span className="text-xs text-muted">
              · {calendar.data.dates.length} 個過去推薦日
            </span>
          )}
        </button>
        {showCalendar && (
          <>
            {calendar.isLoading && <p className="text-muted text-xs">月曆載入中…</p>}
            {calendar.data && (
              <LookbackCalendar
                points={calendar.data.dates}
                todayDate={calendar.data.today_date}
                selectedDate={selectedLookbackDate}
                onSelect={(d) => {
                  setSelectedLookbackDate(d);
                  setSort("prob"); // 切模式時排序鎖回達標機率
                }}
              />
            )}
          </>
        )}
      </div>
      )}

      {/* 雙軌分頁（回看模式不可切，固定波段） */}
      {!isLookback && (
        <div className="mb-4 flex gap-1 border-b border-edge">
          {(["wave", "long"] as Track[]).map((t) => (
            <button
              key={t}
              onClick={() => { setTrack(t); setSort(t === "wave" ? "prob" : "score"); }}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
                displayTrack === t ? "border-sky-500 text-sky-300" : "border-transparent text-muted hover:text-gray-300"
              }`}
            >
              {TRACK_LABELS[t]}軌
              {data && displayTrack === t ? <span className="ml-1.5 text-xs">({main.length})</span> : null}
            </button>
          ))}
          {custom?.strategy && (
            <button key="custom"
              onClick={() => setTrack("custom")}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
                displayTrack === "custom" ? "border-amber-500 text-amber-300"
                                          : "border-transparent text-muted hover:text-gray-300"}`}>
              🧪 {custom.strategy.name}
              {displayTrack === "custom" && <span className="ml-1.5 text-xs">({custom.items.length})</span>}
            </button>
          )}
        </div>
      )}

      {/* 大盤防禦期閘門（僅波段軌）：命中率實證較低 → 預設暫停顯示推薦 */}
      {inDefense && regime && (
        <div className="mb-4 rounded-lg border border-red-700/50 bg-red-950/30 px-3.5 py-2.5 text-sm leading-relaxed text-red-200/90">
          🛡 <b>大盤防禦期</b>——{regime.since} 起加權指數收盤跌破季線逾 2%（現距季線 {regime.gap_pct}%），尚未站回 {Math.round(regime.ma60).toLocaleString()}。
          此時段清單歷史命中率約 <b>{Math.round(regime.defense_hit_rate * 100)}%</b>（平時約 {Math.round(regime.hold_hit_rate * 100)}%）、回撤較深，<b>已預設收起新進場推薦</b>；指數收盤站回季線自動恢復。
          注意：約<b>半數</b>防禦期事後看是正常的——收起是<b>保守偏誤</b>，不是預知會跌。
          <button
            onClick={() => setShowDefenseList((s) => !s)}
            className="ml-2 rounded-md border border-red-700/60 px-2 py-0.5 text-xs text-red-200 hover:bg-red-900/40"
          >
            {showDefenseList ? "收起清單" : "仍要查看清單"}
          </button>
        </div>
      )}

      {/* 回看摘要（清單為空時隱掉，避免和下方空狀態重複） */}
      {isLookback && lbSummary && lbSummary.n > 0 && (
        <div className="mb-4 rounded-lg border border-sky-700/40 bg-sky-950/30 px-3.5 py-2.5 text-xs leading-relaxed">
          <span className="text-sky-300">
            那天推了 <b>{lbSummary.n}</b> 檔，已噴（摸過 +10%） <b>{lbSummary.hit_count}</b> 檔
            {lbHitRate != null ? <>（命中率 <b>{lbHitRate}%</b>）</> : null}
          </span>
          <span className="ml-3 text-gray-300">
            平均至今 <span className={changeColor(lbSummary.avg_return_pct)}>{fmtPct(lbSummary.avg_return_pct)}</span>
            　·　期間 <span className="text-up">{fmtPct(lbSummary.avg_mfe_pct)}</span>
            <span className="mx-0.5">/</span>
            <span className="text-down">{fmtPct(lbSummary.avg_mae_pct)}</span>
          </span>
        </div>
      )}

      {/* 機率門檻（今日與回看共用）：清單以達標機率為主軸；回看=後端依當日 PIT 機率篩 */}
      {displayTrack === "wave" && !gateClosed && (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">達標機率</span>
          <div className="inline-flex items-center gap-2 rounded-lg border border-edge bg-panel px-3 py-1.5">
            <input
              type="range" min={0} max={90} step={5} value={probMin}
              onChange={(e) => setProbMin(Number(e.target.value))}
              className="w-40"
            />
            <span className="w-14 text-sm font-medium tabular-nums text-sky-300">
              {probMin <= 0 ? "全部" : `≥${probMin}%`}
            </span>
          </div>
          <span className="text-xs text-muted">
            機率＝同條件（分數帶×波動帶×大盤狀態）五年歷史命中，非保證　·　顯示 {sorted.length} 檔
          </span>
        </div>
      )}

      {/* 標籤開關：命中率看不上眼的標籤可關掉（不顯示、也不讓該股靠它進主清單）。
          括號＝2026-08 全樣本實測「30日內摸+10%」train/holdout 命中 */}
      {displayTrack === "wave" && !gateClosed && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5 text-sm">
          <span className="text-xs text-muted">標籤</span>
          {TAG_TOGGLES.map(([key, label]) => {
            const off = hiddenTags.has(key);
            const st = tagStats?.stats[`any:${key}`];
            const lift = bestTagLift(tagStats?.stats, key);
            const rate = st ? `${Math.round(st.hit)}%` : "—";
            return (
              <button key={key} onClick={() => toggleTag(key)}
                title={[
                  st ? `實證命中 ${st.hit}%（n=${st.n.toLocaleString()}、平均最深回撤 ${st.avg_mae}%）`
                     : "實證統計尚未產出",
                  segNote(st, tagStats?.holdout_from),
                  lift ? `加成條件 ${lift.label}：${lift.hitTr}%/${lift.hitHo}%`
                       + `（雙段 ${lift.liftTr > 0 ? "+" : ""}${lift.liftTr.toFixed(1)}`
                       + `/${lift.liftHo > 0 ? "+" : ""}${lift.liftHo.toFixed(1)}pp，n=${lift.n.toLocaleString()}）` : "",
                  `點擊${off ? "開啟" : "關閉"}此標籤`,
                ].filter(Boolean).join("。")}
                className={`rounded-full px-2.5 py-1 text-xs transition ${
                  off ? "bg-panel2 text-gray-600 line-through" : "bg-panel2 text-gray-200"
                }`}>
                {label} <span className={off ? "" : "text-muted"}>{rate}</span>
              </button>
            );
          })}
          <span className="text-xs text-muted">
            %＝五年實證命中（隔日高錨、10 日內摸 +10%，全市場基率約 19%）；點擊開關，關閉＝不顯示也不入主清單
          </span>
        </div>
      )}

      {/* 位階篩選（個人偏好，不影響會噴分數；回看模式不適用）*/}
      {displayTrack === "wave" && !isLookback && !gateClosed && (
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">買點</span>
          <div className="inline-flex rounded-lg border border-edge bg-panel p-0.5">
            {POS_FILTERS.map(([val, label]) => (
              <button
                key={val}
                onClick={() => setPosFilter(val)}
                className={`rounded-md px-3 py-1 text-sm font-medium transition ${
                  posFilter === val ? "bg-sky-600 text-white" : "text-muted hover:text-gray-200"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="text-xs text-muted">相對低/中性/偏高＝現價在所選「走勢」區間的高低（隨上方 1/3/6 月連動）；低位盤整＝短線打底蓄勢。僅供擇時參考、不改會噴分數</span>
        </div>
      )}

      {/* 會噴誠實話術：分數=會噴機率(回測實證)，非漲跌保證；高波動雙面刃。回看模式改顯示回看摘要 */}
      {displayTrack === "wave" && !isLookback && !gateClosed && (
        <div className="mb-4 rounded-lg border border-amber-700/50 bg-amber-950/30 px-3.5 py-2.5 text-xs leading-relaxed text-amber-200/90">
          每檔的大字＝<b>達標機率</b>：同條件（會噴排名帶 × 波動帶 × 大盤狀態）在 2021 年起歷史裡
          「<b>隔天最高價進場、10 個交易日內曾摸到 +10%</b>」的實際比率——是<b>歷史條件機率，不是保證</b>，
          旁邊的 n 是該條件的歷史樣本數。機率高的通常是<b>高波動股、雙面刃</b>（同條件的平均最深回撤一併標出）：
          ①會噴的也會崩，請小部位；②能不能入袋全看<b>出場紀律</b>（沒到價要停損）；
          ③大盤狀態變了機率就變（同一檔在深崩/正常日的機率不同）。
        </div>
      )}

      {/* 工具列（防禦期收起清單時一併隱藏；custom 軌不適用） */}
      {!gateClosed && displayTrack !== "custom" && (
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-muted">排序</span>
          {(
            [
              ...(displayTrack === "wave" && !isLookback
                ? ([["prob", "達標機率"], ["entry_timing", "進場時機"]] as [SortKey, string][])
                : isLookback
                  ? ([["prob", "達標機率"]] as [SortKey, string][])
                  : ([["score", "分數"]] as [SortKey, string][])),
              ...(isLookback
                ? ([
                    ["lookback_return", "至今報酬"],
                    ["lookback_mfe", "期間最高"],
                  ] as [SortKey, string][])
                : ([["change", "漲跌幅"]] as [SortKey, string][])),
            ] as [SortKey, string][]
          ).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setSort(k)}
              className={`rounded-md px-2.5 py-1 ${sort === k ? "bg-panel2 text-sky-300" : "text-muted hover:bg-panel2"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-muted">走勢</span>
          {([[20, "近1月"], [60, "近3月"], [120, "近6月"]] as [number, string][]).map(([d, label]) => (
            <button
              key={d}
              onClick={() => setSparkDays(d)}
              className={`rounded-md px-2.5 py-1 ${sparkDays === d ? "bg-panel2 text-sky-300" : "text-muted hover:bg-panel2"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-muted">股價</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            placeholder="最低"
            value={minPrice}
            onChange={(e) => setMinPrice(e.target.value)}
            className="w-16 rounded-md border border-edge bg-panel px-2 py-1 text-sm text-right tabular-nums focus:border-sky-500 focus:outline-none"
          />
          <span className="text-muted">–</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            placeholder="最高"
            value={maxPrice}
            onChange={(e) => setMaxPrice(e.target.value)}
            className="w-16 rounded-md border border-edge bg-panel px-2 py-1 text-sm text-right tabular-nums focus:border-sky-500 focus:outline-none"
          />
          <span className="text-xs text-muted">元</span>
          {(minPrice !== "" || maxPrice !== "") && (
            <button
              onClick={() => {
                setMinPrice("");
                setMaxPrice("");
              }}
              className="ml-0.5 text-xs text-muted hover:text-gray-200"
            >
              清除
            </button>
          )}
        </div>
      </div>
      )}

      {(isLookback ? lookback.isLoading : isLoading) && <p className="text-muted">載入中…</p>}
      {isLookback
        ? lookback.isError && <p className="text-down">載入失敗：{(lookback.error as Error)?.message}</p>
        : isError && <p className="text-down">載入失敗：{(error as Error).message}</p>}

      {isLookback && lookback.data && lookback.data.lookback_date == null && (
        <div className="rounded-xl border border-dashed border-edge py-16 text-center text-sm leading-relaxed text-muted">
          該日沒有過門檻的推薦。可點月曆其他日期。
        </div>
      )}
      {/* 精確組合篩選提示（策略室勾選；只作用於波段當日主清單） */}
      {!isLookback && displayTrack === "wave" && comboFilters.length > 0 && !gateClosed && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-sky-800/50 bg-sky-950/30 px-3 py-2 text-sm">
          <span className="text-sky-300">🔬 精確組合篩選中</span>
          {comboFilters.map((k) => (
            <span key={k} className="rounded bg-panel2 px-1.5 py-0.5 text-xs text-gray-300">{comboLabelOf(k)}</span>
          ))}
          <span className="text-xs text-muted">（符合 {main.length} 檔；在策略室調整勾選）</span>
          <button onClick={clearComboFilters} className="ml-auto rounded-md bg-panel2 px-2 py-0.5 text-xs text-muted hover:text-gray-200">
            ✕ 清除篩選
          </button>
        </div>
      )}
      {!isLookback && data && main.length === 0 && !gateClosed && (
        <div className="rounded-xl border border-dashed border-edge py-16 text-center text-sm leading-relaxed text-muted">
          {!isLookback && displayTrack === "wave" && comboFilters.length > 0
            ? "今日主清單沒有符合勾選精確組合的標的（組合是稀有事件，多數日子為空屬正常；可在策略室調整勾選或清除篩選）。"
            : (minPrice !== "" || maxPrice !== "")
            ? "目前篩選的股價區間內沒有符合的標的，可調整或清除股價上下限。"
            : displayTrack !== "wave"
            ? `今日無符合條件的${TRACK_LABELS[displayTrack]}軌標的`
            : posFilter === "低位盤整"
            ? "目前會噴清單中沒有「低位盤整打底」的標的。會噴股多在上揚趨勢、波動偏大，少見低檔盤整收斂——此篩選多數時候為空屬正常，可切回「全部」。"
            : `目前沒有掛任何標籤（會噴/爆發/強勢延伸/故事股/深跌反攻）的標的，可放寬橫桿`}
        </div>
      )}

      {track === "custom" && custom?.strategy && (
        <div className="rounded-xl border border-edge bg-panel p-4">
          <p className="mb-3 text-xs text-muted">
            自訂策略「{custom.strategy.name}」· {custom.date} 收盤符合條件前 {custom.strategy.top_n} 檔
            · 目標 {custom.strategy.horizon_days} 日 +{custom.strategy.target_pct}%
            {custom.strategy.stop_pct != null && ` · 停損 -${custom.strategy.stop_pct}%`}
            　<Link to="/lab" className="text-sky-400 hover:underline">→ 回實驗室調整</Link>
          </p>
          {custom.items.length === 0 ? (
            <p className="text-sm text-muted">今日無符合條件的股票</p>
          ) : (
            <table className="w-full text-sm">
              <thead><tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">股票</th>
                <th className="py-1 text-right font-normal">收盤</th>
                <th className="py-1 text-right font-normal">排序值</th>
              </tr></thead>
              <tbody>
                {custom.items.map((it) => (
                  <tr key={it.stock_id} className="border-t border-edge/60">
                    <td className="py-1.5">
                      <Link to={`/stocks/${it.stock_id}`} className="hover:underline">
                        <span className="tabular-nums text-muted">{it.stock_id}</span> {it.name}
                      </Link>
                    </td>
                    <td className="py-1.5 text-right tabular-nums">{it.close ?? "—"}</td>
                    <td className="py-1.5 text-right tabular-nums text-muted">
                      {it.sort_value == null ? "—" : Math.round(it.sort_value).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {!gateClosed && displayTrack !== "custom" && (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {main.map((it) => (
          <RecommendationCard
            key={it.stock_id}
            item={hiddenTags.size ? { ...it, passed_styles: it.passed_styles?.filter((t) => !hiddenTags.has(t)) ?? null } : it}
            sparkDays={sparkDays}
            popQualified={!hiddenTags.has("pop") && displayTrack === "wave" && Boolean(it.passed_filter) && (it.total_score ?? 0) >= cutoff}
            tagStats={tagStats?.stats}
          />
        ))}
      </div>
      )}

      {/* 高確信角落影子軌（實驗）：與排序無關的獨立分區，亮燈才佔版面；
          防禦期也顯示（深崩角落正是那時亮，主清單反而關著）。完整檢視在實驗室。 */}
      {displayTrack === "wave" && !isLookback && <CornerSignalsStrip />}

      {/* 觀察區折疊 */}
      {extra.length > 0 && !gateClosed && displayTrack !== "custom" && (
        <div className="mt-6">
          <button
            onClick={() => setShowExtra((s) => !s)}
            className="mb-3 text-sm text-muted hover:text-gray-300"
          >
            {showExtra ? "▼" : "▶"} {extraLabel}（{extra.length}）
          </button>
          {showExtra && (
            <div className="grid grid-cols-1 gap-4 opacity-90 sm:grid-cols-2 lg:grid-cols-3">
              {extra.map((it) => (
                <RecommendationCard key={it.stock_id} item={it} sparkDays={sparkDays} tagStats={tagStats?.stats} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
