import { useEffect, useRef } from "react";
import type { MLEntryDiagnostic, MLEntryDiagnostics, MLEntryHealth as HealthT, MLEntryStatus } from "../api/client";

// 系統狀態（spec §4）：本次 run 敘事 → 四個人讀 gate → 近 60 日 run 歷史 → 折疊（stack／門檻）。
// 欄位中文標籤只是標籤，判定來自後端 health gate。

type G = Record<string, unknown> | undefined;
const num = (v: unknown, d = 0) => (typeof v === "number" ? v.toFixed(d) : "—");

const GATE_VIEW: { key: string; title: string; rows: (g: NonNullable<G>) => [string, string][] }[] = [
  { key: "data_quality", title: "資料品質", rows: (g) => [
    ["Universe 檔數", `${num(g.universe_count)}（參考中位 ${num(g.ref_median)}）`],
    ["核心缺值欄位", Array.isArray(g.bad) && g.bad.length ? (g.bad as string[]).join("、") : "無"],
  ] },
  { key: "feature_health", title: "特徵健康", rows: (g) => [
    ["漂移特徵數", num(g.n_drifted)],
    ["PSI 最大值", num(g.psi_max, 3)],
    ["日級超出範圍", Array.isArray(g.out_of_range_day_level) && g.out_of_range_day_level.length ? (g.out_of_range_day_level as string[]).join("、") : "無"],
  ] },
  { key: "prediction_health", title: "預測健康", rows: (g) => [["判定說明", typeof g.why === "string" ? g.why : "正常"]] },
  { key: "recommendation", title: "推薦分布（只監控）", rows: (g) => [
    ["通過 Gate", `${num(g.qualified_count)}（近 60 日中位 ${num(g.qualified_median_60d, 1)}）`],
    ["近 60 日 NO_TRADE 率", typeof g.no_trade_rate_60d === "number" ? `${(g.no_trade_rate_60d * 100).toFixed(1)}%` : "—"],
  ] },
];

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-t border-gray-800/60 py-1.5 text-sm">
      <span className="text-gray-500">{k}</span><span className="text-right font-mono text-xs text-gray-200">{v}</span>
    </div>
  );
}

