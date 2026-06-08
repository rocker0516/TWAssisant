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

// 類股方向：偏多紅 / 偏空綠 / 中性灰（台股配色）
export function trendColor(trend: string | null | undefined): string {
  if (trend === "偏多") return "text-up";
  if (trend === "偏空") return "text-down";
  return "text-muted";
}

// 熱力圖磚塊底色：方向決定色相、強弱決定濃淡
export function sectorTileColor(trend: string | null | undefined, strength: number | null | undefined): string {
  const s = strength ?? 50;
  const a = (0.25 + (Math.abs(s - 50) / 50) * 0.6).toFixed(2);
  if (trend === "偏多") return `rgba(225,29,72,${a})`;
  if (trend === "偏空") return `rgba(22,163,74,${a})`;
  return "rgba(120,126,143,0.35)";
}

export function scoreColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "text-muted";
  if (v >= 80) return "text-up";
  if (v >= 70) return "text-amber-400";
  if (v >= 60) return "text-yellow-500";
  return "text-muted";
}
