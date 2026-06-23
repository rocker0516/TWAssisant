import { useMemo, useState } from "react";
import { useRecommendations, usePoppableEfficacy, type RecommendationItem, type Track } from "../api/client";
import { RecommendationCard } from "../components/RecommendationCard";
import { consolidationMeta, positionMeta, rangePositionMeta, TRACK_LABELS } from "../lib/format";

type SortKey = "score" | "change";

// 位階/買點篩選（個人偏好，不影響會噴分數）。"all" 不篩；"低位盤整"＝相對低 且 波動收斂打底。
type PosFilter = "all" | "相對低" | "中性" | "偏高" | "低位盤整";
const POS_FILTERS: [PosFilter, string][] = [
  ["all", "全部"], ["相對低", "相對低"], ["中性", "中性"], ["偏高", "偏高"], ["低位盤整", "低位盤整"],
];

function sortItems(items: RecommendationItem[], key: SortKey): RecommendationItem[] {
  return [...items].sort((a, b) =>
    key === "score"
      ? (b.total_score ?? 0) - (a.total_score ?? 0)
      : (b.change_pct ?? -999) - (a.change_pct ?? -999),
  );
}

export default function RecommendationsPage() {
  const [track, setTrack] = useState<Track>("wave");
  const [sort, setSort] = useState<SortKey>("score");
  const [showExtra, setShowExtra] = useState(false);
  const [topPctOverride, setTopPctOverride] = useState<number | null>(null);
  const [posFilter, setPosFilter] = useState<PosFilter>("all");
  const [sparkDays, setSparkDays] = useState(60); // 走勢視窗：近 N 個交易日（預設近3月）
  const { data, isLoading, isError, error } = useRecommendations(track);
  const { data: eff } = usePoppableEfficacy();

  // 波段(會噴)軌：API 回全部過硬篩股，前端用「前 N%」橫桿就地切（分數=百分位，前N% = 分數≥100−N）
  const topPct = topPctOverride ?? (data?.top_pct ?? 20);
  const cutoff = 100 - topPct;

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

  const sorted = useMemo(() => sortItems(data?.items ?? [], sort).filter(matchPos), [data, sort, matchPos]);
  // 波段軌：依橫桿切「達標 / 未達」；長線軌：API 已切 items / near
  const main = useMemo(
    () => (track === "wave" ? sorted.filter((it) => (it.total_score ?? 0) >= cutoff) : sorted),
    [track, sorted, cutoff],
  );
  const extra = useMemo(
    () =>
      track === "wave"
        ? sorted.filter((it) => (it.total_score ?? 0) < cutoff)
        : sortItems(data?.near ?? [], sort),
    [track, sorted, cutoff, data, sort],
  );
  const extraLabel = track === "wave" ? `未達前 ${topPct}% 觀察區` : "接近門檻觀察區";

  const effPct = eff?.threshold != null ? Math.round(100 - eff.threshold) : null;
  const effHit = eff?.overall_hit_rate != null ? Math.round(eff.overall_hit_rate * 100) : null;

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <div className="mb-4 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">進場推薦</h1>
          <p className="text-sm text-muted">
            盤後資料：{data?.date ?? "—"}
            {track === "wave"
              ? `會噴前 ${topPct}%（分數 ≥ ${cutoff}）`
              : `門檻 ≥ ${data?.threshold ?? 70} 分`}
          </p>
        </div>
      </div>

      {/* 雙軌分頁 */}
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

      {/* 會噴門檻橫桿（僅波段軌）*/}
      {track === "wave" && (
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
          <span className="text-xs text-muted">分數 ≥ {cutoff}　·　{main.length} 檔</span>
        </div>
      )}

      {/* 位階篩選（個人偏好，不影響會噴分數）*/}
      {track === "wave" && (
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

      {/* 會噴誠實話術：分數=會噴機率(回測實證)，非漲跌保證；高波動雙面刃 */}
      {track === "wave" && (
        <div className="mb-4 rounded-lg border border-amber-700/50 bg-amber-950/30 px-3.5 py-2.5 text-xs leading-relaxed text-amber-200/90">
          分數＝<b>「會噴機率」</b>——當天全市場 <b>2×波動度＋均線多排</b> 的百分位。
          {effHit != null && effPct != null
            ? <>回測：前 {effPct}% 清單在持有期間摸到可賣停利點（約 +10%）的機率約 <b>{effHit}%</b>。</>
            : <>回測顯示分數越高、持有期間越易出現可賣停利點（約 +10%）。</>}
          <b>不是</b>「會漲」或「會賺」的保證：①多為<b>高波動股、雙面刃</b>（會噴的也會崩），請小部位；
          ②能不能入袋全看<b>出場紀律</b>（沒到價要停損）。
        </div>
      )}

      {/* 工具列 */}
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-muted">排序</span>
          {([["score", "分數"], ["change", "漲跌幅"]] as [SortKey, string][]).map(([k, label]) => (
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
      </div>

      {isLoading && <p className="text-muted">載入中…</p>}
      {isError && <p className="text-down">載入失敗：{(error as Error).message}</p>}

      {data && main.length === 0 && (
        <div className="rounded-xl border border-dashed border-edge py-16 text-center text-sm leading-relaxed text-muted">
          {track !== "wave"
            ? `今日無符合條件的${TRACK_LABELS[track]}軌標的`
            : posFilter === "低位盤整"
            ? "目前會噴清單中沒有「低位盤整打底」的標的。會噴股多在上揚趨勢、波動偏大，少見低檔盤整收斂——此篩選多數時候為空屬正常，可切回「全部」。"
            : `目前無進入前 ${topPct}% 的標的，可放寬橫桿`}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {main.map((it) => (
          <RecommendationCard key={it.stock_id} item={it} sparkDays={sparkDays} />
        ))}
      </div>

      {/* 觀察區折疊 */}
      {extra.length > 0 && (
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
                <RecommendationCard key={it.stock_id} item={it} sparkDays={sparkDays} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
