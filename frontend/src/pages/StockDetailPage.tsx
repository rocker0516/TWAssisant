import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  useHoldingHistory,
  useLevels,
  useOhlcv,
  useStockDetail,
  type LevelsResponse,
  type ScoreDTO,
  type StockDetail,
} from "../api/client";
import { ConfidenceBadge } from "../components/ConfidenceBadge";
import { HoldingTrendChart } from "../components/HoldingTrendChart";
import { KLineChart } from "../components/KLineChart";
import { Markdown } from "../components/Markdown";
import { ReasonChips } from "../components/ReasonChips";
import { ScoreDisplay } from "../components/ScoreDisplay";
import { changeColor, fmtNum, fmtPct, positionMeta, TRACK_LABELS } from "../lib/format";
import { streamSSE } from "../lib/sse";

function HealthCard({ stockId }: { stockId: string }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setText("");
    setBusy(true);
    try {
      await streamSSE(`/stocks/${stockId}/health`, {}, (c) => setText((t) => t + c));
    } catch {
      setText("（健檢產生失敗，請稍後再試）");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-xl border border-edge bg-panel p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold">🤖 AI 健檢</span>
        <button onClick={run} disabled={busy} className="rounded-md bg-sky-600 px-2.5 py-1 text-xs font-medium disabled:opacity-50">
          {busy ? "產生中…" : text ? "重新產生" : "生成健檢"}
        </button>
      </div>
      {text ? <Markdown>{text}</Markdown> : <p className="text-sm text-muted">點「生成健檢」由 AI 綜合技術/籌碼/基本面/類股解讀（不自動生成、保持數據導向）。</p>}
    </div>
  );
}

function fmtScale(billion: number | null | undefined): string {
  if (billion == null) return "—";
  return billion >= 10000 ? `約 ${(billion / 10000).toFixed(1)} 兆` : `約 ${Math.round(billion)} 億`;
}

function EtfCard({ etf }: { etf: StockDetail["etf"] }) {
  if (!etf) {
    return (
      <Card title="ETF 資料">
        <p className="text-sm text-muted">此 ETF 的基金基本資料未涵蓋（多為債券型 ETF），請以技術與籌碼面為主。</p>
        <p className="mt-1 text-xs text-muted">ETF 無個股月營收／本益比。</p>
      </Card>
    );
  }
  return (
    <Card title="ETF 資料">
      <Row label="類型" value={etf.kind ?? "—"} />
      <Row label="追蹤指數" value={etf.track_index ?? "主動式／未對應指數"} />
      <Row
        label="規模"
        value={
          <>
            {fmtScale(etf.scale_billion)}
            {etf.scale_label ? <span className="ml-1.5 rounded bg-panel2 px-1.5 py-0.5 text-xs text-muted">{etf.scale_label}</span> : null}
          </>
        }
      />
      <Row label="成分地區" value={etf.has_foreign == null ? "—" : etf.has_foreign ? "含國外成分股" : "純國內成分股"} />
      <Row label="上市日期" value={etf.listed_date ?? "—"} />
      <p className="mt-2 text-xs text-muted">規模為發行單位數×收盤價之估算值；ETF 無個股月營收/本益比。</p>
    </Card>
  );
}

function Card({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-edge bg-panel p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-gray-200">{title}</div>
        {action}
      </div>
      {children}
    </div>
  );
}

// K 線時間範圍（交易日數）。「全部」涵蓋到 2020 起的回補歷史。
const KLINE_RANGES: { label: string; days: number }[] = [
  { label: "3月", days: 60 },
  { label: "6月", days: 120 },
  { label: "1年", days: 240 },
  { label: "3年", days: 720 },
  { label: "全部", days: 3000 },
];

