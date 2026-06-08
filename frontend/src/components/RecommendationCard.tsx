import { Link } from "react-router-dom";
import type { RecommendationItem } from "../api/client";
import { changeColor, fmtNum, fmtPct, TRACK_LABELS } from "../lib/format";
import { ReasonChips } from "./ReasonChips";
import { ScoreDisplay } from "./ScoreDisplay";

export function RecommendationCard({ item }: { item: RecommendationItem }) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-edge bg-panel p-4 transition hover:border-sky-700">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded bg-sky-900/60 px-1.5 py-0.5 text-xs font-medium text-sky-300">
            {TRACK_LABELS[item.track]}
          </span>
          <Link to={`/stocks/${item.stock_id}`} className="hover:underline">
            <span className="font-semibold">{item.name}</span>
            <span className="ml-1.5 text-sm text-muted">{item.stock_id}</span>
          </Link>
        </div>
        <div className="text-right">
          <div className="font-semibold tabular-nums">{fmtNum(item.close)}</div>
          <div className={`text-xs tabular-nums ${changeColor(item.change_pct)}`}>{fmtPct(item.change_pct)}</div>
        </div>
      </div>

      <ScoreDisplay total={item.total_score} subScores={item.sub_scores} />

      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <div className="text-xs text-muted">買進區間</div>
          <div className="tabular-nums">
            {fmtNum(item.buy_low)} ~ {fmtNum(item.buy_high)}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">參考停損</div>
          <div className="tabular-nums">
            {fmtNum(item.stop_loss)}{" "}
            <span className="text-down">({fmtPct(item.loss_pct)})</span>
          </div>
        </div>
      </div>

      <ReasonChips reasons={item.reasons} />

      <div className="flex items-center justify-between border-t border-edge pt-2 text-xs text-muted">
        <span>{item.sector_name ?? "—"}</span>
        <Link to={`/stocks/${item.stock_id}`} className="text-sky-400 hover:underline">
          詳情 →
        </Link>
      </div>
    </div>
  );
}
