import type { RecommendationItem } from "../api/client";

// 個股「行情白話」：把引擎已算好、平常收在「展開詳情」裡的各面向證據（details.evidence），
// 依會噴邏輯的重要性串成人話，直接攤在卡片上。最多取數段、避免變成資料堆。
//
// 順序＝會噴三支柱優先：趨勢方向（地基）→ 買點高低 → 會噴體質（波動），再帶資金面/型態。
const NARRATIVE_PRIORITY = [
  "trend", // 趨勢方向（站上哪條均線、是否多頭排列）
  "position", // 買點高低（區間位階、乖離、KD 過熱否）
  "entry_timing", // 進場時機（法人剛翻買、外資投信同步、投信連買；無訊號時 evidence=None 自動略過）
  "consolidation", // 打底蓄勢（波動收斂、量縮橫向＝噴出前的彈簧）
  "volatility", // 會噴體質（日均波幅，核心預測子）
  "volume", // 量能放大/量縮
  "chip", // 法人/大戶籌碼動向
  "pattern", // 突破前高與否
  "momentum", // MACD / KD 指標
  "margin", // 融資券
];
const NARRATIVE_MAX = 4;

export type NarrativeTone = "neg" | "pos" | "neutral";
export type NarrativeSegment = { category: string; text: string; tone: NarrativeTone };

// 把 evidence 字串判正/負/中性。
// 規則：先匹配「未X」這類否定組合（避免被裸 keyword 誤判），再算單詞分數。
// 正向：站上/上揚/買超/放大/收斂/轉買/單邊買超/低位/低接/黃金交叉/翻多/未過熱
// 負向：未站上/下方/賣超/量縮/未收斂/高位/過熱/死亡交叉/未翻多/減 N 個百分點/融資...增
function toneOf(text: string): NarrativeTone {
  let s = text;
  let score = 0;
  // 先處理「未X」否定詞——抽走後再做單詞匹配，避免「未過熱」被「過熱」、「未收斂」被「收斂」誤判
  const negationsPositive = [/未過熱/g]; // 「未過熱」＝正面
  const negationsNegative = [/未站上/g, /未收斂/g, /未翻多/g]; // 「未X」＝負面
  for (const re of negationsPositive) {
    const n = (s.match(re) ?? []).length;
    if (n) {
      score += n;
      s = s.replace(re, "");
    }
  }
  for (const re of negationsNegative) {
    const n = (s.match(re) ?? []).length;
    if (n) {
      score -= n;
      s = s.replace(re, "");
    }
  }
  // 大戶減 N 個百分點＝負面（用「減」+ 數字避免誤抓「減碼」「減持」單字）
  const dahuMinus = (s.match(/減\s*\d/g) ?? []).length;
  score -= dahuMinus;
  // 融資餘額增 = 散戶湧入（負面訊號），但避開「融資餘額增 0%」這類弱訊號
  if (/融資餘額.*?增\s*([1-9]\d{1,2}|\d{4,})/.test(s)) score -= 1;
  // 消息面：利空 N 則（N≥1）＝負面；展望消息 N 則（N≥1）＝正面
  if (/利空\s*[1-9]/.test(s)) score -= 1;
  if (/展望消息\s*[1-9]/.test(s)) score += 1;
  // 單詞表
  const posWords = /買超|上揚|站上|放大|收斂|轉買|單邊買超|低位|低接|黃金交叉|翻多|遞增|創近一年新高|年增為正|低基期/g;
  const negWords = /賣超|下方|量縮|高位|過熱|死亡交叉|中後段/g;
  score += (s.match(posWords) ?? []).length;
  score -= (s.match(negWords) ?? []).length;
  if (score >= 1) return "pos";
  if (score <= -1) return "neg";
  return "neutral";
}

export function marketSegments(item: RecommendationItem): NarrativeSegment[] {
  const byCat = new Map<string, string>();
  for (const d of item.details ?? []) {
    if (d.evidence) byCat.set(d.category, d.evidence);
  }
  if (byCat.size === 0) return [];
  // 全部依優先級展開
  const all: NarrativeSegment[] = [];
  for (const cat of NARRATIVE_PRIORITY) {
    const text = byCat.get(cat);
    if (!text) continue;
    all.push({ category: cat, text, tone: toneOf(text) });
  }
  // 風險訊號優先：所有負面段全保留（cap NEG_CAP 段避免單卡爆量），其餘段補滿到 NARRATIVE_MAX。
  // 這樣即使引擎已把 chip 打 0、margin 標融資暴增，使用者也一定看得到，不會被 trend/position
  // 等正面段壓掉（這是三圓 4416 的關鍵 bug：技術面分數高、籌碼面塌但被隱藏）。
  const NEG_CAP = 3;
  const negs = all.filter((s) => s.tone === "neg").slice(0, NEG_CAP);
  const slotsForOthers = Math.max(0, NARRATIVE_MAX - negs.length);
  const others = all.filter((s) => s.tone !== "neg").slice(0, slotsForOthers);
  const picked = new Set([...negs, ...others].map((s) => s.category));
  // 維持原優先級順序輸出，閱讀感跟舊版一致（趨勢→位階→盤整→...）
  return all.filter((s) => picked.has(s.category));
}

// 是否有任何負面段——卡片用來決定「行情」框邊是否要轉警示色
export function hasNegative(segments: NarrativeSegment[]): boolean {
  return segments.some((s) => s.tone === "neg");
}