function RangeSelector({
  value,
  onChange,
}: {
  value: number;
  onChange: (days: number) => void;
}) {
  return (
    <div className="flex gap-1">
      {KLINE_RANGES.map((r) => (
        <button
          key={r.days}
          onClick={() => onChange(r.days)}
          className={`rounded px-2 py-0.5 text-xs ${
            value === r.days ? "bg-sky-600 text-white" : "bg-panel2 text-muted hover:text-gray-200"
          }`}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div className="flex items-center justify-between py-1 text-sm">
      <span className="text-muted">{label}</span>
      <span className={`tabular-nums ${color ?? ""}`}>{value}</span>
    </div>
  );
}

function TrackPanel({ track, score }: { track: string; score: ScoreDTO | null }) {
  return (
    <div className="rounded-lg border border-edge bg-panel2 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium">{TRACK_LABELS[track]}軌</span>
        {score?.passed ? (
          <span className="rounded bg-up/20 px-1.5 py-0.5 text-xs text-up">達門檻</span>
        ) : (
          <span className="rounded bg-panel px-1.5 py-0.5 text-xs text-muted">未達門檻</span>
        )}
      </div>
      {score ? (
        <>
          <div className="flex items-center justify-between">
            <ScoreDisplay total={score.total_score} subScores={score.sub_scores} size="lg" />
            <ConfidenceBadge confidence={score.confidence} coverage={score.coverage} stability={score.stability} size="lg" />
          </div>
          {(() => {
            const pm = positionMeta(score.sub_scores?.position);
            return pm ? (
              <div className="mt-2 text-sm">
                <span className="text-muted">進場位階：</span>
                <span className={`font-medium ${pm.color}`}>{pm.label}</span>
                <span className="ml-1 text-xs text-muted">（相對低＝回檔買點，偏高＝已噴出）</span>
              </div>
            ) : null;
          })()}
          <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div>
              <div className="text-xs text-muted">買進區間</div>
              <div className="tabular-nums">{fmtNum(score.buy_low)} ~ {fmtNum(score.buy_high)}</div>
            </div>
            <div>
              <div className="text-xs text-muted">參考停損</div>
              <div className="tabular-nums">
                {fmtNum(score.stop_loss)} <span className="text-down">({fmtPct(score.loss_pct)})</span>
              </div>
            </div>
          </div>
          <div className="mt-3">
            <ReasonChips reasons={score.reasons} />
          </div>
        </>
      ) : (
        <p className="text-sm text-muted">無評分資料</p>
      )}
    </div>
  );
}

function LevelsCard({ levels }: { levels: LevelsResponse }) {
  // 壓力由高到低、支撐由高到低，現價夾在中間，貼近 K 線視覺由上而下
  const rows = [
    ...[...levels.resistances].sort((a, b) => b.price - a.price),
    ...[...levels.supports].sort((a, b) => b.price - a.price),
  ];
  return (
    <Card title="支撐 / 壓力位（量價客觀計算，非預測）">
      <div className="flex flex-col gap-1.5">
        {rows.map((lv, i) => {
          const isSup = lv.kind === "support";
          return (
            <div key={i} className="flex items-center gap-3 text-sm">
              <span
                className={`w-12 rounded px-1.5 py-0.5 text-center text-xs ${
                  isSup ? "bg-up/15 text-up" : "bg-down/15 text-down"
                }`}
              >
                {isSup ? "支撐" : "壓力"}
              </span>
              <span className="w-20 font-semibold tabular-nums">{fmtNum(lv.price)}</span>
              <span className="w-16 text-xs tabular-nums text-muted">
                {lv.distance_pct > 0 ? "+" : ""}
                {lv.distance_pct}%
              </span>
              <div className="h-1.5 w-16 overflow-hidden rounded bg-panel2">
                <div
                  className={isSup ? "h-full bg-up" : "h-full bg-down"}
                  style={{ width: `${lv.strength}%` }}
                />
              </div>
              <span className="flex flex-1 flex-wrap gap-1">
                {lv.methods.map((m, j) => (
                  <span key={j} className="rounded bg-panel2 px-1.5 py-0.5 text-[11px] text-muted">
                    {m}
                  </span>
                ))}
              </span>
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        強度＝均線／前低／套牢量多來源重疊程度，僅供參考，非買賣建議。
      </p>
    </Card>
  );
}

export default function StockDetailPage() {
  const { id } = useParams();
  const { data: d, isLoading, isError, error } = useStockDetail(id);
  const [klineDays, setKlineDays] = useState(120);
  const { data: ohlcv } = useOhlcv(id, klineDays);
  const { data: levels } = useLevels(id);
  const { data: holdingHistory } = useHoldingHistory(id);

  if (isLoading) return <div className="p-6 text-muted">載入中…</div>;
  if (isError) return <div className="p-6 text-down">載入失敗：{(error as Error).message}</div>;
  if (!d) return null;

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <Link to="/recommendations" className="text-sm text-sky-400 hover:underline">
        ← 進場推薦
      </Link>

      {/* 頭部 */}
      <div className="mb-5 mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{d.name}</h1>
            <span className="text-lg text-muted">{d.stock_id}</span>
            <span className="rounded bg-panel2 px-2 py-0.5 text-xs text-muted">{d.sector_name ?? "—"}</span>
          </div>
          <div className="mt-1 text-xs text-muted">{d.market ?? ""}　盤後 {d.date ?? "—"}</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold tabular-nums">{fmtNum(d.close)}</div>
          <div className={`text-sm tabular-nums ${changeColor(d.change_pct)}`}>
            {fmtNum(d.change)}（{fmtPct(d.change_pct)}）
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_320px]">
        {/* 左主欄 */}
        <div className="flex flex-col gap-5">
          <Card
            title="K 線（日K，疊均線 MA5/20/60 + 支撐/壓力）"
            action={<RangeSelector value={klineDays} onChange={setKlineDays} />}
          >
            {ohlcv && ohlcv.candles.length > 0 ? (
              <KLineChart
                candles={ohlcv.candles}
                levels={[...(levels?.supports ?? []), ...(levels?.resistances ?? [])]}
              />
            ) : (
              <p className="text-muted">無 K 線資料</p>
            )}
          </Card>
          {levels && (levels.supports.length > 0 || levels.resistances.length > 0) && (
            <LevelsCard levels={levels} />
          )}
          {!d.is_etf && holdingHistory && (holdingHistory.points.length >= 2 || holdingHistory.backfilling) && (
            <Card title="集保股權分散趨勢（大戶 vs 散戶，週）">
              {holdingHistory.points.length >= 2 ? (
                <>
                  <HoldingTrendChart points={holdingHistory.points} />
                  <p className="mt-1 text-xs text-muted">
                    大戶／千張持股占比上升＝籌碼集中（偏多）；散戶占比上升＝籌碼鬆動。重點看走向，非單週絕對值。
                    {holdingHistory.backfilling && "　歷史回補中…"}
                  </p>
                </>
              ) : (
                <p className="py-6 text-center text-sm text-muted">正在背景回補近一年集保歷史，請稍候…（約 1 分鐘，會自動更新）</p>
              )}
            </Card>
          )}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <TrackPanel track="wave" score={d.scores["wave"] ?? null} />
            <TrackPanel track="long" score={d.scores["long"] ?? null} />
          </div>
        </div>

        {/* 右側欄 */}
        <div className="flex flex-col gap-5">
          <Card title="籌碼">
            <Row label="外資（張）" value={fmtNum(d.chip?.foreign_net, 0)} color={changeColor(d.chip?.foreign_net)} />
            <Row label="投信（張）" value={fmtNum(d.chip?.trust_net, 0)} color={changeColor(d.chip?.trust_net)} />
            <Row label="自營商（張）" value={fmtNum(d.chip?.dealer_net, 0)} color={changeColor(d.chip?.dealer_net)} />
            <Row label="三大法人合計" value={fmtNum(d.chip?.total_net, 0)} color={changeColor(d.chip?.total_net)} />
            <div className="my-2 border-t border-edge" />
            <Row label="融資餘額（張）" value={fmtNum(d.chip?.margin_balance, 0)} />
            <Row label="融券餘額（張）" value={fmtNum(d.chip?.short_balance, 0)} />
            {d.chip?.big_pct != null && (
              <>
                <div className="my-2 border-t border-edge" />
                <div className="mb-1 text-xs text-muted">
                  集保股權分散（週{d.chip.holding_date ? ` · ${d.chip.holding_date}` : ""}）
                </div>
                <Row label="大戶持股（≥400張）" value={`${fmtNum(d.chip.big_pct, 1)}%`} />
                <Row label="千張大戶（≥1000張）" value={d.chip.over1000_pct != null ? `${fmtNum(d.chip.over1000_pct, 1)}%` : "—"} />
                <Row label="散戶持股（<10張）" value={d.chip.small_pct != null ? `${fmtNum(d.chip.small_pct, 1)}%` : "—"} />
                {d.chip.big_trend != null && (
                  <Row
                    label="大戶近月變化"
                    value={`${d.chip.big_trend > 0 ? "+" : ""}${fmtNum(d.chip.big_trend, 1)} pp`}
                    color={changeColor(d.chip.big_trend)}
                  />
                )}
                <Row label="股東人數" value={fmtNum(d.chip.holders, 0)} />
              </>
            )}
          </Card>
          {d.is_etf ? (
            <EtfCard etf={d.etf} />
          ) : (
            <Card title="基本面">
              <Row label="EPS（近4季）" value={fmtNum(d.fundamental?.eps)} />
              <Row label="月營收 YoY" value={fmtPct(d.fundamental?.revenue_yoy)} color={changeColor(d.fundamental?.revenue_yoy)} />
              <Row label="本益比" value={fmtNum(d.fundamental?.pe)} />
              <Row label="股價淨值比" value={fmtNum(d.fundamental?.pb)} />
              <Row label="殖利率" value={fmtPct(d.fundamental?.dividend_yield)} />
            </Card>
          )}
          <Card title="重要消息">
            {d.news_digest && (
              <div className="mb-3 rounded-lg border border-edge bg-panel2/40 p-3">
                <div className="mb-1 text-xs font-semibold text-muted">🤖 近期消息重點</div>
                <Markdown>{d.news_digest}</Markdown>
              </div>
            )}
            {d.events.length === 0 ? (
              <p className="text-sm text-muted">近期無重大訊息</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {d.events.map((e, i) => (
                  <li key={i} className="text-sm">
                    <div className="flex items-center gap-2">
                      <span className={`rounded px-1.5 py-0.5 text-xs ${e.is_risk ? "bg-down/20 text-down" : e.category === "題材" ? "bg-up/20 text-up" : "bg-panel2 text-muted"}`}>
                        {e.category ?? "—"}
                      </span>
                      <span className="text-xs text-muted">{e.date}</span>
                    </div>
                    <div className="mt-0.5 text-gray-200">{e.title}</div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <HealthCard stockId={d.stock_id} />
        </div>
      </div>
    </div>
  );
}
