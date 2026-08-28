import type {
  Level1HorizonValidation,
  Level1LadderCell,
  Level1Performance,
  Level1QuantileBlock,
  Level1Validation,
} from "../api/client";

// 模型體檢籤：上=凍結驗證（walk-forward OOS，靜態），下=上線實績（Ledger，動態）。
// 誠實原則（設計 §1）：holdout 數字難看也照放；分位圖不美化——5D holdout 頂部
// 非單調是事實，圖下的規則文案（mono < 0.5）就是為它寫的。

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

const METRICS: {
  key: keyof Level1LadderCell;
  label: string;
  tip: string;
  fmt: (v: number) => string;
}[] = [
  { key: "mean_ic", label: "Rank IC",
    tip: "每日 Spearman(score, 實際 N 日報酬) 的平均，僅計入 E_{t,N}（U_t 中具有效未來報酬者）；0.05 以上即具實用排序力",
    fmt: (v) => v.toFixed(4) },
  { key: "icir", label: "ICIR", tip: "IC 均值／IC 波動——穩定度",
    fmt: (v) => v.toFixed(2) },
  { key: "monotonicity", label: "分位單調性",
    tip: "十分位序與實際報酬的相關；1＝完美單調", fmt: (v) => v.toFixed(2) },
  { key: "top20_excess_pct", label: "Top-20 超額",
    tip: "Top-20 日均報酬 − E_{t,N} 全體日均（百分點）",
    fmt: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}pp` },
  { key: "top20_day_win_rate", label: "日勝率",
    tip: "Top-20 贏過 E_{t,N} 均值的日子占比", fmt: (v) => `${(v * 100).toFixed(1)}%` },
  { key: "n_days", label: "評估天數", tip: "", fmt: (v) => String(v) },
];

function QuantileBars({ title, block }: {
  title: string; block: Level1QuantileBlock;
}) {
  const values = block.values;
  const absMax = Math.max(...values.map(Math.abs), 0.1);
  return (
    <div className="min-w-[260px] flex-1">
      <div className="mb-1 text-xs text-gray-400">{title}</div>
      <div className="flex items-end gap-1" style={{ height: 96 }}>
        {values.map((v, i) => (
          <div key={i} className="flex flex-1 flex-col items-center justify-end"
               title={`第 ${i + 1} 分位：${v.toFixed(3)}%`}>
            <div
              className={`w-full rounded-sm ${v >= 0 ? "bg-rose-400/70" : "bg-emerald-400/70"}`}
              style={{ height: Math.max(2, (Math.abs(v) / absMax) * 88) }}
            />
            <div className="mt-0.5 text-[9px] text-gray-600">{i + 1}</div>
          </div>
        ))}
      </div>
      {/* 解讀語意屬後端 artifact——前端不得自帶單調性門檻（設計 §4.1 A2） */}
      <div className="mt-1 text-[11px] text-gray-500">{block.interpretation.text}</div>
    </div>
  );
}

const LADDER_ROWS: { key: string; label: string; note: string }[] = [
  { key: "random", label: "Random", note: "對照組（應 ≈ 0）" },
  { key: "mom_ret20", label: "動能 ret20", note: "免訓練 baseline" },
  { key: "ridge_v2", label: "Ridge v2", note: "線性＋基本面＋regime" },
  { key: "lgbm", label: "LGBM（上線）", note: "非線性，現行版本" },
];

export function Level1HealthPanel({ validation, perf, horizon, k, modelVersion }: {
  validation: Level1Validation | undefined;
  perf: Level1Performance | undefined;
  horizon: number;
  k: number;
  modelVersion: string | null;
}) {
  const hv: Level1HorizonValidation | undefined =
    validation?.horizons?.[String(horizon)];
  const lgbm = hv?.ladder?.lgbm;
  const ridgeMono = hv?.ladder?.ridge_v2?.holdout?.monotonicity;

  return (
    <div className="space-y-6">
      {/* 區 A：凍結驗證 */}
      <section className="space-y-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">
              凍結驗證 — Walk-forward OOS（2022-01 起，dev 只准調參、holdout 只讀）
            </h2>
            {validation && (
              <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-400"
                    title="此區所有數字的出處 artifact（level1_results.json）">
                artifact：{validation.model_version}・{validation.generated_at}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500">
            驗證母體＝E_t,N（可交易 U_t ∩ 有效未來報酬，與榜單同一池）
            {lgbm && <>；holdout 平均 {Math.round(lgbm.holdout.evaluation_n_mean)} 檔／日</>}。
          </p>
        </div>

        {!hv && (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 text-sm text-gray-500">
            驗證報告載入中或不存在（level1_results.json）。
          </div>
        )}

        {lgbm && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {METRICS.map((m) => (
              <div key={m.key} title={m.tip}
                   className="rounded-lg border border-gray-800 bg-gray-900/60 p-2">
                <div className="text-[11px] text-gray-500">{m.label}</div>
                <div className="mt-1 text-xs text-gray-400">
                  dev <b className="text-gray-200">{m.fmt(lgbm.dev_oos[m.key])}</b>
                </div>
                <div className="text-xs text-gray-400">
                  holdout <b className="text-gray-200">{m.fmt(lgbm.holdout[m.key])}</b>
                </div>
              </div>
            ))}
          </div>
        )}

        {hv && (
          <div className="flex flex-wrap gap-4 rounded-lg border border-gray-800 bg-gray-900/60 p-3">
            <QuantileBars title={`十分位實際報酬（dev，${horizon}D，%）`}
                          block={hv.quantiles.dev_oos} />
            <QuantileBars title={`十分位實際報酬（holdout，${horizon}D，%）`}
                          block={hv.quantiles.holdout} />
          </div>
        )}

        {hv && (
          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full text-sm">
              <thead className="bg-gray-900 text-left text-xs text-gray-400">
                <tr>
                  <th className="px-3 py-2">模型</th>
                  <th className="px-3 py-2">說明</th>
                  <th className="px-3 py-2 text-right">dev IC</th>
                  <th className="px-3 py-2 text-right">holdout IC</th>
                  <th className="px-3 py-2 text-right">holdout Top20 超額</th>
                  <th className="px-3 py-2 text-right">holdout 勝率</th>
                </tr>
              </thead>
              <tbody>
                {LADDER_ROWS.map((row) => {
                  const cell = hv.ladder[row.key];
                  if (!cell) return null;
                  const hl = row.key === "lgbm";
                  return (
                    <tr key={row.key}
                        className={`border-t border-gray-800/60 ${hl ? "bg-sky-900/20" : ""}`}>
                      <td className={`px-3 py-2 ${hl ? "font-semibold text-sky-200" : ""}`}>
                        {row.label}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">{row.note}</td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {cell.dev_oos.mean_ic.toFixed(4)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {cell.holdout.mean_ic.toFixed(4)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {cell.holdout.top20_excess_pct >= 0 ? "+" : ""}
                        {cell.holdout.top20_excess_pct.toFixed(2)}pp
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">
                        {(cell.holdout.top20_day_win_rate * 100).toFixed(1)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {ridgeMono != null && (
              <div className="border-t border-gray-800/60 px-3 py-2 text-[11px] text-gray-500">
                Ridge 在 holdout 頂端反單調（mono {ridgeMono.toFixed(2)}）——非線性升級的理由。
              </div>
            )}
          </div>
        )}
      </section>

      {/* 區 B：上線實績 */}
      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold">
            上線實績 — Prediction Ledger（{modelVersion ?? "—"}，自 2026-08-27 起）
          </h2>
          <p className="text-xs text-gray-500">
            凍結驗證是歷史模擬；這裡是上線後逐日寫入、成熟回填、不可重寫的實際紀錄（§15）。
            Ledger 不是用來替代 OOS——它的角色是觀察 Production 是否開始偏離凍結驗證；
            兩者收斂是健康訊號，持續偏離是重驗警訊。
          </p>
        </div>

        {perf && perf.n_days > 0 ? (
          <>
            <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 text-sm">
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                <span>已成熟 <b>{perf.n_days}</b> 個預測日</span>
                <span>
                  Top{k} 平均超額{" "}
                  <b className={perf.mean_excess! >= 0 ? "text-rose-300" : "text-emerald-300"}>
                    {pct(perf.mean_excess, 2)}
                  </b>
                </span>
                <span>日勝率 <b>{pct(perf.day_win_rate)}</b></span>
                <span title="0.5 = 無預測力；越高代表 Top-K 實際排名越前">
                  平均實際百分位 <b>{perf.mean_actual_pct?.toFixed(3) ?? "—"}</b>
                </span>
              </div>
            </div>
            <div className="rounded-lg border border-gray-800">
              {perf.days.slice(-60).reverse().map((d) => (
                <div key={d.prediction_date}
                     className="flex items-center gap-3 border-t border-gray-800/40 px-3 py-1 text-xs first:border-t-0">
                  <span className="w-20 tabular-nums text-gray-500">{d.prediction_date}</span>
                  <div className="h-2 flex-1 rounded bg-gray-800/60">
                    <div
                      className={`h-2 rounded ${d.excess >= 0 ? "bg-rose-400/80" : "bg-emerald-400/80"}`}
                      style={{ width: `${Math.min(Math.abs(d.excess) / 0.03, 1) * 100}%` }}
                    />
                  </div>
                  <span className={`w-16 text-right tabular-nums ${
                    d.excess >= 0 ? "text-rose-300" : "text-emerald-300"}`}>
                    {pct(d.excess, 2)}
                  </span>
                  <span className="w-12 text-right tabular-nums text-gray-400"
                        title="Top-K 平均實際百分位">
                    {d.topk_mean_actual_pct.toFixed(3)}
                  </span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 text-sm text-gray-500">
            v2 於 2026-08-27 上線；{horizon}D 預測需 {horizon} 個交易日後成熟回填。
            首批實績出現前，請先看上方凍結驗證。
          </div>
        )}
      </section>
    </div>
  );
}
