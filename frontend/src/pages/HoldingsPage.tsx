import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useAddTransaction,
  useCreateHolding,
  useDeleteHolding,
  useHoldings,
  type HoldingItem,
  type HoldingStatus,
} from "../api/client";
import { Field, Modal, inputCls } from "../components/Modal";
import { StatusLight } from "../components/StatusLight";
import { changeColor, fmtNum, fmtPct, TRACK_LABELS } from "../lib/format";

const today = () => new Date().toISOString().slice(0, 10);

function pnlColor(v: number | null | undefined) {
  return changeColor(v);
}

// ───── 新增持股表單 ─────
function NewHoldingForm({ onDone }: { onDone: () => void }) {
  const create = useCreateHolding();
  const [f, setF] = useState({ stock_id: "", track: "wave", date: today(), price: "", shares: "" });
  const submit = () => {
    create.mutate(
      {
        stock_id: f.stock_id.trim(),
        track: f.track,
        date: f.date,
        price: Number(f.price),
        shares: Number(f.shares),
      },
      { onSuccess: onDone },
    );
  };
  const ok = f.stock_id && f.price && f.shares;
  return (
    <div>
      <Field label="股票代號">
        <input className={inputCls} value={f.stock_id} onChange={(e) => setF({ ...f, stock_id: e.target.value })} placeholder="2330" />
      </Field>
      <Field label="軌道">
        <select className={inputCls} value={f.track} onChange={(e) => setF({ ...f, track: e.target.value })}>
          <option value="wave">波段</option>
          <option value="long">長線</option>
        </select>
      </Field>
      <div className="grid grid-cols-3 gap-2">
        <Field label="日期"><input type="date" className={inputCls} value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
        <Field label="價格"><input type="number" className={inputCls} value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} /></Field>
        <Field label="張數"><input type="number" className={inputCls} value={f.shares} onChange={(e) => setF({ ...f, shares: e.target.value })} /></Field>
      </div>
      {create.isError && <p className="mb-2 text-sm text-down">{(create.error as Error).message}</p>}
      <button disabled={!ok || create.isPending} onClick={submit} className="w-full rounded-md bg-sky-600 py-2 text-sm font-medium disabled:opacity-50">
        {create.isPending ? "新增中…" : "新增持股"}
      </button>
    </div>
  );
}

// ───── 加碼 / 賣出表單 ─────
function TxnForm({ holding, type, onDone }: { holding: HoldingItem; type: "add" | "sell"; onDone: () => void }) {
  const addTxn = useAddTransaction();
  const [f, setF] = useState({ date: today(), price: String(holding.close ?? ""), shares: "" });
  const submit = () => {
    addTxn.mutate(
      { id: holding.id, body: { type, date: f.date, price: Number(f.price), shares: Number(f.shares) } },
      { onSuccess: onDone },
    );
  };
  const ok = f.price && f.shares && (type === "add" || Number(f.shares) <= holding.shares);
  return (
    <div>
      <p className="mb-3 text-sm text-muted">
        {holding.name}（{holding.stock_id}）目前 {holding.shares} 張 · 均價 {fmtNum(holding.avg_cost)}
      </p>
      <div className="grid grid-cols-3 gap-2">
        <Field label="日期"><input type="date" className={inputCls} value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
        <Field label="價格"><input type="number" className={inputCls} value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} /></Field>
        <Field label={type === "sell" ? `張數（≤${holding.shares}）` : "張數"}><input type="number" className={inputCls} value={f.shares} onChange={(e) => setF({ ...f, shares: e.target.value })} /></Field>
      </div>
      {addTxn.isError && <p className="mb-2 text-sm text-down">{(addTxn.error as Error).message}</p>}
      <button disabled={!ok || addTxn.isPending} onClick={submit} className="w-full rounded-md bg-sky-600 py-2 text-sm font-medium disabled:opacity-50">
        {addTxn.isPending ? "送出中…" : type === "add" ? "加碼" : "賣出"}
      </button>
    </div>
  );
}

