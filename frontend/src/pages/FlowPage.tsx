import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ACTOR_LABELS,
  useFlowRelation,
  useFlowStocks,
  useMarketFlow,
  useSectorFlow,
  useSectorRotation,
  type Actor,
} from "../api/client";
import { MarketFlowChart } from "../components/MarketFlowChart";
import { SectorRotationChart, type FlowMetric } from "../components/SectorRotationChart";
import { changeColor, fmtNum, fmtPct } from "../lib/format";

const ACTORS: Actor[] = ["total", "foreign", "trust", "dealer"];
const RANGES: { label: string; days: number }[] = [
  { label: "季", days: 60 },
  { label: "半年", days: 120 },
  { label: "一年", days: 250 },
  { label: "全部", days: 3000 },
];
const PHASE_COLOR: Record<string, string> = {
  持續買超循環: "text-up",
  由賣轉買: "text-up",
  持續賣超循環: "text-down",
  由買轉賣: "text-down",
  區間整理: "text-muted",
};

// 個股榜排序選項
const SORTS: { key: string; label: string }[] = [
  { key: "foreign_cum20", label: "外資累積" },
  { key: "trust_cum20", label: "投信累積" },
  { key: "dealer_cum20", label: "自營累積" },
  { key: "total_cum20", label: "法人合計" },
  { key: "consec_days", label: "連買天數" },
  { key: "big_trend", label: "大戶增持" },
  { key: "holders_change", label: "股東減少" },
  { key: "sbl_chg20", label: "借券增加" },
  { key: "dt_ratio5", label: "當沖比" },
];

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg px-3 py-1 text-sm transition ${active ? "bg-sky-900/60 text-sky-200" : "bg-panel2 text-gray-400 hover:text-gray-200"}`}
    >
      {children}
    </button>
  );
}

export default function FlowPage() {
  const navigate = useNavigate();
  const [actor, setActor] = useState<Actor>("total");
  const [days, setDays] = useState(250);
  const [sort, setSort] = useState("total_cum20");
  const [metric, setMetric] = useState<FlowMetric>("strength");

  const market = useMarketFlow(days);
  const sectors = useSectorFlow(20);
  const rotation = useSectorRotation(actor, 6);
  const stocks = useFlowStocks(sort, 50);
  const relation = useFlowRelation();

  const a = market.data?.actors?.[actor];
  const phase = a?.phase ?? "—";
  const cumKey = `${actor}_cum` as "total_cum" | "foreign_cum" | "trust_cum" | "dealer_cum";

  // 類股法人資金流向：依選定 actor 的近20日累計排序，取流入/流出前段
  const sItems = (sectors.data?.items ?? []).slice().sort((x, y) => (y[cumKey] ?? 0) - (x[cumKey] ?? 0));
  const inflow = sItems.filter((s) => (s[cumKey] ?? 0) > 0).slice(0, 8);
  const outflow = sItems.filter((s) => (s[cumKey] ?? 0) < 0).slice(-8).reverse();
  const maxAbs = Math.max(1, ...sItems.map((s) => Math.abs(s[cumKey] ?? 0)));

  const rel = a?.relation; // 法人累積 vs 指數（市場層）
  const stockIC = relation.data?.actors?.[actor]; // 個股 法人累積→未來報酬

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <div className="mb-4">
        <h1 className="text-xl font-bold">籌碼動向</h1>
        <p className="text-sm text-muted">
          法人 + 大戶散戶看市場/個股方向。以「整個週期的累積」為主視角，非看當天。市場口徑＝上市三大法人總表（億元）。
        </p>
      </div>

      {/* ── 法人別切換 ── */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">法人別</span>
        {ACTORS.map((x) => (
          <Chip key={x} active={actor === x} onClick={() => setActor(x)}>
            {ACTOR_LABELS[x]}
          </Chip>
        ))}
        <span className="ml-4 text-xs text-muted">區間</span>
        {RANGES.map((r) => (
          <Chip key={r.days} active={days === r.days} onClick={() => setDays(r.days)}>
            {r.label}
          </Chip>
        ))}
      </div>

      {/* ── 上半：市場法人週期 ── */}
      <section className="mb-6 rounded-xl border border-edge bg-panel p-4">
        <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="text-sm text-muted">{ACTOR_LABELS[actor]}週期</span>
          <span className={`text-lg font-bold ${PHASE_COLOR[phase] ?? "text-gray-200"}`}>{phase}</span>
          {a && (
            <span className="text-sm text-muted">
              累積{" "}
              <b className={changeColor(a.cum120 ?? 0)}>{fmtNum(a.cum120, 0)}</b> 億（近半年）／{" "}
              <b className={changeColor(a.cum20 ?? 0)}>{fmtNum(a.cum20, 0)}</b> 億（近20日）
              {a.consec_days !== 0 && (
                <>
                  {" "}·{" "}
                  <b className={a.consec_days > 0 ? "text-up" : "text-down"}>
                    {a.consec_days > 0 ? "連買" : "連賣"} {Math.abs(a.consec_days)} 天
                  </b>
                </>
              )}
            </span>
          )}
        </div>

        {market.isLoading && <p className="py-8 text-center text-muted">載入中…</p>}
        {market.data && <MarketFlowChart data={market.data} actor={actor} />}

        {/* 量化關係卡 */}
        {(rel || stockIC) && (
          <div className="mt-3 rounded-lg bg-panel2 px-3 py-2 text-sm text-gray-300">
            <span className="mr-2 text-xs text-muted">法人 vs 漲跌</span>
            {rel ? (
              <>
                {ACTOR_LABELS[actor]}累積買超期間，指數未來 {rel.h} 日平均{" "}
                <b className={changeColor(rel.avg_ret_pos ?? 0)}>{fmtPct(rel.avg_ret_pos)}</b>
                {rel.winrate_pos != null && <> 、勝率 <b>{Math.round(rel.winrate_pos * 100)}%</b></>}
                {rel.corr != null && <>（相關 {rel.corr}）</>}
              </>
            ) : (
              <span className="text-muted">市場關係樣本不足</span>
            )}
            {stockIC?.ic != null && (
              <span className="ml-1 text-muted">
                ；個股法人累積→未來報酬 IC <b className="text-gray-200">{stockIC.ic}</b>
                {stockIC.winrate_pos != null && <>（勝率 {Math.round(stockIC.winrate_pos * 100)}%）</>}
              </span>
            )}
          </div>
        )}
        {/* 期貨籌碼儀表（TAIFEX） */}
        {market.data?.derivatives && (
          <div className="mt-3 rounded-lg bg-panel2 px-3 py-2 text-sm text-gray-300">
            <span className="mr-2 text-xs text-muted">期貨籌碼</span>
            外資台指期淨部位{" "}
            <b className={changeColor(market.data.derivatives.foreign_oi_latest ?? 0)}>
              {fmtNum(market.data.derivatives.foreign_oi_latest, 0)}
            </b>{" "}
            口
            {market.data.derivatives.foreign_oi_chg20 != null && (
              <>
                （近20日{" "}
                <b className={changeColor(market.data.derivatives.foreign_oi_chg20)}>
                  {market.data.derivatives.foreign_oi_chg20 > 0 ? "+" : ""}
                  {fmtNum(market.data.derivatives.foreign_oi_chg20, 0)}
                </b>{" "}
                口）
              </>
            )}
            {market.data.derivatives.pc_oi_ratio.length > 0 && (
              <>
                {" "}· 選擇權 P/C（未平倉）{" "}
                <b>{fmtNum(market.data.derivatives.pc_oi_ratio[market.data.derivatives.pc_oi_ratio.length - 1], 1)}%</b>
              </>
            )}
            {market.data.derivatives.divergence && (
              <div className="mt-1 text-xs text-muted">
                {market.data.derivatives.divergence}。期現對照為觀察儀表（未經回測驗證，非進出訊號）。
              </div>
            )}
          </div>
        )}
      </section>

      {/* ── 中段：類股法人資金流向（輪動象限圖 + 精確排名）── */}
      <section className="mb-6">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold">類股{ACTOR_LABELS[actor]}資金輪動</h2>
          <span className="text-xs text-muted">X=強度 Y=加速度 · 看資金往哪輪動</span>
          <span className="ml-auto text-xs text-muted">口徑</span>
          <Chip active={metric === "strength"} onClick={() => setMetric("strength")}>強度（佔成交比）</Chip>
          <Chip active={metric === "absolute"} onClick={() => setMetric("absolute")}>絕對張數</Chip>
        </div>
        <div className="rounded-xl border border-edge bg-panel p-3">
          {rotation.isLoading && <p className="py-10 text-center text-muted">載入中…</p>}
          {rotation.data && <SectorRotationChart data={rotation.data} metric={metric} />}
        </div>

        <h3 className="mb-2 mt-4 text-sm font-semibold text-muted">
          精確排名 <span className="font-normal">近20日{ACTOR_LABELS[actor]}淨買超累計（張）</span>
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <FlowColumn title="資金流入" tone="up" items={inflow} cumKey={cumKey} maxAbs={maxAbs} onSelect={(id) => navigate(`/sectors/${id}`)} />
          <FlowColumn title="資金流出" tone="down" items={outflow} cumKey={cumKey} maxAbs={maxAbs} onSelect={(id) => navigate(`/sectors/${id}`)} />
        </div>
      </section>

      {/* ── 下半：個股籌碼榜 ── */}
      <section>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold">個股籌碼榜</h2>
          <span className="text-xs text-muted">排序</span>
          {SORTS.map((s) => (
            <Chip key={s.key} active={sort === s.key} onClick={() => setSort(s.key)}>
              {s.label}
            </Chip>
          ))}
        </div>
        <div className="overflow-x-auto rounded-xl border border-edge">
          <table className="w-full text-sm">
            <thead className="bg-panel2 text-xs text-muted">
              <tr>
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-right">外資20日</th>
                <th className="px-3 py-2 text-right">投信20日</th>
                <th className="px-3 py-2 text-right">自營20日</th>
                <th className="px-3 py-2 text-right">合計60日</th>
                <th className="px-3 py-2 text-center">連買</th>
                <th className="px-3 py-2 text-right">大戶趨勢</th>
                <th className="px-3 py-2 text-right">散戶趨勢</th>
                <th className="px-3 py-2 text-right">股東增減</th>
                <th className="px-3 py-2 text-right">借券20日</th>
                <th className="px-3 py-2 text-right">當沖%</th>
                <th className="px-3 py-2 text-right">漲跌</th>
              </tr>
            </thead>
            <tbody>
              {stocks.data?.items.map((s) => (
                <tr
                  key={s.stock_id}
                  onClick={() => navigate(`/stocks/${s.stock_id}`)}
                  className="cursor-pointer border-t border-edge hover:bg-panel/60"
                >
                  <td className="px-3 py-2">
                    <div className="font-medium">{s.name}</div>
                    <div className="text-xs text-muted">{s.stock_id}　{s.sector_name ?? ""}</div>
                  </td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.foreign_cum20)}`}>{fmtNum(s.foreign_cum20, 0)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.trust_cum20)}`}>{fmtNum(s.trust_cum20, 0)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.dealer_cum20)}`}>{fmtNum(s.dealer_cum20, 0)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.total_cum60)}`}>{fmtNum(s.total_cum60, 0)}</td>
                  <td className={`px-3 py-2 text-center tabular-nums ${s.consec_days > 0 ? "text-up" : s.consec_days < 0 ? "text-down" : "text-muted"}`}>
                    {s.consec_days === 0 ? "—" : `${s.consec_days > 0 ? "買" : "賣"}${Math.abs(s.consec_days)}`}
                  </td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.big_trend)}`}>{s.big_trend == null ? "—" : fmtPct(s.big_trend)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.small_trend != null ? -s.small_trend : null)}`}>{s.small_trend == null ? "—" : fmtPct(s.small_trend)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.holders_change != null ? -s.holders_change : null)}`}>{s.holders_change == null ? "—" : fmtPct(s.holders_change)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.sbl_chg20 != null ? -s.sbl_chg20 : null)}`}>{s.sbl_chg20 == null ? "—" : fmtNum(s.sbl_chg20, 0)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-300">{s.dt_ratio5 == null ? "—" : `${s.dt_ratio5}%`}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${changeColor(s.change_pct)}`}>{fmtPct(s.change_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {stocks.data && stocks.data.items.length === 0 && (
            <p className="px-3 py-6 text-center text-sm text-muted">尚無資料，請先載入籌碼資料。</p>
          )}
        </div>
        <p className="mt-2 text-xs text-muted">
          數字＝近 N 日法人淨買超累計（張，紅買綠賣）；大戶/散戶趨勢＝集保占比近 ~8 週變化（個百分點），散戶/股東以「減少」為偏多上色。借券20日＝借券賣出餘額近20日增減（張，減少＝空方回補偏多上色）；當沖%＝近5日當沖占成交量比（上市限定，高=浮額多）。點列看個股詳情。
        </p>
      </section>
    </div>
  );
}

