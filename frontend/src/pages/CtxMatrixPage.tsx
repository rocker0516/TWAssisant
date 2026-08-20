import { useMemo, useState } from "react";
import { useCtxMatrix, type CtxCell, type CtxChainAudit } from "../api/client";
import { Modal } from "../components/Modal";
import { fmtNum, fmtPct } from "../lib/format";

// 家族分頁籤（挖掘池目前只出 5 族，event 族先留位、無資料時顯示空狀態）
const FAMILY_TABS: { key: string; label: string }[] = [
  { key: "chip", label: "籌碼" },
  { key: "momentum", label: "動能" },
  { key: "volume", label: "量能" },
  { key: "fundamental", label: "基本面" },
  { key: "news", label: "消息" },
  { key: "event", label: "事件" },
];

// 情境切換
const CONTEXT_TABS: { key: string; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "hold", label: "多頭" },
  { key: "defense", label: "空頭" },
  { key: "resonance_strong", label: "共振強" },
  { key: "resonance_weak", label: "共振弱" },
];

// 核心鏈中文名（backend/data/core_chains.json 凍結產物，只有 6 條，直接寫死）
const CORE_CHAIN_LABELS: Record<string, string> = {
  SEMI: "半導體", AISRV: "AI伺服器", APPLE: "蘋概", SHIP: "航運", POWER: "重電/綠能", FIN: "金控",
};
const CORE_CHAIN_ORDER = ["SEMI", "AISRV", "APPLE", "SHIP", "POWER", "FIN"];

const TIER_DOT: Record<string, { icon: string; cls: string; label: string }> = {
  pass: { icon: "●", cls: "text-sky-300", label: "pass：跨窗＋holdout 都撐得住" },
  watch: { icon: "○", cls: "text-amber-300", label: "watch：t 值過門檻但穩定度不足，先觀察" },
  insufficient: { icon: "▨", cls: "text-gray-500", label: "insufficient：樣本不足，無法判定" },
};

function signalShortLabel(signal: string): string {
  const i = signal.indexOf("__");
  return i === -1 ? signal : signal.slice(i + 2);
}

// 格子底色：依 routing_delta 濃淡（台股紅漲綠跌——正值紅系代表比全市場基準更強）
function cellBg(delta: number, tier: string): string {
  if (tier === "insufficient") return "";
  const a = Math.min(0.7, 0.12 + (Math.abs(delta) / 15) * 0.55).toFixed(2);
  if (Math.abs(delta) < 0.05) return "rgba(120,126,143,0.12)";
  return delta > 0 ? `rgba(225,29,72,${a})` : `rgba(22,163,74,${a})`;
}

const INSUFFICIENT_STRIPE =
  "repeating-linear-gradient(45deg, rgba(107,114,128,0.28) 0px, rgba(107,114,128,0.28) 3px, transparent 3px, transparent 7px)";

