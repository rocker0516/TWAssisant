import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  useAttention,
  useChipHistory,
  useDividends,
  useFundamentalHistory,
  useHoldingHistory,
  useHoldings,
  useLevels,
  useIndustryChain,
  useOhlcv,
  usePbRiver,
  usePeRiver,
  useRecommendationMarks,
  useFinancialStatements,
  useStockDetail,
  useTargetPrice,
  useTechSummary,
  type AttentionResponse,
  type DividendsResponse,
  type FinancialStatementsResponse,
  type FundamentalHistoryResponse,
  type HoldingItem,
  type LevelsResponse,
  type ScoreDTO,
  type StockDetail,
  type TechSummaryResponse,
} from "../api/client";
import { ChipTrendChart } from "../components/ChipTrendChart";
import { FundamentalTrendChart } from "../components/FundamentalTrendChart";
import { PeRiverChart } from "../components/PeRiverChart";
import { ConfidenceBadge } from "../components/ConfidenceBadge";
import { NewHoldingForm, TxnForm } from "../components/HoldingForms";
import { HoldingTrendChart } from "../components/HoldingTrendChart";
import { KLineChart } from "../components/KLineChart";
import { Markdown } from "../components/Markdown";
import { Modal } from "../components/Modal";
import { ReasonChips } from "../components/ReasonChips";
import { ScoreDisplay } from "../components/ScoreDisplay";
import { TargetPriceCard } from "../components/TargetPriceCard";
import { changeColor, fmtNum, fmtPct, positionMeta, scoreColor, TRACK_LABELS, trendColor } from "../lib/format";
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

// ───── 買進 / 加碼 / 賣出（與「我的持股」共用後端與表單）─────
function BuySellBar({ stockId, name, close }: { stockId: string; name: string; close: number | null | undefined }) {
  const { data } = useHoldings("open");
  const [modal, setModal] = useState<{ kind: "new" | "add" | "sell"; holding?: HoldingItem } | null>(null);
  // 同一檔可能在波段／長線兩軌各有部位；操作以第一筆為主，其餘請至「我的持股」頁處理
  const held = data?.items.filter((h) => h.stock_id === stockId) ?? [];
  const primary = held[0];
  const totalShares = held.reduce((s, h) => s + h.shares, 0);

  return (
    <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
      {primary ? (
        <>
          <span className="text-sm text-muted">
            持有 {totalShares} 張 · 均價 {fmtNum(primary.avg_cost)}
            {held.length > 1 && <span className="ml-1 text-xs">（{held.length} 筆部位）</span>}
          </span>
          <button onClick={() => setModal({ kind: "add", holding: primary })} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium">
            加碼
          </button>
          <button onClick={() => setModal({ kind: "sell", holding: primary })} className="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium">
            賣出
          </button>
        </>
      ) : (
        <button onClick={() => setModal({ kind: "new" })} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium">
          + 買進建倉
        </button>
      )}

      <Modal title={`買進 ${name}`} open={modal?.kind === "new"} onClose={() => setModal(null)}>
        <NewHoldingForm onDone={() => setModal(null)} defaultStockId={stockId} defaultPrice={close} lockStock />
      </Modal>
      <Modal title={`加碼 ${name}`} open={modal?.kind === "add"} onClose={() => setModal(null)}>
        {modal?.holding && <TxnForm holding={modal.holding} type="add" onDone={() => setModal(null)} />}
      </Modal>
      <Modal title={`賣出 ${name}`} open={modal?.kind === "sell"} onClose={() => setModal(null)}>
        {modal?.holding && <TxnForm holding={modal.holding} type="sell" onDone={() => setModal(null)} />}
      </Modal>
    </div>
  );
}

function fmtScale(billion: number | null | undefined): string {
  if (billion == null) return "—";
  return billion >= 10000 ? `約 ${(billion / 10000).toFixed(1)} 兆` : `約 ${Math.round(billion)} 億`;
}

function ProfileCard({ profile, market }: { profile: NonNullable<StockDetail["profile"]>; market: string | null | undefined }) {
  return (
    <Card title="基本資料">
      <Row label="產業別" value={profile.industry ?? "—"} />
      <Row label="董事長" value={profile.chairman ?? "—"} />
      <Row label="總經理" value={profile.president ?? "—"} />
      <Row label="股本" value={profile.capital_billion != null ? `${fmtNum(profile.capital_billion, 1)} 億` : "—"} />
      <Row label="市值（估）" value={fmtScale(profile.market_cap_billion)} />
      <Row label="成立日期" value={profile.established_date ?? "—"} />
      <Row label={market === "上櫃" ? "上櫃日期" : "上市日期"} value={profile.listed_date ?? "—"} />
      {profile.website && (
        <Row
          label="公司網站"
          value={
            <a href={profile.website.startsWith("http") ? profile.website : `https://${profile.website}`} target="_blank" rel="noreferrer" className="text-sky-400 hover:underline">
              前往 ↗
            </a>
          }
        />
      )}
      <p className="mt-2 text-xs text-muted">市值＝已發行普通股數 × 收盤價之估算值。</p>
    </Card>
  );
}

