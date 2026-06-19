import type { RecommendationItem } from "../api/client";
import { CATEGORY_LABELS, confidenceMeta, fmtNum, fmtPct, scoreColor } from "../lib/format";

// 展開詳情：各面向「分數 + 帶數字證據」+ 可信度白話 + 風險行。
// 把引擎已算出但平常只當條件判斷的數字（買超張數、量能倍數、突破價、乖離）攤開給使用者。
export function EvidencePanel({ item }: { item: RecommendationItem }) {
  const details = item.details ?? [];
  const cov = item.coverage === null || item.coverage === undefined ? null : Math.round(item.coverage * 100);
  const stab = item.stability === null || item.stability === undefined ? null : Math.round(item.stability * 100);
  const conf = confidenceMeta(item.confidence);

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-edge bg-bg/40 p-3 text-xs">
      {details.length > 0 && (
        <div className="flex flex-col gap-2">
          {details.map((d) => (
            <div key={d.category} className="flex flex-col gap-0.5">
              <div className="flex items-baseline gap-1.5">
                <span className="font-medium text-gray-300">{CATEGORY_LABELS[d.category] ?? d.category}</span>
                <span className={`tabular-nums font-semibold ${scoreColor(d.score)}`}>{d.score.toFixed(0)}</span>
              </div>
              {d.evidence && <span className="leading-relaxed text-muted">{d.evidence}</span>}
            </div>
          ))}
        </div>
      )}

      <div className="border-t border-edge pt-2 leading-relaxed text-muted">
        <span className="text-gray-400">可信度</span>　資料完整度 {cov === null ? "—" : `${cov}%`}、
        近期穩定度 {stab === null ? "—" : `${stab}%`} → 信心{conf.label.replace("信心", "")}
      </div>

      <div className="leading-relaxed text-muted">
        <span className="text-gray-400">風險</span>　跌破停損{" "}
        <span className="tabular-nums text-down">
          {fmtNum(item.stop_loss)}（{fmtPct(item.loss_pct)}）
        </span>{" "}
        即轉弱，宜減碼
      </div>
    </div>
  );
}
