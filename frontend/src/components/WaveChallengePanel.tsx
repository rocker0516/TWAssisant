import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import { useWaveChallenge, type WaveCandidate, type WaveChallenge, type WaveEpisode } from "../api/client";

/** 波段命中挑戰（策略室）：把「命中率能不能更高／≥70% 的案例在哪」整段研究攤開。
 *
 * 資料＝ data/wave_challenge.json（scripts/wave_hit_challenge.py --json 凍結產出），
 * 端點只直讀。這裡刻意把**失敗的路**也畫出來（時點開關、桶內歸因），因為這個題目最
 * 容易犯的錯是「看到 80% 就上車」——真正的資訊是那 80% 由什麼買單、以及段間離散多大。
 */

const HIT_COLOR = (h: number | null | undefined) =>
  h == null ? "text-muted" : h >= 70 ? "text-up" : h >= 55 ? "text-sky-300" : "text-down";

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-edge bg-panel2/40 px-3 py-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

/** 段級色塊：崩勢型規則的真實面貌——一排段，顏色＝該段命中率，寬度不等於重要性。 */
function EpisodeStrip({ items }: { items: WaveEpisode[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((e) => (
        <div
          key={e.start + e.n}
          title={`${e.start}｜${e.days} 日 ${e.n} 筆｜命中 ${e.hit}%｜平均最深回落 ${e.mae}%`}
          className={`rounded px-1.5 py-1 text-[11px] tabular-nums ${
            e.n < 10
              ? "border border-dashed border-edge text-muted"
              : e.hit >= 70
                ? "bg-emerald-500/20 text-emerald-200"
                : e.hit >= 50
                  ? "bg-sky-500/15 text-sky-200"
                  : "bg-red-500/15 text-red-200"
          }`}
        >
          {e.start} <b>{Math.round(e.hit)}%</b>
          <span className="opacity-60"> ({e.n})</span>
        </div>
      ))}
    </div>
  );
}

