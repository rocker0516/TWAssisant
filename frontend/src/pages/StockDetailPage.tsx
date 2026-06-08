import { Link, useParams } from "react-router-dom";
import { useOhlcv, useStockDetail, type ScoreDTO } from "../api/client";
import { KLineChart } from "../components/KLineChart";
import { ReasonChips } from "../components/ReasonChips";
import { ScoreDisplay } from "../components/ScoreDisplay";
import { changeColor, fmtNum, fmtPct, TRACK_LABELS } from "../lib/format";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-edge bg-panel p-4">
      <div className="mb-3 text-sm font-semibold text-gray-200">{title}</div>
      {children}
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
          <ScoreDisplay total={score.total_score} subScores={score.sub_scores} size="lg" />
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

export default function StockDetailPage() {
  const { id } = useParams();
  const { data: d, isLoading, isError, error } = useStockDetail(id);
  const { data: ohlcv } = useOhlcv(id);

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
          <Card title="K 線（日K，疊均線 MA5/20/60）">
            {ohlcv && ohlcv.candles.length > 0 ? (
              <KLineChart candles={ohlcv.candles} />
            ) : (
              <p className="text-muted">無 K 線資料</p>
            )}
          </Card>
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
          </Card>
          <Card title="基本面">
            <Row label="EPS（近4季）" value={fmtNum(d.fundamental?.eps)} />
            <Row label="月營收 YoY" value={fmtPct(d.fundamental?.revenue_yoy)} color={changeColor(d.fundamental?.revenue_yoy)} />
            <Row label="本益比" value={fmtNum(d.fundamental?.pe)} />
            <Row label="股價淨值比" value={fmtNum(d.fundamental?.pb)} />
            <Row label="殖利率" value={fmtPct(d.fundamental?.dividend_yield)} />
          </Card>
        </div>
      </div>
    </div>
  );
}
