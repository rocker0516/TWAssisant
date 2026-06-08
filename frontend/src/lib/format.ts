// 評分 5 類中文標籤（懸停顯示用）
export const CATEGORY_LABELS: Record<string, string> = {
  // 波段軌
  trend: "趨勢",
  momentum: "動能",
  volume: "量能",
  chip: "籌碼",
  pattern: "型態",
  // 長線軌
  profit: "獲利",
  growth: "營收成長",
  valuation: "估值",
  quality: "體質",
  trend_aux: "趨勢輔助",
};

export const TRACK_LABELS: Record<string, string> = { wave: "波段", long: "長線" };

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("zh-TW", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  const s = v.toLocaleString("zh-TW", { minimumFractionDigits: 0, maximumFractionDigits: digits });
  return `${v > 0 ? "+" : ""}${s}%`;
}

// 台股紅漲綠跌
export function changeColor(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "text-muted";
  return v > 0 ? "text-up" : "text-down";
}

export function scoreColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "text-muted";
  if (v >= 80) return "text-up";
  if (v >= 70) return "text-amber-400";
  if (v >= 60) return "text-yellow-500";
  return "text-muted";
}
