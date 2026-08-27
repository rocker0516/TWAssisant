import { useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import { Link } from "react-router-dom";
import {
  COMBO_FILTER_KEY,
  readComboFilters,
  useComboSamples,
  useLookbackSensitivity,
  useLookbackStats,
  usePaperSimulate,
  useSignalDecay,
  useTagCooccurrence,
  type LookbackGroupStat,
  type PaperSimResponse,
  type LabStyle,
} from "../api/client";
import { BacktestLabSection } from "../components/BacktestLab";
import { CornerLabSection } from "../components/CornerSignalsPanel";
import { WaveChallengeSection } from "../components/WaveChallengePanel";
import { Modal } from "../components/Modal";
import { changeColor, fmtNum, fmtPct } from "../lib/format";

// 風格標籤（與推薦卡一致）＋ 注意/處置事件策略（判官驗證：處置後10日 holdout 命中71%）
const STYLE_LABELS: Record<string, string> = {
  pop: "會噴", explosive: "爆發", strong: "強勢延伸", story: "故事股", crash: "深跌反攻",
  punish: "🔥處置動能", notice: "⚡注意動能",
};

// 期間選擇 → since ISO 日期
const RANGES: { key: string; label: string; days: number | null }[] = [
  { key: "90", label: "近 3 個月", days: 90 },
  { key: "180", label: "近半年", days: 180 },
  { key: "365", label: "近一年", days: 365 },
  { key: "all", label: "全部歷史", days: null },
];

function sinceOf(key: string): string | undefined {
  const r = RANGES.find((x) => x.key === key);
  if (!r || r.days == null) return undefined;
  const d = new Date();
  d.setDate(d.getDate() - r.days);
  return d.toISOString().slice(0, 10);
}

export default function LabPage() {
  const [range, setRange] = useState("180");
  const since = useMemo(() => sinceOf(range), [range]);

  return (
    <div className="w-full px-6 py-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-xl font-bold">策略室</h1>
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button key={r.key} onClick={() => setRange(r.key)}
              className={`rounded-md px-3 py-1 text-xs transition ${range === r.key ? "bg-sky-900/60 text-sky-200" : "text-muted hover:bg-panel2"}`}>
              {r.label}
            </button>
          ))}
        </div>
      </div>
      <p className="mb-5 text-xs text-muted">
        用歷史推薦（逐日落庫的當時快照）驗證策略：模擬照單操作的實際績效、哪類推薦準、門檻怎麼調。
        進場錨＝推薦隔日最高價（追高最壞情境，偏保守）。
      </p>

      <WaveChallengeSection />
      <BacktestLabSection />
      <PaperSection since={since} />
      <StatsSection since={since} />
      <CooccurrenceSection since={since} />
      <SignalDecaySection />
      <SensitivitySection since={since} />
      <CornerLabSection />
    </div>
  );
}

// ─────────────────────────── 訊號時變效力 ───────────────────────────

const DECAY_COLORS: Record<string, string> = {
  pop: "#38bdf8", explosive: "#ef4444", strong: "#f59e0b", story: "#a78bfa",
  crash: "#f472b6", punish: "#e879f9", notice: "#fbbf24",
};

function SignalDecayChart({ data }: { data: NonNullable<ReturnType<typeof useSignalDecay>["data"]> }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const yms = data.base.map((p) => p.ym);
    chart.setOption({
      backgroundColor: "transparent",
      grid: { top: 30, right: 16, bottom: 24, left: 44 },
      legend: {
        top: 0, textStyle: { color: "#9ca3af", fontSize: 11 },
        data: data.signals.map((s) => STYLE_LABELS[s.key] ?? s.key),
      },
      tooltip: { trigger: "axis", backgroundColor: "#1f2430", borderColor: "#374151", textStyle: { color: "#e5e7eb", fontSize: 12 }, valueFormatter: (v: number) => `${v > 0 ? "+" : ""}${v}pp` },
      xAxis: { type: "category", data: yms, axisLabel: { color: "#6b7280", fontSize: 10 }, axisLine: { lineStyle: { color: "#374151" } } },
      yAxis: { type: "value", name: "lift(pp)", nameTextStyle: { color: "#6b7280" }, axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } } },
      series: data.signals.map((s) => {
        const m = new Map(s.points.map((p) => [p.ym, p.lift]));
        return {
          name: STYLE_LABELS[s.key] ?? s.key, type: "line", symbol: "circle", symbolSize: 3,
          connectNulls: false, data: yms.map((ym) => m.get(ym) ?? null),
          lineStyle: { width: 1.6, color: DECAY_COLORS[s.key] }, itemStyle: { color: DECAY_COLORS[s.key] },
        };
      }),
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); chart.dispose(); };
  }, [data]);
  return <div ref={ref} className="h-72 w-full" />;
}

