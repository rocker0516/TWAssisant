import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "./types";

export type RecommendationList = components["schemas"]["RecommendationList"];
export type RecommendationItem = components["schemas"]["RecommendationItem"];
export type StockDetail = components["schemas"]["StockDetail"];
export type OhlcvResponse = components["schemas"]["OhlcvResponse"];
export type Candle = components["schemas"]["Candle"];
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
