import type { MLEntryHealth as HealthT, MLEntryStatus } from "../api/client";

// 系統狀態（附錄 C；FRS §22–§23、§26）：版本、最近 run、三個健康 gate 明細、NO_TRADE 原因、監控門檻。

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-t border-gray-800/60 py-1.5 text-sm">
      <span className="text-gray-500">{k}</span>
      <span className="text-right font-mono text-xs text-gray-200">{v}</span>
    </div>
  );
}

function Gate({ name, g }: { name: string; g: Record<string, unknown> | undefined }) {
  if (!g) return null;
  const ok = g.ok as boolean | undefined;
  const detail = Object.entries(g).filter(([k]) => k !== "ok").map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`).join("・");
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
      <div className="flex items-center gap-2 text-sm">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${ok == null ? "bg-gray-500" : ok ? "bg-emerald-400" : "bg-rose-400"}`} />
        <span className="font-semibold">{name}</span>
        <span className="text-xs text-gray-500">{ok == null ? "" : ok ? "通過" : "未通過（fail-closed）"}</span>
      </div>
      <div className="mt-1 break-all text-xs text-gray-500">{detail}</div>
    </div>
  );
}

export function MLEntrySystemView({ status, health, isLoading }: { status: MLEntryStatus | undefined; health: HealthT | undefined; isLoading: boolean }) {
  if (isLoading || !status) return <div className="py-8 text-center text-gray-500">載入中…</div>;
  const s = status.stack;
  const r = status.last_run;
  const thr = health?.monitoring_thresholds;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
        <h2 className="mb-2 text-base font-semibold">Serving stack</h2>
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
      <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
        <h2 className="mb-2 text-base font-semibold">最近 run</h2>
        {r ? (
          <>
            <Row k="run_id" v={r.run_id} />
            <Row k="signal_date" v={r.signal_date} />
            <Row k="status" v={<span className={r.status === "OK" ? "text-emerald-300" : r.status === "SYSTEM_NO_TRADE" ? "text-rose-300" : ""}>{r.status}</span>} />
            <Row k="no_trade_reason" v={r.no_trade_reason ?? "—"} />
            <Row k="universe / qualified / rec" v={`${r.universe_count} / ${r.qualified_count} / ${r.recommendation_count}`} />
            <Row k="code_commit" v={r.code_commit} />
            {r.no_trade_text && <p className="mt-2 text-xs text-gray-400">{r.no_trade_text}</p>}
          </>
        ) : <p className="text-sm text-gray-500">尚無 run。</p>}
      </div>
      <div className="space-y-3 lg:col-span-2">
        <h2 className="text-base font-semibold">健康 gate（最近 run）</h2>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Gate name="Data quality" g={r?.health?.data_quality} />
          <Gate name="Feature health" g={r?.health?.feature_health} />
          <Gate name="Prediction health" g={r?.health?.prediction_health} />
          <Gate name="Recommendation drift" g={r?.health?.recommendation} />
        </div>
      </div>
      {thr && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4 lg:col-span-2">
          <h2 className="mb-2 text-base font-semibold">監控門檻（configs/mlentry/monitoring.yaml）</h2>
          <pre className="overflow-x-auto text-xs text-gray-400">{JSON.stringify(thr, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