export default function CtxMatrixPage() {
  const { data, isLoading, isError, error } = useCtxMatrix();
  const [family, setFamily] = useState("chip");
  const [ctx, setCtx] = useState("all");
  const [selected, setSelected] = useState<{ group: string; groupLabel: string; signal: string } | null>(null);

  const cellIndex = useMemo(() => {
    const m = new Map<string, CtxCell>();
    if (!data) return m;
    for (const c of data.cells) m.set(`${c.group}::${c.context}::${c.signal}`, c);
    return m;
  }, [data]);

  const chainAuditByGroup = useMemo(() => {
    const m = new Map<string, CtxChainAudit>();
    if (!data) return m;
    for (const a of data.chain_audit) m.set(a.chain_id, a);
    return m;
  }, [data]);

  const signals = useMemo(() => {
    if (!data) return [];
    return data.signals
      .filter((s) => s.family === family)
      .map((s) => s.signal)
      .sort();
  }, [data, family]);

  const { coreRows, tpexRows, baselineGroup } = useMemo(() => {
    if (!data) return { coreRows: [] as { id: string; label: string }[], tpexRows: [] as { id: string; label: string }[], baselineGroup: null as { id: string; label: string } | null };
    const core = data.groups.filter((g) => g.group_kind === "core_chain");
    const coreSorted = [...core].sort(
      (a, b) => CORE_CHAIN_ORDER.indexOf(a.group) - CORE_CHAIN_ORDER.indexOf(b.group),
    );
    const tpex = data.groups
      .filter((g) => g.group_kind === "tpex_chain")
      .sort((a, b) => a.group.localeCompare(b.group, "zh-Hant"));
    const market = data.groups.find((g) => g.group_kind === "all_market") ?? null;
    return {
      coreRows: coreSorted.map((g) => ({ id: g.group, label: CORE_CHAIN_LABELS[g.group] ?? g.group })),
      tpexRows: tpex.map((g) => ({ id: g.group, label: g.group })),
      baselineGroup: market ? { id: market.group, label: "全市場（baseline）" } : null,
    };
  }, [data]);

  if (isLoading) {
    return <div className="w-full px-6 py-6 text-sm text-muted">載入情境矩陣中…</div>;
  }
  if (isError || !data) {
    const msg = error instanceof Error ? error.message : "";
    return (
      <div className="w-full px-6 py-6 text-sm text-rose-400">
        情境矩陣載入失敗{msg && `：${msg}`}
        {msg.includes("404") && "（ctx_matrix.json 尚未產生，情境路由軌未啟用）"}
      </div>
    );
  }

  const rows = [...coreRows, ...tpexRows, ...(baselineGroup ? [baselineGroup] : [])];
  const baseline = baselineGroup ? baselineGroup.id : null;

  const openCell = (groupId: string, groupLabel: string, signal: string) =>
    setSelected({ group: groupId, groupLabel, signal });

  const selectedCell = selected ? cellIndex.get(`${selected.group}::${ctx}::${selected.signal}`) : null;
  const selectedBaseline = selected && baseline ? cellIndex.get(`${baseline}::${ctx}::${selected.signal}`) : null;

  return (
    <div className="w-full px-6 py-6">
      <h1 className="mb-1 text-xl font-bold">情境矩陣</h1>
      <p className="mb-4 text-xs text-muted">
        訊號在「類股群 × 大盤情境」下的路由效力熱力圖——格色依 routing_delta（相對全市場基準的差距），純觀察層、不影響排序。
      </p>

      {/* 頂部資訊列 */}
      <div className="mb-5 flex flex-wrap items-center gap-2 rounded-lg border border-edge bg-panel p-3 text-xs">
        <span className="rounded bg-sky-900/50 px-2 py-1 font-medium text-sky-200">
          KPI：{data.kpi.horizon} 日內曾達 ≥ +{data.kpi.x}%
        </span>
        <span className="text-muted">生成時間 {data.generated_at || "—"}</span>
        <span
          className="cursor-help text-muted"
          title={data.excluded_features.length ? data.excluded_features.join("、") : "無"}
        >
          排除特徵 {data.excluded_features.length} 個
        </span>
        <span className="ml-auto flex gap-1.5">
          <span className="rounded bg-sky-900/40 px-2 py-0.5 text-sky-200">pass {data.counts.pass}</span>
          <span className="rounded bg-amber-900/40 px-2 py-0.5 text-amber-200">watch {data.counts.watch}</span>
          <span className="rounded bg-gray-700/40 px-2 py-0.5 text-gray-300">insufficient {data.counts.insufficient}</span>
          <span className="rounded bg-rose-900/40 px-2 py-0.5 text-rose-300">fail {data.counts.fail}</span>
        </span>
      </div>

      {/* 家族分頁籤 */}
      <div className="mb-2 flex gap-1">
        {FAMILY_TABS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFamily(f.key)}
            className={`rounded-md px-3 py-1.5 text-xs transition ${
              family === f.key ? "bg-sky-900/60 text-sky-200" : "text-muted hover:bg-panel2"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* 情境切換 */}
      <div className="mb-4 flex gap-1">
        {CONTEXT_TABS.map((c) => (
          <button
            key={c.key}
            onClick={() => setCtx(c.key)}
            className={`rounded-md border px-2.5 py-1 text-[11px] transition ${
              ctx === c.key
                ? "border-sky-700 bg-sky-900/40 text-sky-200"
                : "border-edge text-muted hover:border-sky-800/60"
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      {/* 圖例 */}
      <div className="mb-3 flex flex-wrap items-center gap-3 text-[11px] text-muted">
        <span><span className="text-sky-300">●</span> pass</span>
        <span><span className="text-amber-300">○</span> watch</span>
        <span><span className="text-gray-500">▨</span> insufficient（灰斜紋）</span>
        <span className="ml-2">格內小字＝n_picks；底色濃淡＝routing_delta 大小（紅＝優於基準、綠＝劣於基準）</span>
      </div>

      {signals.length === 0 ? (
        <div className="rounded-lg border border-edge bg-panel p-6 text-center text-sm text-muted">
          此族在目前挖掘結果中沒有通過稽核的訊號。
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-edge">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>
                <th className="sticky left-0 z-10 min-w-[104px] border-b border-r border-edge bg-panel px-2 py-2 text-left font-medium text-muted">
                  類股群
                </th>
                {signals.map((sig) => (
                  <th
                    key={sig}
                    title={sig}
                    className="border-b border-edge bg-panel px-1.5 py-2 text-center font-medium text-muted"
                  >
                    <span className="block max-w-[64px] truncate">{signalShortLabel(sig)}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const isBaseline = row.id === baseline;
                const audit = chainAuditByGroup.get(row.id);
                const needsReview = audit?.verdict === "建議檢討成員";
                return (
                  <tr key={row.id} className={isBaseline ? "bg-panel2/40" : undefined}>
                    <td
                      className={`sticky left-0 z-10 border-r border-b border-edge px-2 py-1.5 text-left ${
                        isBaseline ? "bg-panel2/60 font-medium text-gray-200" : "bg-panel text-gray-300"
                      }`}
                    >
                      <span className="flex items-center gap-1">
                        {needsReview && (
                          <span
                            className="cursor-help text-amber-400"
                            title={`建議檢討成員：pass ${audit?.n_pass}/${audit?.n_cells}（fail ${audit?.n_fail}），命中率明顯低於全體鏈平均`}
                          >
                            ⚠
                          </span>
                        )}
                        {row.label}
                      </span>
                    </td>
                    {signals.map((sig) => {
                      const cell = cellIndex.get(`${row.id}::${ctx}::${sig}`);
                      if (!cell) {
                        return (
                          <td key={sig} className="border-b border-edge px-1 py-1 text-center text-gray-700">
                            —
                          </td>
                        );
                      }
                      const dot = TIER_DOT[cell.tier];
                      const bg = cell.tier === "insufficient" ? undefined : cellBg(cell.routing_delta, cell.tier);
                      return (
                        <td
                          key={sig}
                          onClick={() => openCell(row.id, row.label, sig)}
                          title={`${row.label} × ${signalShortLabel(sig)}：n_picks=${cell.n_picks}`}
                          className="cursor-pointer border-b border-edge px-1 py-1 text-center transition hover:outline hover:outline-1 hover:outline-sky-600"
                          style={{ background: cell.tier === "insufficient" ? INSUFFICIENT_STRIPE : bg }}
                        >
                          <div className="flex flex-col items-center leading-tight">
                            {dot && <span className={`text-[10px] ${dot.cls}`}>{dot.icon}</span>}
                            <span className="text-gray-200">{fmtNum(cell.n_picks, 0)}</span>
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        title={selected ? `${selected.groupLabel} × ${signalShortLabel(selected.signal)}` : ""}
        open={!!selected}
        onClose={() => setSelected(null)}
      >
        {selected && selectedCell ? (
          <div className="space-y-2 text-sm">
            <div className="mb-2 flex items-center gap-2 text-xs text-muted">
              情境：{CONTEXT_TABS.find((c) => c.key === ctx)?.label ?? ctx}
              {TIER_DOT[selectedCell.tier] && (
                <span className={TIER_DOT[selectedCell.tier].cls}>
                  {TIER_DOT[selectedCell.tier].icon} {selectedCell.tier}
                </span>
              )}
            </div>
            <Row label="命中率 hit" value={fmtPct(selectedCell.hit)} />
            <Row label="對照組差 ctrl" value={fmtPct(selectedCell.ctrl)} />
            <Row label="t 值 t_ctrl" value={fmtNum(selectedCell.t_ctrl)} />
            <Row
              label="三折 fold_ctrl"
              value={selectedCell.fold_ctrl.length
                ? selectedCell.fold_ctrl.map((f) => (f === null ? "—" : fmtPct(f))).join(" / ")
                : "—"}
            />
            <Row
              label="holdout 對照"
              value={selectedCell.holdout_ctrl === null || selectedCell.holdout_ctrl === undefined
                ? "—" : fmtPct(selectedCell.holdout_ctrl)}
            />
            <Row label="樣本 n_picks / n_days" value={`${fmtNum(selectedCell.n_picks, 0)} / ${fmtNum(selectedCell.n_days, 0)}`} />
            <div className="my-2 border-t border-edge" />
            <Row
              label="全市場基準 ctrl"
              value={selectedBaseline ? fmtPct(selectedBaseline.ctrl) : "—"}
            />
            <Row
              label="routing_delta（相對基準）"
              value={fmtPct(selectedCell.routing_delta)}
              valueClass={selectedCell.routing_delta > 0 ? "text-rose-400" : selectedCell.routing_delta < 0 ? "text-emerald-400" : "text-muted"}
            />
          </div>
        ) : (
          <div className="text-sm text-muted">此情境下無資料。</div>
        )}
      </Modal>
    </div>
  );
}

function Row({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted">{label}</span>
      <span className={valueClass ?? "text-gray-200"}>{value}</span>
    </div>
  );
}
