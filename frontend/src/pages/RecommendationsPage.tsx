import { useMemo, useState } from "react";
import {
  useRecommendations,
  useRecommendationsLookback,
  useRecommendationsLookbackCalendar,
  usePoppableEfficacy,
  type RecommendationItem,
  type Track,
  type WaveStyle,
  useTagComboStats,
} from "../api/client";
import { RecommendationCard } from "../components/RecommendationCard";
import { LookbackCalendar } from "../components/LookbackCalendar";
import { changeColor, consolidationMeta, fmtPct, positionMeta, rangePositionMeta, TRACK_LABELS } from "../lib/format";

type SortKey = "score" | "entry_timing" | "change" | "lookback_return" | "lookback_mfe";

// 位階/買點篩選（個人偏好，不影響會噴分數）。"all" 不篩；"低位盤整"＝相對低 且 波動收斂打底。
type PosFilter = "all" | "相對低" | "中性" | "偏高" | "低位盤整";
const POS_FILTERS: [PosFilter, string][] = [
  ["all", "全部"], ["相對低", "相對低"], ["中性", "中性"], ["偏高", "偏高"], ["低位盤整", "低位盤整"],
];

// 注意：排序只改顯示順序，不改清單成員（成員由會噴分數 cutoff 決定）。「進場時機」＝在已選出
// 的會噴清單『內部』把法人剛進場的往前排——研究實證清單內高時機半比低時機半多噴 +2.6pp。
function sortItems(items: RecommendationItem[], key: SortKey): RecommendationItem[] {
  return [...items].sort((a, b) => {
    switch (key) {
      case "score":
        return (b.total_score ?? 0) - (a.total_score ?? 0);
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
  });
}

export default function RecommendationsPage() {
  const [track, setTrack] = useState<Track>("wave");
  const [sort, setSort] = useState<SortKey>("score");
  const [showExtra, setShowExtra] = useState(false);
  const [topPctOverride, setTopPctOverride] = useState<number | null>(null);
  const [posFilter, setPosFilter] = useState<PosFilter>("all");
  const [minPrice, setMinPrice] = useState(""); // 股價下限（元，空＝不限）
  const [maxPrice, setMaxPrice] = useState(""); // 股價上限（元，空＝不限）
  const [sparkDays, setSparkDays] = useState(60); // 走勢視窗：近 N 個交易日（預設近3月）
  const [showCalendar, setShowCalendar] = useState(false); // 月曆折疊
  const [showDefenseList, setShowDefenseList] = useState(false); // 大盤防禦期仍要查看清單
  const [selectedLookbackDate, setSelectedLookbackDate] = useState<string | null>(null); // null=今天；否則=月曆點選的推薦日
  // 風格改標籤制（2026-07-28）：不再分頁切換，清單=會噴候選∪風格股，
  // 每檔卡片顯示標籤（會噴/爆發/強勢延伸/故事股/深跌反攻），標籤越多排越前。
  // 回看只支援波段軌；切到長線軌時自動回到今天
  const effTrack: Track = selectedLookbackDate ? "wave" : track;
  const effStyle: WaveStyle = "pop";
  const { data, isLoading, isError, error } = useRecommendations(effTrack, effStyle);
  const { data: eff } = usePoppableEfficacy();
  const { data: tagStats } = useTagComboStats();

  // 波段(會噴)軌：API 回全部過硬篩股，前端用「前 N%」橫桿就地切（分數=百分位，前N% = 分數≥100−N）
  const topPct = topPctOverride ?? (data?.top_pct ?? 20);
  const cutoff = 100 - topPct;

  // 大盤 regime 閘門（僅波段軌）：防禦期(收盤跌破季線逾2%未站回)清單命中率實證較低
  // (39.5% vs 47.4%，walk-forward 三段皆成立)，預設收起清單、可手動展開。
  const regime = data?.regime ?? null;
  const inDefense = track === "wave" && selectedLookbackDate == null && regime?.state === "defense";
  const gateClosed = inDefense && !showDefenseList;

  // 月曆摘要（命中率）：隨風格切換（爆發=純門檻篩成員的命中率）
  const calendar = useRecommendationsLookbackCalendar(topPct, effStyle);

  // 回看：指定推薦日的清單（含 review），同樣隨風格
  const lookback = useRecommendationsLookback({
    date: selectedLookbackDate,
    topPct: selectedLookbackDate ? topPct : undefined,
    style: effStyle,
  });

  // 位階/買點篩選（僅波段軌、個人偏好）：比對卡片同一套標籤，缺料則濾掉。
  // 相對低/中性/偏高＝依「走勢」視窗算的區間位階（與卡片徽章同一套 rangePositionMeta，隨 1/3/6 月連動）。
  // 「低位盤整」＝低檔盤整打底（盤整分≥50，低位已內建在分數裡；此為後端短線打底訊號，不隨走勢視窗變）。
  const matchPos = useMemo(
    () =>
      track !== "wave" || posFilter === "all"
        ? () => true
        : posFilter === "低位盤整"
        ? (it: RecommendationItem) => consolidationMeta(it.sub_scores?.consolidation) !== null
        : (it: RecommendationItem) =>
            (rangePositionMeta(it.spark, sparkDays) ?? positionMeta(it.sub_scores?.position))?.label === posFilter,
    [track, posFilter, sparkDays],
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
        : sortItems(baseItems, sort).filter(matchPos).filter(matchPrice),
    [isLookback, baseItems, sort, matchPos, matchPrice],
  );
  // 標籤數：會噴(過硬篩且分數達橫桿) + 各純門檻風格；標籤越多=越多獨立驗證的訊號共振
  const tagCountOf = useMemo(
    () => (it: RecommendationItem) =>
      (it.passed_styles?.length ?? 0)
      + (it.passed_filter && (it.total_score ?? 0) >= cutoff ? 1 : 0),
    [cutoff],
  );
  // 波段軌：主清單=至少一個標籤，標籤多者在前（穩定排序保留次要排序鍵）；
  // 觀察區=0標籤（未達橫桿又無風格）；長線軌/回看沿用原邏輯
  const main = useMemo(
    () =>
      isLookback
        ? [...sorted].sort((a, b) => tagCountOf(b) - tagCountOf(a))
        : track === "wave"
        ? sorted.filter((it) => tagCountOf(it) > 0).sort((a, b) => tagCountOf(b) - tagCountOf(a))
        : sorted,
    [isLookback, track, sorted, tagCountOf],
  );
  const extra = useMemo(
    () =>
      isLookback
        ? []
        : track === "wave"
        ? sorted.filter((it) => tagCountOf(it) === 0)
        : sortItems(data?.near ?? [], sort).filter(matchPrice),
    [isLookback, track, sorted, tagCountOf, data, sort, matchPrice],
  );
  const extraLabel = track === "wave" ? `未達前 ${topPct}% 觀察區` : "接近門檻觀察區";

  // 回看摘要
  const lbSummary = lookback.data?.summary;
  const lbHitRate = lbSummary?.hit_rate != null ? Math.round(lbSummary.hit_rate * 100) : null;

  const effPct = eff?.threshold != null ? Math.round(100 - eff.threshold) : null;
  const effHit = eff?.overall_hit_rate != null ? Math.round(eff.overall_hit_rate * 100) : null;
  const effHitClose = eff?.overall_hit_rate_close != null ? Math.round(eff.overall_hit_rate_close * 100) : null;
  const effDays = eff?.horizon ?? 30;

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
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
                {track === "wave"
                  ? `　會噴前 ${topPct}%＋風格標籤（標籤越多排越前）`
                  : `　門檻 ≥ ${data?.threshold ?? 70} 分`}
              </>
            )}
          </p>
        </div>
      </div>

      {/* 回看月曆：波段軌專屬。切到回看時長線軌會自動切回波段 */}
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
                  setSort("score"); // 切模式時把排序鎖回會噴分數
                }}
              />
            )}
          </>
        )}
      </div>

      {/* 雙軌分頁（回看模式不可切，固定波段） */}
      {!isLookback && (
        <div className="mb-4 flex gap-1 border-b border-edge">
          {(["wave", "long"] as Track[]).map((t) => (
            <button
              key={t}
              onClick={() => setTrack(t)}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
                track === t ? "border-sky-500 text-sky-300" : "border-transparent text-muted hover:text-gray-300"
              }`}
            >
              {TRACK_LABELS[t]}軌
              {data && track === t ? <span className="ml-1.5 text-xs">({main.length})</span> : null}
            </button>
          ))}
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

      {/* 會噴門檻橫桿（僅波段軌）：只影響「會噴」標籤的門檻，不影響風格標籤 */}
      {track === "wave" && !gateClosed && (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted">嚴格度</span>
          <input
            type="range"
            min={1}
            max={50}
            value={topPct}
            onChange={(e) => setTopPctOverride(Number(e.target.value))}
            className="h-1.5 w-56 cursor-pointer accent-sky-500"
          />
          <span className="text-sm font-medium text-sky-300">前 {topPct}%</span>
          <span className="text-xs text-muted">
            分數 ≥ {cutoff} 得「會噴」標籤　·　共 {main.length} 檔　·　標籤越多排越前
          </span>
        </div>
      )}

      {/* 位階篩選（個人偏好，不影響會噴分數；回看模式不適用）*/}
      {track === "wave" && !isLookback && !gateClosed && (
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
      {track === "wave" && !isLookback && !gateClosed && (
        <div className="mb-4 rounded-lg border border-amber-700/50 bg-amber-950/30 px-3.5 py-2.5 text-xs leading-relaxed text-amber-200/90">
          分數＝<b>當天全市場的「會噴排名」百分位</b>（2×波動度＋均線多排＋52週位階＋PB 四因子）——
          是<b>相對排名不是機率</b>；機率看標籤旁的同條件五年實證命中率。
          {effHitClose != null && effPct != null
            ? <>回測：前 {effPct}% 清單，<b>隔天開盤進場後 {effDays} 個交易日內碰到 +10%</b> 的機率約 <b>{effHitClose}%</b>
                {effHit != null ? <>（追高到隔天最高則約 {effHit}%）</> : null}。</>
            : <>回測顯示分數越高、{effDays} 個交易日內越易碰到 +10% 停利點。</>}
          <b>不是</b>「會漲」或「會賺」的保證：①多為<b>高波動股、雙面刃</b>（會噴的也會崩），請小部位；
          ②能不能入袋全看<b>出場紀律</b>（沒到價要停損）。
        </div>
      )}

      {/* 工具列（防禦期收起清單時一併隱藏） */}
      {!gateClosed && (
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-muted">排序</span>
          {(
            [
              ["score", "會噴分數"],
              ...(track === "wave" && !isLookback ? [["entry_timing", "進場時機"] as [SortKey, string]] : []),
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
      {!isLookback && data && main.length === 0 && !gateClosed && (
        <div className="rounded-xl border border-dashed border-edge py-16 text-center text-sm leading-relaxed text-muted">
          {(minPrice !== "" || maxPrice !== "")
            ? "目前篩選的股價區間內沒有符合的標的，可調整或清除股價上下限。"
            : track !== "wave"
            ? `今日無符合條件的${TRACK_LABELS[track]}軌標的`
            : posFilter === "低位盤整"
            ? "目前會噴清單中沒有「低位盤整打底」的標的。會噴股多在上揚趨勢、波動偏大，少見低檔盤整收斂——此篩選多數時候為空屬正常，可切回「全部」。"
            : `目前沒有掛任何標籤（會噴/爆發/強勢延伸/故事股/深跌反攻）的標的，可放寬橫桿`}
        </div>
      )}

      {!gateClosed && (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {main.map((it) => (
          <RecommendationCard
            key={it.stock_id}
            item={it}
            sparkDays={sparkDays}
            popQualified={Boolean(it.passed_filter) && (it.total_score ?? 0) >= cutoff}
            tagStats={tagStats?.stats}
          />
        ))}
      </div>
      )}

      {/* 觀察區折疊 */}
      {extra.length > 0 && !gateClosed && (
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
