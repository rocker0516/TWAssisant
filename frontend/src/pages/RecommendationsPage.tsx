import { useMemo, useState } from "react";
import { useRecommendations, useSettings, type RecommendationItem, type Track } from "../api/client";
import { RecommendationCard } from "../components/RecommendationCard";
import { TRACK_LABELS } from "../lib/format";

type SortKey = "score" | "change";

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
  const [showNear, setShowNear] = useState(false);
  const { data, isLoading, isError, error } = useRecommendations(track);
  const { data: settings } = useSettings();
  const waveStyle = settings?.scoring?.wave?.style ?? "breakout";

  const items = useMemo(() => sortItems(data?.items ?? [], sort), [data, sort]);
  const near = useMemo(() => sortItems(data?.near ?? [], sort), [data, sort]);

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <div className="mb-4 flex items-end justify-between">
        <div>
          <h1 className="text-xl font-bold">進場推薦</h1>
          <p className="text-sm text-muted">
            盤後資料：{data?.date ?? "—"}　門檻 ≥ {data?.threshold ?? 70} 分
            {track === "wave" && (
              <span className="ml-2 rounded bg-sky-900/50 px-1.5 py-0.5 text-xs text-sky-300">
                風格：{waveStyle === "pullback" ? "回檔低接" : "突破追強"}
              </span>
            )}
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
            {data && track === t ? <span className="ml-1.5 text-xs">({data.items.length})</span> : null}
          </button>
        ))}
      </div>

      {/* 工具列 */}
      <div className="mb-4 flex items-center gap-2 text-sm">
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

      {isLoading && <p className="text-muted">載入中…</p>}
      {isError && <p className="text-down">載入失敗：{(error as Error).message}</p>}

      {data && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-edge py-16 text-center text-muted">
          今日無符合條件的{TRACK_LABELS[track]}軌標的
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((it) => (
          <RecommendationCard key={it.stock_id} item={it} />
        ))}
      </div>

      {/* 接近門檻折疊區 */}
      {near.length > 0 && (
        <div className="mt-6">
          <button
            onClick={() => setShowNear((s) => !s)}
            className="mb-3 text-sm text-muted hover:text-gray-300"
          >
            {showNear ? "▼" : "▶"} 接近門檻觀察區（{near.length}）
          </button>
          {showNear && (
            <div className="grid grid-cols-1 gap-4 opacity-90 sm:grid-cols-2 lg:grid-cols-3">
              {near.map((it) => (
                <RecommendationCard key={it.stock_id} item={it} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
