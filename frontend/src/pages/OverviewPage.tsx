import { Link } from "react-router-dom";
import { useOverview } from "../api/client";
import { changeColor, fmtNum, fmtPct, scoreColor, trendColor, TRACK_LABELS } from "../lib/format";

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center">
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${color ?? ""}`}>{value}</div>
    </div>
  );
}

function Widget({ title, to, children }: { title: string; to: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-edge bg-panel p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">{title}</h2>
        <Link to={to} className="text-xs text-sky-400 hover:underline">查看 →</Link>
      </div>
      {children}
    </div>
  );
}

export default function OverviewPage() {
  const { data, isLoading } = useOverview();
  if (isLoading || !data) return <div className="p-6 text-muted">載入中…</div>;
  const m = data.market;

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <h1 className="mb-1 text-xl font-bold">今日總覽</h1>
      <p className="mb-4 text-sm text-muted">盤後資料：{m.date ?? "—"}</p>

      {/* 大盤狀態列 */}
      <div className="mb-5 grid grid-cols-3 gap-3 rounded-xl border border-edge bg-panel p-4 sm:grid-cols-6">
        <Stat label="成交額(億)" value={fmtNum(m.turnover_billion, 0)} />
        <Stat label="上漲" value={String(m.advancers)} color="text-up" />
        <Stat label="下跌" value={String(m.decliners)} color="text-down" />
        <Stat label="外資(張)" value={fmtNum(m.foreign_net, 0)} color={changeColor(m.foreign_net)} />
        <Stat label="投信(張)" value={fmtNum(m.trust_net, 0)} color={changeColor(m.trust_net)} />
        <Stat label="自營(張)" value={fmtNum(m.dealer_net, 0)} color={changeColor(m.dealer_net)} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* 持股提醒（最優先）*/}
        <Widget title="💼 持股提醒" to="/holdings">
          {data.holdings_alerts.length === 0 ? (
            <p className="text-sm text-muted">目前無需處理的持股</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {data.holdings_alerts.map((a) => (
                <li key={a.stock_id} className="flex items-center gap-2 text-sm">
                  <span>{a.light}</span>
                  <Link to={`/stocks/${a.stock_id}`} className="font-medium hover:underline">{a.name}</Link>
                  <span className={`tabular-nums ${changeColor(a.return_pct)}`}>{fmtPct(a.return_pct)}</span>
                  <span className="truncate text-xs text-muted">{a.signals.join("、")}</span>
                </li>
              ))}
            </ul>
          )}
        </Widget>

        {/* 進場推薦 */}
        <Widget title="🎯 進場推薦" to="/recommendations">
          <div className="mb-2 text-sm text-muted">
            波段 <span className="font-semibold text-gray-200">{data.reco_wave_count}</span> 檔 ·
            長線 <span className="font-semibold text-gray-200">{data.reco_long_count}</span> 檔
          </div>
          <ul className="flex flex-col gap-1">
            {data.reco_top.map((r) => (
              <li key={`${r.stock_id}-${r.track}`} className="flex items-center justify-between text-sm">
                <Link to={`/stocks/${r.stock_id}`} className="hover:underline">
                  <span className="mr-1 rounded bg-sky-900/60 px-1 text-xs text-sky-300">{TRACK_LABELS[r.track]}</span>
                  {r.name}
                </Link>
                <span className={`tabular-nums ${scoreColor(r.total_score)}`}>{r.total_score?.toFixed(0)}</span>
              </li>
            ))}
          </ul>
        </Widget>

        {/* 類股強弱 */}
        <Widget title="📊 類股強弱" to="/sectors">
          <ul className="flex flex-col gap-1">
            {data.sectors_top.map((s) => (
              <li key={s.id} className="flex items-center justify-between text-sm">
                <Link to={`/sectors/${s.id}`} className="hover:underline">{s.name}</Link>
                <span className="flex items-center gap-2">
                  <span className={`text-xs ${trendColor(s.trend_short)}`}>{s.rotation_stage}</span>
                  <span className={`tabular-nums ${scoreColor(s.strength_score)}`}>{s.strength_score?.toFixed(0)}</span>
                </span>
              </li>
            ))}
          </ul>
        </Widget>

        {/* 重要消息 */}
        <Widget title="📰 重要消息" to="/recommendations">
          {data.recent_events.length === 0 ? (
            <p className="text-sm text-muted">近期無重大消息</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {data.recent_events.map((e, i) => (
                <li key={i} className="flex items-center gap-2 text-sm">
                  <span className={`rounded px-1 text-xs ${e.is_risk ? "bg-down/20 text-down" : "bg-panel2 text-muted"}`}>{e.category}</span>
                  <Link to={`/stocks/${e.stock_id}`} className="hover:underline">{e.name}</Link>
                  <span className="truncate text-xs text-muted">{e.title}</span>
                </li>
              ))}
            </ul>
          )}
        </Widget>
      </div>
    </div>
  );
}
