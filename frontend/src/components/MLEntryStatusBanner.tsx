import type { MLEntryRun, MLEntryStack } from "../api/client";

// 固定橫幅（附錄 C 定位）：Model status / Promotion 一路從 registry 帶到頁面，
// 避免 UI 看起來像已驗證的正式交易模型。文案由後端 stack 欄位決定，前端只 render。
export function MLEntryStatusBanner({ stack, run }: { stack: MLEntryStack | undefined; run: MLEntryRun | null | undefined }) {
  if (!stack) return null;
  const shadow = stack.model_status !== "PROMOTED";
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${
      shadow ? "border-amber-700/60 bg-amber-950/30" : "border-emerald-700/60 bg-emerald-950/30"}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-semibold">
          Model status：<span className={shadow ? "text-amber-300" : "text-emerald-300"}>{stack.model_status_label}</span>
        </span>
        <span>
          Promotion：<span className={stack.promotion_eligible ? "text-emerald-300" : "text-rose-300"}>
            {stack.promotion_eligible ? "Qualified" : "Not Qualified"}
          </span>
        </span>
        <span className="text-gray-400">Deployment：{stack.deployment_mode}</span>
        {run && (
          <span className="text-gray-400">
            最近 run：{run.signal_date}・{run.status}
            {run.no_trade_reason ? `・${run.no_trade_reason}` : ""}
          </span>
        )}
      </div>
      {shadow && (
        <p className="mt-1 text-xs text-amber-200/80">
          本頁為 <b>Research Recommendation</b>：policy 已凍結、模型每日照規則出訊號並回填結果，用來累積前瞻證據；
          未通過 promotion contract 前不是正式進場推薦。
        </p>
      )}
    </div>
  );
}
