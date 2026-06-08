import { CATEGORY_LABELS, scoreColor } from "../lib/format";

type Props = {
  total: number | null | undefined;
  subScores: Record<string, number> | null | undefined;
  size?: "sm" | "lg";
};

// 總分 + 視覺條；懸停跳出 5 大類細項（卡片預設乾淨）
export function ScoreDisplay({ total, subScores, size = "sm" }: Props) {
  const pct = Math.max(0, Math.min(100, total ?? 0));
  return (
    <div className="group relative inline-block">
      <div className="flex items-center gap-2">
        <span className={`font-semibold tabular-nums ${size === "lg" ? "text-2xl" : "text-lg"} ${scoreColor(total)}`}>
          {total?.toFixed(1) ?? "—"}
        </span>
        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-panel2">
          <div className="h-full rounded-full bg-sky-500" style={{ width: `${pct}%` }} />
        </div>
      </div>

      {subScores && (
        <div className="invisible absolute left-0 top-full z-20 mt-1 w-44 rounded-lg border border-edge bg-panel2 p-2 opacity-0 shadow-xl transition group-hover:visible group-hover:opacity-100">
          {Object.entries(subScores).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2 py-0.5 text-xs">
              <span className="w-14 shrink-0 text-muted">{CATEGORY_LABELS[k] ?? k}</span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg">
                <div className="h-full rounded-full bg-sky-400" style={{ width: `${Math.max(0, Math.min(100, v))}%` }} />
              </div>
              <span className="w-8 text-right tabular-nums text-gray-300">{v.toFixed(0)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