/** ATR 階梯：命中率–案例數前緣。單調上升本身就是反過擬合的證據，所以用線圖呈現。 */
function LadderChart({ data }: { data: WaveChallenge }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const xs = data.ladder.map((r) => `${r.atr}%`);
    const thin = data.ladder.map((r) => r.holdout.n < 30);
    chart.setOption({
      grid: { left: 44, right: 52, top: 34, bottom: 26 },
      tooltip: {
        trigger: "axis",
        formatter: (ps: any[]) => {
          const i = ps[0].dataIndex;
          const r = data.ladder[i];
          return (
            `<b>ATR &gt; ${r.atr}%</b>（＋大盤≤-2.3％＋流動篩）<br/>` +
            `挖掘 ${r.mine.hit}%（n=${r.mine.n} / ${r.mine.days} 日 / ${r.mine.per_day} 檔·日 / MAE ${r.mine.mae}%）<br/>` +
            `holdout ${r.holdout.hit}%（n=${r.holdout.n} / ${r.holdout.days} 日 / MAE ${r.holdout.mae}%）` +
            `${r.holdout.n < 30 ? " ← 樣本已不足，不可評估" : ""}<br/>` +
            `段級中位 ${r.ep_median ?? "—"}%、最差段 ${r.ep_min ?? "—"}%、≥70% ${r.ep_ge70}/${r.ep_n} 段<br/>` +
            `崩日覆蓋 ${r.day_cover}%、每月約 ${r.per_month} 個案例`
          );
        },
      },
      legend: { top: 2, textStyle: { color: "#94a3b8", fontSize: 11 }, itemHeight: 8 },
      xAxis: { type: "category", data: xs, name: "ATR 門檻", nameTextStyle: { color: "#64748b" },
               axisLabel: { color: "#94a3b8", fontSize: 11 } },
      yAxis: [
        { type: "value", name: "命中率 %", max: 100, nameTextStyle: { color: "#64748b", fontSize: 10 },
          axisLabel: { color: "#94a3b8", fontSize: 11 }, splitLine: { lineStyle: { color: "#1e293b" } } },
        { type: "value", name: "月案例", nameTextStyle: { color: "#64748b", fontSize: 10 },
          axisLabel: { color: "#64748b", fontSize: 11 }, splitLine: { show: false } },
      ],
      series: [
        { name: "月案例數", type: "bar", yAxisIndex: 1, data: data.ladder.map((r) => r.per_month),
          itemStyle: { color: "rgba(100,116,139,0.35)" }, barWidth: "42%" },
        { name: "挖掘窗命中", type: "line", data: data.ladder.map((r) => r.mine.hit),
          smooth: true, symbolSize: 6, lineStyle: { width: 2 }, itemStyle: { color: "#38bdf8" } },
        { name: "holdout 命中", type: "line", data: data.ladder.map((r, i) => ({
            value: r.holdout.hit,
            itemStyle: { color: thin[i] ? "#64748b" : "#f472b6" },
            symbol: thin[i] ? "emptyCircle" : "circle",
          })),
          smooth: true, symbolSize: 7, lineStyle: { width: 2, color: "#f472b6" } },
        { name: "段級中位", type: "line", data: data.ladder.map((r) => r.ep_median),
          smooth: true, symbolSize: 4, lineStyle: { width: 1.5, type: "dashed", color: "#fbbf24" },
          itemStyle: { color: "#fbbf24" } },
        { name: "70% 線", type: "line", data: data.ladder.map(() => 70), symbol: "none",
          lineStyle: { width: 1, type: "dotted", color: "#34d399" }, tooltip: { show: false } },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [data]);
  return <div ref={ref} className="h-72 w-full" />;
}

function CandidateTable({ items }: { items: WaveCandidate[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-edge">
      <table className="w-full text-sm">
        <thead className="bg-panel2 text-xs text-muted">
          <tr>
            <th className="px-3 py-2 text-left">規則</th>
            <th className="px-2 py-2 text-right">挖掘命中</th>
            <th className="px-2 py-2 text-right">holdout 命中</th>
            <th className="px-2 py-2 text-right">MAE 挖/後</th>
            <th className="px-2 py-2 text-right">段中位</th>
            <th className="px-2 py-2 text-right">最差段</th>
            <th className="px-2 py-2 text-right">≥70% 段</th>
            <th className="px-2 py-2 text-right">月案例</th>
          </tr>
        </thead>
        <tbody>
          {items.map((c) => {
            const adopted = c.id === "⑨";
            return (
              <tr key={c.id} className={`border-t border-edge ${adopted ? "bg-emerald-950/25" : ""}`}>
                <td className="px-3 py-2">
                  {adopted && <span className="mr-1 rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-200">已採用</span>}
                  <span className={adopted ? "font-medium" : ""}>{c.rule}</span>
                </td>
                <td className={`px-2 py-2 text-right tabular-nums ${HIT_COLOR(c.mine.hit)}`}>
                  {c.mine.hit}%<span className="ml-1 text-[10px] text-muted">n{c.mine.n}/{c.mine.days}日</span>
                </td>
                <td className={`px-2 py-2 text-right tabular-nums ${HIT_COLOR(c.holdout.hit)}`}>
                  {c.holdout.hit}%<span className="ml-1 text-[10px] text-muted">n{c.holdout.n}/{c.holdout.days}日</span>
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-muted">
                  {c.mine.mae}/{c.holdout.mae}%
                </td>
                <td className={`px-2 py-2 text-right tabular-nums ${HIT_COLOR(c.ep.median)}`}>{c.ep.median ?? "—"}%</td>
                <td className="px-2 py-2 text-right tabular-nums text-down">{c.ep.min ?? "—"}%</td>
                <td className="px-2 py-2 text-right tabular-nums text-muted">{c.ep.ge70}/{c.ep.n_eff}</td>
                <td className="px-2 py-2 text-right tabular-nums text-muted">{c.per_month}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Collapse({ title, hint, children, defaultOpen = false }: {
  title: string; hint?: string; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl border border-edge bg-panel">
      <button onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-panel2/50">
        <span className="text-muted">{open ? "▾" : "▸"}</span>
        <span className="font-medium">{title}</span>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </button>
      {open && <div className="border-t border-edge px-3 py-3">{children}</div>}
    </div>
  );
}

export function WaveChallengeSection() {
  const { data } = useWaveChallenge();
  if (!data) return null;
  if (!data.available) {
    return (
      <section className="mb-8">
        <h2 className="mb-2 text-base font-semibold">🎯 波段命中挑戰</h2>
        <p className="rounded-xl border border-dashed border-edge bg-panel p-4 text-sm text-muted">{data.note}</p>
      </section>
    );
  }
  const adoptedRow = data.candidates.find((c) => c.id === "⑨");
  const cur = data.candidates.find((c) => c.id === "①");
  const f = data.frontier;

  return (
    <section className="mb-8">
      <h2 className="mb-1 text-base font-semibold">
        🎯 波段命中挑戰 — 命中率能拉多高？≥70% 的案例在哪？
      </h2>
      <p className="mb-3 text-xs text-muted">
        目標＝{data.target_label}。挖掘窗 {data.windows.mine} 定義規則、holdout {data.windows.holdout} 只驗一次
        （全市場基率 {data.base_rate.mine}% / {data.base_rate.holdout}%）。
        產出時間 {data.generated_at}，重跑 <code className="text-[11px]">scripts/wave_hit_challenge.py --json</code> 會更新。
      </p>

      {adoptedRow && cur && (
        <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="深跌反攻 · 改版前 holdout" value={`${cur.holdout.hit}%`} sub={`段中位 ${cur.ep.median}%`} />
          <Stat label="改版後 holdout" value={`${adoptedRow.holdout.hit}%`} sub={`段中位 ${adoptedRow.ep.median}%`} />
          <Stat label="最差的一段" value={`${adoptedRow.ep.min}%`} sub={`${adoptedRow.ep.ge70}/${adoptedRow.ep.n_eff} 段 ≥70%`} />
          <Stat label="兩窗皆 ≥70% 的規則" value={`${f?.over70.length ?? 0} 組`}
                sub={`虛無校準 ${f?.null.draws ?? 0} 抽樣中 ${f?.null.ge70 ?? 0} 組`} />
        </div>
      )}

      <div className="mb-3 rounded-xl border border-edge bg-panel p-3 text-sm leading-relaxed">
        <p className="mb-1">
          <b className="text-emerald-300">① 命中率可以顯著提高，槓桿幾乎全在「池怎麼定義」。</b>{" "}
          最大一筆是可修的產品缺陷：舊版深跌反攻要求 (強勢延伸|故事股)，這個條件與「高波動」在資料上幾乎互斥，
          等於這條軌<b>結構性地選不到真正的高波動反彈標的</b>。拆掉它、把 ATR 門檻拉到 9%、加上流動篩後，
          holdout 由 {cur?.holdout.hit}% → {adoptedRow?.holdout.hit}%，而且 MAE 反而更淺
          （{cur?.holdout.mae}% → {adoptedRow?.holdout.mae}%）。
        </p>
        <p>
          <b className="text-amber-300">② ≥70% 確實存在（推翻上一輪結論），但它由「波動度」買單，不是選股能力。</b>{" "}
          窮舉 {f?.tested} 組規則有 {f?.over70.length} 組兩窗皆 ≥70%，虛無校準證明不是多重比較的產物；
          但同日同 ATR 桶內做配對後，那些「第三軸」的增量掉到 0 或負——所以定案<b>只調 ATR 門檻與流動篩</b>，
          不把第三軸寫進規則。代價寫在下面那條前緣曲線上：要 70%，就得接受每月只剩幾個案例。
        </p>
      </div>

      <div className="mb-3 rounded-xl border border-edge bg-panel p-3">
        <div className="mb-1 flex items-baseline gap-2">
          <h3 className="text-sm font-medium">命中率–案例數前緣（ATR 門檻是唯一單調旋鈕）</h3>
          <span className="text-[11px] text-muted">
            池＝ATR &gt; 門檻 ∧ 大盤距季線 ≤ -2.3% ∧ 股價≥20 元 ∧ 成交值≥1 億；共 {data.deep_days} 個崩日
          </span>
        </div>
        <LadderChart data={data} />
        <p className="text-[11px] text-muted">
          空心灰點＝holdout 樣本 &lt; 30 筆，<b>已不可評估</b>（ATR≥10% 之後只剩個位數）——所以定案停在 9%，
          不追更漂亮的 81%。黃色虛線是段級中位：它比日加權命中率誠實，因為同一段裡每天選到的是同一批股票。
        </p>
      </div>

      <div className="mb-3">
        <h3 className="mb-1 text-sm font-medium">定案候選比較</h3>
        <CandidateTable items={data.candidates} />
        <p className="mt-1 text-[11px] text-muted">
          「最差段」只在<b>樣本≥10 筆的段</b>裡取，而「≥70% 段」欄的分母就是這種段有幾個。
          門檻愈嚴、合格的段愈少，最差段會因此看起來變好——⑩ 的「最差 65%」是只剩 5 段的結果，
          不是它比較安全。比較時請看同一個分母。
        </p>
      </div>

      {data.oos && data.oos.n > 0 && (
        <div className="mb-3 rounded-xl border border-sky-800/60 bg-sky-950/20 p-3">
          <div className="mb-1 flex flex-wrap items-baseline gap-2">
            <span className="text-sm font-medium text-sky-200">真 out-of-sample：規則定案之後才發生的崩段</span>
            <span className="text-[11px] text-muted">
              研究標籤只到 2026-07-01，這一段的門檻沒有一個是在這裡挑的
            </span>
          </div>
          <div className="mb-2 flex flex-wrap items-baseline gap-3 text-sm">
            <span className="tabular-nums">
              合計 <b className={HIT_COLOR(data.oos.hit)}>{data.oos.hit}%</b>
              <span className="ml-1 text-[11px] text-muted">n={data.oos.n}／{data.oos.days.length} 日</span>
            </span>
            <span className="text-[11px] text-muted">平均最深回落 {data.oos.mae}%</span>
          </div>
          <div className="mb-2 flex flex-wrap gap-1">
            {data.oos.days.map((x) => (
              <span key={x.date} title={`${x.n} 檔，平均最深回落 ${x.mae}%`}
                className={`rounded px-1.5 py-0.5 text-[11px] tabular-nums ${
                  x.hit >= 70 ? "bg-emerald-500/20 text-emerald-200"
                    : x.hit >= 50 ? "bg-sky-500/15 text-sky-200" : "bg-red-500/15 text-red-200"}`}>
                {x.date.slice(5)} {Math.round(x.hit)}%<span className="opacity-60"> ({x.n})</span>
              </span>
            ))}
          </div>
          <p className="text-[11px] text-muted">{data.oos.note}</p>
        </div>
      )}

      {data.adopted && (
        <div className="mb-3 rounded-xl border border-emerald-800/60 bg-emerald-950/20 p-3">
          <div className="mb-1 text-sm font-medium text-emerald-200">已套用到「深跌反攻」風格</div>
          <div className="mb-2 font-mono text-xs text-emerald-100/90">{data.adopted.rule}</div>
          <ul className="mb-2 list-inside list-disc text-xs text-muted">
            {data.adopted.changes.map((c) => <li key={c}>{c}</li>)}
          </ul>
          <div className="mb-1 text-xs text-muted">逐個崩段的實際命中（虛線框＝樣本 &lt; 10 筆，不列入離散度）：</div>
          <EpisodeStrip items={data.adopted.episodes} />
        </div>
      )}

      <div className="flex flex-col gap-2">
        <Collapse title={`兩窗皆 ≥70% 的 ${f?.over70.length ?? 0} 組規則`}
                  hint={`窮舉 ${f?.tested} 組、可評估 ${f?.evaluable} 組；虛無校準 ${f?.null.draws} 次抽樣 0 組達標`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="px-2 py-1 text-left">規則</th>
                  <th className="px-2 py-1 text-right">挖掘</th>
                  <th className="px-2 py-1 text-right">holdout</th>
                  <th className="px-2 py-1 text-right">總案例</th>
                  <th className="px-2 py-1 text-right">月案例</th>
                </tr>
              </thead>
              <tbody>
                {f?.over70.map((r) => (
                  <tr key={r.rule} className="border-t border-edge/60">
                    <td className="px-2 py-1 font-mono">{r.rule}</td>
                    <td className="px-2 py-1 text-right tabular-nums text-up">
                      {r.m_hit}%<span className="ml-1 text-[10px] text-muted">n{r.m_n}/{r.m_days}日</span>
                    </td>
                    <td className="px-2 py-1 text-right tabular-nums text-up">
                      {r.h_hit}%<span className="ml-1 text-[10px] text-muted">n{r.h_n}/{r.h_days}日</span>
                    </td>
                    <td className="px-2 py-1 text-right tabular-nums text-muted">{r.cases}</td>
                    <td className="px-2 py-1 text-right tabular-nums text-muted">{r.per_month}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-muted">
            虛無校準＝把 hit10 在<b>同一天內</b>隨機重排（保留逐日基率、只打散橫截面），整組窮舉重跑 5 次：
            {f?.null.draws} 次抽樣裡兩窗皆 ≥65% 的有 {f?.null.ge65} 組、≥70% 的有 {f?.null.ge70} 組。
            也就是說上表不是「挖」出來的。但它們幾乎都是同一件事的不同寫法（高 ATR），
            所以我們沒有把它們逐條做成規則。
          </p>
        </Collapse>

        <Collapse title="為什麼不把第三軸寫進規則（同日同 ATR 桶配對增量）"
                  hint="控掉波動度之後還剩多少，才是真的選股邊際；採用門檻＝挖掘桶內 ≥+2pp 且雙窗同號">
          <table className="w-full text-xs">
            <thead className="text-muted">
              <tr>
                <th className="px-2 py-1 text-left">軸</th>
                <th className="px-2 py-1 text-right">挖掘 桶內增量</th>
                <th className="px-2 py-1 text-right">holdout 桶內增量</th>
                <th className="px-2 py-1 text-left">判定</th>
              </tr>
            </thead>
            <tbody>
              {data.attribution.map((a) => {
                return (
                  <tr key={a.axis} className="border-t border-edge/60">
                    <td className="px-2 py-1">{a.axis}</td>
                    <td className={`px-2 py-1 text-right tabular-nums ${(a.mine?.pp ?? 0) > 0 ? "text-up" : "text-down"}`}>
                      {a.mine ? `${a.mine.pp > 0 ? "+" : ""}${a.mine.pp}pp (t${a.mine.t})` : "—"}
                    </td>
                    <td className={`px-2 py-1 text-right tabular-nums ${(a.holdout?.pp ?? 0) > 0 ? "text-up" : "text-down"}`}>
                      {a.holdout ? `${a.holdout.pp > 0 ? "+" : ""}${a.holdout.pp}pp (t${a.holdout.t})` : "—"}
                    </td>
                    <td className={`px-2 py-1 ${a.adopted ? "text-up" : "text-muted"}`}>{a.verdict}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-muted">
            讀法：這些軸「加上去命中率會變高」是真的，但變高的原因是它們把池子的 ATR 分布往上推。
            在同一天、同一個 ATR 桶內做配對後增量歸零 —— 那就不是選股能力，寫進規則只會是過擬合。
          </p>
        </Collapse>

        <Collapse title="為什麼這條軌不設停損" defaultOpen
                  hint="−8% 停損 = −25pp 命中率；風控是時間不是價格">
          {data.stop_sensitivity && (
            <>
              <table className="w-full text-xs">
                <thead className="text-muted">
                  <tr>
                    <th className="px-2 py-1 text-left">停損設定</th>
                    <th className="px-2 py-1 text-right">挖掘命中</th>
                    <th className="px-2 py-1 text-right">holdout 命中</th>
                    <th className="px-2 py-1 text-right">相對無停損</th>
                  </tr>
                </thead>
                <tbody>
                  {data.stop_sensitivity.table.map((r) => (
                    <tr key={String(r.stop)} className={`border-t border-edge/60 ${r.stop == null ? "bg-emerald-950/20" : ""}`}>
                      <td className="px-2 py-1">
                        {r.stop == null ? <span className="text-emerald-300">✔ 無停損（定版）</span> : `−${r.stop}%`}
                        {r.stop === 8 && <span className="ml-1 text-[10px] text-amber-300">← 改版前的波段上限</span>}
                      </td>
                      <td className={`px-2 py-1 text-right tabular-nums ${HIT_COLOR(r.mine)}`}>{r.mine}%</td>
                      <td className={`px-2 py-1 text-right tabular-nums ${HIT_COLOR(r.holdout)}`}>{r.holdout}%</td>
                      <td className="px-2 py-1 text-right tabular-nums text-muted">
                        {r.stop == null ? "—" : `${r.d_mine} / ${r.d_holdout}pp`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-[11px] leading-relaxed text-muted">
                逐根走路徑、同日既碰停損又碰目標保守記停損（＝真的照這條線操作的結果，不是用
                MFE/MAE 事後兜的）。關鍵不是「停損會少賺」，而是<b>停損線切在訊號自己的呼吸幅度上</b>：
                期間曾浮虧 &gt;10% 的部位佔 {data.stop_sensitivity.tail.deep10_share}%，
                <b className="text-up"> 其中仍有 {data.stop_sensitivity.tail.deep10_still_hit}% 最後照樣摸到 +10%</b>
                （&gt;15% 者也還有 {data.stop_sensitivity.tail.deep15_still_hit}%）。
                停損把「路還沒走完」誤判成「論點錯了」。
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-amber-200/80">
                代價要看清楚：無停損時期間最深浮虧 中位 {data.stop_sensitivity.tail.dip_p50}%、
                P90 {data.stop_sensitivity.tail.dip_p90}%、最差 {data.stop_sensitivity.tail.dip_worst}%；
                未命中那批到期平均 {data.stop_sensitivity.tail.miss_avg_ret}%、最差 {data.stop_sensitivity.tail.miss_worst_ret}%；
                單筆期望 +{data.stop_sensitivity.tail.avg_ret}%。
                <b> 這條軌的風控是持有天期上限與部位大小，不是停損線。</b>
              </p>
            </>
          )}
        </Collapse>

        <Collapse title="池紀律：為什麼線上也要排除注意/處置"
                  hint="同一條規則、只差池子怎麼定義，holdout 差 10pp">
          <table className="w-full text-xs">
            <thead className="text-muted">
              <tr>
                <th className="px-2 py-1 text-left">池</th>
                <th className="px-2 py-1 text-right">挖掘命中</th>
                <th className="px-2 py-1 text-right">holdout 命中</th>
                <th className="px-2 py-1 text-right">檔·日</th>
              </tr>
            </thead>
            <tbody>
              {data.pool_discipline.map((r, i) => (
                <tr key={r.pool} className={`border-t border-edge/60 ${i === 0 ? "bg-emerald-950/20" : ""}`}>
                  <td className="px-2 py-1">{i === 0 && <span className="mr-1 text-emerald-300">✔</span>}{r.pool}</td>
                  <td className={`px-2 py-1 text-right tabular-nums ${HIT_COLOR(r.mine?.hit)}`}>{r.mine?.hit}%</td>
                  <td className={`px-2 py-1 text-right tabular-nums ${HIT_COLOR(r.holdout?.hit)}`}>{r.holdout?.hit}%</td>
                  <td className="px-2 py-1 text-right tabular-nums text-muted">{r.mine?.per_day}／{r.holdout?.per_day}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-muted">
            挖掘窗幾乎不受影響、holdout 差 10pp——所以這不是「又一個調參」，而是<b>口徑對齊</b>：
            對外掛的命中率是在乾淨池上量的，線上就必須用同一個池。另外注意/處置在策略室本來就是
            <b>獨立</b>的事件策略（處置後 10 日 holdout 命中 71%），混進深跌反攻會讓兩條邊際疊在
            同一個標籤上、事後無法歸因；處置股又是人工撮合／預收款，可交易性也不同。
          </p>
        </Collapse>

        <Collapse title="已經試過而失敗的路（時點型開關）" hint="記錄下來，避免下次重挖">
          <ul className="flex flex-col gap-2">
            {data.regime_gates.map((g) => (
              <li key={g.gate} className="rounded-lg border border-edge bg-panel2/30 px-3 py-2">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-mono text-xs">{g.gate}</span>
                  <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-200">{g.verdict}</span>
                  {g.mine && g.holdout && (
                    <span className="text-[11px] tabular-nums text-muted">
                      挖 {g.mine.hit}%（n{g.mine.n}）／後 {g.holdout.hit}%（n{g.holdout.n}）
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-[11px] text-muted">{g.why}</div>
              </li>
            ))}
          </ul>
        </Collapse>

        <Collapse title="想自己驗？用下面的回測實驗室重建這條規則" hint="欄位都已經加進註冊表">
          <ol className="list-inside list-decimal text-xs leading-relaxed text-muted">
            <li>條件：<code>波動度 ATR</code> 大於 <b>9</b>、<code>大盤距季線</code> 小於等於 <b>-2.3</b>、
              <code>收盤價</code> 大於等於 <b>20</b>、<code>成交金額</code> 大於等於 <b>100000000</b>、
              <code>注意/處置窗內</code> 小於 <b>1</b>（最後一條＝研究用的「乾淨池」）。</li>
            <li>目標 <b>+10%</b>、持有 <b>10</b> 日、排序用 <code>波動度 ATR</code> 由大到小。</li>
            <li>回測結果會附<b>段級中位／最差段</b>——條件型策略請優先看這兩個數字，不要看總命中率。</li>
          </ol>
          <p className="mt-2 text-[11px] text-muted">
            實驗室重建的數字會比本頁略低幾個百分點：本頁的研究池另外排除了 ETF、上市未滿 60 日、
            20 日均量 &lt; 500 張的標的；且實驗室的「持有 10 日」是<b>進場當根起算 10 根</b>，
            研究標籤是<b>進場之後 10 根</b>，差一根約 4pp。
          </p>
        </Collapse>
      </div>

      <ul className="mt-3 list-inside list-disc text-[11px] leading-relaxed text-muted">
        {data.caveats.map((c) => <li key={c}>{c}</li>)}
      </ul>
    </section>
  );
}
