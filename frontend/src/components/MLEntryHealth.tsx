import type { MLEntryConvergenceRow, MLEntryHealth as HealthT } from "../api/client";

// 模型體檢（spec §3，mockup convergence A）：前瞻進度 → Frozen vs Live 收斂對照 → 逐日成熟紀錄。
// 收斂判定由後端 convergence() 提供（描述性；20 成熟日只看不決策、60 為判斷點）。

function fmt(v: number | null, f: MLEntryConvergenceRow["fmt"]): string {
  if (v == null) return "—";
  if (f === "x") return `${v.toFixed(2)}×`;
  if (f === "ratio") return v.toFixed(2);
  if (f === "pct") return `${(v * 100).toFixed(2)}%`;
  if (f === "num3") return v.toFixed(3);
  return v % 1 === 0 ? String(v) : v.toFixed(1);
}

const VERDICT_CLS: Record<string, string> = {
  "CI 內": "text-emerald-300", "分布內": "text-emerald-300", "CI 外": "text-amber-300", "分布外": "text-amber-300",
  "參考": "text-gray-500", "累積中": "text-gray-500",
};

function Progress({ n, observe = 20, decide = 60 }: { n: number; observe?: number; decide?: number }) {
  const w = Math.min(n / decide, 1) * 100;
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
      <div className="flex justify-between text-sm"><b>前瞻觀察進度</b><span className="tabular-nums text-gray-400">mature_days {n} / {decide}</span></div>
      <div className="relative mt-2 h-2 rounded bg-gray-800">
        <div className="absolute inset-y-0 left-0 rounded bg-sky-400" style={{ width: `${w}%` }} />
        <div className="absolute -top-1 h-4 w-0.5 bg-gray-400" style={{ left: `${(observe / decide) * 100}%` }} />
      </div>
      <div className="relative mt-1 h-4 text-[11px] text-gray-500">
        <span className="absolute -translate-x-1/2" style={{ left: `${(observe / decide) * 100}%` }}>{observe}：首個觀察點（只看不決策）</span>
        <span className="absolute right-0">{decide}：判斷點</span>
      </div>
    </div>
  );
}

export function MLEntryHealthView({ health, isLoading }: { health: HealthT | undefined; isLoading: boolean }) {
  if (isLoading || !health) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  const fv = health.frozen_validation;
  return (
    <div className="space-y-6">
      <Progress n={health.live.matured_days} />

      <section>
        <div className="mb-2 flex flex-wrap items-baseline gap-3">
          <h2 className="text-base font-semibold">Frozen OOF vs 前瞻實績</h2>
          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${fv.promotion_result === "PASS" ? "bg-emerald-900 text-emerald-200" : "bg-rose-900 text-rose-200"}`}>
            Promotion = {fv.promotion_result}
          </span>
          <span className="text-xs text-gray-500">{fv.note}</span>
        </div>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr>
                <th className="px-3 py-2">指標</th><th className="px-3 py-2 text-right">Frozen OOF</th>
                <th className="px-3 py-2 text-right">Frozen 區間</th><th className="px-3 py-2 text-right">Live 20D</th>
                <th className="px-3 py-2 text-right">Live 60D</th><th className="px-3 py-2">收斂（20D／60D）</th>
              </tr>
            </thead>
            <tbody>
              {health.convergence.map((r) => (
                <tr key={r.key} className="border-t border-gray-800/60">
                  <td className="px-3 py-2">{r.label}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmt(r.frozen, r.fmt)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-xs text-gray-500">
                    {r.band ? `${r.band_kind === "ci" ? "CI" : "p5–p95"} [${fmt(r.band[0], r.fmt)}, ${fmt(r.band[1], r.fmt)}]` : "—"}
                  </td>
                  {(["20", "60"] as const).map((w) => (
                    <td key={w} className="px-3 py-2 text-right tabular-nums">
                      {r.verdict[w] === "累積中" && r.live[w] == null ? <span className="text-gray-500">累積中</span> : fmt(r.live[w], r.fmt)}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-xs">
                    <span className={VERDICT_CLS[r.verdict["20"]] ?? ""}>{r.verdict["20"]}</span>
                    <span className="text-gray-600"> ／ </span>
                    <span className={VERDICT_CLS[r.verdict["60"]] ?? ""}>{r.verdict["60"]}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-gray-800/60 px-3 py-1.5 text-[11px] text-gray-500">
            「收斂」為描述性比對；20 成熟日只看不決策，60 成熟日為判斷點（附錄 C）。
          </div>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">逐日成熟紀錄（Top-5）</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr>
                <th className="px-3 py-2">推薦日</th><th className="px-3 py-2 text-right">推薦數</th>
                <th className="px-3 py-2 text-right">TARGET</th><th className="px-3 py-2 text-right">STOP</th>
                <th className="px-3 py-2 text-right">TIMEOUT</th><th className="px-3 py-2 text-right">當日 Lift</th>
                <th className="px-3 py-2 text-right">當日 Net10</th>
              </tr>
            </thead>
            <tbody>
              {health.live.days.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-500">尚無成熟日；第一個 10D 成熟日約在首個 run 後 11 個交易日。</td></tr>
              )}
              {health.live.days.map((d) => (
                <tr key={d.signal_date} className="border-t border-gray-800/60 tabular-nums">
                  <td className="px-3 py-2 text-gray-400">{d.signal_date}</td>
                  <td className="px-3 py-2 text-right">{d.n_rec}</td>
                  <td className="px-3 py-2 text-right text-rose-300">{d.target}</td>
                  <td className="px-3 py-2 text-right text-emerald-300">{d.stop}</td>
                  <td className="px-3 py-2 text-right text-gray-400">{d.timeout}</td>
                  <td className="px-3 py-2 text-right">{d.lift == null ? "—" : `${d.lift.toFixed(2)}×`}</td>
                  <td className={`px-3 py-2 text-right ${d.net10 == null ? "" : d.net10 >= 0 ? "text-rose-300" : "text-emerald-300"}`}>
                    {d.net10 == null ? "—" : `${(d.net10 * 100).toFixed(2)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
