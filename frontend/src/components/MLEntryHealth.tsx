import type { MLEntryCheck, MLEntryHealth as HealthT, MLEntryLiveWindow } from "../api/client";

// 模型體檢（附錄 C）：Frozen Validation（B9 policy_baseline_v1 dev OOF）與 Live Forward Performance。
// 門檻與判定來自後端 promotion contract；前端只 render。

const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const num = (v: unknown, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");
const ci = (v: unknown, fmt: (x: number) => string) =>
  Array.isArray(v) && v.length === 2 ? `[${fmt(v[0] as number)}, ${fmt(v[1] as number)}]` : "";

function Stat({ label, value, sub, ok }: { label: string; value: string; sub?: string; ok?: boolean | null }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${ok == null ? "" : ok ? "text-emerald-300" : "text-rose-300"}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

function LiveRow({ w, item }: { w: string; item: MLEntryLiveWindow }) {
  return (
    <tr className="border-t border-gray-800/60">
      <td className="px-3 py-2">{w}D</td>
      <td className="px-3 py-2 text-right tabular-nums text-gray-400">{item.days}</td>
      <td className="px-3 py-2 text-right tabular-nums text-gray-400">{item.n_rec}</td>
      <td className="px-3 py-2 text-right tabular-nums">{item.target_lift != null ? `${item.target_lift.toFixed(2)}×` : "—"}</td>
      <td className="px-3 py-2 text-right tabular-nums">{item.stop_ratio != null ? item.stop_ratio.toFixed(2) : "—"}</td>
      <td className="px-3 py-2 text-right tabular-nums">{item.mean_net10 != null ? pct(item.mean_net10, 2) : "—"}</td>
      <td className="px-3 py-2 text-right tabular-nums text-gray-400">{item.ece_target_10d.toFixed(3)}</td>
    </tr>
  );
}

export function MLEntryHealthView({ health, isLoading }: { health: HealthT | undefined; isLoading: boolean }) {
  if (isLoading || !health) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  const fv = health.frozen_validation;
  const m = fv.metrics;
  const chk = fv.promotion_check;
  const c = (k: string) => (chk[k] as MLEntryCheck | undefined);
  const liveWindows = Object.entries(health.live.windows);
  return (
    <div className="space-y-6">
      <section>
        <div className="mb-2 flex flex-wrap items-baseline gap-3">
          <h2 className="text-base font-semibold">Frozen Validation</h2>
          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${fv.promotion_result === "PASS" ? "bg-emerald-900 text-emerald-200" : "bg-rose-900 text-rose-200"}`}>
            Promotion = {fv.promotion_result}
          </span>
          <span className="text-xs text-gray-500">{fv.note}</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="TargetLift@5" value={`${num(m.target_lift_at_5)}×`} ok={c("target_lift_at_5")?.pass}
                sub={`門檻 ≥ ${fv.thresholds.target_lift_at_5_min}・CI ${ci(m.ci_target_lift, (x) => x.toFixed(2))}`} />
          <Stat label="StopRatio@5" value={num(m.stop_ratio_at_5)} ok={c("stop_ratio_at_5")?.pass}
                sub={`門檻 ≤ ${fv.thresholds.stop_ratio_at_5_max}・CI ${ci(m.ci_stop_ratio, (x) => x.toFixed(2))}`} />
          <Stat label="Coverage" value={pct(m.coverage as number)} ok={c("coverage")?.pass}
                sub={`門檻 ≥ ${fv.thresholds.coverage_min ?? "—"}`} />
          <Stat label="Worst fold Lift@5" value={num(m.worst_fold_lift_at_5)} ok={c("worst_fold_lift_at_5")?.pass}
                sub={`門檻 > ${fv.thresholds.worst_fold_lift_at_5_min}`} />
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <Stat label="每筆淨報酬 10D（扣 0.585%）" value={pct(m.mean_net10 as number, 2)}
                sub={`區塊 bootstrap CI ${ci(m.ci_net10, (x) => `${(x * 100).toFixed(2)}%`)}`} />
          <Stat label="市場基率（同期）" value={`${pct(m.market_target_rate as number)} / ${pct(m.market_stop_rate as number)}`} sub="Target / Stop，10D" />
          <Stat label="Dev 期間" value={`${m.dev_folds ?? "—"} folds`}
                sub={Array.isArray(m.dev_period) ? `${m.dev_period[0]} ～ ${m.dev_period[1]}` : ""} />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">Live Forward Performance</h2>
        <p className="mb-2 text-xs text-gray-500">
          前瞻資料：policy 凍結後每日照規則出訊號，成熟（10 個交易日）後回填。已成熟 {health.live.matured_days} 個交易日。
          與 Frozen Validation 同定義；累積 20 / 60 個成熟日後才有意義。
        </p>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr><th className="px-3 py-2">視窗</th><th className="px-3 py-2 text-right">成熟日</th><th className="px-3 py-2 text-right">推薦筆數</th>
                  <th className="px-3 py-2 text-right">Lift@5</th><th className="px-3 py-2 text-right">StopRatio@5</th>
                  <th className="px-3 py-2 text-right">淨報酬 10D</th><th className="px-3 py-2 text-right">ECE</th></tr>
            </thead>
            <tbody>
              {liveWindows.length === 0 && <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-500">尚無成熟資料。</td></tr>}
              {liveWindows.map(([w, item]) => <LiveRow key={w} w={w} item={item} />)}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">近 60 日 run 歷史</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr><th className="px-3 py-2">日期</th><th className="px-3 py-2">狀態</th><th className="px-3 py-2">原因</th>
                  <th className="px-3 py-2 text-right">Universe</th><th className="px-3 py-2 text-right">通過 Gate</th><th className="px-3 py-2 text-right">推薦</th></tr>
            </thead>
            <tbody>
              {health.history.map((h) => (
                <tr key={h.signal_date} className="border-t border-gray-800/60">
                  <td className="px-3 py-2">{h.signal_date}</td>
                  <td className={`px-3 py-2 ${h.status === "OK" ? "text-emerald-300" : h.status === "SYSTEM_NO_TRADE" ? "text-rose-300" : "text-gray-300"}`}>{h.status}</td>
                  <td className="px-3 py-2 text-gray-400">{h.no_trade_reason ?? ""}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{h.universe_count}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{h.qualified_count}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{h.recommendation_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
