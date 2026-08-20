import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "./types";

// 型別一律由 openapi 生成（npm run gen:api），這裡只取別名。
// 欄位語意的真相來源是 backend/app/api/schemas.py 的註解——別在這邊手寫副本，
// 那份副本沒有任何機制保證它跟後端一致（本檔曾因此累積出三塊手補型別）。
export type RecommendationList = components["schemas"]["RecommendationList"];
export type RecommendationItem = components["schemas"]["RecommendationItem"];
export type LongTargetZone = components["schemas"]["LongTargetZone"];
export type LongGraduation = components["schemas"]["LongGraduation"];
export type RecommendationDetail = components["schemas"]["RecommendationDetail"];
export type RecommendationLookbackResponse = components["schemas"]["RecommendationLookbackResponse"];
export type LookbackReview = components["schemas"]["LookbackReview"];
export type LookbackSummary = components["schemas"]["LookbackSummary"];
export type LookbackDatePoint = components["schemas"]["LookbackDatePoint"];
export type LookbackCalendar = components["schemas"]["LookbackCalendar"];
export type StockDetail = components["schemas"]["StockDetail"];
export type OhlcvResponse = components["schemas"]["OhlcvResponse"];
export type Candle = components["schemas"]["Candle"];
export type LevelsResponse = components["schemas"]["LevelsResponse"];
export type LevelDTO = components["schemas"]["LevelDTO"];
export type HoldingHistoryResponse = components["schemas"]["HoldingHistoryResponse"];
export type HoldingPoint = components["schemas"]["HoldingPoint"];
export type ChipHistoryResponse = components["schemas"]["ChipHistoryResponse"];
export type ChipPoint = components["schemas"]["ChipPoint"];
export type FundamentalHistoryResponse = components["schemas"]["FundamentalHistoryResponse"];
export type DividendsResponse = components["schemas"]["DividendsResponse"];
export type PeRiverResponse = components["schemas"]["PeRiverResponse"];
export type IndustryChainResponse = components["schemas"]["IndustryChainResponse"];
export type ScoreDTO = components["schemas"]["ScoreDTO"];
export type HoldingsResponse = components["schemas"]["HoldingsResponse"];
export type HoldingItem = components["schemas"]["HoldingItem"];
export type EntrySnapshot = components["schemas"]["EntrySnapshot"];
export type ThesisStatus = components["schemas"]["ThesisStatus"];
export type HoldingCreate = components["schemas"]["HoldingCreate"];
export type TransactionCreate = components["schemas"]["TransactionCreate"];
export type HoldingPatch = components["schemas"]["HoldingPatch"];
export type SectorList = components["schemas"]["SectorList"];
// openapi 型別未重跑，手補資金移動欄（後端 schemas.SectorItem 已回）
export type SectorItem = components["schemas"]["SectorItem"] & {
  turnover_chg5?: number | null; // 成交佔比 vs 前5日均（個百分點，+=資金移入）
};
export type SectorDetail = components["schemas"]["SectorDetail"];
// openapi 型別未重跑，手補細分聚合欄（後端 schemas.SectorConstituent 已回）
export type SectorConstituent = components["schemas"]["SectorConstituent"] & {
  mom5_pct?: number | null;   // 近 5 交易日漲跌 %
  mom20_pct?: number | null;  // 近 20 交易日漲跌 %
  above_ma20?: boolean | null; // 站上月線
  inst_net5?: number | null;  // 近 5 日三大法人淨買超（張）
  inst_net20?: number | null; // 近 20 日三大法人淨買超（張）
  rev_yoy?: number | null;    // 最新月營收年增 %
};
// SectorDetail 手補大盤同期動能（細分相對強弱基準）
export type SectorDetailExtra = components["schemas"]["SectorDetail"] & {
  market_mom5?: number | null;
  market_mom20?: number | null;
};
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
// 策略室限定：注意/處置事件策略（推薦頁風格篩選不含）
export type LabStyle = WaveStyle | "punish" | "notice";