function SummaryBar({ s }: { s: { count: number; total_market_value: number; total_unrealized_pnl: number; total_return_pct: number | null; total_realized_pnl: number } }) {
  const cell = (label: string, value: string, color?: string) => (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${color ?? ""}`}>{value}</div>
    </div>
  );
  return (
    <div className="mb-5 grid grid-cols-2 gap-4 rounded-xl border border-edge bg-panel p-4 sm:grid-cols-4">
      {cell("持股數", String(s.count))}
      {cell("總市值", fmtNum(s.total_market_value, 0))}
      {cell("未實現損益", fmtNum(s.total_unrealized_pnl, 0), pnlColor(s.total_unrealized_pnl))}
      {cell("總報酬率", fmtPct(s.total_return_pct), pnlColor(s.total_return_pct))}
    </div>
  );
}

export default function HoldingsPage() {
  const [tab, setTab] = useState<HoldingStatus>("open");
  const [expanded, setExpanded] = useState<number | null>(null);
  const [modal, setModal] = useState<{ kind: "new" | "add" | "sell"; holding?: HoldingItem } | null>(null);
  const { data, isLoading } = useHoldings(tab);
  const del = useDeleteHolding();

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">我的持股</h1>
        <button onClick={() => setModal({ kind: "new" })} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium">
          + 新增持股
        </button>
      </div>

      {data && <SummaryBar s={data.summary} />}

      <div className="mb-4 flex gap-1 border-b border-edge">
        {(["open", "closed"] as HoldingStatus[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${tab === t ? "border-sky-500 text-sky-300" : "border-transparent text-muted hover:text-gray-300"}`}>
            {t === "open" ? "持有中" : "已出場 · 歷史"}
          </button>
        ))}
      </div>

      {isLoading && <p className="text-muted">載入中…</p>}
      {data && data.items.length === 0 && (
        <div className="rounded-xl border border-dashed border-edge py-16 text-center text-muted">
          {tab === "open" ? "目前沒有持股，點右上角新增" : "尚無已出場紀錄"}
        </div>
      )}

      {data && data.items.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-edge">
          <table className="w-full text-sm">
            <thead className="bg-panel2 text-xs text-muted">
              <tr>
                <th className="px-3 py-2 text-left">狀態</th>
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-left">軌道</th>
                <th className="px-3 py-2 text-right">現價</th>
                <th className="px-3 py-2 text-right">{tab === "open" ? "報酬率" : "已實現"}</th>
                <th className="px-3 py-2 text-left">出場訊號</th>
                <th className="px-3 py-2 text-right">動作</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((h) => (
                <RowGroup
                  key={h.id}
                  h={h}
                  tab={tab}
                  expanded={expanded === h.id}
                  onToggle={() => setExpanded(expanded === h.id ? null : h.id)}
                  onAdd={() => setModal({ kind: "add", holding: h })}
                  onSell={() => setModal({ kind: "sell", holding: h })}
                  onDelete={() => { if (confirm(`刪除 ${h.name} 全部紀錄？`)) del.mutate(h.id); }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal title="新增持股" open={modal?.kind === "new"} onClose={() => setModal(null)}>
        <NewHoldingForm onDone={() => setModal(null)} />
      </Modal>
      <Modal title="加碼" open={modal?.kind === "add"} onClose={() => setModal(null)}>
        {modal?.holding && <TxnForm holding={modal.holding} type="add" onDone={() => setModal(null)} />}
      </Modal>
      <Modal title="賣出" open={modal?.kind === "sell"} onClose={() => setModal(null)}>
        {modal?.holding && <TxnForm holding={modal.holding} type="sell" onDone={() => setModal(null)} />}
      </Modal>
    </div>
  );
}

function RowGroup({ h, tab, expanded, onToggle, onAdd, onSell, onDelete }: {
  h: HoldingItem; tab: HoldingStatus; expanded: boolean;
  onToggle: () => void; onAdd: () => void; onSell: () => void; onDelete: () => void;
}) {
  return (
    <>
      <tr className="border-t border-edge hover:bg-panel/60">
        <td className="px-3 py-2"><StatusLight light={h.light} level={h.level} /></td>
        <td className="px-3 py-2">
          <Link to={`/stocks/${h.stock_id}`} className="hover:underline">
            <span className="font-medium">{h.name}</span>
            <span className="ml-1 text-xs text-muted">{h.stock_id}</span>
          </Link>
        </td>
        <td className="px-3 py-2 text-muted">{TRACK_LABELS[h.track]}</td>
        <td className="px-3 py-2 text-right tabular-nums">
          {fmtNum(h.close)}
          <span className={`ml-1 text-xs ${changeColor(h.change_pct)}`}>{fmtPct(h.change_pct)}</span>
        </td>
        <td className={`px-3 py-2 text-right tabular-nums ${pnlColor(tab === "open" ? h.return_pct : h.realized_pnl)}`}>
          {tab === "open" ? fmtPct(h.return_pct) : fmtNum(h.realized_pnl, 0)}
        </td>
        <td className="px-3 py-2 text-xs text-gray-300">{h.signals.slice(0, 2).join("、") || "—"}</td>
        <td className="px-3 py-2 text-right">
          <div className="flex justify-end gap-2 text-xs">
            {tab === "open" && <button onClick={onAdd} className="text-sky-400 hover:underline">加碼</button>}
            {tab === "open" && <button onClick={onSell} className="text-amber-400 hover:underline">賣出</button>}
            <button onClick={onToggle} className="text-muted hover:text-gray-200">{expanded ? "收合" : "展開"}</button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-edge bg-bg/40">
          <td colSpan={7} className="px-4 py-3">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <div className="mb-1 text-xs text-muted">部位</div>
                <div className="text-sm">持有 {h.shares} 張 · 均價 {fmtNum(h.avg_cost)}</div>
                <div className="text-sm">市值 {fmtNum(h.market_value, 0)} · 未實現 <span className={pnlColor(h.unrealized_pnl)}>{fmtNum(h.unrealized_pnl, 0)}</span></div>
              </div>
              <div>
                <div className="mb-1 text-xs text-muted">出場參考</div>
                <div className="text-sm">停損價 {fmtNum(h.hard_stop)}</div>
                <div className="text-sm">持有高點 {fmtNum(h.highest)} · 回落 {fmtPct(h.drawdown_pct)} {h.trail_active ? "（移動停利啟動）" : ""}</div>
              </div>
              <div>
                <div className="mb-1 text-xs text-muted">全部出場訊號</div>
                {h.signals.length ? (
                  <ul className="list-inside list-disc text-sm text-gray-300">{h.signals.map((s, i) => <li key={i}>{s}</li>)}</ul>
                ) : (
                  <div className="text-sm text-muted">無</div>
                )}
              </div>
            </div>
            <div className="mt-3">
              <div className="mb-1 text-xs text-muted">交易明細</div>
              <table className="w-full text-xs">
                <thead className="text-muted"><tr><th className="text-left">類型</th><th className="text-left">日期</th><th className="text-right">價格</th><th className="text-right">張數</th></tr></thead>
                <tbody>
                  {h.transactions.map((t) => (
                    <tr key={t.id}>
                      <td>{t.type === "buy" ? "買進" : t.type === "add" ? "加碼" : "賣出"}</td>
                      <td>{t.date}</td>
                      <td className="text-right tabular-nums">{fmtNum(t.price)}</td>
                      <td className="text-right tabular-nums">{t.shares}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-3 text-right">
              <button onClick={onDelete} className="text-xs text-down hover:underline">刪除此持股</button>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
