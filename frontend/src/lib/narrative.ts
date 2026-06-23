import type { RecommendationItem } from "../api/client";

// 個股「行情白話」：把引擎已算好、平常收在「展開詳情」裡的各面向證據（details.evidence），
// 依會噴邏輯的重要性串成一句人話，直接攤在卡片上。最多取數段、避免變成資料堆。
//
// 順序＝會噴三支柱優先：趨勢方向（地基）→ 買點高低 → 會噴體質（波動），再帶資金面/型態。
// 長線軌目前無 evidence → 回 null，卡片自然不顯示。
const NARRATIVE_PRIORITY = [
  "trend", // 趨勢方向（站上哪條均線、是否多頭排列）
  "position", // 買點高低（區間位階、乖離、KD 過熱否）
  "consolidation", // 打底蓄勢（波動收斂、量縮橫向＝噴出前的彈簧）
  "volatility", // 會噴體質（日均波幅，核心預測子）
  "volume", // 量能放大/量縮
  "chip", // 法人/大戶籌碼動向
  "pattern", // 突破前高與否
  "momentum", // MACD / KD 指標
  "margin", // 融資券
];
const NARRATIVE_MAX = 4;

export function marketNarrative(item: RecommendationItem): string | null {
  const byCat = new Map<string, string>();
  for (const d of item.details ?? []) {
    if (d.evidence) byCat.set(d.category, d.evidence);
  }
  if (byCat.size === 0) return null;
  const parts: string[] = [];
  for (const cat of NARRATIVE_PRIORITY) {
    const ev = byCat.get(cat);
    if (ev) parts.push(ev);
    if (parts.length >= NARRATIVE_MAX) break;
  }
  return parts.length ? parts.join("；") : null;
}
