import { useMemo, useState } from "react";
import { DndContext, type DragEndEvent, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import { arrayMove, SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Link } from "react-router-dom";
import { useFearGreed, useOverview, useSettings, useUpdateSettings, type OverviewResponse } from "../api/client";
import { FearGreedCard } from "../components/FearGreedCard";
import { Markdown } from "../components/Markdown";
import { changeColor, fmtNum, fmtPct, scoreColor, trendColor, TRACK_LABELS } from "../lib/format";

function Stat({ label, value, color, hint }: { label: string; value: string; color?: string; hint?: string }) {
  return (
    <div className="text-center">
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${color ?? ""}`}>{value}</div>
      {hint && <div className="text-[10px] leading-tight text-muted">{hint}</div>}
    </div>
  );
}

// 各 widget 內容（依 key 渲染，順序/顯示由 settings.layout 控制）
const WIDGETS: Record<string, { title: string; to: string; render: (d: OverviewResponse) => React.ReactNode }> = {
  holdings: {
    title: "💼 持股提醒", to: "/holdings",
    render: (d) => d.holdings_alerts.length === 0 ? <p className="text-sm text-muted">目前無需處理的持股</p> : (
      <ul className="flex flex-col gap-2">
        {d.holdings_alerts.map((a) => (
          <li key={a.stock_id} className="flex items-center gap-2 text-sm">
            <span>{a.light}</span>
            <Link to={`/stocks/${a.stock_id}`} className="font-medium hover:underline">{a.name}</Link>
            <span className={`tabular-nums ${changeColor(a.return_pct)}`}>{fmtPct(a.return_pct)}</span>
            <span className="truncate text-xs text-muted">{a.signals.join("、")}</span>
          </li>
        ))}
      </ul>
    ),
  },
  recommendations: {
    title: "🎯 進場推薦", to: "/recommendations",
    render: (d) => (
      <>
        {/* 進場推薦單純化（2026-08-26）：入口只呈現波段軌，長線檔數與長線列就地隱藏（API 仍回雙軌） */}
        <div className="mb-2 text-sm text-muted">波段 <span className="font-semibold text-gray-200">{d.reco_wave_count}</span> 檔</div>
        <ul className="flex flex-col gap-1">
          {d.reco_top.filter((r) => r.track === "wave").map((r) => (
            <li key={`${r.stock_id}-${r.track}`} className="flex items-center justify-between text-sm">
              <Link to={`/stocks/${r.stock_id}`} className="hover:underline">
                <span className="mr-1 rounded bg-sky-900/60 px-1 text-xs text-sky-300">{TRACK_LABELS[r.track]}</span>{r.name}
              </Link>
              <span className={`tabular-nums ${scoreColor(r.total_score)}`}>{r.total_score?.toFixed(0)}</span>
            </li>
          ))}
        </ul>
      </>
    ),
  },
  sectors: {
    title: "📊 類股強弱", to: "/sectors",
    render: (d) => (
      <ul className="flex flex-col gap-1">
        {d.sectors_top.map((s) => (
          <li key={s.id} className="flex items-center justify-between text-sm">
            <Link to={`/sectors/${s.id}`} className="hover:underline">{s.name}</Link>
            <span className="flex items-center gap-2">
              <span className={`text-xs ${trendColor(s.trend_short)}`}>{s.rotation_stage}</span>
              <span className={`tabular-nums ${scoreColor(s.strength_score)}`}>{s.strength_score?.toFixed(0)}</span>
            </span>
          </li>
        ))}
      </ul>
    ),
  },
  events: {
    title: "📰 重要消息", to: "/recommendations",
    render: (d) => d.recent_events.length === 0 ? <p className="text-sm text-muted">近期無重大消息</p> : (
      <ul className="flex flex-col gap-1.5">
        {d.recent_events.map((e, i) => (
          <li key={i} className="flex items-center gap-2 text-sm">
            <span className={`rounded px-1 text-xs ${e.is_risk ? "bg-down/20 text-down" : "bg-panel2 text-muted"}`}>{e.category}</span>
            <Link to={`/stocks/${e.stock_id}`} className="hover:underline">{e.name}</Link>
            <span className="truncate text-xs text-muted">{e.title}</span>
          </li>
        ))}
      </ul>
    ),
  },
};

const ALL_KEYS = Object.keys(WIDGETS);

function SortableWidget({ id, edit, hidden, onToggle, data }: {
  id: string; edit: boolean; hidden: boolean; onToggle: () => void; data: OverviewResponse;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id, disabled: !edit });
  const def = WIDGETS[id];
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 }}
      className={`rounded-xl border bg-panel p-4 ${hidden ? "border-dashed border-edge opacity-50" : "border-edge"}`}>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          {edit && <span {...attributes} {...listeners} className="cursor-grab text-muted">⠿</span>}
          {def.title}
        </h2>
        {edit ? (
          <button onClick={onToggle} className="text-xs text-muted hover:text-gray-200">{hidden ? "顯示" : "隱藏"}</button>
        ) : (
          <Link to={def.to} className="text-xs text-sky-400 hover:underline">查看 →</Link>
        )}
      </div>
      {!hidden && def.render(data)}
    </div>
  );
}

export default function OverviewPage() {
  const { data } = useOverview();
  const { data: fearGreed } = useFearGreed();
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const [edit, setEdit] = useState(false);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const layout = settings?.layout ?? {};
  const order: string[] = useMemo(() => {
    const o = (layout.widgets as string[]) ?? ALL_KEYS;
    return [...o.filter((k) => ALL_KEYS.includes(k)), ...ALL_KEYS.filter((k) => !o.includes(k))];
  }, [layout.widgets]);
  const hidden: string[] = (layout.hidden as string[]) ?? [];

  const save = (next: { widgets?: string[]; hidden?: string[] }) =>
    update.mutate({ key: "layout", partial: { widgets: order, hidden, ...next } });

  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (over && active.id !== over.id) {
      save({ widgets: arrayMove(order, order.indexOf(String(active.id)), order.indexOf(String(over.id))) });
    }
  };
  const toggleHide = (k: string) => save({ hidden: hidden.includes(k) ? hidden.filter((x) => x !== k) : [...hidden, k] });

  if (!data) return <div className="p-6 text-muted">載入中…</div>;
  const m = data.market;
  const visible = edit ? order : order.filter((k) => !hidden.includes(k));

  return (
    <div className="w-full px-6 py-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-xl font-bold">今日總覽</h1>
        <button onClick={() => setEdit((e) => !e)} className="rounded-md bg-panel2 px-3 py-1 text-sm text-gray-300 hover:bg-edge">
          {edit ? "完成" : "自訂版面"}
        </button>
      </div>
      <p className="mb-4 text-sm text-muted">盤後資料：{m.date ?? "—"}</p>

      <div className="mb-5 grid grid-cols-3 gap-3 rounded-xl border border-edge bg-panel p-4 sm:grid-cols-6">
        <Stat label="成交額(億)" value={fmtNum(m.turnover_billion, 0)} />
        <Stat label="上漲" value={String(m.advancers)} color="text-up" />
        <Stat label="下跌" value={String(m.decliners)} color="text-down" />
        <Stat label="外資(張)" value={fmtNum(m.foreign_net, 0)} color={changeColor(m.foreign_net)} />
        <Stat label="投信(張)" value={fmtNum(m.trust_net, 0)} color={changeColor(m.trust_net)} />
        <Stat label="自營(張)" value={fmtNum(m.dealer_net, 0)} color={changeColor(m.dealer_net)} />
      </div>

      {m.pct_above_ma20 != null && (
        <div className="mb-5 rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 text-sm font-semibold">市場廣度 / 分化</div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="站上月線" value={`${m.pct_above_ma20}%`}
              color={m.pct_above_ma20 >= 60 ? "text-up" : m.pct_above_ma20 < 40 ? "text-down" : undefined} />
            <Stat label="站上季線" value={m.pct_above_ma60 == null ? "—" : `${m.pct_above_ma60}%`} />
            <Stat label="外資 買/賣 家數" value={`${m.foreign_buy_count ?? "—"} / ${m.foreign_sell_count ?? "—"}`} />
            <Stat label="投信 買/賣 家數" value={`${m.trust_buy_count ?? "—"} / ${m.trust_sell_count ?? "—"}`}
              hint={m.trust_top10_concentration != null ? `買超前10檔占 ${m.trust_top10_concentration}%${m.trust_top10_concentration > 50 ? "（集中少數）" : ""}` : undefined} />
          </div>
        </div>
      )}

      {fearGreed && fearGreed.score != null && <FearGreedCard data={fearGreed} />}

      {data.market_note && !edit && (
        <div className="mb-5 rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 text-sm font-semibold">🤖 盤勢總結</div>
          <Markdown>{data.market_note}</Markdown>
        </div>
      )}

      <DndContext sensors={sensors} onDragEnd={onDragEnd}>
        <SortableContext items={visible} strategy={verticalListSortingStrategy}>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {visible.map((k) => (
              <SortableWidget key={k} id={k} edit={edit} hidden={hidden.includes(k)} onToggle={() => toggleHide(k)} data={data} />
            ))}
          </div>
        </SortableContext>
      </DndContext>
    </div>
  );
}
