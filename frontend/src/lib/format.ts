// 評分 5 類中文標籤（懸停顯示用）
export const CATEGORY_LABELS: Record<string, string> = {
  // 波段軌
  trend: "趨勢",
  momentum: "動能",
  volume: "量能",
  chip: "籌碼",
  margin: "融資券",
  pattern: "型態",
  position: "位階",
  volatility: "波動度",
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

// 位階：由波段「位階分」推回相對位置。分數高＝買在相對低（回檔買點）。
export function positionMeta(posScore: number | null | undefined): { label: string; color: string } | null {
  if (posScore === null || posScore === undefined) return null;
  if (posScore >= 60) return { label: "相對低", color: "text-sky-400" };
  if (posScore >= 35) return { label: "中性", color: "text-muted" };
  return { label: "偏高", color: "text-amber-400" };
}

export function scoreColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "text-muted";
  if (v >= 80) return "text-up";
  if (v >= 70) return "text-amber-400";
  if (v >= 60) return "text-yellow-500";
  return "text-muted";
}

// 分數可信度（完整度×共識度）→ 燈號。刻意用藍/琥珀/灰，避開台股紅漲綠跌語意。
// 衡量「這個分數可不可信」，非看多程度。
export function confidenceMeta(v: number | null | undefined): {
  label: string;
  text: string;
  dot: string;
} {
  if (v === null || v === undefined) return { label: "資料不足", text: "text-muted", dot: "bg-gray-500" };
  if (v >= 75) return { label: "信心高", text: "text-sky-400", dot: "bg-sky-400" };
  if (v >= 50) return { label: "信心中", text: "text-amber-400", dot: "bg-amber-400" };
  return { label: "信心低", text: "text-zinc-400", dot: "bg-zinc-500" };
}
