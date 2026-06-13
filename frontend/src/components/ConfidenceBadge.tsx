import { confidenceMeta } from "../lib/format";

type Props = {
  confidence: number | null | undefined;
  coverage: number | null | undefined; // 0~1
  stability?: number | null | undefined; // 0.8~1
  size?: "sm" | "lg";
};

// 分數可信度燈號：圓點 + 標籤(+分數)，懸停說明完整度/共識/語意。
// 與 ScoreDisplay 並排，回答「這個分數可不可信」。
export function ConfidenceBadge({ confidence, coverage, stability, size = "sm" }: Props) {
  const m = confidenceMeta(confidence);
  const covPct = coverage === null || coverage === undefined ? null : Math.round(coverage * 100);
  const stabPct = stability === null || stability === undefined ? null : Math.round(stability * 100);
  const textCls = size === "lg" ? "text-sm" : "text-xs";
  return (
    <div className="group/conf relative inline-flex shrink-0 cursor-default items-center gap-1.5 whitespace-nowrap">
      <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${m.dot}`} />
      <span className={`font-medium ${textCls} ${m.text}`}>{m.label}</span>
      {confidence !== null && confidence !== undefined && (
        <span className={`tabular-nums text-muted ${textCls}`}>{confidence.toFixed(0)}</span>
      )}
      <div className="invisible absolute left-0 top-full z-30 mt-1 w-56 rounded-lg border border-edge bg-panel2 p-2.5 text-xs opacity-0 shadow-xl transition group-hover/conf:visible group-hover/conf:opacity-100">
        <p className="mb-1 font-medium text-gray-200">
          分數可信度 {confidence !== null && confidence !== undefined ? `${confidence.toFixed(0)}/100` : "—"}
        </p>
        <p className="text-muted">資料完整度 {covPct === null ? "—" : `${covPct}%`}</p>
        <p className="text-muted">各面向共識程度（一致越高越可信）</p>
        <p className="text-muted">近期穩定度 {stabPct === null ? "—" : `${stabPct}%`}（忽高忽低則打折）</p>
        <p className="mt-1.5 text-muted/80">完整度 × 共識度 × 穩定度。衡量分數可不可信，非看多程度。</p>
      </div>
    </div>
  );
}
