import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "./types";

export type RecommendationList = components["schemas"]["RecommendationList"];
export type RecommendationItem = components["schemas"]["RecommendationItem"];
export type RecommendationDetail = components["schemas"]["RecommendationDetail"];
export type StockDetail = components["schemas"]["StockDetail"];
export type OhlcvResponse = components["schemas"]["OhlcvResponse"];
export type Candle = components["schemas"]["Candle"];
export type LevelsResponse = components["schemas"]["LevelsResponse"];
export type LevelDTO = components["schemas"]["LevelDTO"];
export type HoldingHistoryResponse = components["schemas"]["HoldingHistoryResponse"];
export type HoldingPoint = components["schemas"]["HoldingPoint"];
export type ScoreDTO = components["schemas"]["ScoreDTO"];
export type HoldingsResponse = components["schemas"]["HoldingsResponse"];
export type HoldingItem = components["schemas"]["HoldingItem"];
export type HoldingCreate = components["schemas"]["HoldingCreate"];
export type TransactionCreate = components["schemas"]["TransactionCreate"];
export type HoldingPatch = components["schemas"]["HoldingPatch"];
export type SectorList = components["schemas"]["SectorList"];
export type SectorItem = components["schemas"]["SectorItem"];
export type SectorDetail = components["schemas"]["SectorDetail"];
export type SectorConstituent = components["schemas"]["SectorConstituent"];
export type OverviewResponse = components["schemas"]["OverviewResponse"];
export type IntelResponse = components["schemas"]["IntelResponse"];
export type IntelEvent = components["schemas"]["IntelEvent"];
export type ThemeDigest = components["schemas"]["ThemeDigest"];
export type StockSearchItem = components["schemas"]["StockSearchItem"];
export type WatchlistsResponse = components["schemas"]["WatchlistsResponse"];
export type WatchlistItemCreate = components["schemas"]["WatchlistItemCreate"];

export type Track = "wave" | "long";
export type HoldingStatus = "open" | "closed";

