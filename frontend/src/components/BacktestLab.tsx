import { useMemo, useState } from "react";
import {
  useActivateStrategy,
  useBacktest,
  useCreateStrategy,
  useDeactivateStrategy,
  useDeleteStrategy,
  usePatchStrategy,
  useStrategies,
  useStrategyFields,
  type BacktestResult,
  type Condition,
  type FieldMeta,
  type Strategy,
} from "../api/client";
import { inputCls } from "../components/Modal";
import { changeColor, fmtPct } from "../lib/format";

// 簡單模式模板：編譯成 conditions，套用後仍可在條件列微調（模板只是起步值，不鎖死）。
const TEMPLATES: { name: string; desc: string; conditions: Condition[] }[] = [
  { name: "法人進駐低位股", desc: "投信5日買超>0、股價低於季線",
    conditions: [
      { field: "trust_net_5", op: "gt", value: 0 },
      { field: "ma60_gap", op: "lt", value: 0 },
    ] },
  { name: "營收動能股", desc: "營收YoY>20%、站上月線",
    conditions: [
      { field: "rev_yoy", op: "gt", value: 20 },
      { field: "ma20_gap", op: "gt", value: 0 },
    ] },
  { name: "大戶吸籌", desc: "大戶占比>40%、外資5日買超>0",
    conditions: [
      { field: "big_pct", op: "gt", value: 40 },
      { field: "foreign_net_5", op: "gt", value: 0 },
    ] },
];

const OP_LABELS: Record<string, string> = {
  gt: "大於", lt: "小於", gte: "大於等於", lte: "小於等於",
  streak_gt: "連N日大於", streak_lt: "連N日小於",
};
const OPS = Object.keys(OP_LABELS);
const isStreakOp = (op: string) => op === "streak_gt" || op === "streak_lt";

type DraftStrategy = Omit<Strategy, "id" | "is_active"> & { id: number };

function toDraft(s: Strategy): DraftStrategy {
  return {
    id: s.id, name: s.name, conditions: structuredClone(s.conditions),
    sort_field: s.sort_field, sort_desc: s.sort_desc, top_n: s.top_n,
    target_pct: s.target_pct, horizon_days: s.horizon_days, stop_pct: s.stop_pct,
  };
}

function isDirty(draft: DraftStrategy, saved: Strategy): boolean {
  return (
    draft.name !== saved.name ||
    draft.sort_field !== saved.sort_field ||
    draft.sort_desc !== saved.sort_desc ||
    draft.top_n !== saved.top_n ||
    draft.target_pct !== saved.target_pct ||
    draft.horizon_days !== saved.horizon_days ||
    draft.stop_pct !== saved.stop_pct ||
    JSON.stringify(draft.conditions) !== JSON.stringify(saved.conditions)
  );
}

// ─────────────────────────── 主體 ───────────────────────────