export type TagStatEntry = {
  n: number; hit: number; avg_mae: number;
  n_tr?: number; hit_tr?: number | null;   // 挖掘窗（< 2025-07-01）
  n_ho?: number; hit_ho?: number | null;   // holdout（≥ 2025-07-01）
};
export type TagComboStats = {
  generated_at?: string;
  note?: string;
  holdout_from?: string; // 雙段切點；用來判斷某標籤是不是「單段實證」
  stats: Record<string, TagStatEntry>;
};

// 量能加成條件（鍵與 build_tag_combo_stats.py 的 lift:<tag>|<cond> 一致）
const LIFT_CONDS: [string, string][] = [["volup", "×量增>1.5"], ["voldn", "×量縮<0.8"]];

export type TagLift = {
  cond: string; label: string; n: number;
  hitTr: number; hitHo: number; liftTr: number; liftHo: number;
};

/** 某標籤最強的「雙段都贏基線 ≥minPp」加成條件；沒有就回 null（不再手抄過期數字）。 */
export function bestTagLift(
  stats: TagComboStats["stats"] | undefined, tag: string, minPp = 4,
): TagLift | null {
  const base = stats?.[`any:${tag}`];
  if (!stats || base?.hit_tr == null || base?.hit_ho == null) return null; // crash 無 holdout 段
  let best: TagLift | null = null;
  for (const [cond, label] of LIFT_CONDS) {
    const v = stats[`lift:${tag}|${cond}`];
    if (v?.hit_tr == null || v.hit_ho == null) continue;
    const liftTr = v.hit_tr - base.hit_tr, liftHo = v.hit_ho - base.hit_ho;
    if (Math.min(liftTr, liftHo) < minPp) continue;
    if (!best || Math.min(liftTr, liftHo) > Math.min(best.liftTr, best.liftHo)) {
      best = { cond, label, n: v.n, hitTr: v.hit_tr, hitHo: v.hit_ho, liftTr, liftHo };
    }
  }
  return best;
}

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

export interface TargetPriceEntry {
  date: string;
  target_price: number;
  prev_target: number | null;
  direction: "up" | "down" | "flat" | "new";
  target_high: number | null;
  target_low: number | null;
  analyst_count: number | null;
  rating_bull: number | null;
  rating_neutral: number | null;
  rating_bear: number | null;
  eps_est: number | null;
  hit: boolean;
  hit_date: string | null;
  upside_pct: number | null;
}

export interface TargetPriceResponse {
  stock_id: string;
  latest: TargetPriceEntry | null;
  history: TargetPriceEntry[];
}