const BASE = "/api";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${detail}`.trim());
  }
  return res.json() as Promise<T>;
}

export type WaveStyle = "breakout" | "pullback";

export function useRecommendations(track: Track, style?: WaveStyle) {
  const styleQ = track === "wave" && style ? `&style=${style}` : "";
  return useQuery({
    queryKey: ["recommendations", track, track === "wave" ? (style ?? null) : null],
    queryFn: () => getJson<RecommendationList>(`/recommendations?track=${track}${styleQ}`),
  });
}

export function useStockSearch(q: string) {
  const term = q.trim();
  return useQuery({
    queryKey: ["stock-search", term],
    queryFn: () => getJson<StockSearchItem[]>(`/stocks/search?q=${encodeURIComponent(term)}`),
    enabled: term.length >= 1,
    staleTime: 60_000,
  });
}

// ── 分數校準（L4 回測）──

export type CalibrationBucket = {
  lo: number;
  hi: number;
  n: number;
  hit_rate: number | null;
  median_ret: number | null;
};
export type CalibrationConfTier = CalibrationBucket & { tier: string };
export type Calibration = {
  generated_at?: string;
  track: string;
  window: { from?: string | null; to?: string | null; score_dates: number };
  horizons: number[];
  buckets: Record<string, CalibrationBucket[]>;
  baseline: Record<string, number | null>;
  by_confidence?: Record<string, CalibrationConfTier[]>;
  actionable_score?: number;
  samples: number;
  note: string;
};

export function useCalibration() {
  return useQuery({
    queryKey: ["calibration"],
    queryFn: () => getJson<Calibration>("/calibration"),
  });
}

export function useRecomputeCalibration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => sendJson<{ status: string }>("POST", "/calibration/recompute"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calibration"] }),
  });
}

// ── 逐筆期望值回測 ──

export type ExpectancyStats = {
  n: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  expectancy: number | null;
  payoff: number | null;
  avg_hold: number | null;
  forced_pct: number | null;
};
export type ExpectancyScoreRow = ExpectancyStats & { lo: number; hi: number };
export type ExpectancyConfRow = ExpectancyStats & { tier: string; lo: number; hi: number };
export type Expectancy = {
  generated_at?: string;
  track: string;
  window: { from?: string | null; to?: string | null; entry_dates: number };
  cost_pct?: number;
  max_hold?: number;
  actionable_score?: number;
  overall: ExpectancyStats;
  overall_stop_only?: ExpectancyStats;
  control?: ExpectancyStats;
  by_score: ExpectancyScoreRow[];
  by_confidence: ExpectancyConfRow[];
  note: string;
};

export function useExpectancy() {
  return useQuery({
    queryKey: ["expectancy"],
    queryFn: () => getJson<Expectancy>("/expectancy"),
  });
}

export function useRecomputeExpectancy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => sendJson<{ status: string }>("POST", "/expectancy/recompute"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["expectancy"] }),
  });
}

// ── 出場參數掃描 + walk-forward ──

export type SweepParams = { stop_cap: number; trail_trigger: number; trail_pullback: number; break_ma: boolean };
export type SweepRow = { params: SweepParams; n: number; expectancy: number | null; win_rate: number | null; payoff: number | null; avg_mae?: number | null };
export type SweepFold = {
  test_from: string;
  test_to: string;
  picked: SweepParams;
  train_expectancy: number | null;
  oos_expectancy: number | null;
  default_oos_expectancy: number | null;
  n_test: number;
};
export type ParamSweep = {
  generated_at?: string;
  track: string;
  window?: { from?: string; to?: string };
  grid_size?: number;
  default?: { params: SweepParams; n: number; expectancy: number | null; win_rate: number | null; payoff: number | null };
  best_full?: SweepRow | null;
  grid_top: SweepRow[];
  boundary?: { at_max: string[]; is_runaway: boolean; message: string };
  walkforward: {
    folds?: SweepFold[];
    oos_optimized?: number | null;
    oos_default?: number | null;
    oos_optimized_mae?: number | null;
    oos_default_mae?: number | null;
    edge?: number | null;
    verdict?: string;
  };
  note: string;
};

export function useParamSweep() {
  return useQuery({
    queryKey: ["param-sweep"],
    queryFn: () => getJson<ParamSweep>("/param-sweep"),
  });
}

export function useRecomputeParamSweep() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => sendJson<{ status: string }>("POST", "/param-sweep/recompute"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["param-sweep"] }),
  });
}

export function useStockDetail(stockId: string | undefined) {
  return useQuery({
    queryKey: ["stock", stockId],
    queryFn: () => getJson<StockDetail>(`/stocks/${stockId}`),
    enabled: !!stockId,
  });
}

export function useOhlcv(stockId: string | undefined, days = 120) {
  return useQuery({
    queryKey: ["ohlcv", stockId, days],
    queryFn: () => getJson<OhlcvResponse>(`/stocks/${stockId}/ohlcv?days=${days}`),
    enabled: !!stockId,
  });
}

export function useLevels(stockId: string | undefined) {
  return useQuery({
    queryKey: ["levels", stockId],
    queryFn: () => getJson<LevelsResponse>(`/stocks/${stockId}/levels`),
    enabled: !!stockId,
  });
}

export function useHoldingHistory(stockId: string | undefined) {
  return useQuery({
    queryKey: ["holding-history", stockId],
    queryFn: () => getJson<HoldingHistoryResponse>(`/stocks/${stockId}/holding-history`),
    enabled: !!stockId,
    // 史料不足時後端背景回補近一年；回補期間每 6 秒重抓，補完曲線自動長出來。
    refetchInterval: (q) => (q.state.data?.backfilling ? 6000 : false),
  });
}

async function sendJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${detail}`.trim());
  }
  return res.json() as Promise<T>;
}

// ── 持股 ──

export function useHoldings(status: HoldingStatus) {
  return useQuery({
    queryKey: ["holdings", status],
    queryFn: () => getJson<HoldingsResponse>(`/holdings?status=${status}`),
  });
}

export function useCreateHolding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: HoldingCreate) => sendJson<HoldingItem>("POST", "/holdings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["holdings"] }),
  });
}