function SignalDecaySection() {
  const { data } = useSignalDecay();
  if (!data || data.signals.length === 0) return null;
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-base font-semibold">⏳ 訊號時變效力 — 影響度不是常數</h2>
      <div className="mb-3 rounded-xl border border-edge bg-panel p-3">
        <SignalDecayChart data={data} />
        <p className="mt-1 text-[11px] text-muted">
          每月「該訊號樣本的 10 日碰 +10% 率 − 當月全市場基率」＝lift。線在 0 之上＝該月有效；
          月樣本 &lt;20 不畫點。訊號效力隨市況起伏，近期走弱的訊號應降低信任。
        </p>
      </div>
      <div className="overflow-x-auto rounded-xl border border-edge">
        <table className="w-full text-sm">
          <thead className="bg-panel2 text-xs text-muted">
            <tr>
              <th className="px-3 py-2 text-left">訊號</th>
              <th className="px-3 py-2 text-right">全期命中</th>
              <th className="px-3 py-2 text-right">全期lift</th>
              <th className="px-3 py-2 text-right">近3月命中</th>
              <th className="px-3 py-2 text-right">近3月lift</th>
              <th className="px-3 py-2 text-right">趨勢</th>
            </tr>
          </thead>
          <tbody>
            {[...data.signals].sort((a, b) => (b.lift_recent ?? -99) - (a.lift_recent ?? -99)).map((s) => {
              const d = (s.lift_recent ?? 0) - (s.lift_all ?? 0);
              return (
                <tr key={s.key} className="border-t border-edge">
                  <td className="px-3 py-1.5">{STYLE_LABELS[s.key] ?? s.key}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{s.hit_all}%</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{s.lift_all != null && s.lift_all > 0 ? "+" : ""}{s.lift_all}pp</td>
                  <td className="px-3 py-1.5 text-right tabular-nums font-medium">{s.hit_recent}%</td>
                  <td className={`px-3 py-1.5 text-right tabular-nums font-medium ${(s.lift_recent ?? 0) > 0 ? "text-up" : "text-down"}`}>
                    {s.lift_recent != null && s.lift_recent > 0 ? "+" : ""}{s.lift_recent}pp
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    {d > 2 ? <span className="text-up">▲ 增強</span> : d < -2 ? <span className="text-down">▼ 走弱</span> : <span className="text-muted">≈ 持平</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ─────────────────────────── 標籤共存機率 ───────────────────────────

// 組合鍵 "explosive+notice" → 中文顯示
function comboLabel(key: string): string {
  return key.split("+").map((t) => STYLE_LABELS[t] ?? t).join(" + ");
}

// 樣本明細（點樣本數開啟）：按月分組，點單筆跳個股頁並讓 K 線定位到訊號日
function ComboSamplesPanel({ combo, since }: { combo: string; since?: string }) {
  const { data, isLoading } = useComboSamples(combo, since);
  if (isLoading) return <p className="py-8 text-center text-sm text-muted">載入樣本中…</p>;
  if (!data || data.samples.length === 0) return <p className="py-8 text-center text-sm text-muted">無樣本</p>;
  const byMonth = new Map<string, typeof data.samples>();
  for (const s of data.samples) {
    const ym = s.date.slice(0, 7);
    if (!byMonth.has(ym)) byMonth.set(ym, []);
    byMonth.get(ym)!.push(s);
  }
  return (
    <div className="flex max-h-[65vh] flex-col gap-3 overflow-y-auto pr-1">
      {[...byMonth.entries()].map(([ym, list]) => {
        const hits = list.filter((s) => s.hit === true).length;
        const judged = list.filter((s) => s.hit != null).length;
        return (
          <div key={ym}>
            <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-muted">
              <span>{ym}</span>
              <span className="rounded bg-panel2 px-1.5 py-0.5 font-normal">
                {list.length} 筆{judged > 0 && ` · 碰到 ${hits}/${judged}（${Math.round((hits / judged) * 100)}%）`}
              </span>
            </div>
            <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
              {list.map((s, i) => (
                <Link
                  key={`${s.stock_id}-${s.date}-${i}`}
                  to={`/stocks/${s.stock_id}?at=${s.date}`}
                  className="flex items-center gap-2 rounded-lg border border-edge bg-panel2/40 px-2.5 py-1.5 text-sm hover:border-sky-700 hover:bg-panel2"
                  title="點擊跳到個股頁，K 線會定位到訊號日"
                >
                  <span className="text-xs tabular-nums text-muted">{s.date.slice(5)}</span>
                  <span className="font-medium">{s.name}</span>
                  <span className="text-xs text-muted">{s.stock_id}</span>
                  <span className="ml-auto flex items-center gap-2">
                    {s.hit === true && <span className="text-xs text-up">✓ 碰到{s.mfe_pct != null ? ` +${fmtNum(s.mfe_pct, 1)}%` : ""}</span>}
                    {s.hit === false && <span className="text-xs text-muted">✗ {s.ret_pct != null ? fmtPct(s.ret_pct) : ""}</span>}
                    {s.hit == null && <span className="text-xs text-sky-300">評估中</span>}
                  </span>
                </Link>
              ))}
            </div>
          </div>
        );
      })}
      <p className="text-[11px] text-muted">✓＝10 日內碰到 +10%（後附期間最高漲幅）；✗ 後為第 10 日收盤報酬。點任一筆開個股頁並定位當時 K 線。</p>
    </div>
  );
}

function CooccurrenceSection({ since }: { since?: string }) {
  const { data } = useTagCooccurrence(since);
  const [sortBy, setSortBy] = useState<"n" | "hit">("n");
  const [samplesFor, setSamplesFor] = useState<string | null>(null);
  // 勾選組合 → 進場推薦主清單只顯示這些精確組合（localStorage 溝通，全不勾＝不過濾）
  const [selected, setSelected] = useState<Set<string>>(() => new Set(readComboFilters()));
  const toggleCombo = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      localStorage.setItem(COMBO_FILTER_KEY, JSON.stringify([...next]));
      return next;
    });
  };
  const clearCombos = () => {
    setSelected(new Set());
    localStorage.setItem(COMBO_FILTER_KEY, "[]");
  };
  if (!data || data.matrix.length === 0) return null;
  const { tags, counts, matrix } = data;
  const combos = [...data.combos].sort((a, b) =>
    sortBy === "n" ? b.n - a.n : (b.hit_rate ?? -1) - (a.hit_rate ?? -1),
  );
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-base font-semibold">🔗 標籤共存機率 — 已有 A 時同時有 B 的比例</h2>
      <div className="overflow-x-auto rounded-xl border border-edge">
        <table className="w-full text-sm">
          <thead className="bg-panel2 text-xs text-muted">
            <tr>
              <th className="px-3 py-2 text-left">已有 ↓ / 同時有 →</th>
              {tags.map((t) => (
                <th key={t} className="px-2 py-2 text-right">{STYLE_LABELS[t] ?? t}</th>
              ))}
              <th className="px-3 py-2 text-right">樣本數</th>
            </tr>
          </thead>
          <tbody>
            {tags.map((rowTag, i) => (
              <tr key={rowTag} className="border-t border-edge">
                <td className="px-3 py-1.5 font-medium">{STYLE_LABELS[rowTag] ?? rowTag}</td>
                {tags.map((colTag, j) => {
                  const v = matrix[i]?.[j];
                  if (i === j) return <td key={colTag} className="px-2 py-1.5 text-right text-xs text-muted">—</td>;
                  return (
                    <td key={colTag} className="relative px-2 py-1.5 text-right tabular-nums">
                      {v == null ? (
                        <span className="text-xs text-muted">—</span>
                      ) : (
                        <>
                          <span
                            aria-hidden
                            className="absolute inset-y-1 right-0 rounded-sm bg-sky-500/25"
                            style={{ width: `${Math.min(v, 100) * 0.9}%` }}
                          />
                          <span className={`relative ${v >= 30 ? "font-semibold text-sky-200" : v >= 10 ? "text-gray-200" : "text-muted"}`}>
                            {v.toFixed(0)}%
                          </span>
                        </>
                      )}
                    </td>
                  );
                })}
                <td className="px-3 py-1.5 text-right text-xs tabular-nums text-muted">{counts[rowTag]?.toLocaleString() ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        讀法：橫列＝條件（已有該標籤的樣本），欄位＝其中同時帶另一標籤的比例。
        例如「處置動能」列的「爆發」欄＝處置股同時是爆發標籤的機率。樣本＝波段軌每日評分列；
        注意/處置窗與徽章同口徑（公告後 ~5/~10 交易日）。列樣本 &lt;30 不顯示。
      </p>

      {combos.length > 0 && (
        <>
          <div className="mb-2 mt-5 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">精確組合全枚舉 — 每種實際出現的標籤集合（含成效）</h3>
            <div className="flex items-center gap-2">
              {selected.size > 0 && (
                <span className="flex items-center gap-1.5 rounded-full border border-sky-800/60 bg-sky-950/40 px-2.5 py-0.5 text-xs text-sky-300">
                  已勾 {selected.size} 組 → 進場推薦只顯示這些組合
                  <button onClick={clearCombos} className="text-muted hover:text-gray-200" title="清除全部">✕</button>
                </span>
              )}
              <div className="flex gap-1">
                {([["n", "按樣本數"], ["hit", "按命中率"]] as const).map(([k, label]) => (
                  <button key={k} onClick={() => setSortBy(k)}
                    className={`rounded-md px-2.5 py-1 text-xs ${sortBy === k ? "bg-sky-900/60 text-sky-200" : "text-muted hover:bg-panel2"}`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="overflow-x-auto rounded-xl border border-edge">
            <table className="w-full text-sm">
              <thead className="bg-panel2 text-xs text-muted">
                <tr>
                  <th className="px-2 py-2 text-center" title="勾選＝進場推薦主清單只顯示勾中的組合">篩</th>
                  <th className="px-3 py-2 text-left">組合（精確集合，不含其他標籤）</th>
                  <th className="px-3 py-2 text-right">樣本數</th>
                  <th className="px-3 py-2 text-right">占比</th>
                  <th className="px-3 py-2 text-right">10日碰到率</th>
                  <th className="px-3 py-2 text-right">10日均報酬</th>
                  <th className="px-3 py-2 text-right">均MFE</th>
                  <th className="px-3 py-2 text-right">均MAE</th>
                </tr>
              </thead>
              <tbody>
                {combos.map((c) => (
                  <tr key={c.key} className={`border-t border-edge hover:bg-panel/60 ${selected.has(c.key) ? "bg-sky-950/30" : ""}`}>
                    <td className="px-2 py-1.5 text-center">
                      <input type="checkbox" checked={selected.has(c.key)} onChange={() => toggleCombo(c.key)}
                        className="accent-sky-500" />
                    </td>
                    <td className="px-3 py-1.5">{comboLabel(c.key)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      <button
                        onClick={() => setSamplesFor(c.key)}
                        className="rounded px-1.5 py-0.5 text-sky-300 underline decoration-dotted underline-offset-2 hover:bg-sky-950/40"
                        title="查看樣本明細（哪些股票、哪些時段；可點單筆跳個股 K 線）"
                      >
                        {c.n.toLocaleString()}
                      </button>
                    </td>
                    <td className="px-3 py-1.5 text-right text-xs tabular-nums text-muted">{c.share_pct}%</td>
                    <td className={`px-3 py-1.5 text-right tabular-nums ${c.hit_rate != null && c.hit_rate >= 0.5 ? "font-semibold text-up" : ""}`}>
                      {c.hit_rate != null ? fmtPct(c.hit_rate * 100) : "—"}
                    </td>
                    <td className={`px-3 py-1.5 text-right tabular-nums ${changeColor(c.avg_ret_pct)}`}>{fmtPct(c.avg_ret_pct)}</td>
                    <td className="px-3 py-1.5 text-right text-xs tabular-nums text-muted">{fmtPct(c.avg_mfe_pct)}</td>
                    <td className="px-3 py-1.5 text-right text-xs tabular-nums text-down">{fmtPct(c.avg_mae_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted">
            「精確集合」＝樣本當日的完整標籤組合（單標籤列＝該標籤獨佔、無其他共存）。
            成效口徑與分組統計相同：隔日最高價進場、10 交易日窗（與推薦目標一致）；n&lt;30 的組合不列。
            高命中組合請再用模擬倉驗證可交易性（路徑波動大的組合緊停損會先被掃）。
          </p>
          <Modal
            title={samplesFor ? `樣本明細 — ${comboLabel(samplesFor)}` : "樣本明細"}
            open={samplesFor != null}
            onClose={() => setSamplesFor(null)}
          >
            {samplesFor && <ComboSamplesPanel combo={samplesFor} since={since} />}
          </Modal>
        </>
      )}
    </section>
  );
}

// ─────────────────────────── 模擬倉 ───────────────────────────

function PaperSection({ since }: { since?: string }) {
  const [style, setStyle] = useState<LabStyle>("pop");
  const [probMin, setProbMin] = useState(50);
  const [topN, setTopN] = useState(3);
  const [holdDays, setHoldDays] = useState(20);
  const [stopPct, setStopPct] = useState(0);   // 0＝不設停損（波段軌定版）
  // 波段軌 2026-08-24 定版：預設不設停損（−8% 停損實測讓命中率掉 25pp）。
  // 實驗室保留切換，因為這裡本來就是拿來比的。
  const { data, isLoading } = usePaperSimulate({
    since, style, probMin, topN, holdDays,
    stopPct: stopPct === 0 ? undefined : stopPct,
  });
  const [showAll, setShowAll] = useState(false);

  const s = data?.stats;
  const cell = (label: string, value: string, color?: string) => (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${color ?? ""}`}>{value}</div>
    </div>
  );

  return (
    <section className="mb-8">
      <h2 className="mb-2 text-base font-semibold">📈 模擬倉 — 如果每天照單操作</h2>
      <div className="mb-3 flex flex-wrap items-end gap-4 rounded-xl border border-edge bg-panel p-3 text-sm">
        <label className="flex flex-col gap-1 text-xs text-muted">
          風格
          <select value={style} onChange={(e) => setStyle(e.target.value as LabStyle)}
            className="rounded-md border border-edge bg-panel2 px-2 py-1 text-sm text-gray-200">
            {Object.entries(STYLE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          機率門檻 ≥{probMin}%
          <input type="range" min={0} max={90} step={10} value={probMin}
            onChange={(e) => setProbMin(Number(e.target.value))} className="w-32" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          每日最多 {topN} 檔
          <input type="range" min={1} max={10} value={topN}
            onChange={(e) => setTopN(Number(e.target.value))} className="w-28" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          最長持有 {holdDays} 日
          <input type="range" min={5} max={60} step={5} value={holdDays}
            onChange={(e) => setHoldDays(Number(e.target.value))} className="w-28" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted"
          title="波段軌定版＝不設停損：這條軌挑的是高波動標的，停損線會切在它自己的呼吸幅度上（實測 −8% 讓命中率掉 25pp，而期間浮虧>10% 的部位仍有 47% 最後照樣達標）。想看停損版可以在這裡切換比較。">
          停損
          <select value={stopPct} onChange={(e) => setStopPct(Number(e.target.value))}
            className="rounded-md border border-edge bg-panel2 px-2 py-1 text-sm text-gray-200">
            <option value={0}>不設（定版）</option>
            <option value={8}>−8%</option>
            <option value={10}>−10%</option>
            <option value={15}>−15%</option>
          </select>
        </label>
        <div className="ml-auto text-xs text-muted">
          規則：隔日高進場 → {stopPct === 0 ? "" : "觸停損/"}停利(+{data?.target_pct ?? 10}%)/逾期收盤出，每筆等權
        </div>
      </div>

      {isLoading && <p className="text-muted">模擬中…</p>}
      {s && (
        <>
          <div className="mb-3 grid grid-cols-2 gap-4 rounded-xl border border-edge bg-panel p-4 sm:grid-cols-6">
            {cell("總筆數", `${s.trades}（持有中 ${s.open}）`)}
            {cell("勝率", s.win_rate != null ? fmtPct(s.win_rate * 100) : "—",
              s.win_rate != null && s.win_rate >= 0.5 ? "text-up" : undefined)}
            {cell("平均每筆", fmtPct(s.avg_return_pct), changeColor(s.avg_return_pct))}
            {cell("累計已實現", fmtPct(s.total_return_pct), changeColor(s.total_return_pct))}
            {cell("平均持有", s.avg_days_held != null ? `${s.avg_days_held} 日` : "—")}
            {cell("最大回撤", s.max_drawdown_pct != null ? `−${fmtNum(s.max_drawdown_pct)}%` : "—", "text-down")}
          </div>
          {data && data.equity.length >= 2 && <EquityChart data={data} />}
          {data && data.positions.length > 0 && (
            <div className="overflow-hidden rounded-xl border border-edge">
              <table className="w-full text-sm">
                <thead className="bg-panel2 text-xs text-muted">
                  <tr>
                    <th className="px-3 py-2 text-left">股票</th>
                    <th className="px-3 py-2 text-left">訊號日</th>
                    <th className="px-3 py-2 text-right">進場</th>
                    <th className="px-3 py-2 text-right">{stopPct === 0 ? "停利" : "停損/停利"}</th>
                    <th className="px-3 py-2 text-left">出場</th>
                    <th className="px-3 py-2 text-right">報酬</th>
                    <th className="px-3 py-2 text-right">持有</th>
                    <th className="px-3 py-2 text-right">當時機率</th>
                  </tr>
                </thead>
                <tbody>
                  {(showAll ? data.positions : data.positions.slice(0, 15)).map((p, i) => (
                    <tr key={`${p.stock_id}-${p.signal_date}-${i}`} className="border-t border-edge hover:bg-panel/60">
                      <td className="px-3 py-1.5">
                        <Link to={`/stocks/${p.stock_id}`} className="hover:underline">
                          <span className="font-medium">{p.name}</span>
                          <span className="ml-1 text-xs text-muted">{p.stock_id}</span>
                        </Link>
                      </td>
                      <td className="px-3 py-1.5 text-xs text-muted">{p.signal_date}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmtNum(p.entry_price)}</td>
                      <td className="px-3 py-1.5 text-right text-xs tabular-nums text-muted">
                        {p.stop_price != null && `${fmtNum(p.stop_price)} / `}{fmtNum(p.target_price)}
                      </td>
                      <td className="px-3 py-1.5 text-xs">
                        {p.status === "open"
                          ? <span className="text-sky-300">持有中</span>
                          : <span className="text-muted">{p.exit_date} · {p.exit_reason === "target" ? "🎯停利" : p.exit_reason === "stop" ? "🛑停損" : "⏰逾期"}</span>}
                      </td>
                      <td className={`px-3 py-1.5 text-right tabular-nums ${changeColor(p.return_pct)}`}>{fmtPct(p.return_pct)}</td>
                      <td className="px-3 py-1.5 text-right text-xs tabular-nums text-muted">{p.days_held} 日</td>
                      <td className="px-3 py-1.5 text-right text-xs tabular-nums text-muted">{p.prob_hit != null ? `${p.prob_hit}%` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.positions.length > 15 && (
                <button onClick={() => setShowAll(!showAll)}
                  className="w-full border-t border-edge py-2 text-xs text-sky-400 hover:bg-panel/60">
                  {showAll ? "收合" : `展開全部 ${data.positions.length} 筆`}
                </button>
              )}
            </div>
          )}
          {data && data.positions.length === 0 && (
            <div className="rounded-xl border border-dashed border-edge py-10 text-center text-muted">
              此條件下沒有符合的歷史推薦，放寬機率門檻或拉長期間試試
            </div>
          )}
        </>
      )}
    </section>
  );
}

function EquityChart({ data }: { data: PaperSimResponse }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const last = data.equity[data.equity.length - 1]?.cum_return_pct ?? 0;
    chart.setOption({
      backgroundColor: "transparent",
      grid: { top: 16, right: 16, bottom: 24, left: 52 },
      tooltip: { trigger: "axis", backgroundColor: "#1f2430", borderColor: "#374151", textStyle: { color: "#e5e7eb", fontSize: 12 } },
      xAxis: { type: "category", data: data.equity.map((p) => p.date), axisLabel: { color: "#6b7280", fontSize: 10 }, axisLine: { lineStyle: { color: "#374151" } } },
      yAxis: { type: "value", name: "累計%", nameTextStyle: { color: "#6b7280", fontSize: 10 }, axisLabel: { color: "#6b7280", fontSize: 10 }, splitLine: { lineStyle: { color: "#1f2430" } } },
      series: [{
        name: "累計已實現報酬",
        type: "line", smooth: true, symbol: "none",
        data: data.equity.map((p) => p.cum_return_pct),
        lineStyle: { color: last >= 0 ? "#f43f5e" : "#10b981", width: 2 },
        areaStyle: { opacity: 0.08, color: last >= 0 ? "#f43f5e" : "#10b981" },
      }],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); chart.dispose(); };
  }, [data]);
  return <div ref={ref} className="mb-3 h-52 w-full rounded-xl border border-edge bg-panel p-2" />;
}

// ─────────────────────────── 勝率分析 ───────────────────────────

function StatGroupTable({ title, rows }: { title: string; rows: LookbackGroupStat[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-edge">
      <div className="bg-panel2 px-3 py-2 text-xs font-medium text-gray-300">{title}</div>
      <table className="w-full text-sm">
        <thead className="text-xs text-muted">
          <tr>
            <th className="px-3 py-1.5 text-left">分組</th>
            <th className="px-3 py-1.5 text-right">樣本</th>
            <th className="px-3 py-1.5 text-right">10日碰到率</th>
            <th className="px-3 py-1.5 text-right">平均報酬</th>
            <th className="px-3 py-1.5 text-right">平均最深回撤</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-edge">
              <td className="px-3 py-1.5">{STYLE_LABELS[r.key] ?? r.key}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-muted">{r.n}</td>
              <td className={`px-3 py-1.5 text-right tabular-nums ${r.hit_rate != null && r.hit_rate >= 0.5 ? "text-up" : ""}`}>
                {r.hit_rate != null ? fmtPct(r.hit_rate * 100) : "—"}
              </td>
              <td className={`px-3 py-1.5 text-right tabular-nums ${changeColor(r.avg_return_pct)}`}>{fmtPct(r.avg_return_pct)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-down">{fmtPct(r.avg_mae_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatsSection({ since }: { since?: string }) {
  const { data, isLoading } = useLookbackStats(since);
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-base font-semibold">🎯 勝率分析 — 哪類推薦準</h2>
      {isLoading && <p className="text-muted">統計中…</p>}
      {data && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <StatGroupTable title="按風格標籤" rows={data.by_style} />
          <StatGroupTable title="按當日分數帶" rows={data.by_score_bin} />
        </div>
      )}
      {data && (
        <p className="mt-2 text-xs text-muted">
          樣本已排除距今不足 {data.min_age_days} 個交易日的推薦（還沒走完不計）；平均報酬＝進場錨抱到今天。
        </p>
      )}
    </section>
  );
}

// ─────────────────────────── 參數敏感度 ───────────────────────────

/** 命中率 + Wilson 95% 區間的水平條。區間畫出來，薄樣本的長條就騙不了人。 */
function HitBar({ rate, lo, hi, max, dim }: {
  rate: number | null; lo: number | null; hi: number | null; max: number; dim: boolean;
}) {
  if (rate == null) return <span className="text-muted">—</span>;
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-2 flex-1 overflow-hidden rounded bg-panel2">
        {lo != null && hi != null && (
          <div className="absolute inset-y-0 bg-sky-500/20"
            style={{ left: pct(lo), width: `calc(${pct(hi)} - ${pct(lo)})` }} />
        )}
        <div className={`absolute inset-y-0 left-0 ${dim ? "bg-sky-500/25" : "bg-sky-500/70"}`}
          style={{ width: pct(rate) }} />
      </div>
      <span className="w-11 text-right text-xs tabular-nums">{fmtPct(rate * 100)}</span>
    </div>
  );
}

function SensitivitySection({ since }: { since?: string }) {
  const { data, isLoading } = useLookbackSensitivity(since);
  const max = Math.max(
    0.05,
    ...(data?.points.map((p) => p.hit_rate_hi ?? p.hit_rate ?? 0) ?? [0.05]),
  );
  const calMax = Math.max(
    5,
    ...(data?.calibration.flatMap((b) => [b.pred_avg, (b.hit_rate ?? 0) * 100]) ?? [5]),
  );
  return (
    <section className="mb-8">
      <h2 className="mb-1 text-base font-semibold">🎛️ 參數敏感度 — 機率門檻拉多高才划算</h2>
      {isLoading && <p className="text-muted">掃描中…</p>}
      {data && (
        <>
          <p className="mb-2 text-xs text-muted">
            涵蓋 <b className="text-body">{data.entry_days}</b> 個進場日
            ／基準碰到率 <b className="text-body">{fmtPct((data.base_hit_rate ?? 0) * 100)}</b>
            （不設門檻）。同日個股高度相關，<b className="text-body">日數才是有效樣本數</b>。
          </p>
          <div className="overflow-x-auto rounded-xl border border-edge">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-panel2 text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 text-left">機率門檻</th>
                  <th className="px-3 py-2 text-right">樣本 / 日數</th>
                  <th className="px-3 py-2 text-right">檔/日</th>
                  <th className="px-3 py-2 text-left">10日碰到率（含95%區間）</th>
                  <th className="px-3 py-2 text-right">倍數</th>
                  <th className="px-3 py-2 text-right">報酬 開盤錨</th>
                  <th className="px-3 py-2 text-right">最大有利／不利</th>
                </tr>
              </thead>
              <tbody>
                {data.points.map((p) => (
                  <tr key={p.prob_min} className={`border-t border-edge ${p.reliable ? "" : "opacity-45"}`}>
                    <td className="px-3 py-1.5 tabular-nums">
                      ≥ {p.prob_min}%
                      {!p.reliable && <span className="ml-1 rounded bg-amber-900/40 px-1 text-[10px] text-amber-300">樣本不足</span>}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted">
                      {fmtNum(p.n)} <span className="text-[11px]">/ {p.days} 天</span>
                      {p.day_cover != null && p.day_cover < 0.5 && (
                        <span className="ml-1 text-[10px] text-amber-400">僅 {Math.round(p.day_cover * 100)}% 交易日有貨</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted">{p.avg_daily_n ?? "—"}</td>
                    <td className="px-3 py-1.5">
                      <HitBar rate={p.hit_rate} lo={p.hit_rate_lo} hi={p.hit_rate_hi} max={max} dim={!p.reliable} />
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{p.lift != null ? `${p.lift}×` : "—"}</td>
                    <td className={`px-3 py-1.5 text-right tabular-nums ${changeColor(p.avg_return_open_pct)}`}>
                      {fmtPct(p.avg_return_open_pct)}
                    </td>
                    <td className="px-3 py-1.5 text-right text-xs tabular-nums text-muted">
                      <span className="text-rose-400">{fmtPct(p.avg_mfe_pct)}</span>
                      {" / "}
                      <span className="text-emerald-400">{fmtPct(p.avg_mae_pct)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-muted">
            門檻只挑少不挑好時，<b className="text-body">倍數</b>不會跟著升；報酬用<b className="text-body">隔日開盤錨</b>
            （買在隔日最高的最壞情境會讓報酬恆為負，那是追高懲罰、不是策略績效）。
            碰到率是「摸到過 +10%」，<b className="text-body">最大不利</b>提醒它可能是先跌爛才彈上去的。
          </p>

          <h3 className="mb-1 mt-5 text-sm font-semibold">📐 機率校準 — 說 40% 真的有 40% 嗎</h3>
          <div className="overflow-x-auto rounded-xl border border-edge">
            <table className="w-full min-w-[620px] text-sm">
              <thead className="bg-panel2 text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 text-left">預測機率帶</th>
                  <th className="px-3 py-2 text-right">樣本 / 日數</th>
                  <th className="px-3 py-2 text-right">查表說</th>
                  <th className="px-3 py-2 text-left">實際（含95%區間）</th>
                  <th className="px-3 py-2 text-right">誤差</th>
                </tr>
              </thead>
              <tbody>
                {data.calibration.map((b) => (
                  <tr key={`${b.lo}-${b.hi}`} className={`border-t border-edge ${b.reliable ? "" : "opacity-45"}`}>
                    <td className="px-3 py-1.5 tabular-nums">{b.lo}–{b.hi}%</td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted">
                      {fmtNum(b.n)} <span className="text-[11px]">/ {b.days} 天</span>
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted">{fmtPct(b.pred_avg)}</td>
                    <td className="px-3 py-1.5">
                      <HitBar rate={b.hit_rate} lo={b.hit_rate_lo} hi={b.hit_rate_hi} max={calMax / 100} dim={!b.reliable} />
                    </td>
                    {/* 不用 changeColor：負誤差=機率高估，染成台股跌色的綠會讀成「沒事」 */}
                    <td className={`px-3 py-1.5 text-right tabular-nums ${
                      b.err_pp == null ? "text-muted"
                        : b.err_pp <= -10 ? "text-amber-300"
                        : b.err_pp <= -4 ? "text-amber-400/70" : "text-muted"}`}>
                      {b.err_pp != null ? `${b.err_pp > 0 ? "+" : ""}${b.err_pp} pp` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-muted">
            誤差為<b className="text-body">負＝機率高估</b>，且愈往高機率端愈嚴重。累積門檻表看不出這件事，
            因為「≥40%」那格把 40% 與 60% 的樣本混在一起。查表以 2021 年起全市場為母體，
            而 10 日碰到率本身逐季在 9.5%～35% 之間漂移，故絕對機率天生帶 regime 偏移，看<b className="text-body">倍數</b>比看絕對值穩。
          </p>
        </>
      )}
    </section>
  );
}
