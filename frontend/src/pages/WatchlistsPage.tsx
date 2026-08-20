import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useAddWatchItem,
  useCreateWatchlist,
  useDeleteWatchItem,
  useItemToHolding,
  useWatchlists,
} from "../api/client";
import { Field, inputCls, Modal } from "../components/Modal";
import { changeColor, fmtNum, fmtPct, scoreColor } from "../lib/format";

const LIGHT_EMOJI: Record<string, string> = { green: "🟢", yellow: "🟡", white: "⚪" };
const today = () => new Date().toISOString().slice(0, 10);

export default function WatchlistsPage() {
  const { data, isLoading } = useWatchlists();
  const createWl = useCreateWatchlist();
  const addItem = useAddWatchItem();
  const delItem = useDeleteWatchItem();
  const toHolding = useItemToHolding();

  const [active, setActive] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  const [convert, setConvert] = useState<{ itemId: number; name: string } | null>(null);
  const [form, setForm] = useState({ stock_id: "", target_price: "" });
  const [conv, setConv] = useState({ track: "wave", date: today(), price: "", shares: "" });

  if (isLoading || !data) return <div className="p-6 text-muted">載入中…</div>;
  const lists = data.watchlists;
  const current = lists[active];

  const addNewList = () => {
    const name = prompt("新清單名稱（如 短線 / 存股 / AI概念）");
    if (name) createWl.mutate(name);
  };

  return (
    <div className="w-full px-6 py-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">觀察清單</h1>
        {current && (
          <button onClick={() => setAddOpen(true)} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium">
            + 加入觀察
          </button>
        )}
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-1 border-b border-edge">
        {lists.map((wl, i) => (
          <button key={wl.id} onClick={() => setActive(i)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${i === active ? "border-sky-500 text-sky-300" : "border-transparent text-muted hover:text-gray-300"}`}>
            {wl.name} <span className="text-xs">({wl.items.length})</span>
          </button>
        ))}
        <button onClick={addNewList} className="px-3 py-2 text-sm text-muted hover:text-sky-300">+ 新清單</button>
      </div>

      {!current && <div className="rounded-xl border border-dashed border-edge py-16 text-center text-muted">尚無清單，點「+ 新清單」建立</div>}

      {current && current.items.length === 0 && (
        <div className="rounded-xl border border-dashed border-edge py-16 text-center text-muted">此清單尚無標的</div>
      )}

      {current && current.items.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-edge">
          <table className="w-full text-sm">
            <thead className="bg-panel2 text-xs text-muted">
              <tr>
                <th className="px-3 py-2 text-left">狀態</th>
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-right">現價</th>
                <th className="px-3 py-2 text-right">目標價</th>
                <th className="px-3 py-2 text-right">波段</th>
                <th className="px-3 py-2 text-right">長線</th>
                <th className="px-3 py-2 text-left">提醒</th>
                <th className="px-3 py-2 text-right">動作</th>
              </tr>
            </thead>
            <tbody>
              {current.items.map((it) => (
                <tr key={it.id} className="border-t border-edge hover:bg-panel/60">
                  <td className="px-3 py-2">{LIGHT_EMOJI[it.light]}</td>
                  <td className="px-3 py-2">
                    <Link to={`/stocks/${it.stock_id}`} className="hover:underline">
                      {it.name} <span className="text-xs text-muted">{it.stock_id}</span>
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {fmtNum(it.close)}<span className={`ml-1 text-xs ${changeColor(it.change_pct)}`}>{fmtPct(it.change_pct)}</span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtNum(it.target_price)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${scoreColor(it.wave_score)}`}>{it.wave_score?.toFixed(0) ?? "—"}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${scoreColor(it.long_score)}`}>{it.long_score?.toFixed(0) ?? "—"}</td>
                  <td className="px-3 py-2 text-xs text-sky-300">{it.reminders.join("、") || "—"}</td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex justify-end gap-2 text-xs">
                      <button onClick={() => { setConvert({ itemId: it.id, name: it.name }); setConv({ track: "wave", date: today(), price: String(it.close ?? ""), shares: "" }); }} className="text-sky-400 hover:underline">轉持股</button>
                      <button onClick={() => delItem.mutate(it.id)} className="text-down hover:underline">移除</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 加入觀察 */}
      <Modal title="加入觀察" open={addOpen} onClose={() => setAddOpen(false)}>
        <Field label="股票代號"><input className={inputCls} value={form.stock_id} onChange={(e) => setForm({ ...form, stock_id: e.target.value })} placeholder="2330" /></Field>
        <Field label="目標價（可選）"><input type="number" className={inputCls} value={form.target_price} onChange={(e) => setForm({ ...form, target_price: e.target.value })} /></Field>
        {addItem.isError && <p className="mb-2 text-sm text-down">{(addItem.error as Error).message}</p>}
        <button disabled={!form.stock_id || addItem.isPending}
          onClick={() => addItem.mutate({ wlId: current.id, body: { stock_id: form.stock_id.trim(), target_price: form.target_price ? Number(form.target_price) : null } }, { onSuccess: () => { setAddOpen(false); setForm({ stock_id: "", target_price: "" }); } })}
          className="w-full rounded-md bg-sky-600 py-2 text-sm font-medium disabled:opacity-50">加入</button>
      </Modal>

      {/* 轉持股 */}
      <Modal title="轉為持股" open={!!convert} onClose={() => setConvert(null)}>
        {convert && (
          <div>
            <p className="mb-3 text-sm text-muted">{convert.name}</p>
            <Field label="軌道">
              <select className={inputCls} value={conv.track} onChange={(e) => setConv({ ...conv, track: e.target.value })}>
                <option value="wave">波段</option><option value="long">長線</option>
              </select>
            </Field>
            <div className="grid grid-cols-3 gap-2">
              <Field label="日期"><input type="date" className={inputCls} value={conv.date} onChange={(e) => setConv({ ...conv, date: e.target.value })} /></Field>
              <Field label="價格"><input type="number" className={inputCls} value={conv.price} onChange={(e) => setConv({ ...conv, price: e.target.value })} /></Field>
              <Field label="張數"><input type="number" className={inputCls} value={conv.shares} onChange={(e) => setConv({ ...conv, shares: e.target.value })} /></Field>
            </div>
            <button disabled={!conv.price || !conv.shares || toHolding.isPending}
              onClick={() => toHolding.mutate({ itemId: convert.itemId, body: { track: conv.track, date: conv.date, price: Number(conv.price), shares: Number(conv.shares) } }, { onSuccess: () => setConvert(null) })}
              className="w-full rounded-md bg-sky-600 py-2 text-sm font-medium disabled:opacity-50">轉為持股</button>
          </div>
        )}
      </Modal>
    </div>
  );
}