export function useAddTransaction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: TransactionCreate }) =>
      sendJson<HoldingItem>("POST", `/holdings/${id}/transactions`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["holdings"] }),
  });
}

export function usePatchHolding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: HoldingPatch }) =>
      sendJson<HoldingItem>("PATCH", `/holdings/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["holdings"] }),
  });
}

export function useDeleteHolding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => sendJson<{ ok: boolean }>("DELETE", `/holdings/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["holdings"] }),
  });
}

// ── 類股 ──

export function useSectors() {
  return useQuery({ queryKey: ["sectors"], queryFn: () => getJson<SectorList>("/sectors") });
}

export function useSectorDetail(sectorId: string | undefined) {
  return useQuery({
    queryKey: ["sector", sectorId],
    queryFn: () => getJson<SectorDetail>(`/sectors/${sectorId}`),
    enabled: !!sectorId,
  });
}

// ── 總覽 ──

export function useOverview() {
  return useQuery({ queryKey: ["overview"], queryFn: () => getJson<OverviewResponse>("/overview") });
}

export type IntelFilter = { days?: number; category?: string; riskOnly?: boolean };

export function useIntel(filter: IntelFilter = {}) {
  const { days = 14, category, riskOnly = false } = filter;
  const params = new URLSearchParams({ days: String(days), risk_only: String(riskOnly) });
  if (category) params.set("category", category);
  return useQuery({
    queryKey: ["intel", days, category ?? "", riskOnly],
    queryFn: () => getJson<IntelResponse>(`/intel?${params.toString()}`),
  });
}

// ── 設定 ──

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AppSettings = Record<string, any>;

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => getJson<AppSettings>("/settings") });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, partial }: { key: string; partial: unknown }) =>
      sendJson<AppSettings>("PUT", `/settings/${key}`, partial),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
}

export function useResetSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => sendJson<AppSettings>("POST", `/settings/${key}/reset`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });
}

export function useRecompute() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => sendJson<unknown>("POST", "/settings/recompute"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recommendations"] });
      qc.invalidateQueries({ queryKey: ["sectors"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });
}

// ── 系統狀態 / 資料載入（設定頁）──

export interface PipelineStepResult {
  name: string;
  status: string;
  seconds?: number;
  error?: string;
}
export interface SystemStatus {
  db: string;
  counts: Record<string, number>;
  pipeline_running: boolean;
  last_pipeline_run: {
    trading_date: string | null;
    status: string;
    finished_at: string | null;
    steps: PipelineStepResult[] | null;
  } | null;
}

export function useSystemStatus() {
  return useQuery({
    queryKey: ["system-status"],
    queryFn: () => getJson<SystemStatus>("/system/status"),
    // 跑 pipeline 時每 3 秒輪詢進度，閒置時不輪詢。
    refetchInterval: (q) => (q.state.data?.pipeline_running ? 3000 : false),
  });
}

export function useTriggerPipeline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      sendJson<{ accepted: boolean; reason?: string; trading_date: string }>("POST", "/pipeline/run"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["system-status"] }),
  });
}

// ── 資料來源測試（設定頁）──

export function useTestSource() {
  return useMutation({
    mutationFn: ({ name, token, save }: { name: string; token?: string; save?: boolean }) =>
      sendJson<{ ok: boolean; reason: string }>("POST", `/sources/${name}/test`, { token, save }),
  });
}

// ── 觀察清單 ──

export function useWatchlists() {
  return useQuery({ queryKey: ["watchlists"], queryFn: () => getJson<WatchlistsResponse>("/watchlists") });
}

export function useCreateWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => sendJson("POST", "/watchlists", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });
}

export function useAddWatchItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ wlId, body }: { wlId: number; body: WatchlistItemCreate }) =>
      sendJson("POST", `/watchlists/${wlId}/items`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });
}

export function useDeleteWatchItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: number) => sendJson("DELETE", `/watchlist-items/${itemId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });
}

export function useItemToHolding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, body }: { itemId: number; body: { track: string; date: string; price: number; shares: number } }) =>
      sendJson("POST", `/watchlist-items/${itemId}/to-holding`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["watchlists"] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
  });
}