function SectorBriefCard({ brief }: { brief: NonNullable<StockDetail["sector_brief"]> }) {
  return (
    <Card
      title="所屬類股"
      action={
        <Link to={`/sectors/${brief.sector_id}`} className="text-xs text-sky-400 hover:underline">
          {brief.name} 詳情 →
        </Link>
      }
    >
      <div className="flex items-center gap-3">
        <span className={`text-2xl font-bold tabular-nums ${scoreColor(brief.strength_score)}`}>
          {brief.strength_score?.toFixed(0) ?? "—"}
        </span>
        <div className="text-sm">
          <span className={trendColor(brief.trend_short)}>{brief.trend_short ?? "—"}</span>
          <span className="mx-1 text-muted">/</span>
          <span className={trendColor(brief.trend_long)}>{brief.trend_long ?? "—"}</span>
          <span className="ml-2 rounded bg-panel2 px-1.5 py-0.5 text-xs text-gray-300">{brief.rotation_stage ?? "—"}</span>
        </div>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-4 text-sm text-muted">
        <div>5日動能 <span className={changeColor(brief.momentum_5)}>{fmtPct(brief.momentum_5)}</span></div>
        <div>20日動能 <span className={changeColor(brief.momentum_20)}>{fmtPct(brief.momentum_20)}</span></div>
        <div className="col-span-2">法人5日 <span className={changeColor(brief.foreign_net)}>{fmtNum(brief.foreign_net, 0)} 張</span></div>
      </div>
      <p className="mt-2 text-[11px] text-muted">類股強弱/輪動影響長線軌評分之類股修正。</p>
    </Card>
  );
}