export function useTargetPrice(stockId: string | undefined) {
  return useQuery({
    queryKey: ["target-price", stockId],
    queryFn: () => getJson<TargetPriceResponse>(`/stocks/${stockId}/target-price`),
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

export function useFundamentalHistory(stockId: string | undefined) {
  return useQuery({
    queryKey: ["fundamental-history", stockId],
    queryFn: () => getJson<FundamentalHistoryResponse>(`/stocks/${stockId}/fundamental-history`),
    enabled: !!stockId,
  });
}

export function useDividends(stockId: string | undefined) {
  return useQuery({
    queryKey: ["dividends", stockId],
    queryFn: () => getJson<DividendsResponse>(`/stocks/${stockId}/dividends`),
    enabled: !!stockId,
    staleTime: 24 * 60 * 60 * 1000, // 股利資料一天內不重打（後端已 30 天快取）
  });
}

export function usePeRiver(stockId: string | undefined) {
  return useQuery({
    queryKey: ["pe-river", stockId],
    queryFn: () => getJson<PeRiverResponse>(`/stocks/${stockId}/pe-river`),
    enabled: !!stockId,
  });
}

// 本淨比河流圖：後端與 PE 河流共用回應結構（pe_levels/current_pe 欄位承載 PB 值）
export function usePbRiver(stockId: string | undefined) {
  return useQuery({
    queryKey: ["pb-river", stockId],
    queryFn: () => getJson<PeRiverResponse>(`/stocks/${stockId}/pb-river`),
    enabled: !!stockId,
  });
}

// openapi 型別未重跑，手寫（對應後端 schemas.TechSummaryResponse）
export type TechSummaryResponse = {
  stock_id: string;
  date?: string | null;
  kd_k?: number | null;
  kd_d?: number | null;
  macd?: number | null;
  macd_signal?: number | null;
  macd_hist?: number | null;
  bias_20?: number | null;
  bias_60?: number | null;
  beta?: number | null;
  high_52w?: number | null;
  low_52w?: number | null;
  dist_high_pct?: number | null;
  dist_low_pct?: number | null;
  volatility_pct?: number | null;
};

// openapi 型別未重跑，手寫（對應後端 schemas.FinancialStatementsResponse）
export type FinStatementQuarter = {
  label: string;
  cash?: number | null;
  current_assets?: number | null;
  total_assets?: number | null;
  current_liab?: number | null;
  total_liab?: number | null;
  equity?: number | null;
  inventories?: number | null;
  receivables?: number | null;
  debt_ratio?: number | null;
  current_ratio?: number | null;
  bps?: number | null;
  op_cf?: number | null;
  inv_cf?: number | null;
  fin_cf?: number | null;
  capex?: number | null;
  fcf?: number | null;
};
export type FinancialStatementsResponse = {
  stock_id: string;
  quarters: FinStatementQuarter[];
};

export function useFinancialStatements(stockId: string | undefined) {
  return useQuery({
    queryKey: ["financial-statements", stockId],
    queryFn: () => getJson<FinancialStatementsResponse>(`/stocks/${stockId}/financial-statements`),
    enabled: !!stockId,
    staleTime: 24 * 60 * 60 * 1000, // 財報一天內不重打（後端已 30 天快取）
  });
}

// openapi 型別未重跑，手寫（對應後端 schemas.FearGreedResponse）
export type FearGreedComponent = {
  key: string;
  label: string;
  desc?: string | null;
  score: number;
  value?: number | null;
};
export type UsFearGreed = {
  score: number;
  rating: string;
  label: string;
  prev_close?: number | null;
  prev_week?: number | null;
  prev_month?: number | null;
  prev_year?: number | null;
  history: { date: string; score: number }[];
};
export type FearGreedResponse = {
  date?: string | null;
  score?: number | null;
  label?: string | null;
  components: FearGreedComponent[];
  history: { date: string; score: number }[];
  us?: UsFearGreed | null;
};

export function useFearGreed() {
  return useQuery({
    queryKey: ["fear-greed"],
    queryFn: () => getJson<FearGreedResponse>("/market/fear-greed"),
    staleTime: 60 * 60 * 1000, // 日更資料，一小時內不重打
  });
}

// openapi 型別未重跑，手寫（對應後端 schemas.AttentionResponse）
export type AttentionEntry = {
  date: string;
  kind: string; // notice / punish
  times?: number | null;
  begin_date?: string | null;
  end_date?: string | null;
  reason?: string | null;
};
export type AttentionResponse = {
  stock_id: string;
  status?: string | null; // punish / notice / null
  punish_end?: string | null;
  notice_count_30d: number;
  entries: AttentionEntry[];
};

export function useAttention(stockId: string | undefined) {
  return useQuery({
    queryKey: ["attention", stockId],
    queryFn: () => getJson<AttentionResponse>(`/stocks/${stockId}/attention`),
    enabled: !!stockId,
  });
}

export function useTechSummary(stockId: string | undefined) {
  return useQuery({
    queryKey: ["tech-summary", stockId],
    queryFn: () => getJson<TechSummaryResponse>(`/stocks/${stockId}/tech-summary`),
    enabled: !!stockId,
  });
}

export function useIndustryChain(stockId: string | undefined) {
  return useQuery({
    queryKey: ["industry-chain", stockId],
    queryFn: () => getJson<IndustryChainResponse>(`/stocks/${stockId}/industry-chain`),
    enabled: !!stockId,
    staleTime: 24 * 60 * 60 * 1000, // 產業鏈為慢變資料
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

// ── 回測實驗室 ──

export type Condition = { field: string; op: string; value: number | { n: number; threshold: number } };
export type Strategy = {
  id: number; name: string; conditions: Condition[];
  sort_field: string; sort_desc: boolean; top_n: number;
  target_pct: number; horizon_days: number; stop_pct: number | null; is_active: boolean;
};
export type FieldMeta = { key: string; label: string; group: string; unit: string };
export type BacktestResult = {
  samples: number; hits: number; hit_rate: number | null; base_rate: number | null;
  lift: number | null; avg_max_drawdown: number | null;
  monthly: { month: string; samples: number; hits: number }[];
  recent: { date: string; stock_id: string; name: string; entry: number; hit: boolean;
            stopped: boolean; max_gain_pct: number; max_dd_pct: number }[];
  warn_loose: boolean; signal_days: number;
};
export type StrategyDaily = {
  strategy: Strategy | null; date: string | null;
  items: { stock_id: string; name: string; close: number | null; sort_value: number | null }[];
};

export function useStrategyFields() {
  return useQuery({ queryKey: ["strategy-fields"], staleTime: Infinity,
    queryFn: () => getJson<FieldMeta[]>("/lab/strategies/fields") });
}
export function useStrategies() {
  return useQuery({ queryKey: ["strategies"],
    queryFn: () => getJson<Strategy[]>("/lab/strategies/") });
}
function useStrategyMutation<T, A>(fn: (a: A) => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["strategies"] });
    qc.invalidateQueries({ queryKey: ["strategy-daily"] });
  }});
}
export function useCreateStrategy() {
  return useStrategyMutation((body: Partial<Strategy>) =>
    sendJson<Strategy>("POST", "/lab/strategies/", body));
}
export function usePatchStrategy() {
  return useStrategyMutation(({ id, ...body }: Partial<Strategy> & { id: number; clear_stop?: boolean }) =>
    sendJson<Strategy>("PATCH", `/lab/strategies/${id}`, body));
}
export function useDeleteStrategy() {
  return useStrategyMutation((id: number) =>
    sendJson<{ ok: boolean }>("DELETE", `/lab/strategies/${id}`));
}
export function useActivateStrategy() {
  return useStrategyMutation((id: number) =>
    sendJson<Strategy>("POST", `/lab/strategies/${id}/activate`));
}
export function useDeactivateStrategy() {
  return useStrategyMutation(() =>
    sendJson<{ ok: boolean }>("POST", "/lab/strategies/deactivate"));
}
export function useBacktest() {
  return useMutation({ mutationFn: ({ id, start, end }: { id: number; start: string; end: string }) =>
    sendJson<BacktestResult>("POST", `/lab/strategies/${id}/backtest`, { start, end }) });
}
export function useActiveStrategyDaily() {
  return useQuery({ queryKey: ["strategy-daily"],
    queryFn: () => getJson<StrategyDaily>("/lab/strategies/active/daily") });
}

// ── 帳號 ──

// /auth/me 回傳三態：無登入牆（auth_enabled=false）／未登入／已登入。
// 不在 openapi schema（後端回 dict），手寫小型別。
export type Me = {
  authenticated: boolean;
  auth_enabled: boolean;
  email?: string | null;
  tier?: string | null;   // free / pro
  role?: string | null;   // user / admin
};

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => getJson<Me>("/auth/me"),
    staleTime: 5 * 60 * 1000, // 身分很少變，登入/登出都會整頁導向
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
  /** floor=分年地板紀律（既有）；stable_edge=兩窗同日增量皆正（試跑中） */
  origin: "floor" | "stable_edge";
  floor: number; // 挖掘窗(2021-24)分年地板命中 %
  per_year: Record<string, { hit: number | null; n: number; days?: number }>;
  edge_mine_pp: number | null;
  edge_holdout_pp: number | null;
  /** 真 OOS 期實測的同日同錨增量（試跑結果，可能與挖掘期反號） */
  oos_edge_pp: number | null;
  holdout_hit: number | null;
  holdout_n: number | null;
  caveat: string | null;
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
  id: string; atoms: string[]; family_label: string;
  origin: "floor" | "stable_edge";
  floor: number;
  /** 該角落自己的及格線：floor 用地板、stable_edge 用它的 holdout 命中 */
  benchmark: number;
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

// ─────────── 策略室（模擬倉 / 勝率分析 / 敏感度）───────────

export type PaperPosition = {
  stock_id: string; name: string;
  signal_date: string; entry_date: string; entry_price: number;
  stop_price: number; target_price: number;
  status: "open" | "closed";
  exit_date: string | null; exit_price: number | null;
  exit_reason: "stop" | "target" | "timeout" | null;
  return_pct: number | null; days_held: number;
  score: number | null; prob_hit: number | null;
};
export type PaperSimStats = {
  trades: number; closed: number; open: number; wins: number;
  win_rate: number | null; avg_return_pct: number | null;
  total_return_pct: number | null; open_unrealized_pct: number | null;
  avg_days_held: number | null; max_drawdown_pct: number | null;
};
export type PaperSimResponse = {
  since: string | null; today_date: string | null;
  style: LabStyle; prob_min: number; top_n: number;
  hold_days: number; stop_pct: number; target_pct: number;
  stats: PaperSimStats;
  equity: { date: string; cum_return_pct: number }[];
  positions: PaperPosition[];
};

export type PaperSimParams = {
  since?: string; style?: LabStyle; probMin?: number;
  topN?: number; holdDays?: number; stopPct?: number;
};

export function usePaperSimulate(p: PaperSimParams) {
  const params = new URLSearchParams();
  if (p.since) params.set("since", p.since);
  if (p.style) params.set("style", p.style);
  if (p.probMin != null) params.set("prob_min", String(p.probMin));
  if (p.topN != null) params.set("top_n", String(p.topN));
  if (p.holdDays != null) params.set("hold_days", String(p.holdDays));
  if (p.stopPct != null) params.set("stop_pct", String(p.stopPct));
  return useQuery({
    queryKey: ["paper-sim", p],
    queryFn: () => getJson<PaperSimResponse>(`/paper/simulate?${params}`),
    staleTime: 5 * 60_000,
  });
}

export type LookbackGroupStat = {
  key: string; n: number; hit_count: number; hit_rate: number | null;
  avg_return_pct: number | null; avg_mfe_pct: number | null; avg_mae_pct: number | null;
};
export type LookbackStatsResponse = {
  since: string | null; today_date: string | null; min_age_days: number;
  by_style: LookbackGroupStat[]; by_score_bin: LookbackGroupStat[];
};

export function useLookbackStats(since?: string) {
  const q = since ? `?since=${since}` : "";
  return useQuery({
    queryKey: ["lookback-stats", since ?? "all"],
    queryFn: () => getJson<LookbackStatsResponse>(`/recommendations/lookback/stats${q}`),
    staleTime: 10 * 60_000,
  });
}

// 標籤共存矩陣：matrix[i][j] = P(同時有 tags[j] | 已有 tags[i])，%
// 精確組合鍵的標準順序（與後端 _COOC_TAGS 一致；組合鍵＝依此順序 join "+"）
export const COMBO_TAG_ORDER = ["pop", "explosive", "strong", "story", "crash", "punish", "notice"] as const;
export function comboKeyOf(tags: Iterable<string>): string {
  const s = new Set(tags);
  return COMBO_TAG_ORDER.filter((t) => s.has(t)).join("+");
}
// 策略室勾選的組合 → 進場推薦主清單篩選（localStorage 溝通）
export const COMBO_FILTER_KEY = "comboFilters";
export function readComboFilters(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(COMBO_FILTER_KEY) ?? "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

export type TagComboStat = {
  key: string;
  n: number;
  share_pct: number;
  hit_rate?: number | null;
  avg_ret_pct?: number | null;
  avg_mfe_pct?: number | null;
  avg_mae_pct?: number | null;
};
export type CooccurrenceResponse = {
  since?: string | null;
  today_date?: string | null;
  tags: string[];
  counts: Record<string, number>;
  matrix: (number | null)[][];
  combos: TagComboStat[];
};

export function useTagCooccurrence(since?: string) {
  const q = since ? `?since=${since}` : "";
  return useQuery({
    queryKey: ["tag-cooccurrence", since ?? "all"],
    queryFn: () => getJson<CooccurrenceResponse>(`/recommendations/lookback/cooccurrence${q}`),
    staleTime: 10 * 60_000,
  });
}

export type ComboSample = {
  date: string;
  stock_id: string;
  name: string;
  hit?: boolean | null;
  ret_pct?: number | null;
  mfe_pct?: number | null;
  mae_pct?: number | null;
};
export type ComboSamplesResponse = {
  combo: string;
  since?: string | null;
  samples: ComboSample[];
};

export function useComboSamples(combo: string | null, since?: string) {
  const q = new URLSearchParams();
  if (combo) q.set("combo", combo);
  if (since) q.set("since", since);
  return useQuery({
    queryKey: ["combo-samples", combo, since ?? "all"],
    queryFn: () => getJson<ComboSamplesResponse>(`/recommendations/lookback/cooccurrence/samples?${q}`),
    enabled: !!combo,
    staleTime: 10 * 60_000,
  });
}

export type SignalDecayPoint = { ym: string; n: number; hit?: number | null; lift?: number | null };
export type SignalDecaySeries = {
  key: string;
  points: SignalDecayPoint[];
  hit_all?: number | null;
  lift_all?: number | null;
  hit_recent?: number | null;
  lift_recent?: number | null;
};
export type SignalDecayResponse = {
  today_date?: string | null;
  base: SignalDecayPoint[];
  signals: SignalDecaySeries[];
};

export function useSignalDecay() {
  return useQuery({
    queryKey: ["signal-decay"],
    queryFn: () => getJson<SignalDecayResponse>("/recommendations/signal-decay"),
    staleTime: 60 * 60_000,
  });
}

export type SensitivityPoint = {
  prob_min: number; n: number; days: number; day_cover: number | null;
  avg_daily_n: number | null; hit_count: number;
  hit_rate: number | null; hit_rate_lo: number | null; hit_rate_hi: number | null;
  day_hit_rate: number | null; lift: number | null;
  avg_return_pct: number | null; avg_return_open_pct: number | null;
  avg_mfe_pct: number | null; avg_mae_pct: number | null;
  reliable: boolean;
};
export type CalibrationBin = {
  lo: number; hi: number; n: number; days: number; pred_avg: number;
  hit_rate: number | null; hit_rate_lo: number | null; hit_rate_hi: number | null;
  err_pp: number | null; reliable: boolean;
};
export type SensitivityResponse = {
  since: string | null; today_date: string | null; min_age_days: number;
  entry_days: number; base_hit_rate: number | null;
  points: SensitivityPoint[]; calibration: CalibrationBin[]; note: string;
};

export function useLookbackSensitivity(since?: string) {
  const q = since ? `?since=${since}` : "";
  return useQuery({
    queryKey: ["lookback-sensitivity", since ?? "all"],
    queryFn: () => getJson<SensitivityResponse>(`/recommendations/lookback/sensitivity${q}`),
    staleTime: 10 * 60_000,
  });
}
