import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useDeleteHolding,
  useHoldings,
  type HoldingItem,
  type HoldingStatus,
} from "../api/client";
import { Modal } from "../components/Modal";
import { NewHoldingForm, TxnForm } from "../components/HoldingForms";
import { StatusLight } from "../components/StatusLight";
import { changeColor, fmtNum, fmtPct, TRACK_LABELS } from "../lib/format";

function pnlColor(v: number | null | undefined) {
  return changeColor(v);
}

// 進場論點狀態（entry_snapshot vs 最新分數）
const THESIS_META: Record<string, { label: string; cls: string }> = {
  intact: { label: "論點成立", cls: "bg-emerald-500/10 text-emerald-300" },
  weakening: { label: "論點轉弱", cls: "bg-amber-500/15 text-amber-300" },
  broken: { label: "論點失效", cls: "bg-red-500/15 text-red-300" },
  unknown: { label: "論點未知", cls: "bg-gray-500/10 text-gray-400" },
};

function ThesisChip({ h }: { h: HoldingItem }) {
  const t = h.thesis;
  if (!t) return null;
  const m = THESIS_META[t.status] ?? THESIS_META.unknown;
  return (
    <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${m.cls}`}
      title={t.messages.join("；") || `進場 ${t.entry_score ?? "—"} 分 → 最新 ${t.latest_score ?? "—"} 分`}>
      {m.label}
    </span>
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
    <div className="w-full px-6 py-6">
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
          {tab === "open" && <ThesisChip h={h} />}
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
            {h.entry_snapshot && (
              <div className="mt-3 rounded-lg border border-edge bg-panel/50 p-3">
                <div className="mb-1 text-xs text-muted">
                  進場理由快照（{h.entry_snapshot.score_date} 當時評分 {h.entry_snapshot.total_score ?? "—"} 分）
                  {h.thesis && h.thesis.latest_score != null && (
                    <span className="ml-2">
                      → 最新 {h.thesis.latest_score} 分
                      {h.thesis.messages.length > 0 && (
                        <span className="ml-1 text-amber-400">（{h.thesis.messages.join("；")}）</span>
                      )}
                    </span>
                  )}
                </div>
                {h.entry_snapshot.reasons?.length ? (
                  <div className="flex flex-wrap gap-1.5">
                    {h.entry_snapshot.reasons.map((r, i) => (
                      <span key={i} className="rounded bg-sky-500/10 px-1.5 py-0.5 text-xs text-sky-300">{r}</span>
                    ))}
                  </div>
                ) : (
                  <div className="text-xs text-muted">無理由紀錄</div>
                )}
                <div className="mt-1.5 text-xs text-muted">
                  當時買點 {fmtNum(h.entry_snapshot.buy_low)}–{fmtNum(h.entry_snapshot.buy_high)} ·
                  停損 {fmtNum(h.entry_snapshot.stop_loss)} · 收盤 {fmtNum(h.entry_snapshot.close)}
                </div>
              </div>
            )}
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
