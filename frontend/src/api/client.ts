import { useQuery } from "@tanstack/react-query";
import type { components } from "./types";

export type RecommendationList = components["schemas"]["RecommendationList"];
export type RecommendationItem = components["schemas"]["RecommendationItem"];
export type StockDetail = components["schemas"]["StockDetail"];
export type OhlcvResponse = components["schemas"]["OhlcvResponse"];
export type Candle = components["schemas"]["Candle"];
export type ScoreDTO = components["schemas"]["ScoreDTO"];

export type Track = "wave" | "long";

const BASE = "/api";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${detail}`.trim());
  }
  return res.json() as Promise<T>;
}

export function useRecommendations(track: Track) {
  return useQuery({
    queryKey: ["recommendations", track],
    queryFn: () => getJson<RecommendationList>(`/recommendations?track=${track}`),
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