export function BacktestLabSection() {
  const { data: strategies } = useStrategies();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const createStrategy = useCreateStrategy();

  const list = strategies ?? [];
  const selected = list.find((s) => s.id === selectedId) ?? null;

  const createOne = async () => {
    const s = await createStrategy.mutateAsync({
      name: `新策略 ${list.length + 1}`,
      conditions: [],
      sort_field: "trust_net_5", sort_desc: true, top_n: 10,
      target_pct: 10, horizon_days: 10, stop_pct: null,
    });
    setSelectedId(s.id);
  };

  return (
    <section className="mb-8">
      <h2 className="mb-2 text-base font-semibold">🧪 回測實驗室 — 自訂條件、跑歷史勝率</h2>
      {list.length === 0 ? (
        <div className="rounded-xl border border-dashed border-edge bg-panel p-8 text-center">
          <p className="mb-3 text-sm text-muted">還沒有任何策略。用簡單模式的起步模板，或從零開始自訂條件。</p>
          <button onClick={createOne} disabled={createStrategy.isPending}
            className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50">
            {createStrategy.isPending ? "建立中…" : "建立第一個策略"}
          </button>
        </div>
      ) : (
        <div className="flex gap-4">
          <StrategyList list={list} selectedId={selectedId} onSelect={setSelectedId} onCreate={createOne}
            creating={createStrategy.isPending} />
          <div className="flex-1">
            {selected ? <StrategyEditor key={selected.id} strategy={selected} /> : (
              <div className="rounded-xl border border-dashed border-edge bg-panel p-8 text-center text-sm text-muted">
                從左側選一個策略
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

// ─────────────────────────── 策略清單 ───────────────────────────

function StrategyList({ list, selectedId, onSelect, onCreate, creating }: {
  list: Strategy[]; selectedId: number | null; onSelect: (id: number) => void;
  onCreate: () => void; creating: boolean;
}) {
  const activate = useActivateStrategy();
  const deactivate = useDeactivateStrategy();
  const del = useDeleteStrategy();

  return (
    <div className="w-56 shrink-0">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-muted">策略庫（{list.length}）</span>
        <button onClick={onCreate} disabled={creating}
          className="rounded-md bg-panel2 px-2 py-1 text-xs hover:bg-edge disabled:opacity-50">
          {creating ? "建立中…" : "＋ 新增"}
        </button>
      </div>
      <div className="flex flex-col gap-1">
        {list.map((s) => (
          <div key={s.id}
            className={`group flex items-center gap-1.5 rounded-lg border px-2.5 py-2 text-sm ${
              s.id === selectedId ? "border-sky-700 bg-sky-950/30" : "border-edge bg-panel hover:bg-panel2"}`}>
            <button onClick={() => onSelect(s.id)} className="flex-1 truncate text-left">
              {s.name}
              {s.is_active && <span className="ml-1.5 rounded bg-emerald-900/50 px-1 py-0.5 text-[10px] text-emerald-300">啟用中</span>}
            </button>
            <button
              onClick={() => (s.is_active ? deactivate.mutate(undefined) : activate.mutate(s.id))}
              title={s.is_active ? "停用" : "設為啟用"}
              className="shrink-0 rounded px-1 text-xs text-muted hover:text-gray-200"
            >
              {s.is_active ? "⏸" : "▶"}
            </button>
            <button
              onClick={() => { if (confirm(`刪除「${s.name}」？`)) { del.mutate(s.id); if (s.id === selectedId) onSelect(-1); } }}
              title="刪除"
              className="shrink-0 rounded px-1 text-xs text-muted hover:text-down"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────── 條件列（簡單/專業共用）───────────────────────────

function groupFields(fields: FieldMeta[]): [string, FieldMeta[]][] {
  const m = new Map<string, FieldMeta[]>();
  for (const f of fields) {
    if (!m.has(f.group)) m.set(f.group, []);
    m.get(f.group)!.push(f);
  }
  return [...m.entries()];
}

function ConditionRow({ cond, fields, onChange, onDelete }: {
  cond: Condition; fields: FieldMeta[];
  onChange: (c: Condition) => void; onDelete: () => void;
}) {
  const grouped = useMemo(() => groupFields(fields), [fields]);
  const streak = isStreakOp(cond.op);
  const setOp = (op: string) => {
    if (isStreakOp(op) === streak) { onChange({ ...cond, op }); return; }
    onChange({ ...cond, op, value: isStreakOp(op) ? { n: 3, threshold: 0 } : 0 });
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select className={`${inputCls} w-40`} value={cond.field}
        onChange={(e) => onChange({ ...cond, field: e.target.value })}>
        {grouped.map(([g, fs]) => (
          <optgroup key={g} label={g}>
            {fs.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </optgroup>
        ))}
      </select>
      <select className={`${inputCls} w-28`} value={cond.op} onChange={(e) => setOp(e.target.value)}>
        {OPS.map((op) => <option key={op} value={op}>{OP_LABELS[op]}</option>)}
      </select>
      {streak ? (
        <>
          <label className="flex items-center gap-1 text-xs text-muted">
            連
            <input type="number" className={`${inputCls} w-16`}
              value={(cond.value as { n: number }).n}
              onChange={(e) => onChange({ ...cond, value: { ...(cond.value as { n: number; threshold: number }), n: Number(e.target.value) } })} />
            日
          </label>
          <label className="flex items-center gap-1 text-xs text-muted">
            門檻
            <input type="number" className={`${inputCls} w-20`}
              value={(cond.value as { threshold: number }).threshold}
              onChange={(e) => onChange({ ...cond, value: { ...(cond.value as { n: number; threshold: number }), threshold: Number(e.target.value) } })} />
          </label>
        </>
      ) : (
        <input type="number" className={`${inputCls} w-24`} value={cond.value as number}
          onChange={(e) => onChange({ ...cond, value: Number(e.target.value) })} />
      )}
      <button onClick={onDelete} className="text-xs text-muted hover:text-down">✕</button>
    </div>
  );
}

function ConditionsEditor({ conditions, fields, onChange }: {
  conditions: Condition[]; fields: FieldMeta[]; onChange: (c: Condition[]) => void;
}) {
  const addCondition = () => {
    const first = fields[0];
    onChange([...conditions, { field: first?.key ?? "", op: "gt", value: 0 }]);
  };
  return (
    <div className="flex flex-col gap-2">
      {conditions.length === 0 && <p className="text-xs text-muted">尚無條件（無條件＝全市場皆入選，僅靠排序取前 N 檔）</p>}
      {conditions.map((c, i) => (
        <ConditionRow key={i} cond={c} fields={fields}
          onChange={(nc) => onChange(conditions.map((x, j) => (j === i ? nc : x)))}
          onDelete={() => onChange(conditions.filter((_, j) => j !== i))} />
      ))}
      <button onClick={addCondition} className="w-fit rounded-md bg-panel2 px-3 py-1.5 text-xs hover:bg-edge">
        ＋ 加條件
      </button>
    </div>
  );
}

// ─────────────────────────── 編輯器 ───────────────────────────

function StrategyEditor({ strategy }: { strategy: Strategy }) {
  const { data: fields } = useStrategyFields();
  const patch = usePatchStrategy();
  const backtest = useBacktest();

  const [draft, setDraft] = useState<DraftStrategy>(() => toDraft(strategy));
  const [mode, setMode] = useState<"simple" | "pro">("pro");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [result, setResult] = useState<BacktestResult | null>(null);

  const dirty = isDirty(draft, strategy);
  const fieldList = fields ?? [];

  const save = () => patch.mutateAsync({
    id: draft.id, name: draft.name, conditions: draft.conditions,
    sort_field: draft.sort_field, sort_desc: draft.sort_desc, top_n: draft.top_n,
    target_pct: draft.target_pct, horizon_days: draft.horizon_days,
    stop_pct: draft.stop_pct, clear_stop: draft.stop_pct === null,
  });

  const runBacktest = async (s: string, e: string) => {
    if (!s || !e) return;
    if (dirty) await save();
    const r = await backtest.mutateAsync({ id: draft.id, start: s, end: e });
    setResult(r);
  };

  const quickRange = (days: number) => {
    const e = new Date();
    const s = new Date();
    s.setDate(s.getDate() - days);
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    const sIso = iso(s), eIso = iso(e);
    setStart(sIso); setEnd(eIso);
    runBacktest(sIso, eIso);
  };

  const applyTemplate = (t: (typeof TEMPLATES)[number]) => {
    setDraft({ ...draft, conditions: structuredClone(t.conditions) });
  };

  return (
    <div className="flex flex-col gap-4">
      {/* 名稱 */}
      <div className="rounded-xl border border-edge bg-panel p-4">
        <label className="block">
          <span className="mb-1 block text-xs text-muted">策略名稱</span>
          <input className={inputCls} value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </label>
      </div>

      {/* 模式切換 + 條件 */}
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="font-semibold">選股條件</span>
          <div className="flex gap-1">
            {(["simple", "pro"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)}
                className={`rounded-md px-3 py-1 text-xs ${mode === m ? "bg-sky-900/60 text-sky-200" : "text-muted hover:bg-panel2"}`}>
                {m === "simple" ? "簡單" : "專業"}
              </button>
            ))}
          </div>
        </div>
        {mode === "simple" && (
          <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {TEMPLATES.map((t) => (
              <button key={t.name} onClick={() => applyTemplate(t)}
                className="rounded-lg border border-edge bg-panel2/60 p-2.5 text-left hover:border-sky-700">
                <div className="text-sm font-medium">{t.name}</div>
                <div className="mt-0.5 text-[11px] text-muted">{t.desc}</div>
              </button>
            ))}
          </div>
        )}
        <ConditionsEditor conditions={draft.conditions} fields={fieldList}
          onChange={(c) => setDraft({ ...draft, conditions: c })} />
      </div>

      {/* 目標區 */}
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-3 font-semibold">目標</div>
        <div className="flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="mb-1 block text-xs text-muted">N 日內</span>
            <input type="number" className={`${inputCls} w-24`} value={draft.horizon_days}
              onChange={(e) => setDraft({ ...draft, horizon_days: Number(e.target.value) })} />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-muted">碰到 +X%</span>
            <input type="number" className={`${inputCls} w-24`} value={draft.target_pct}
              onChange={(e) => setDraft({ ...draft, target_pct: Number(e.target.value) })} />
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input type="checkbox" checked={draft.stop_pct !== null}
              onChange={(e) => setDraft({ ...draft, stop_pct: e.target.checked ? 5 : null })} />
            停損
          </label>
          {draft.stop_pct !== null && (
            <label className="block">
              <span className="mb-1 block text-xs text-muted">−Y%</span>
              <input type="number" className={`${inputCls} w-24`} value={draft.stop_pct}
                onChange={(e) => setDraft({ ...draft, stop_pct: Number(e.target.value) })} />
            </label>
          )}
        </div>
      </div>

      {/* 排序區 */}
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-3 font-semibold">排序（取前 N 檔）</div>
        <div className="flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="mb-1 block text-xs text-muted">排序欄位</span>
            <select className={`${inputCls} w-44`} value={draft.sort_field}
              onChange={(e) => setDraft({ ...draft, sort_field: e.target.value })}>
              {groupFields(fieldList).map(([g, fs]) => (
                <optgroup key={g} label={g}>
                  {fs.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                </optgroup>
              ))}
            </select>
          </label>
          <div className="flex gap-1 pb-0.5">
            {([[true, "由高到低"], [false, "由低到高"]] as const).map(([v, label]) => (
              <button key={label} onClick={() => setDraft({ ...draft, sort_desc: v })}
                className={`rounded-md px-3 py-1.5 text-xs ${draft.sort_desc === v ? "bg-sky-900/60 text-sky-200" : "bg-panel2 text-muted hover:bg-edge"}`}>
                {label}
              </button>
            ))}
          </div>
          <label className="block">
            <span className="mb-1 block text-xs text-muted">top N</span>
            <input type="number" min={1} className={`${inputCls} w-20`} value={draft.top_n}
              onChange={(e) => setDraft({ ...draft, top_n: Number(e.target.value) })} />
          </label>
        </div>
      </div>

      {/* 儲存 */}
      <div className="flex items-center gap-3">
        <button onClick={() => save()} disabled={!dirty || patch.isPending}
          className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50">
          {patch.isPending ? "儲存中…" : "儲存"}
        </button>
        {!dirty && <span className="text-xs text-muted">已儲存</span>}
        {patch.isError && <span className="text-sm text-down">{(patch.error as Error).message}</span>}
      </div>

      {/* 回測區 */}
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-3 font-semibold">回測</div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex gap-1">
            {([[3, "3 個月"], [6, "6 個月"], [12, "12 個月"]] as const).map(([m, label]) => (
              <button key={m} onClick={() => quickRange(m * 30)} disabled={backtest.isPending}
                className="rounded-md bg-panel2 px-3 py-1.5 text-xs hover:bg-edge disabled:opacity-50">
                {label}
              </button>
            ))}
          </div>
          <label className="block">
            <span className="mb-1 block text-xs text-muted">起</span>
            <input type="date" className={`${inputCls} w-36`} value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-muted">迄</span>
            <input type="date" className={`${inputCls} w-36`} value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <button onClick={() => runBacktest(start, end)} disabled={!start || !end || backtest.isPending}
            className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50">
            {backtest.isPending ? "回測中…" : "跑回測"}
          </button>
        </div>
        {backtest.isError && <p className="mt-2 text-sm text-down">{(backtest.error as Error).message}</p>}
        {result && <div className="mt-4"><ResultPanel result={result} /></div>}
      </div>
    </div>
  );
}

// ─────────────────────────── 回測結果面板 ───────────────────────────

function ResultPanel({ result: r }: { result: BacktestResult }) {
  const cell = (label: string, value: string, color?: string) => (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-2xl font-semibold tabular-nums ${color ?? ""}`}>{value}</div>
    </div>
  );
  const maxSamples = Math.max(1, ...r.monthly.map((m) => m.samples));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {r.samples < 30 && (
          <span className="rounded-full bg-amber-900/40 px-2.5 py-0.5 text-xs text-amber-300">樣本不足</span>
        )}
        <span className="text-xs text-muted">訊號日數 {r.signal_days}</span>
      </div>
      {r.warn_loose && (
        <div className="rounded-lg border border-amber-700/50 bg-amber-950/30 px-3 py-2 text-xs text-amber-200/90">
          條件太鬆（單日入選 &gt;200 檔）——結果接近全市場基率，建議收緊條件。
        </div>
      )}
      <div className="grid grid-cols-2 gap-4 rounded-lg border border-edge bg-panel2/40 p-4 sm:grid-cols-4">
        {cell("命中率", r.hit_rate != null ? fmtPct(r.hit_rate * 100) : "—",
          r.hit_rate != null && r.base_rate != null && r.hit_rate >= r.base_rate ? "text-up" : undefined)}
        {cell("基率", r.base_rate != null ? fmtPct(r.base_rate * 100) : "—")}
        {cell("lift", r.lift != null ? `${r.lift > 0 ? "+" : ""}${r.lift.toFixed(1)}pp` : "—",
          r.lift != null ? changeColor(r.lift) : undefined)}
        {cell("樣本數", `${r.hits}/${r.samples}`)}
      </div>

      {r.monthly.length > 0 && (
        <div className="rounded-lg border border-edge bg-panel2/30 p-3">
          <div className="mb-2 text-xs text-muted">逐月樣本數與命中</div>
          <div className="flex flex-col gap-1.5">
            {r.monthly.map((m) => {
              const rate = m.samples > 0 ? m.hits / m.samples : 0;
              return (
                <div key={m.month} className="flex items-center gap-2 text-xs">
                  <span className="w-16 shrink-0 tabular-nums text-muted">{m.month}</span>
                  <div className="relative h-4 flex-1 overflow-hidden rounded bg-panel2">
                    <div className="absolute inset-y-0 left-0 bg-sky-500/30" style={{ width: `${(m.samples / maxSamples) * 100}%` }} />
                    <div className="absolute inset-y-0 left-0 bg-sky-500/70" style={{ width: `${(m.samples / maxSamples) * 100 * rate}%` }} />
                  </div>
                  <span className="w-20 shrink-0 tabular-nums text-muted">{m.hits}/{m.samples}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {r.recent.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-edge">
          <table className="w-full text-sm">
            <thead className="bg-panel2 text-xs text-muted">
              <tr>
                <th className="px-3 py-2 text-left">日期</th>
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-right">進場價</th>
                <th className="px-3 py-2 text-center">結果</th>
                <th className="px-3 py-2 text-right">最高漲</th>
                <th className="px-3 py-2 text-right">最深回撤</th>
              </tr>
            </thead>
            <tbody>
              {r.recent.map((row, i) => (
                <tr key={`${row.stock_id}-${row.date}-${i}`} className="border-t border-edge">
                  <td className="px-3 py-1.5 text-xs tabular-nums text-muted">{row.date}</td>
                  <td className="px-3 py-1.5">
                    <span className="font-medium">{row.name}</span>
                    <span className="ml-1 text-xs text-muted">{row.stock_id}</span>
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{row.entry}</td>
                  <td className="px-3 py-1.5 text-center text-xs">
                    {row.hit ? <span className="text-up">✔ 命中</span> : row.stopped ? <span className="text-down">✕ 停損</span> : <span className="text-muted">未達標</span>}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-up">{fmtPct(row.max_gain_pct)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-down">{fmtPct(row.max_dd_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
