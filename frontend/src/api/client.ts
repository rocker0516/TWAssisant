import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "./types";

export type RecommendationList = components["schemas"]["RecommendationList"];
// openapi 型別未重跑，手補標籤制新欄（後端 schemas.RecommendationItem 已回）
export type RecommendationItem = components["schemas"]["RecommendationItem"] & {
  passed_styles?: string[] | null; // 通過的純門檻風格（explosive/strong/story/crash）
  passed_filter?: boolean | null; // 會噴硬篩(含遲滯)是否通過
  prob_hit?: number | null; // 同條件歷史命中%（分數帶×波動帶×大盤狀態查五年表）
  prob_n?: number | null;
  prob_cond?: string | null;
  prob_mae?: number | null; // 同條件歷史平均最深回撤%
};
export type RecommendationDetail = components["schemas"]["RecommendationDetail"];
export type RecommendationLookbackResponse = components["schemas"]["RecommendationLookbackResponse"];
export type LookbackReview = components["schemas"]["LookbackReview"];
export type LookbackSummary = components["schemas"]["LookbackSummary"];
// 月曆端點型別（新加，尚未跑 openapi 生型別；等 openapi 重跑後改回 components["schemas"][…]）
export type LookbackDatePoint = {
  date: string;
  n: number;
  hit_count: number;
  hit_rate: number | null;
};
export type LookbackCalendar = {
  today_date: string | null;
  top_pct: number;
  cutoff: number;
  dates: LookbackDatePoint[];
};
export type StockDetail = components["schemas"]["StockDetail"];
export type OhlcvResponse = components["schemas"]["OhlcvResponse"];
export type Candle = components["schemas"]["Candle"];
export type LevelsResponse = components["schemas"]["LevelsResponse"];
export type LevelDTO = components["schemas"]["LevelDTO"];
export type HoldingHistoryResponse = components["schemas"]["HoldingHistoryResponse"];
export type HoldingPoint = components["schemas"]["HoldingPoint"];
export type ChipHistoryResponse = components["schemas"]["ChipHistoryResponse"];
export type ChipPoint = components["schemas"]["ChipPoint"];
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
export type MarketFlowResponse = components["schemas"]["MarketFlowResponse"];
export type MarketFlowActor = components["schemas"]["MarketFlowActor"];
export type SectorFlowList = components["schemas"]["SectorFlowList"];
export type SectorFlowItem = components["schemas"]["SectorFlowItem"];
export type FlowStockList = components["schemas"]["FlowStockList"];
export type FlowStockItem = components["schemas"]["FlowStockItem"];
export type InstPriceRelation = components["schemas"]["InstPriceRelation"];
export type ChipAlertList = components["schemas"]["ChipAlertList"];
export type ChipAlertItem = components["schemas"]["ChipAlertItem"];
export type SectorRotationResponse = components["schemas"]["SectorRotationResponse"];
export type SectorRotationItem = components["schemas"]["SectorRotationItem"];
export type SectorRotationPoint = components["schemas"]["SectorRotationPoint"];

// 法人別（合計/外資/投信/自營）
export type Actor = "total" | "foreign" | "trust" | "dealer";
export const ACTOR_LABELS: Record<Actor, string> = {
  total: "三大法人",
  foreign: "外資",
  trust: "投信",
  dealer: "自營商",
};

export type Track = "wave" | "long";
export type HoldingStatus = "open" | "closed";

const BASE = "/api";