function FlowColumn({
  title,
  tone,
  items,
  cumKey,
  maxAbs,
  onSelect,
}: {
  title: string;
  tone: "up" | "down";
  items: { id: number; name: string; constituents?: number | null; [k: string]: number | string | null | undefined }[];
  cumKey: string;
  maxAbs: number;
  onSelect: (id: number) => void;
}) {
  const barColor = tone === "up" ? "bg-rose-500/70" : "bg-emerald-500/70";
  return (
    <div className="rounded-xl border border-edge bg-panel p-3">
      <div className={`mb-2 text-sm font-semibold ${tone === "up" ? "text-up" : "text-down"}`}>{title}</div>
      {items.length === 0 && <p className="py-3 text-center text-xs text-muted">無</p>}
      <div className="flex flex-col gap-1.5">
        {items.map((s) => {
          const v = (s[cumKey] as number) ?? 0;
          const w = Math.min(100, (Math.abs(v) / maxAbs) * 100);
          return (
            <div key={s.id} onClick={() => onSelect(s.id)} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-panel2">
              <span className="w-20 shrink-0 truncate text-sm">{s.name}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-panel2">
                <div className={`h-full rounded-full ${barColor}`} style={{ width: `${w}%` }} />
              </div>
              <span className={`w-20 shrink-0 text-right text-xs tabular-nums ${tone === "up" ? "text-up" : "text-down"}`}>{fmtNum(v, 0)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
