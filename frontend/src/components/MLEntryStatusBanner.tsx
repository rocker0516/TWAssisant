import type { MLEntryRun, MLEntryStack } from "../api/client";

// 今日健康條（spec §1）：徽章＋判讀句（後端 verdict）＋四顆 gate 燈號＋前瞻進度。
// 判讀語意全在後端；前端只 render。Research Shadow 徽章不得移除。

export const GATES: { key: string; label: string }[] = [
  { key: "data_quality", label: "資料" },
  { key: "feature_health", label: "特徵" },
  { key: "prediction_health", label: "預測" },
  { key: "recommendation", label: "推薦分布" },
];

function dotClass(g: Record<string, unknown> | undefined): string {
  if (!g || g.ok === undefined) return g ? "bg-emerald-400" : "bg-gray-600";   // recommendation 無 ok 欄：有資料即綠
  return g.ok ? "bg-emerald-400" : "bg-rose-400";
}

export function MLEntryStatusBanner({ stack, run, progress, onGateClick }: {
  stack?: MLEntryStack; run?: MLEntryRun | null;
  progress?: { matured_days: number; decide_at: number };
  onGateClick: (gate: string) => void;
}) {
  if (!stack) return null;
  const promoted = stack.model_status === "PROMOTED";
  const v = run?.verdict;
  const tone = v?.tone ?? "quiet";
  const frame = tone === "fail" ? "border-rose-800 bg-rose-950/30" : "border-gray-800 bg-gray-900/60";
  return (
    <div className={`rounded-lg border px-4 py-3 ${frame}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span title={promoted ? "已通過 promotion contract" : "未通過 promotion contract，非正式進場推薦"}
              className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                promoted ? "bg-emerald-900 text-emerald-200" : "bg-amber-900/70 text-amber-200"}`}>
          {promoted ? "Promoted" : stack.model_status_label}
        </span>
        <span className={`text-base font-bold ${tone === "fail" ? "text-rose-300" : "text-gray-100"}`}>
          {run ? `${run.signal_date.slice(5)} ${v?.headline ?? run.status}` : "尚無 run"}
        </span>
        {progress && (
          <span className="ml-auto text-xs tabular-nums text-gray-500">
            前瞻 {progress.matured_days}/{progress.decide_at}
          </span>
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-xs text-gray-400">
          {run ? v?.detail : "每日 21:30 MLEntryDailyStep 執行後產生"}
        </span>
        {run && (
          <span className="ml-auto flex items-center gap-1.5">
            {GATES.map((g) => {
              const gv = run.health?.[g.key] as Record<string, unknown> | undefined;
              return (
                <button key={g.key} onClick={() => onGateClick(g.key)} title={`${g.label}：${
                  gv?.ok === false ? "未通過" : gv ? "通過" : "未執行"}`}
                        className={`h-2.5 w-2.5 rounded-full ${dotClass(gv)}`} />
              );
            })}
            {tone === "fail" && (
              <button onClick={() => onGateClick("feature_health")} className="ml-2 text-xs text-sky-300 hover:underline">
                看原因 →
              </button>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