/** 401 = session 過期/未登入 → 整頁導去登入（避免每個頁面各自處理）。 */
function redirectToLoginOn401(res: Response) {
  if (res.status === 401 && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    redirectToLoginOn401(res);
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${detail}`.trim());
  }
  return res.json() as Promise<T>;
}

// 波段風格：pop=會噴(硬篩+前N%)；explosive=爆發(極高波動+上揚月線，純門檻篩)
export type WaveStyle = "pop" | "explosive" | "strong" | "story" | "crash";

export type TagComboStats = {
  generated_at?: string;
  note?: string;
  stats: Record<string, { n: number; hit: number; avg_mae: number }>;
};

// 標籤組合五年實證命中（靜態統計，卡片顯示用）
export function useTagComboStats() {
  return useQuery({
    queryKey: ["tag-combo-stats"],
    queryFn: () => getJson<TagComboStats>(`/recommendations/tag-stats`),
    staleTime: Infinity,
  });
}

export function useRecommendations(track: Track, style: WaveStyle = "pop") {
  return useQuery({
    queryKey: ["recommendations", track, style],
    queryFn: () =>
      getJson<RecommendationList>(`/recommendations?track=${track}&style=${style}`),
  });
}

// 回看：指定推薦日（月曆點選）；未給 date 就用 N 個交易日前
export function useRecommendationsLookback(
  opts: { date?: string | null; days?: number; probMin?: number; style?: WaveStyle },
) {
  const { date, days, probMin, style } = opts;
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  else if (days != null && days > 0) params.set("days", String(days));
  if (probMin != null && probMin > 0) params.set("prob_min", String(probMin));
  if (style && style !== "pop") params.set("style", style);
  const enabled = !!date || (days != null && days > 0);
  return useQuery({
    queryKey: ["recommendations-lookback", date ?? null, days ?? null, probMin ?? 0, style ?? "pop"],
    queryFn: () => getJson<RecommendationLookbackResponse>(`/recommendations/lookback?${params}`),
    enabled,
  });
}

// 月曆：每個過去 Score 日一筆命中率
export function useRecommendationsLookbackCalendar(probMin?: number, style: WaveStyle = "pop") {
  const params = new URLSearchParams();
  if (probMin != null && probMin > 0) params.set("prob_min", String(probMin));
  if (style !== "pop") params.set("style", style);
  return useQuery({
    queryKey: ["recommendations-lookback-calendar", probMin ?? 0, style],
    queryFn: () => getJson<LookbackCalendar>(`/recommendations/lookback/calendar?${params}`),
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

// 會噴清單成效回測
export type PoppableEffByDate = {
  date: string;
  n: number;
  list_hit_rate: number | null;
  base_hit_rate: number | null;
  lift: number | null;
  avg_mfe: number | null;
  avg_dd: number | null;
  coil_n?: number;
  coil_hit_rate?: number | null;
  exp_n?: number; // 爆發風格（atr>7%+上揚月線）當日檔數
  exp_hit_rate?: number | null;
};
export type PoppableEffDetail = {
  stock_id: string;
  name: string;
  pop: number;
  atr: number;
  mfe: number;
  dd: number;
  cret: number | null;
  hit: boolean;
  coil?: boolean;
};
export type PoppableEfficacy = {
  track: string;
  style: string;
  generated_at?: string;
  horizon?: number;
  pop_target?: number;
  threshold?: number;
  window: { from?: string | null; to?: string | null; entry_dates: number };
  by_date: PoppableEffByDate[];
  overall_hit_rate?: number | null;  // 買在隔天最高（保守；追高最壞情境）
  overall_hit_rate_close?: number | null;  // 買在隔天開盤（一般實務進場）
  total_list: number;
  coil_total?: number;
  coil_overall_hit_rate?: number | null;
  explosive_total?: number; // 爆發風格總樣本
  overall_explosive_hit_rate?: number | null; // 爆發：買在隔天最高
  overall_explosive_hit_rate_close?: number | null; // 爆發：買在隔天開盤
  detail_date?: string | null;
  detail: PoppableEffDetail[];
  note?: string;
};

export function usePoppableEfficacy() {
  return useQuery({
    queryKey: ["poppable-efficacy"],
    queryFn: () => getJson<PoppableEfficacy>("/poppable-efficacy"),
  });
}

export function useRecomputePoppableEfficacy() {
  const qc = useQueryClient();
  return useMutation({
    // 背景重算：POST 立即返回，再輪詢 status 直到跑完（避免長 HTTP 逾時；isPending 期間按鈕維持「回測中」）
    mutationFn: async () => {
      await sendJson<{ running: boolean }>("POST", "/poppable-efficacy/recompute");
      for (let i = 0; i < 240; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const st = await getJson<{ running: boolean; error?: string | null }>(
          "/poppable-efficacy/recompute/status",
        );
        if (!st.running) {
          if (st.error) throw new Error(st.error);
          return;
        }
      }
      throw new Error("重算逾時");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["poppable-efficacy"] }),
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

export interface MarkDTO {
  date: string;
  status: "hit" | "miss" | "pending";
  hit_date: string | null;
  ret_pct: number | null;
}

export interface RecommendationMarksResponse {
  stock_id: string;
  marks: MarkDTO[];
}

export function useRecommendationMarks(stockId: string | undefined, days = 120) {
  return useQuery({
    queryKey: ["recommendation-marks", stockId, days],
    queryFn: () => getJson<RecommendationMarksResponse>(`/stocks/${stockId}/recommendation-marks?days=${days}`),
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

export function useChipHistory(stockId: string | undefined, days = 120) {
  return useQuery({
    queryKey: ["chip-history", stockId, days],
    queryFn: () => getJson<ChipHistoryResponse>(`/stocks/${stockId}/chip-history?days=${days}`),
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
    redirectToLoginOn401(res);
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

// ── 籌碼動向（法人 + 大戶散戶）──

export function useMarketFlow(days = 250) {
  return useQuery({
    queryKey: ["flow-market", days],
    queryFn: () => getJson<MarketFlowResponse>(`/flow/market?days=${days}`),
  });
}

export function useSectorFlow(lookback = 20) {
  return useQuery({
    queryKey: ["flow-sectors", lookback],
    queryFn: () => getJson<SectorFlowList>(`/flow/sectors?lookback=${lookback}`),
  });
}

export function useSectorRotation(actor: Actor = "total", weeks = 6) {
  return useQuery({
    queryKey: ["flow-rotation", actor, weeks],
    queryFn: () => getJson<SectorRotationResponse>(`/flow/rotation?actor=${actor}&weeks=${weeks}`),
  });
}

export function useFlowStocks(sort = "total_cum20", limit = 50) {
  return useQuery({
    queryKey: ["flow-stocks", sort, limit],
    queryFn: () => getJson<FlowStockList>(`/flow/stocks?sort=${sort}&limit=${limit}`),
  });
}

export function useFlowRelation() {
  return useQuery({
    queryKey: ["flow-relation"],
    queryFn: () => getJson<InstPriceRelation>("/flow/relation"),
  });
}

export function useChipAlerts() {
  return useQuery({
    queryKey: ["flow-alerts"],
    queryFn: () => getJson<ChipAlertList>("/flow/alerts"),
  });
}

export function useRecomputeFlowRelation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => sendJson<InstPriceRelation>("POST", "/flow/relation/recompute"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["flow-relation"] }),
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

// ── 高確信角落影子軌（實驗）：data/corners.json 凍結挖掘產物 + corner_signals ──
export type CornerStock = { stock_id: string; name: string; close: number | null };
export type CornerFired = {
  id: string;
  atoms: string[];
  family: "crash" | "dip" | "allweather";
  family_label: string;
  floor: number; // 挖掘窗(2021-24)分年地板命中 %
  per_year: Record<string, { hit: number | null; n: number; days: number }>;
  stocks: CornerStock[];
};
export type CornerSignalsResponse = {
  date: string | null;
  evaluated: boolean;
  total_corners: number;
  fired: CornerFired[];
  recent: { date: string; signals: number; corners: number }[];
  note: string;
};

export function useCornerSignals() {
  return useQuery({
    queryKey: ["corner-signals"],
    queryFn: () => getJson<CornerSignalsResponse>(`/corners`),
    staleTime: 5 * 60_000,
  });
}

export type CornerReviewRow = {
  id: string; atoms: string[]; family_label: string; floor: number;
  n: number; matured: number; hits: number; hit_rate: number | null;
  pending: number; early_hits: number;
};
export type CornerReviewResponse = {
  as_of: string | null;
  oos_from: string;
  overall_unique: { n: number; matured: number; hits: number; hit_rate: number | null; pending: number; early_hits: number };
  by_corner: CornerReviewRow[];
  by_day: { date: string; n: number; matured: number; hits: number; hit_rate: number | null; pending: number; early_hits: number }[];
  note: string;
};

export function useCornerReview(enabled: boolean) {
  return useQuery({
    queryKey: ["corner-review"],
    queryFn: () => getJson<CornerReviewResponse>(`/corners/review`),
    staleTime: 5 * 60_000,
    enabled,
  });
}
