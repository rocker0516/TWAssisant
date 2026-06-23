import { useState } from "react";
import { useAddTransaction, useCreateHolding, type HoldingItem } from "../api/client";
import { Field, inputCls } from "./Modal";
import { fmtNum } from "../lib/format";

export const today = () => new Date().toISOString().slice(0, 10);

// ───── 新增持股 / 買進表單 ─────
export function NewHoldingForm({
  onDone,
  defaultStockId,
  defaultPrice,
  lockStock,
}: {
  onDone: () => void;
  defaultStockId?: string;
  defaultPrice?: number | null;
  lockStock?: boolean;
}) {
  const create = useCreateHolding();
  const [f, setF] = useState({
    stock_id: defaultStockId ?? "",
    track: "wave",
    date: today(),
    price: defaultPrice != null ? String(defaultPrice) : "",
    shares: "",
  });
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
        <input
          className={`${inputCls} ${lockStock ? "opacity-60" : ""}`}
          value={f.stock_id}
          onChange={(e) => setF({ ...f, stock_id: e.target.value })}
          placeholder="2330"
          disabled={lockStock}
        />
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
export function TxnForm({ holding, type, onDone }: { holding: HoldingItem; type: "add" | "sell"; onDone: () => void }) {
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