// 觀測診斷（Spec A）：只記錄、不影響出單；attention 只換顏色。數字與判定全來自後端 envelope。
const fmtPct = (v: unknown, d = 1) => (typeof v === "number" ? `${(v * 100).toFixed(d)}%` : "—");
const fmtNum = (v: unknown, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");

function diagTone(d: MLEntryDiagnostic | undefined): { dot: string; label: string } {
  if (!d || !d.evaluated) return { dot: "bg-gray-600", label: d?.error_type ? `未評估（${d.error_type}）` : d?.reason === "THRESHOLD_NOT_CONFIGURED" ? "未設門檻" : "未評估" };
  return d.attention ? { dot: "bg-amber-400", label: "提醒" } : { dot: "bg-emerald-700", label: "正常" };
}

const DIAG_VIEW: { key: keyof MLEntryDiagnostics; title: string; sub?: string; rows: (d: MLEntryDiagnostic) => [string, string][] }[] = [
  { key: "freshness", title: "資料新鮮度", rows: (d) => Object.entries((d.sources ?? {}) as Record<string, Record<string, unknown>>).map(([n, s]) =>
      [n, s.mode === "ingestion_watermark"
        ? `落後 ${fmtNum(s.lag_trading_days, 0)} 日（pipeline #${s.watermark_pipeline_run_id ?? "—"} ${s.watermark_business_date ?? "—"}）`
        : `落後 ${fmtNum(s.lag_days, 0)} 日（${s.max_date ?? "—"}）`]) },
  { key: "sanity", title: "結構檢查", rows: (d) => [
      ["有價格檔數", fmtNum(d.universe_present, 0)],
      ["結構性髒資料", `${fmtNum(d.hard_flag_count, 0)}（${fmtPct(d.hard_flag_ratio, 2)}）`],
      ["缺價格／歷史不足", `${fmtNum((d.elig as Record<string, unknown> | undefined)?.NO_PRICE, 0)}／${fmtNum((d.elig as Record<string, unknown> | undefined)?.HISTORY_TOO_SHORT, 0)}`]] },
  { key: "feature_shift", title: "特徵分布位移", sub: "7-point reference-quantile ECDF gap（非 KS）", rows: (d) => [
      ["評估特徵數", `${fmtNum(d.n_evaluated, 0)}（略過 ${Array.isArray(d.skipped) ? d.skipped.length : "—"}）`],
      ["|mean z| 超門檻", fmtNum(d.n_mean_z_gt, 0)],
      ["ECDF gap 超門檻", fmtNum(d.n_gap_gt, 0)],
      ["最大 gap", (() => { const t = (d.top as { name: string; quantile_cdf_gap_7pt: number | null }[] | undefined)?.[0];
        return t ? `${t.name} ${fmtNum(t.quantile_cdf_gap_7pt, 3)}` : "—"; })()],
      ["模式來源", String(d.monitor_mode_source ?? "—")]] },
  { key: "recommendation", title: "推薦分布", sub: "市值＝run 當下股本×收盤，非 PIT", rows: (d) => {
      const ms = (d.sector as Record<string, unknown> | undefined)?.max_sector as Record<string, unknown> | null | undefined;
      const mc = d.mcap_tercile as Record<string, unknown> | undefined; const lq = d.liquidity_tercile as Record<string, unknown> | undefined;
      const sq = d.score_q as Record<string, unknown> | null | undefined;
      return [
        ["通過 Gate／推薦", `${fmtNum(d.qualified_count, 0)}／${fmtNum(d.recommendation_count, 0)}`],
        ["Score p10／50／90", sq ? `${fmtNum(sq.p10, 3)}／${fmtNum(sq.p50, 3)}／${fmtNum(sq.p90, 3)}` : "—"],
        ["最大類股", ms ? `#${ms.sector_id} 佔 ${fmtPct(ms.recommendation_share, 0)}（universe ${fmtPct(ms.universe_share, 1)}，${fmtNum(ms.overweight, 1)}×）` : "—"],
        ["市值 低／中／高", mc ? `${fmtNum(mc.low, 0)}／${fmtNum(mc.mid, 0)}／${fmtNum(mc.high, 0)}（缺 ${fmtNum(mc.missing_count, 0)}）` : "—"],
        ["流動性 低／中／高", lq ? `${fmtNum(lq.low, 0)}／${fmtNum(lq.mid, 0)}／${fmtNum(lq.high, 0)}` : "—"]];
    } },
];

export function MLEntrySystemView({ status, health, isLoading, focusGate }: {
  status: MLEntryStatus | undefined; health: HealthT | undefined; isLoading: boolean; focusGate?: string | null;
}) {
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => {
    if (focusGate) refs.current[focusGate]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusGate]);
  if (isLoading || !status) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  const s = status.stack; const r = status.last_run; const v = r?.verdict;
  const fh = (r?.health?.feature_health ?? {}) as Record<string, unknown>;
  const psi = (fh.drifted_psi ?? {}) as Record<string, { psi: number; thr: number }>;
  const drifted = (fh.drifted ?? []) as string[];
  return (
    <div className="space-y-5">
      <section className={`rounded-lg border p-4 ${v?.tone === "fail" ? "border-rose-800 bg-rose-950/30" : "border-gray-800 bg-gray-900/60"}`}>
        <h2 className="text-base font-semibold">{r ? `${r.signal_date}：${v?.headline ?? r.status}` : "尚無 run"}</h2>
        {r && <p className="mt-1 text-sm text-gray-300">{v?.detail}</p>}
        {r && <p className="mt-1 text-xs text-gray-500">run_id {r.run_id}・Universe {r.universe_count}・通過 Gate {r.qualified_count}・推薦 {r.recommendation_count}・commit {r.code_commit}</p>}
        {r?.audit && (
          <p className="mt-1 font-mono text-[11px] text-gray-500">
            snapshot {r.audit.data_snapshot_id ?? "—"}・stack {r.audit.serving_stack_hash ?? "—"}・commit {r.audit.code_commit ?? "—"}
            {r.audit.as_of_mismatch && <span className="ml-2 text-rose-300">特徵快照日 {r.audit.feature_snapshot_as_of} ≠ 請求日 {r.audit.requested_as_of}</span>}
          </p>
        )}
        {drifted.length > 0 && (
          <table className="mt-3 w-full max-w-md text-xs">
            <thead className="text-left text-gray-500"><tr><th className="py-1">漂移特徵</th><th className="py-1 text-right">PSI</th><th className="py-1 text-right">門檻</th></tr></thead>
            <tbody>
              {drifted.map((n) => (
                <tr key={n} className="border-t border-gray-800/60 font-mono">
                  <td className="py-1">{n}</td>
                  <td className="py-1 text-right">{psi[n] ? psi[n].psi.toFixed(3) : "—"}</td>
                  <td className="py-1 text-right text-gray-500">{psi[n] ? psi[n].thr.toFixed(3) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {GATE_VIEW.map((gv) => {
          const g = r?.health?.[gv.key] as G;
          const ok = g?.ok as boolean | undefined;
          return (
            <div key={gv.key} ref={(el) => { refs.current[gv.key] = el; }}
                 className={`rounded-lg border bg-gray-900/60 p-3 ${focusGate === gv.key ? "border-sky-500" : "border-gray-800"}`}>
              <div className="flex items-center gap-2 text-sm">
                <span className={`inline-block h-2.5 w-2.5 rounded-full ${!g ? "bg-gray-600" : ok === false ? "bg-rose-400" : "bg-emerald-400"}`} />
                <span className="font-semibold">{gv.title}</span>
                <span className="text-xs text-gray-500">{!g ? "未執行" : ok === false ? "未通過（fail-closed）" : "通過"}</span>
              </div>
              {g && <div className="mt-1">{gv.rows(g).map(([k, val]) => <Row key={k} k={k} v={val} />)}</div>}
            </div>
          );
        })}
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">觀測診斷 <span className="text-xs font-normal text-gray-500">只記錄，不影響出單</span></h2>
        {!r?.diagnostics ? (
          <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3 text-sm text-gray-500">此 run 無診斷紀錄（舊版 run）。</div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {DIAG_VIEW.map((dv) => {
              const d = r.diagnostics?.[dv.key]; const tone = diagTone(d);
              return (
                <div key={dv.key} className={`rounded-lg border bg-gray-900/60 p-3 ${d?.attention ? "border-amber-700/70" : "border-gray-800"}`}>
                  <div className="flex items-center gap-2 text-sm">
                    <span className={`inline-block h-2.5 w-2.5 rounded-full ${tone.dot}`} />
                    <span className="font-semibold">{dv.title}</span>
                    <span className={`text-xs ${d?.attention ? "text-amber-300" : "text-gray-500"}`}>{tone.label}</span>
                  </div>
                  {dv.sub && <div className="mt-0.5 text-[11px] text-gray-500">{dv.sub}</div>}
                  {d && <div className="mt-1">{dv.rows(d).map(([k, val]) => <Row key={k} k={k} v={val} />)}</div>}
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-base font-semibold">近 60 日 run 歷史</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-left text-xs text-gray-400">
              <tr><th className="px-3 py-2">日期</th><th className="px-3 py-2">狀態</th><th className="px-3 py-2">原因</th>
                  <th className="px-3 py-2 text-right">Universe</th><th className="px-3 py-2 text-right">通過 Gate</th>
                  <th className="px-3 py-2 text-right">推薦</th><th className="px-3 py-2 text-right">漂移特徵數</th><th className="px-3 py-2 text-right">提醒數</th></tr>
            </thead>
            <tbody>
              {(health?.history ?? []).map((h) => (
                <tr key={h.signal_date} className="border-t border-gray-800/60 tabular-nums">
                  <td className="px-3 py-2">{h.signal_date}</td>
                  <td className={`px-3 py-2 ${h.status === "OK" ? "text-gray-200" : h.status === "SYSTEM_NO_TRADE" ? "text-rose-300" : "text-gray-400"}`}>{h.status}</td>
                  <td className="px-3 py-2 text-gray-400">{h.no_trade_reason ?? ""}</td>
                  <td className="px-3 py-2 text-right">{h.universe_count}</td>
                  <td className="px-3 py-2 text-right">{h.qualified_count}</td>
                  <td className="px-3 py-2 text-right">{h.recommendation_count}</td>
                  <td className="px-3 py-2 text-right text-gray-400">{h.n_drifted ?? "—"}</td>
                  <td className={`px-3 py-2 text-right ${h.attention_count ? "text-amber-300" : "text-gray-400"}`}>{h.attention_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <details className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
        <summary className="cursor-pointer text-sm font-semibold">Serving stack 版本</summary>
        <div className="mt-2">
          <Row k="model_version" v={s.model_version ?? "—"} />
          <Row k="calibration_version" v={s.calibration_version ?? "—"} />
          <Row k="policy_version" v={`${s.policy_version ?? "—"}（${s.policy_name ?? "—"}）`} />
          <Row k="feature_version" v={s.feature_version ?? "—"} />
          <Row k="label_version" v={s.label_version ?? "—"} />
          <Row k="dataset_version" v={s.dataset_version ?? "—"} />
          <Row k="trained_through" v={s.trained_through ?? "—"} />
          <Row k="model_status" v={s.model_status} />
          <Row k="deployment_mode" v={s.deployment_mode} />
          <Row k="promotion_eligible" v={String(s.promotion_eligible)} />
          <Row k="final_holdout_access" v={String(status.final_holdout_access)} />
        </div>
      </details>
      {health?.monitoring_thresholds && (
        <details className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
          <summary className="cursor-pointer text-sm font-semibold">監控門檻（configs/mlentry/monitoring.yaml）</summary>
          <pre className="mt-2 overflow-x-auto text-xs text-gray-400">{JSON.stringify(health.monitoring_thresholds, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