function DividendCard({ data }: { data: DividendsResponse }) {
  if (data.entries.length === 0) return null;
  return (
    <Card title="股利政策">
      {data.cash_12m != null && (
        <div className="mb-2 rounded-lg bg-panel2/60 px-3 py-2 text-sm">
          近 12 個月現金股利 <b className="tabular-nums">{fmtNum(data.cash_12m)}</b> 元
          {data.yield_12m != null && (
            <span className="text-muted">（現金殖利率 {fmtNum(data.yield_12m)}%）</span>
          )}
        </div>
      )}
      <div className="mb-1 grid grid-cols-[1fr_3.5rem_5rem_3.5rem] gap-1 text-xs text-muted">
        <span>期別</span>
        <span className="text-right">現金</span>
        <span className="text-right">除息日</span>
        <span className="text-right">填息</span>
      </div>
      <div className="flex flex-col">
        {data.entries.slice(0, 8).map((e, i) => (
          <div key={i} className="grid grid-cols-[1fr_3.5rem_5rem_3.5rem] gap-1 py-0.5 text-sm">
            <span className="truncate text-muted">{e.period}</span>
            <span className="text-right tabular-nums">{e.cash ? fmtNum(e.cash) : "—"}</span>
            <span className="text-right text-xs tabular-nums text-muted">{e.cash_ex_date?.slice(2) ?? "—"}</span>
            <span className="text-right text-xs tabular-nums">
              {e.filled == null ? "—" : e.filled ? <span className="text-up">{e.fill_days}日</span> : <span className="text-down">未填</span>}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        現金/股票股利含盈餘與公積分配；填息＝收盤回到除息前價位所需交易日數。
      </p>
    </Card>
  );
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

function MarginRow({ label, value, qoq }: { label: string; value: number | null | undefined; qoq: number | null | undefined }) {
  return (
    <Row
      label={label}
      value={
        <>
          {value != null ? `${fmtNum(value, 1)}%` : "—"}
          {qoq != null && qoq !== 0 && (
            <span className={`ml-1.5 text-xs ${changeColor(qoq)}`}>
              {qoq > 0 ? "▲" : "▼"}{fmtNum(Math.abs(qoq), 1)}pp
            </span>
          )}
        </>
      }
    />
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

// ───── 技術指標摘要（KD/MACD/乖離/Beta/52週位置/波動）─────
function TechSummaryCard({ t }: { t: TechSummaryResponse }) {
  const kdSignal =
    t.kd_k != null && t.kd_d != null ? (t.kd_k > t.kd_d ? "黃金交叉偏多" : "死亡交叉偏空") : null;
  return (
    <Card title="技術指標摘要">
      {t.kd_k != null && (
        <Row
          label="KD（9日）"
          value={
            <>
              K {fmtNum(t.kd_k, 1)} / D {fmtNum(t.kd_d, 1)}
              {kdSignal && (
                <span className={`ml-1.5 text-xs ${t.kd_k! > t.kd_d! ? "text-up" : "text-down"}`}>{kdSignal}</span>
              )}
            </>
          }
        />
      )}
      {t.macd_hist != null && (
        <Row
          label="MACD 柱"
          value={fmtNum(t.macd_hist, 2)}
          color={changeColor(t.macd_hist)}
        />
      )}
      {t.bias_20 != null && <Row label="20日乖離" value={fmtPct(t.bias_20)} color={changeColor(t.bias_20)} />}
      {t.bias_60 != null && <Row label="60日乖離" value={fmtPct(t.bias_60)} color={changeColor(t.bias_60)} />}
      <div className="my-2 border-t border-edge" />
      {t.beta != null && (
        <Row
          label="Beta 係數（1年）"
          value={
            <>
              {fmtNum(t.beta)}
              <span className="ml-1.5 text-xs text-muted">{t.beta > 1.2 ? "波動大於大盤" : t.beta < 0.8 ? "波動小於大盤" : "與大盤相近"}</span>
            </>
          }
        />
      )}
      {t.volatility_pct != null && <Row label="年化波動率" value={`${fmtNum(t.volatility_pct, 1)}%`} />}
      {t.high_52w != null && (
        <Row
          label="52週高"
          value={
            <>
              {fmtNum(t.high_52w)}
              {t.dist_high_pct != null && <span className={`ml-1.5 text-xs ${changeColor(t.dist_high_pct)}`}>{fmtPct(t.dist_high_pct)}</span>}
            </>
          }
        />
      )}
      {t.low_52w != null && (
        <Row
          label="52週低"
          value={
            <>
              {fmtNum(t.low_52w)}
              {t.dist_low_pct != null && <span className={`ml-1.5 text-xs ${changeColor(t.dist_low_pct)}`}>{fmtPct(t.dist_low_pct)}</span>}
            </>
          }
        />
      )}
      <p className="mt-2 text-[11px] text-muted">Beta＝近一年日報酬對加權指數迴歸；乖離＝現價偏離均線幅度。</p>
    </Card>
  );
}

// ───── 財務報表（簡明季度損益：營收/EPS/三率/ROE）─────
function QuarterlyFinCard({ data }: { data: FundamentalHistoryResponse }) {
  const rows = [...data.quarters].reverse(); // 新→舊
  if (rows.length === 0) return null;
  return (
    <Card title="季度損益摘要（簡明損益表）">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted">
              <th className="py-1 text-left font-normal">季度</th>
              <th className="py-1 text-right font-normal">營收(億)</th>
              <th className="py-1 text-right font-normal">EPS</th>
              <th className="py-1 text-right font-normal">毛利率</th>
              <th className="py-1 text-right font-normal">營益率</th>
              <th className="py-1 text-right font-normal">淨利率</th>
              <th className="py-1 text-right font-normal">ROE</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 12).map((q) => (
              <tr key={q.label} className="border-t border-edge/60">
                <td className="py-1 text-muted">{q.label}</td>
                <td className="py-1 text-right tabular-nums">{q.revenue != null ? fmtNum(q.revenue, 1) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.eps != null ? fmtNum(q.eps) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.gross_margin != null ? `${fmtNum(q.gross_margin, 1)}%` : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.op_margin != null ? `${fmtNum(q.op_margin, 1)}%` : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.net_margin != null ? `${fmtNum(q.net_margin, 1)}%` : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.roe != null ? `${fmtNum(q.roe, 1)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-muted">
        資料為單季損益重點欄位；資產負債表／現金流量表見同分類其他卡片。
      </p>
    </Card>
  );
}

// ───── 資產負債表摘要（FinMind 懶抓快取）─────
function BalanceSheetCard({ data }: { data: FinancialStatementsResponse }) {
  if (data.quarters.length === 0) return null;
  const latest = data.quarters[0];
  return (
    <Card title="資產負債表摘要（期末餘額，億元）">
      {latest.bps != null && (
        <div className="mb-2 rounded-lg bg-panel2/60 px-3 py-2 text-sm">
          最新每股淨值 <b className="tabular-nums">{fmtNum(latest.bps)}</b> 元
          {latest.debt_ratio != null && <span className="text-muted">（負債比 {fmtNum(latest.debt_ratio, 1)}%）</span>}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted">
              <th className="py-1 text-left font-normal">季度</th>
              <th className="py-1 text-right font-normal">現金</th>
              <th className="py-1 text-right font-normal">總資產</th>
              <th className="py-1 text-right font-normal">總負債</th>
              <th className="py-1 text-right font-normal">權益</th>
              <th className="py-1 text-right font-normal">負債比</th>
              <th className="py-1 text-right font-normal">流動比</th>
            </tr>
          </thead>
          <tbody>
            {data.quarters.slice(0, 12).map((q) => (
              <tr key={q.label} className="border-t border-edge/60">
                <td className="py-1 text-muted">{q.label}</td>
                <td className="py-1 text-right tabular-nums">{q.cash != null ? fmtNum(q.cash, 0) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.total_assets != null ? fmtNum(q.total_assets, 0) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.total_liab != null ? fmtNum(q.total_liab, 0) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.equity != null ? fmtNum(q.equity, 0) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.debt_ratio != null ? `${fmtNum(q.debt_ratio, 1)}%` : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.current_ratio != null ? `${fmtNum(q.current_ratio, 0)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-muted">
        負債比＝負債總額/資產總額（越低越穩健）；流動比＝流動資產/流動負債（&gt;100% 表短期償債無虞）。源：FinMind 財報。
      </p>
    </Card>
  );
}

// ───── 現金流量表摘要（單季化）─────
function CashFlowCard({ data }: { data: FinancialStatementsResponse }) {
  if (data.quarters.length === 0 || data.quarters.every((q) => q.op_cf == null)) return null;
  return (
    <Card title="現金流量表摘要（單季，億元）">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted">
              <th className="py-1 text-left font-normal">季度</th>
              <th className="py-1 text-right font-normal">營業</th>
              <th className="py-1 text-right font-normal">投資</th>
              <th className="py-1 text-right font-normal">籌資</th>
              <th className="py-1 text-right font-normal">資本支出</th>
              <th className="py-1 text-right font-normal">自由現金流</th>
            </tr>
          </thead>
          <tbody>
            {data.quarters.slice(0, 12).map((q) => (
              <tr key={q.label} className="border-t border-edge/60">
                <td className="py-1 text-muted">{q.label}</td>
                <td className={`py-1 text-right tabular-nums ${changeColor(q.op_cf)}`}>{q.op_cf != null ? fmtNum(q.op_cf, 0) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.inv_cf != null ? fmtNum(q.inv_cf, 0) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.fin_cf != null ? fmtNum(q.fin_cf, 0) : "—"}</td>
                <td className="py-1 text-right tabular-nums">{q.capex != null ? fmtNum(q.capex, 0) : "—"}</td>
                <td className={`py-1 text-right tabular-nums ${changeColor(q.fcf)}`}>{q.fcf != null ? fmtNum(q.fcf, 0) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-muted">
        營業現金流持續為正且 ≥ 淨利＝獲利品質佳；自由現金流＝營業＋資本支出（負值資本支出＝投資擴產）。源：FinMind 財報（年度累計已差分單季）。
      </p>
    </Card>
  );
}

// ───── 注意/處置：頭部徽章 + 明細卡 ─────
// 使用者實證觀點：被列注意/處置常伴隨上漲動能（熱錢聚集的果），徽章以「動能/警示」雙語意呈現。
function AttentionBadge({ att }: { att: AttentionResponse }) {
  if (!att.status) return null;
  if (att.status === "punish") {
    return (
      <span
        title={`處置期間分盤交易、流動性受限${att.punish_end ? `，至 ${att.punish_end}` : ""}；歷史上處置股常帶強動能（高波動）`}
        className="rounded bg-fuchsia-500/20 px-2 py-0.5 text-xs font-medium text-fuchsia-300"
      >
        🚫 處置中{att.punish_end ? ` ~${att.punish_end.slice(5)}` : ""}
      </span>
    );
  }
  return (
    <span
      title="近 5 日曾列注意股：熱錢聚集、波動放大；歷史上列注意常伴隨上漲動能"
      className="rounded bg-amber-500/20 px-2 py-0.5 text-xs font-medium text-amber-300"
    >
      ⚡ 注意股{att.notice_count_30d > 1 ? ` ×${att.notice_count_30d}` : ""}
    </span>
  );
}

function AttentionCard({ att }: { att: AttentionResponse }) {
  if (att.entries.length === 0) return null;
  return (
    <Card title="注意 / 處置紀錄（近 90 日）">
      <ul className="flex flex-col gap-1.5">
        {att.entries.slice(0, 10).map((e, i) => (
          <li key={i} className="text-sm">
            <div className="flex items-center gap-2">
              <span className={`rounded px-1.5 py-0.5 text-xs ${e.kind === "punish" ? "bg-fuchsia-500/15 text-fuchsia-300" : "bg-amber-500/15 text-amber-300"}`}>
                {e.kind === "punish" ? "處置" : "注意"}
              </span>
              <span className="text-xs tabular-nums text-muted">{e.date}</span>
              {e.times != null && e.times > 1 && <span className="text-xs text-muted">累計 {e.times} 次</span>}
              {e.kind === "punish" && e.begin_date && e.end_date && (
                <span className="text-xs tabular-nums text-muted">{e.begin_date.slice(5)}~{e.end_date.slice(5)}</span>
              )}
            </div>
            {e.reason && <div className="mt-0.5 truncate text-xs text-muted" title={e.reason}>{e.reason}</div>}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        列注意＝波動/量能觸及公告標準（熱錢聚集）；處置＝分盤撮合、流動性受限。
        本站實證（2020 起 4.9 萬事件）：處置後 10 日勝率 55%、中位 +1.9%（短線動能確實存在）；
        注意股平均 +1.7% 但中位 −0.8%（右尾樂透：少數大漲拉高平均，典型結果偏弱）。非買賣建議。
      </p>
    </Card>
  );
}

// ───── 模組化版面：分類 + 顯示/隱藏 + 排序（localStorage 持久化）─────
const MODULE_CATS = ["基本面", "技術面", "籌碼面", "消息面", "財務報表", "評分與AI"] as const;
type ModuleCat = (typeof MODULE_CATS)[number];

type ModuleDef = {
  id: string;
  title: string;
  cat: ModuleCat;
  col: "main" | "side";
  node: React.ReactNode | null; // null＝此股無資料（客製面板中反灰）
};

type ModuleSettings = { hidden: string[]; order: string[] };
const SETTINGS_KEY = "stock-modules-v1";

function loadSettings(): ModuleSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) {
      const s = JSON.parse(raw);
      if (Array.isArray(s.hidden) && Array.isArray(s.order)) return s;
    }
  } catch {
    /* 壞資料回預設 */
  }
  return { hidden: [], order: [] };
}

/** 儲存的順序在前（過濾不存在的 id），未出現在儲存順序的模組依預設順序附加在後。 */
function applyOrder(modules: ModuleDef[], order: string[]): ModuleDef[] {
  const byId = new Map(modules.map((m) => [m.id, m]));
  const sorted: ModuleDef[] = [];
  for (const id of order) {
    const m = byId.get(id);
    if (m) {
      sorted.push(m);
      byId.delete(id);
    }
  }
  return [...sorted, ...modules.filter((m) => byId.has(m.id))];
}

function CustomizePanel({
  modules,
  settings,
  onChange,
}: {
  modules: ModuleDef[];
  settings: ModuleSettings;
  onChange: (s: ModuleSettings) => void;
}) {
  const ordered = applyOrder(modules, settings.order);
  const toggle = (id: string) => {
    const hidden = settings.hidden.includes(id)
      ? settings.hidden.filter((h) => h !== id)
      : [...settings.hidden, id];
    onChange({ ...settings, hidden });
  };
  const move = (id: string, dir: -1 | 1) => {
    // 只在同欄位（主欄/側欄）內交換順序，避免跨欄搬移造成版面混亂
    const ids = ordered.map((m) => m.id);
    const col = modules.find((m) => m.id === id)?.col;
    const sameCol = ordered.filter((m) => m.col === col).map((m) => m.id);
    const pos = sameCol.indexOf(id);
    const target = sameCol[pos + dir];
    if (!target) return;
    const i = ids.indexOf(id);
    const j = ids.indexOf(target);
    [ids[i], ids[j]] = [ids[j], ids[i]];
    onChange({ ...settings, order: ids });
  };
  return (
    <div className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto pr-1">
      {MODULE_CATS.map((cat) => {
        const items = ordered.filter((m) => m.cat === cat);
        if (items.length === 0) return null;
        return (
          <div key={cat}>
            <div className="mb-1.5 text-xs font-semibold text-muted">{cat}</div>
            <div className="flex flex-col gap-1">
              {items.map((m) => {
                const off = settings.hidden.includes(m.id);
                const noData = m.node === null;
                return (
                  <div
                    key={m.id}
                    className={`flex items-center gap-2 rounded-lg border border-edge bg-panel2/50 px-2.5 py-1.5 text-sm ${noData ? "opacity-45" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={!off}
                      onChange={() => toggle(m.id)}
                      className="accent-sky-500"
                      id={`mod-${m.id}`}
                    />
                    <label htmlFor={`mod-${m.id}`} className="flex-1 cursor-pointer select-none">
                      {m.title}
                      {noData && <span className="ml-1.5 text-xs text-muted">（此股無資料）</span>}
                    </label>
                    <span className="rounded bg-panel px-1.5 py-0.5 text-[10px] text-muted">{m.col === "main" ? "主欄" : "側欄"}</span>
                    <button onClick={() => move(m.id, -1)} className="rounded px-1.5 py-0.5 text-xs text-muted hover:bg-panel hover:text-gray-200" title="上移">▲</button>
                    <button onClick={() => move(m.id, 1)} className="rounded px-1.5 py-0.5 text-xs text-muted hover:bg-panel hover:text-gray-200" title="下移">▼</button>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
      <button
        onClick={() => onChange({ hidden: [], order: [] })}
        className="self-start rounded-md bg-panel2 px-3 py-1.5 text-xs text-muted hover:text-gray-200"
      >
        還原預設版面
      </button>
    </div>
  );
}

export default function StockDetailPage() {
  const { id } = useParams();
  // 策略室樣本跳轉：?at=YYYY-MM-DD → K 線標「訊號日」並定位視窗
  const focusDate = new URLSearchParams(window.location.search).get("at");
  const { data: d, isLoading, isError, error } = useStockDetail(id);
  const [klineDays, setKlineDays] = useState(() => {
    if (!focusDate) return 120;
    // 依訊號日距今自動選足夠涵蓋的區間（日曆日 ≈ 交易日 × 1.45）
    const calDays = Math.ceil((Date.now() - new Date(focusDate).getTime()) / 86_400_000);
    const need = Math.ceil(calDays / 1.45) + 60;
    return KLINE_RANGES.map((r) => r.days).find((v) => v >= need) ?? 3000;
  });
  const { data: ohlcv } = useOhlcv(id, klineDays);
  const { data: levels } = useLevels(id);
  const { data: recMarks } = useRecommendationMarks(id, klineDays);
  const { data: targetPrice } = useTargetPrice(id);
  const [tpLineOn, setTpLineOn] = useState(() => localStorage.getItem("tp-line") !== "0");
  const toggleTpLine = (on: boolean) => {
    setTpLineOn(on);
    localStorage.setItem("tp-line", on ? "1" : "0");
  };
  const [chipDays, setChipDays] = useState(120);
  const { data: chipHistory } = useChipHistory(id, chipDays);
  const { data: holdingHistory } = useHoldingHistory(id);
  const isEtf = d?.is_etf ?? false;
  const { data: fundHistory } = useFundamentalHistory(isEtf ? undefined : id);
  const { data: dividends } = useDividends(isEtf ? undefined : id);
  const { data: peRiver } = usePeRiver(isEtf ? undefined : id);
  const { data: industryChain } = useIndustryChain(isEtf ? undefined : id);
  const { data: pbRiver } = usePbRiver(isEtf ? undefined : id);
  const { data: techSummary } = useTechSummary(id);
  const { data: finStatements } = useFinancialStatements(isEtf ? undefined : id);
  const { data: attention } = useAttention(id);

  // 版面客製化：顯示/隱藏 + 排序（localStorage 持久化，跨個股共用）
  const [settings, setSettings] = useState<ModuleSettings>(loadSettings);
  const saveSettings = (s: ModuleSettings) => {
    setSettings(s);
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  };
  const [activeCat, setActiveCat] = useState<ModuleCat | "全部">("全部");
  const [customizeOpen, setCustomizeOpen] = useState(false);

  if (isLoading) return <div className="p-6 text-muted">載入中…</div>;
  if (isError) return <div className="p-6 text-down">載入失敗：{(error as Error).message}</div>;
  if (!d) return null;

  // ───── 模組註冊表：所有卡片依 分類×欄位 註冊，node=null 表示此股無該資料 ─────
  const modules: ModuleDef[] = [
    {
      id: "kline", title: "K 線圖", cat: "技術面", col: "main",
      node: (
        <Card
          title="K 線（日K，疊均線 MA5/20/60 + 支撐/壓力 + 推薦標記）"
          action={<RangeSelector value={klineDays} onChange={setKlineDays} />}
        >
          {ohlcv && ohlcv.candles.length > 0 ? (
            <KLineChart
              candles={ohlcv.candles}
              levels={[...(levels?.supports ?? []), ...(levels?.resistances ?? [])]}
              marks={recMarks?.marks ?? []}
              targetPrice={tpLineOn ? targetPrice?.latest?.target_price ?? null : null}
              focusDate={focusDate}
            />
          ) : (
            <p className="text-muted">無 K 線資料</p>
          )}
        </Card>
      ),
    },
    {
      id: "levels", title: "支撐 / 壓力位", cat: "技術面", col: "main",
      node: levels && (levels.supports.length > 0 || levels.resistances.length > 0)
        ? <LevelsCard levels={levels} />
        : null,
    },
    {
      id: "chip-trend", title: "籌碼趨勢圖", cat: "籌碼面", col: "main",
      node: chipHistory && chipHistory.points.length >= 2 ? (
        <Card
          title="籌碼趨勢（法人每日買賣超 + 累計、融資融券）"
          action={<RangeSelector value={chipDays} onChange={setChipDays} />}
        >
          <ChipTrendChart points={chipHistory.points} />
          <p className="mt-1 text-xs text-muted">
            紅柱買超／綠柱賣超；累計線向上＝法人持續進貨、向下＝持續調節。融資增＝散戶槓桿加碼、融券增＝空方轉強。
            {chipHistory.points.length < chipDays * 0.6 && (
              <span className="text-amber-500">　籌碼歷史僅 {chipHistory.points.length} 個交易日（回補中，選長區間暫顯示相同範圍）。</span>
            )}
          </p>
        </Card>
      ) : null,
    },
    {
      id: "holding-trend", title: "集保股權分散趨勢", cat: "籌碼面", col: "main",
      node: !d.is_etf && holdingHistory && (holdingHistory.points.length >= 2 || holdingHistory.backfilling) ? (
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
      ) : null,
    },
    {
      id: "fund-trend", title: "基本面趨勢圖", cat: "基本面", col: "main",
      node: !d.is_etf && fundHistory && (fundHistory.revenues.length >= 2 || fundHistory.quarters.length >= 2) ? (
        <Card title="基本面趨勢（月營收＋YoY、單季 EPS＋三率）">
          <FundamentalTrendChart data={fundHistory} />
          <p className="mt-1 text-xs text-muted">
            YoY 線向上＝成長加速；三率同步走揚＝產品組合/議價力改善。單季財報約在季後 45 天內公布。
            {fundHistory.backfilling && "　歷史回補中…"}
          </p>
        </Card>
      ) : null,
    },
    {
      id: "pe-river", title: "本益比河流圖", cat: "基本面", col: "main",
      node: !d.is_etf && peRiver && peRiver.points.length >= 2 ? (
        <Card
          title="本益比河流圖（歷史 PE 分位帶）"
          action={
            peRiver.current_pe != null && (
              <span className="text-xs text-muted">
                現在 PE {fmtNum(peRiver.current_pe)}
                {peRiver.pe_percentile != null && (
                  <span className={`ml-1 rounded px-1.5 py-0.5 ${peRiver.pe_percentile >= 70 ? "bg-down/15 text-down" : peRiver.pe_percentile <= 30 ? "bg-up/15 text-up" : "bg-panel2"}`}>
                    歷史第 {Math.round(peRiver.pe_percentile)} 百分位
                  </span>
                )}
              </span>
            )
          }
        >
          <PeRiverChart data={peRiver} />
          <p className="mt-1 text-xs text-muted">
            股價貼下緣（綠）＝相對自身歷史便宜、貼上緣（紅）＝偏貴；帶寬隨 EPS 變動。僅相對估值參考，非買賣建議。
            {peRiver.backfilling && "　估值歷史回補中，河流會隨回補逐步加長…"}
          </p>
        </Card>
      ) : null,
    },
    {
      id: "pb-river", title: "本淨比河流圖", cat: "基本面", col: "main",
      node: !d.is_etf && pbRiver && pbRiver.points.length >= 2 ? (
        <Card
          title="本淨比河流圖（歷史 PB 分位帶）"
          action={
            pbRiver.current_pe != null && (
              <span className="text-xs text-muted">
                現在 PB {fmtNum(pbRiver.current_pe)}
                {pbRiver.pe_percentile != null && (
                  <span className={`ml-1 rounded px-1.5 py-0.5 ${pbRiver.pe_percentile >= 70 ? "bg-down/15 text-down" : pbRiver.pe_percentile <= 30 ? "bg-up/15 text-up" : "bg-panel2"}`}>
                    歷史第 {Math.round(pbRiver.pe_percentile)} 百分位
                  </span>
                )}
              </span>
            )
          }
        >
          <PeRiverChart data={pbRiver} />
          <p className="mt-1 text-xs text-muted">
            以每日官方 PB 分位數 × 隱含每股淨值畫帶；景氣循環股（獲利波動大、PE 失真）看 PB 河流較穩定。
            {pbRiver.backfilling && "　估值歷史回補中…"}
          </p>
        </Card>
      ) : null,
    },
    {
      id: "fin-quarters", title: "季度損益摘要", cat: "財務報表", col: "main",
      node: !d.is_etf && fundHistory && fundHistory.quarters.length > 0
        ? <QuarterlyFinCard data={fundHistory} />
        : null,
    },
    {
      id: "balance-sheet", title: "資產負債表摘要", cat: "財務報表", col: "main",
      node: !d.is_etf && finStatements && finStatements.quarters.length > 0
        ? <BalanceSheetCard data={finStatements} />
        : null,
    },
    {
      id: "cash-flow", title: "現金流量表摘要", cat: "財務報表", col: "main",
      node: !d.is_etf && finStatements && finStatements.quarters.some((q) => q.op_cf != null)
        ? <CashFlowCard data={finStatements} />
        : null,
    },
    {
      id: "industry-chain", title: "產業鏈定位", cat: "基本面", col: "main",
      node: !d.is_etf && industryChain && industryChain.chains.length > 0 ? (
        <Card title="產業鏈定位（櫃買中心產業價值鏈平台）">
          <div className="flex flex-col gap-4">
            {industryChain.chains.slice(0, 3).map((ch) => (
              <div key={ch.chain_id}>
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">{ch.chain_name}</span>
                  {ch.my_nodes.slice(0, 4).map((n) => (
                    <span key={n} title={n} className="max-w-48 truncate rounded bg-sky-500/15 px-1.5 py-0.5 text-xs text-sky-300">{n}</span>
                  ))}
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                  {ch.streams.map((st) => (
                    <div key={st.stream} className="rounded-lg border border-edge bg-panel2/40 p-2">
                      <div className="mb-1.5 text-xs font-medium text-muted">{st.stream}</div>
                      <div className="flex flex-wrap gap-1">
                        {st.nodes.map((n) => (
                          <span
                            key={n.name}
                            title={`${n.name}（${n.count} 家）`}
                            className={`max-w-full truncate rounded px-1.5 py-0.5 text-[11px] ${
                              n.mine ? "bg-sky-600 font-medium text-white" : "bg-panel2 text-muted"
                            }`}
                          >
                            {n.name}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {industryChain.chains.length > 3 && (
              <p className="text-xs text-muted">另屬 {industryChain.chains.length - 3} 條產業鏈（略）</p>
            )}
          </div>
          <p className="mt-2 text-xs text-muted">藍色＝本公司所在環節。資料源：ic.tpex.org.tw（公司自行申報彙整）。</p>
        </Card>
      ) : null,
    },
    {
      id: "tracks", title: "波段／長線評分", cat: "評分與AI", col: "main",
      node: (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TrackPanel track="wave" score={d.scores["wave"] ?? null} />
          <TrackPanel track="long" score={d.scores["long"] ?? null} />
        </div>
      ),
    },
    // ───── 側欄 ─────
    {
      id: "target-price", title: "法人目標價", cat: "消息面", col: "side",
      node: targetPrice ? <TargetPriceCard data={targetPrice} lineOn={tpLineOn} onToggleLine={toggleTpLine} /> : null,
    },
    {
      id: "sector-brief", title: "所屬類股", cat: "基本面", col: "side",
      node: d.sector_brief ? <SectorBriefCard brief={d.sector_brief} /> : null,
    },
    {
      id: "tech-summary", title: "技術指標摘要", cat: "技術面", col: "side",
      node: techSummary && (techSummary.kd_k != null || techSummary.beta != null || techSummary.high_52w != null)
        ? <TechSummaryCard t={techSummary} />
        : null,
    },
    {
      id: "chip-now", title: "籌碼現況", cat: "籌碼面", col: "side",
      node: (
        <Card title="籌碼">
          <Row label="外資（張）" value={fmtNum(d.chip?.foreign_net, 0)} color={changeColor(d.chip?.foreign_net)} />
          <Row label="投信（張）" value={fmtNum(d.chip?.trust_net, 0)} color={changeColor(d.chip?.trust_net)} />
          <Row label="自營商（張）" value={fmtNum(d.chip?.dealer_net, 0)} color={changeColor(d.chip?.dealer_net)} />
          <Row label="三大法人合計" value={fmtNum(d.chip?.total_net, 0)} color={changeColor(d.chip?.total_net)} />
          <div className="my-2 border-t border-edge" />
          <Row label="融資餘額（張）" value={fmtNum(d.chip?.margin_balance, 0)} />
          <Row label="融券餘額（張）" value={fmtNum(d.chip?.short_balance, 0)} />
          {d.chip?.sbl_balance != null && (
            <Row
              label="借券賣出餘額（張）"
              value={`${fmtNum(d.chip.sbl_balance, 0)}${d.chip.sbl_chg20 != null ? `（20日 ${d.chip.sbl_chg20 > 0 ? "+" : ""}${fmtNum(d.chip.sbl_chg20, 0)}）` : ""}`}
              color={d.chip.sbl_chg20 != null ? changeColor(-d.chip.sbl_chg20) : undefined}
            />
          )}
          {d.chip?.dt_ratio5 != null && <Row label="當沖占比（近5日）" value={`${d.chip.dt_ratio5}%`} />}
          {(d.chip?.insider_pct_chg != null || d.chip?.insider_pledge_pct != null) && (
            <>
              <div className="my-2 border-t border-edge" />
              <div className="mb-1 text-xs text-muted">董監持股（月）</div>
              {d.chip.insider_pct_chg != null && (
                <Row
                  label="董監持股月變化"
                  value={`${d.chip.insider_pct_chg > 0 ? "+" : ""}${fmtNum(d.chip.insider_pct_chg, 2)}%`}
                  color={changeColor(d.chip.insider_pct_chg)}
                />
              )}
              {d.chip.insider_pledge_pct != null && (
                <Row label="董監設質比率" value={`${fmtNum(d.chip.insider_pledge_pct, 1)}%`} />
              )}
            </>
          )}
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
      ),
    },
    {
      id: "etf", title: "ETF 資料", cat: "基本面", col: "side",
      node: d.is_etf ? <EtfCard etf={d.etf} /> : null,
    },
    {
      id: "profile", title: "基本資料", cat: "基本面", col: "side",
      node: !d.is_etf && d.profile ? <ProfileCard profile={d.profile} market={d.market} /> : null,
    },
    {
      id: "fund-now", title: "基本面現況", cat: "基本面", col: "side",
      node: !d.is_etf ? (
        <Card title="基本面">
          <Row label="EPS（近4季）" value={fmtNum(d.fundamental?.eps)} />
          <Row label="本益比" value={fmtNum(d.fundamental?.pe)} />
          <Row label="股價淨值比" value={fmtNum(d.fundamental?.pb)} />
          <Row label="殖利率" value={fmtPct(d.fundamental?.dividend_yield)} />
          {d.fundamental?.revenue_ym && (
            <>
              <div className="my-2 border-t border-edge" />
              <div className="mb-1 text-xs text-muted">月營收（{d.fundamental.revenue_ym}）</div>
              <Row label="營收" value={d.fundamental.month_revenue != null ? `${fmtNum(d.fundamental.month_revenue, 1)} 億` : "—"} />
              <Row label="年增 YoY" value={fmtPct(d.fundamental.revenue_yoy)} color={changeColor(d.fundamental.revenue_yoy)} />
              <Row label="月增 MoM" value={fmtPct(d.fundamental.revenue_mom)} color={changeColor(d.fundamental.revenue_mom)} />
              {d.fundamental.rev_yoy_streak != null && d.fundamental.rev_yoy_streak >= 2 && (
                <Row label="YoY 連續成長" value={`${d.fundamental.rev_yoy_streak} 個月`} color="text-up" />
              )}
            </>
          )}
          {d.fundamental?.fin_quarter && (
            <>
              <div className="my-2 border-t border-edge" />
              <div className="mb-1 text-xs text-muted">獲利能力（{d.fundamental.fin_quarter} 單季）</div>
              <Row
                label="單季 EPS"
                value={
                  <>
                    {fmtNum(d.fundamental.quarter_eps)}
                    {d.fundamental.eps_yoy != null && (
                      <span className={`ml-1.5 text-xs ${changeColor(d.fundamental.eps_yoy)}`}>年增 {fmtPct(d.fundamental.eps_yoy)}</span>
                    )}
                  </>
                }
              />
              <MarginRow label="毛利率" value={d.fundamental.gross_margin} qoq={d.fundamental.gross_margin_qoq} />
              <MarginRow label="營益率" value={d.fundamental.op_margin} qoq={d.fundamental.op_margin_qoq} />
              <MarginRow label="淨利率" value={d.fundamental.net_margin} qoq={d.fundamental.net_margin_qoq} />
              {d.fundamental.roe != null && <Row label="ROE" value={`${fmtNum(d.fundamental.roe, 1)}%`} />}
            </>
          )}
        </Card>
      ) : null,
    },
    {
      id: "dividends", title: "股利政策", cat: "基本面", col: "side",
      node: !d.is_etf && dividends && dividends.entries.length > 0 ? <DividendCard data={dividends} /> : null,
    },
    {
      id: "attention", title: "注意/處置紀錄", cat: "消息面", col: "side",
      node: attention && attention.entries.length > 0 ? <AttentionCard att={attention} /> : null,
    },
    {
      id: "events", title: "重要消息", cat: "消息面", col: "side",
      node: (
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
      ),
    },
    {
      id: "health", title: "AI 健檢", cat: "評分與AI", col: "side",
      node: <HealthCard stockId={d.stock_id} />,
    },
  ];

  const ordered = applyOrder(modules, settings.order);
  const visibleIn = (col: "main" | "side") =>
    ordered.filter(
      (m) =>
        m.col === col &&
        m.node !== null &&
        !settings.hidden.includes(m.id) &&
        (activeCat === "全部" || m.cat === activeCat),
    );
  const mainMods = visibleIn("main");
  const sideMods = visibleIn("side");

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <Link to="/recommendations" className="text-sm text-sky-400 hover:underline">
        ← 進場推薦
      </Link>

      {/* 頭部 */}
      <div className="mb-5 mt-3 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="text-2xl font-bold">{d.name}</h1>
            <span className="text-lg text-muted">{d.stock_id}</span>
            <span className="rounded bg-panel2 px-2 py-0.5 text-xs text-muted">{d.sector_name ?? "—"}</span>
            {attention && <AttentionBadge att={attention} />}
            {/* 業務標籤（官方產業價值鏈細分節點，去重）*/}
            {[...new Set(d.chains.map((c) => c.node_name).filter(Boolean))].slice(0, 4).map((t) => (
              <span key={t} title={t!} className="max-w-40 truncate rounded bg-sky-500/15 px-2 py-0.5 text-xs text-sky-300">
                {t}
              </span>
            ))}
          </div>
          <div className="mt-1 text-xs text-muted">{d.market ?? ""}　盤後 {d.date ?? "—"}</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold tabular-nums">{fmtNum(d.close)}</div>
          <div className={`text-sm tabular-nums ${changeColor(d.change_pct)}`}>
            {fmtNum(d.change)}（{fmtPct(d.change_pct)}）
          </div>
          <BuySellBar stockId={d.stock_id} name={d.name} close={d.close} />
        </div>
      </div>

      {/* 分類導覽 + 自訂版面 */}
      <div className="sticky top-0 z-10 mb-4 flex flex-wrap items-center gap-1.5 rounded-xl border border-edge bg-panel/90 px-3 py-2 backdrop-blur">
        {(["全部", ...MODULE_CATS] as const).map((c) => (
          <button
            key={c}
            onClick={() => setActiveCat(c)}
            className={`rounded-full px-3 py-1 text-xs ${activeCat === c ? "bg-sky-600 text-white" : "bg-panel2 text-muted hover:text-gray-200"}`}
          >
            {c}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={() => setCustomizeOpen(true)}
          className="rounded-full bg-panel2 px-3 py-1 text-xs text-muted hover:text-gray-200"
          title="選擇顯示哪些模組並調整順序"
        >
          ⚙ 自訂版面
        </button>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_320px]">
        {/* 左主欄 */}
        <div className="flex flex-col gap-5">
          {mainMods.map((m) => (
            <div key={m.id}>{m.node}</div>
          ))}
          {mainMods.length === 0 && (
            <p className="py-10 text-center text-sm text-muted">此分類在主欄沒有可顯示的模組（可能無資料或已隱藏）。</p>
          )}
        </div>

        {/* 右側欄 */}
        <div className="flex flex-col gap-5">
          {sideMods.map((m) => (
            <div key={m.id}>{m.node}</div>
          ))}
        </div>
      </div>

      <Modal title="自訂版面（勾選顯示、▲▼ 調整順序）" open={customizeOpen} onClose={() => setCustomizeOpen(false)}>
        <CustomizePanel modules={modules} settings={settings} onChange={saveSettings} />
      </Modal>
    </div>
  );
}
