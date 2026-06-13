import { useEffect, useRef, useState } from "react";
import {
  useCalibration,
  useExpectancy,
  useParamSweep,
  useRecompute,
  useRecomputeCalibration,
  useRecomputeExpectancy,
  useRecomputeParamSweep,
  useResetSettings,
  useSettings,
  useSystemStatus,
  useTestSource,
  useTriggerPipeline,
  useUpdateSettings,
} from "../api/client";
import { useQueryClient } from "@tanstack/react-query";
import { inputCls } from "../components/Modal";
import { CATEGORY_LABELS, changeColor } from "../lib/format";
import { applyTheme, getStoredTheme, type Theme } from "../lib/theme";

const SECTIONS = [
  { key: "data", label: "資料更新" },
  { key: "scoring", label: "評分與推薦" },
  { key: "sector", label: "類股方向" },
  { key: "exit", label: "出場提醒" },
  { key: "sources", label: "資料來源" },
  { key: "calibration", label: "分數校準" },
  { key: "expectancy", label: "逐筆期望值" },
  { key: "param_sweep", label: "參數掃描" },
  { key: "general", label: "一般" },
] as const;

const EXIT_LABELS: Record<string, string> = {
  stop_cap: "停損 %",
  trail_trigger: "移動停利啟動 %",
  trail_pullback: "回落 %",
};

function NumGrid({ obj, labels, onChange }: { obj: Record<string, number>; labels: Record<string, string>; onChange: (k: string, v: number) => void }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {Object.entries(obj).filter(([, v]) => typeof v === "number").map(([k, v]) => (
        <label key={k} className="block">
          <span className="mb-1 block text-xs text-muted">{labels[k] ?? k}</span>
          <input type="number" className={inputCls} value={v}
            onChange={(e) => onChange(k, Number(e.target.value))} />
        </label>
      ))}
    </div>
  );
}

export default function SettingsPage() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const reset = useResetSettings();
  const recompute = useRecompute();
  const [section, setSection] = useState<string>("scoring");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [draft, setDraft] = useState<any>(null);
  const [draftFor, setDraftFor] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const lastSettings = useRef<unknown>(null);

  // 切分頁或 settings 重載時，於 render 階段同步重置 draft（不延後到 effect，
  // 否則會有一格 render 仍拿著別區的舊 draft，讓對應面板讀到 undefined 而崩潰）。
  // 純檢視區（sources/calibration/general）無對應設定 → 給空物件讓頁面守門通過。
  if (settings && (draftFor !== section || lastSettings.current !== settings)) {
    setDraft(settings[section] ? structuredClone(settings[section]) : {});
    setDraftFor(section);
    setSaved(false);
    lastSettings.current = settings;
  }

  const save = async () => {
    await update.mutateAsync({ key: section, partial: draft });
    if (section === "scoring" || section === "sector") await recompute.mutateAsync();
    setSaved(true);
  };
  const doReset = () => reset.mutate(section);

  // draftFor !== section 代表上面剛排了重置、draft 還是別區的舊值；此格 render
  // 先不渲染面板（React 會在同一輪同步重渲染成對齊後的 draft，使用者看不到閃爍）。
  if (!settings || !draft || draftFor !== section)
    return <div className="p-6 text-muted">載入中…</div>;

  return (
    <div className="mx-auto flex max-w-6xl gap-6 px-6 py-6">
      <aside className="w-40 shrink-0">
        <h1 className="mb-3 text-xl font-bold">設定</h1>
        <nav className="flex flex-col gap-0.5">
          {SECTIONS.map((s) => (
            <button key={s.key} onClick={() => setSection(s.key)}
              className={`rounded-lg px-3 py-2 text-left text-sm ${section === s.key ? "bg-sky-900/50 text-sky-200" : "text-gray-300 hover:bg-panel2"}`}>
              {s.label}
            </button>
          ))}
        </nav>
      </aside>

      <div className="flex-1">
        {/* 評分與推薦 */}
        {section === "scoring" && (
          <div className="flex flex-col gap-5">
            {(["wave", "long"] as const).map((tk) => (
              <div key={tk} className="rounded-xl border border-edge bg-panel p-4">
                <div className="mb-3 font-semibold">{tk === "wave" ? "波段軌" : "長線軌"}</div>
                {tk === "wave" && (
                  <div className="mb-4">
                    <div className="mb-1.5 text-xs text-muted">進場風格</div>
                    <div className="flex gap-2">
                      {([["breakout", "突破追強", "站上量增、買在突破/近高"], ["pullback", "回檔低接", "已回檔到區間下緣、不要求量增"]] as const).map(([val, label, hint]) => (
                        <button key={val} type="button"
                          onClick={() => setDraft({ ...draft, wave: { ...draft.wave, style: val } })}
                          className={`flex-1 rounded-lg border px-3 py-2 text-left text-sm ${(draft.wave.style ?? "breakout") === val ? "border-sky-600 bg-sky-900/40 text-sky-200" : "border-edge bg-panel2 text-gray-300 hover:bg-edge"}`}>
                          <div className="font-medium">{label}</div>
                          <div className="text-[11px] text-muted">{hint}</div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                <NumGrid obj={draft[tk].weights} labels={CATEGORY_LABELS}
                  onChange={(k, v) => setDraft({ ...draft, [tk]: { ...draft[tk], weights: { ...draft[tk].weights, [k]: v } } })} />
                <label className="mt-3 block w-40">
                  <span className="mb-1 block text-xs text-muted">推薦門檻</span>
                  <input type="number" className={inputCls} value={draft[tk].threshold}
                    onChange={(e) => setDraft({ ...draft, [tk]: { ...draft[tk], threshold: Number(e.target.value) } })} />
                </label>
              </div>
            ))}
            <p className="text-xs text-muted">配分自由給分、系統自動換算比例（不需加總 100）。儲存後當日重算、即時生效。</p>
          </div>
        )}

        {/* 類股方向 */}
        {section === "sector" && (
          <div className="rounded-xl border border-edge bg-panel p-4">
            <div className="mb-3 font-semibold">類股強弱三維度配分</div>
            <NumGrid obj={draft.weights} labels={{ momentum: "動能", fund: "資金", tech: "技術" }}
              onChange={(k, v) => setDraft({ ...draft, weights: { ...draft.weights, [k]: v } })} />
          </div>
        )}

        {/* 出場提醒 */}
        {section === "exit" && (
          <div className="flex flex-col gap-5">
            {(["wave", "long"] as const).map((tk) => (
              <div key={tk} className="rounded-xl border border-edge bg-panel p-4">
                <div className="mb-3 font-semibold">{tk === "wave" ? "波段軌" : "長線軌"}出場參數</div>
                <NumGrid obj={draft[tk]} labels={EXIT_LABELS}
                  onChange={(k, v) => setDraft({ ...draft, [tk]: { ...draft[tk], [k]: v } })} />
                <label className="mt-3 flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={draft[tk].break_ma_exit ?? true}
                    onChange={(e) => setDraft({ ...draft, [tk]: { ...draft[tk], break_ma_exit: e.target.checked } })} />
                  <span>跌破{tk === "wave" ? "月線" : "季線"}即建議出場</span>
                  <span className="text-xs text-muted">（關閉＝只降為警示、不催出場；回測顯示放寬較佳但回撤較大）</span>
                </label>
              </div>
            ))}
            <p className="text-xs text-muted">出場參數即時反映於持股頁的停損價與移動停利判斷。</p>
          </div>
        )}

        {/* 資料更新（手動載入 + 排程）*/}
        {section === "data" && <DataPanel />}

        {/* 資料來源 */}
        {section === "sources" && <SourcesPanel />}

        {/* 分數校準（L4 回測）*/}
        {section === "calibration" && <CalibrationPanel />}

        {/* 逐筆期望值回測 */}
        {section === "expectancy" && <ExpectancyPanel />}

        {/* 出場參數掃描 + walk-forward */}
        {section === "param_sweep" && <SweepPanel />}

        {/* 一般（主題）*/}
        {section === "general" && <GeneralPanel />}

        {section !== "data" && section !== "sources" && section !== "general" && section !== "calibration" && section !== "expectancy" && section !== "param_sweep" && (
          <div className="mt-5 flex items-center gap-3">
            <button onClick={save} disabled={update.isPending || recompute.isPending}
              className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50">
              {update.isPending || recompute.isPending ? "儲存中…" : "儲存並套用"}
            </button>
            <button onClick={doReset} className="text-sm text-muted hover:text-gray-200">恢復預設</button>
            {saved && <span className="text-sm text-up">已套用 ✓</span>}
          </div>
        )}
      </div>
    </div>
  );
}

const STEP_LABELS: Record<string, string> = {
  fetch: "抓取行情/籌碼",
  indicator: "技術指標",
  sector: "類股強弱",
  news: "消息面",
  scoring: "評分",
  exit: "出場訊號",
  llm: "AI 解讀",
  notify: "通知",
};

function DataPanel() {
  const qc = useQueryClient();
  const { data: status } = useSystemStatus();
  const trigger = useTriggerPipeline();
  const { data: settings } = useSettings();
  const update = useUpdateSettings();

  const running = !!status?.pipeline_running;
  const last = status?.last_pipeline_run ?? null;

  // pipeline 從「跑→停」時，刷新各頁資料。
  const prevRunning = useRef(false);
  useEffect(() => {
    if (prevRunning.current && !running) {
      ["recommendations", "overview", "sectors", "holdings", "watchlists", "system-status"].forEach((k) =>
        qc.invalidateQueries({ queryKey: [k] }),
      );
    }
    prevRunning.current = running;
  }, [running, qc]);

  // 排程 draft（避免每次打字都送 API）
  const sched = settings?.general?.schedule ?? { enabled: true, time: "21:30" };
  const [enabled, setEnabled] = useState<boolean>(sched.enabled);
  const [time, setTime] = useState<string>(sched.time);
  const [schedSaved, setSchedSaved] = useState(false);
  useEffect(() => {
    setEnabled(sched.enabled);
    setTime(sched.time);
  }, [sched.enabled, sched.time]);

  const saveSchedule = async () => {
    await update.mutateAsync({ key: "general", partial: { schedule: { enabled, time } } });
    setSchedSaved(true);
  };

  const statusBadge = (s: string) =>
    s === "success" ? "🟢 成功" : s === "running" ? "🟡 進行中" : s === "failed" ? "🔴 失敗" : s;

  return (
    <div className="flex flex-col gap-4">
      {/* 手動載入 */}
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-1 font-semibold">立即載入</div>
        <p className="text-sm text-muted">
          手動執行一次盤後 pipeline（抓行情/籌碼 → 算指標 → 類股 → 消息 → 評分 → 出場 → AI → 通知）。
          冪等可重跑，台股盤後資料約 21:00 後才齊。
        </p>
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={() => trigger.mutate()}
            disabled={running || trigger.isPending}
            className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {running ? "載入中…" : trigger.isPending ? "啟動中…" : "立即載入"}
          </button>
          {running && <span className="text-sm text-amber-400">資料更新中，請稍候（每 3 秒自動刷新進度）</span>}
          {!running && trigger.data?.accepted === false && (
            <span className="text-sm text-muted">已有任務在跑</span>
          )}
        </div>
      </div>

      {/* 最近一次執行 */}
      {last && (
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="font-semibold">最近一次執行</span>
            <span className="text-sm">{statusBadge(running ? "running" : last.status)}</span>
          </div>
          <p className="text-xs text-muted">
            交易日 {last.trading_date ?? "—"}
            {last.finished_at && !running && `・完成於 ${last.finished_at.replace("T", " ").slice(0, 19)}`}
          </p>
          {last.steps && last.steps.length > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
              {last.steps.map((st) => (
                <div key={st.name} className="flex items-center gap-1.5 text-xs">
                  <span>{st.status === "ok" ? "🟢" : "🔴"}</span>
                  <span className="text-gray-300">{STEP_LABELS[st.name] ?? st.name}</span>
                  {st.seconds != null && <span className="text-muted">{st.seconds}s</span>}
                </div>
              ))}
            </div>
          )}
          {last.steps?.some((s) => s.status !== "ok") && (
            <p className="mt-2 text-xs text-down">
              {last.steps.filter((s) => s.error).map((s) => s.error).join("；")}
            </p>
          )}
        </div>
      )}

      {/* 定時載入 */}
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-1 font-semibold">定時載入（後端內建排程）</div>
        <p className="text-sm text-muted">
          後端開著時，每天到時間自動載入；啟動後端時也會補跑「應已完成卻還沒跑」的交易日。
          後端沒開的時段不會觸發，可回來手動按「立即載入」補。
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={enabled}
              onChange={(e) => { setEnabled(e.target.checked); setSchedSaved(false); }} />
            <span>啟用每日自動載入</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted">時間</span>
            <input
              type="time"
              className={`${inputCls} w-32`}
              value={time}
              disabled={!enabled}
              onChange={(e) => { setTime(e.target.value); setSchedSaved(false); }}
            />
          </label>
          <button
            onClick={saveSchedule}
            disabled={update.isPending}
            className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {update.isPending ? "儲存中…" : "儲存排程"}
          </button>
          {schedSaved && <span className="text-sm text-up">已套用 ✓</span>}
        </div>
      </div>
    </div>
  );
}

function SourcesPanel() {
  const test = useTestSource();
  const [tokens, setTokens] = useState<Record<string, string>>({ fugle: "", finmind: "" });
  const [result, setResult] = useState<Record<string, string>>({});

  const run = (name: string) => {
    test.mutate(
      { name, token: tokens[name] || undefined, save: !!tokens[name] },
      { onSuccess: (r) => setResult({ ...result, [name]: `${r.ok ? "🟢" : "🔴"} ${r.reason}` }) },
    );
  };
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">行情與籌碼走證交所官方資料（免 token）。Fugle / FinMind 供進階資料，填入後測試連線、測通才儲存（存 Keychain，不入庫）。</p>
      {(["twse", "fugle", "finmind"] as const).map((name) => (
        <div key={name} className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 font-semibold">{name === "twse" ? "TWSE 證交所（行情/籌碼/重訊）" : name === "fugle" ? "Fugle 富果（細 K）" : "FinMind（主檔/備援）"}</div>
          {name !== "twse" && (
            <input className={`${inputCls} mb-2`} type="password" placeholder="貼上 API token"
              value={tokens[name]} onChange={(e) => setTokens({ ...tokens, [name]: e.target.value })} />
          )}
          <div className="flex items-center gap-3">
            <button onClick={() => run(name)} disabled={test.isPending} className="rounded-md bg-panel2 px-3 py-1.5 text-sm hover:bg-edge">測試連線</button>
            {result[name] && <span className="text-sm">{result[name]}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

function CalibrationPanel() {
  const { data: cal, isLoading } = useCalibration();
  const recompute = useRecomputeCalibration();
  const fmtRet = (v: number | null) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);

  if (isLoading) return <div className="text-muted">載入中…</div>;
  const has = cal && cal.samples > 0 && cal.horizons.length > 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-1 font-semibold">分數校準回測（波段軌）</div>
        <p className="text-sm text-muted">
          分數高的，未來真的比較會漲嗎？把歷史每一天的波段分數分桶，量未來 N 日漲跌幅的
          <b className="text-gray-200"> 中位數 </b>與<b className="text-gray-200"> 命中率（上漲比例）</b>。
        </p>
        {has && (
          <p className="mt-2 text-xs text-muted">
            回測視窗 {cal!.window.from} ~ {cal!.window.to}・{cal!.window.score_dates} 個交易日・
            樣本 {cal!.samples.toLocaleString()}　|　計算於 {cal!.generated_at}
          </p>
        )}
        <button
          onClick={() => recompute.mutate()}
          disabled={recompute.isPending}
          className="mt-3 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
        >
          {recompute.isPending ? "回測中…（約 1 分鐘）" : "重新計算"}
        </button>
        {recompute.isError && <span className="ml-3 text-sm text-down">失敗，請重試</span>}
      </div>

      {!has && <p className="text-sm text-muted">尚無校準資料，按「重新計算」產生。</p>}

      {has &&
        cal!.horizons.map((h) => {
          const rows = cal!.buckets[String(h)] ?? [];
          const base = cal!.baseline[String(h)] ?? null;
          return (
            <div key={h} className="rounded-xl border border-edge bg-panel p-4">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="font-semibold">未來 {h} 交易日</span>
                <span className="text-xs text-muted">整體基準中位 {fmtRet(base)}</span>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-muted">
                    <th className="py-1 text-left font-normal">分數區間</th>
                    <th className="py-1 text-right font-normal">樣本</th>
                    <th className="py-1 text-right font-normal">命中率</th>
                    <th className="py-1 text-right font-normal">中位報酬</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((b) => (
                    <tr key={b.lo} className="border-t border-edge/60">
                      <td className="py-1.5 tabular-nums">{b.lo}–{b.hi}</td>
                      <td className="py-1.5 text-right tabular-nums text-muted">{b.n}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {b.hit_rate === null ? "—" : `${Math.round(b.hit_rate * 100)}%`}
                      </td>
                      <td className={`py-1.5 text-right tabular-nums ${changeColor(b.median_ret)}`}>
                        {fmtRet(b.median_ret)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}

      {has && cal!.by_confidence && Object.keys(cal!.by_confidence).length > 0 && (
        <div className="rounded-xl border border-sky-900/60 bg-panel p-4">
          <div className="mb-1 font-semibold">可信度能再提升命中率嗎？</div>
          <p className="mb-3 text-xs text-muted">
            在可操作分數帶（≥{cal!.actionable_score ?? 70} 分）內，再依「可信度」分層比命中率。
            若高信心一層明顯較高，代表可信度是獨立有效的第二道篩。
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">可信度</th>
                {cal!.horizons.map((h) => (
                  <th key={h} className="py-1 text-right font-normal">{h}日命中率</th>
                ))}
                <th className="py-1 text-right font-normal">樣本</th>
              </tr>
            </thead>
            <tbody>
              {["高", "中", "低"].map((tier) => {
                const cell = (h: number) => (cal!.by_confidence![String(h)] ?? []).find((t) => t.tier === tier);
                const nMax = Math.max(...cal!.horizons.map((h) => cell(h)?.n ?? 0));
                return (
                  <tr key={tier} className="border-t border-edge/60">
                    <td className="py-1.5">信心{tier}</td>
                    {cal!.horizons.map((h) => {
                      const c = cell(h);
                      return (
                        <td key={h} className="py-1.5 text-right tabular-nums">
                          {c?.hit_rate == null ? "—" : `${Math.round(c.hit_rate * 100)}%`}
                        </td>
                      );
                    })}
                    <td className="py-1.5 text-right tabular-nums text-muted">{nMax}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {has && <p className="text-xs text-muted/80">{cal!.note}</p>}
    </div>
  );
}

function ExpectancyPanel() {
  const { data: exp, isLoading } = useExpectancy();
  const recompute = useRecomputeExpectancy();
  const pct = (v: number | null | undefined, sign = false) =>
    v === null || v === undefined ? "—" : `${sign && v > 0 ? "+" : ""}${v.toFixed(2)}%`;
  const rate = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

  if (isLoading) return <div className="text-muted">載入中…</div>;
  const has = exp && exp.overall && exp.overall.n > 0;

  const scenarios = has
    ? [
        { label: "系統推薦（忠實出場）", s: exp!.overall },
        { label: "同上，只停損+移停（不含跌破月線）", s: exp!.overall_stop_only },
        { label: "對照：只過硬篩、不看分數", s: exp!.control },
      ].filter((x) => x.s)
    : [];

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-1 font-semibold">逐筆交易期望值回測（波段軌）</div>
        <p className="text-sm text-muted">
          照系統真實規則逐筆模擬：分數達門檻隔日開盤買，觸發真實出場訊號（停損/跌破月線/移動停利）
          隔日開盤賣。問<b className="text-gray-200"> 每筆期望值 </b>是否為正、分數/可信度越高是否越賺。
        </p>
        {has && (
          <p className="mt-2 text-xs text-muted">
            {exp!.window.from} ~ {exp!.window.to}・進場日 {exp!.window.entry_dates}・
            含來回成本 {exp!.cost_pct}%・最長持有 {exp!.max_hold} 日・計算於 {exp!.generated_at}
          </p>
        )}
        <button onClick={() => recompute.mutate()} disabled={recompute.isPending}
          className="mt-3 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50">
          {recompute.isPending ? "回測中…（數分鐘）" : "重新計算"}
        </button>
        {recompute.isError && <span className="ml-3 text-sm text-down">失敗，請重試</span>}
      </div>

      {!has && <p className="text-sm text-muted">尚無回測資料，按「重新計算」產生。</p>}

      {has && (
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 font-semibold">情境比較</div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">策略</th>
                <th className="py-1 text-right font-normal">筆數</th>
                <th className="py-1 text-right font-normal">勝率</th>
                <th className="py-1 text-right font-normal">賺賠比</th>
                <th className="py-1 text-right font-normal">每筆期望值</th>
                <th className="py-1 text-right font-normal">持有</th>
              </tr>
            </thead>
            <tbody>
              {scenarios.map((x, i) => (
                <tr key={i} className="border-t border-edge/60">
                  <td className="py-1.5">{x.label}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{x.s!.n}</td>
                  <td className="py-1.5 text-right tabular-nums">{rate(x.s!.win_rate)}</td>
                  <td className="py-1.5 text-right tabular-nums">{x.s!.payoff ?? "—"}</td>
                  <td className={`py-1.5 text-right font-medium tabular-nums ${changeColor(x.s!.expectancy)}`}>
                    {pct(x.s!.expectancy, true)}
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{x.s!.avg_hold ?? "—"}日</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {has && (
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 font-semibold">分數越高 → 每筆越賺嗎？</div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">分數區間</th>
                <th className="py-1 text-right font-normal">筆數</th>
                <th className="py-1 text-right font-normal">勝率</th>
                <th className="py-1 text-right font-normal">賺賠比</th>
                <th className="py-1 text-right font-normal">期望值</th>
              </tr>
            </thead>
            <tbody>
              {exp!.by_score.map((b) => (
                <tr key={b.lo} className="border-t border-edge/60">
                  <td className="py-1.5 tabular-nums">{b.lo}–{b.hi}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{b.n}</td>
                  <td className="py-1.5 text-right tabular-nums">{rate(b.win_rate)}</td>
                  <td className="py-1.5 text-right tabular-nums">{b.payoff ?? "—"}</td>
                  <td className={`py-1.5 text-right tabular-nums ${changeColor(b.expectancy)}`}>{pct(b.expectancy, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {has && exp!.by_confidence.length > 0 && (
        <div className="rounded-xl border border-sky-900/60 bg-panel p-4">
          <div className="mb-2 font-semibold">可信度越高 → 每筆越賺嗎？（分數 ≥{exp!.actionable_score ?? 70}）</div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">可信度</th>
                <th className="py-1 text-right font-normal">筆數</th>
                <th className="py-1 text-right font-normal">勝率</th>
                <th className="py-1 text-right font-normal">賺賠比</th>
                <th className="py-1 text-right font-normal">期望值</th>
              </tr>
            </thead>
            <tbody>
              {exp!.by_confidence.map((t) => (
                <tr key={t.tier} className="border-t border-edge/60">
                  <td className="py-1.5">信心{t.tier}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{t.n}</td>
                  <td className="py-1.5 text-right tabular-nums">{rate(t.win_rate)}</td>
                  <td className="py-1.5 text-right tabular-nums">{t.payoff ?? "—"}</td>
                  <td className={`py-1.5 text-right tabular-nums ${changeColor(t.expectancy)}`}>{pct(t.expectancy, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {has && <p className="text-xs text-muted/80">{exp!.note}</p>}
    </div>
  );
}

function fmtParams(p: { stop_cap: number; trail_trigger: number; trail_pullback: number; break_ma: boolean }) {
  return `停損${p.stop_cap} 啟動${p.trail_trigger} 回落${p.trail_pullback} ${p.break_ma ? "月線出" : "不看月線"}`;
}

function SweepPanel() {
  const { data: sw, isLoading } = useParamSweep();
  const recompute = useRecomputeParamSweep();
  const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const rate = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

  if (isLoading) return <div className="text-muted">載入中…</div>;
  const wf = sw?.walkforward;
  const has = sw && wf && (wf.oos_optimized !== undefined && wf.oos_optimized !== null);
  const best_full = sw?.best_full;

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-1 font-semibold">出場參數掃描（walk-forward 驗證）</div>
        <p className="text-sm text-muted">
          進場規則固定，只掃出場參數（停損／移動停利／含不含跌破月線）。
          <b className="text-gray-200"> 用前段資料挑最佳參數、套到沒看過的後段</b>，比「最佳化 vs 預設」誰贏——
          這才看得出調參是真有效還是過擬合。
        </p>
        {sw?.window?.from && (
          <p className="mt-2 text-xs text-muted">
            {sw.window.from} ~ {sw.window.to}・掃 {sw.grid_size} 組・計算於 {sw.generated_at}
          </p>
        )}
        <button onClick={() => recompute.mutate()} disabled={recompute.isPending}
          className="mt-3 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50">
          {recompute.isPending ? "掃描中…（數分鐘）" : "重新計算"}
        </button>
        {recompute.isError && <span className="ml-3 text-sm text-down">失敗，請重試</span>}
      </div>

      {!has && <p className="text-sm text-muted">尚無掃描資料，按「重新計算」產生。</p>}

      {has && (
        <div className="rounded-xl border border-sky-900/60 bg-panel p-4">
          <div className="mb-2 font-semibold">樣本外結論</div>
          <div className="flex gap-6">
            <div>
              <div className="text-xs text-muted">最佳化（樣本外）</div>
              <div className={`text-2xl font-semibold tabular-nums ${changeColor(wf!.oos_optimized)}`}>{pct(wf!.oos_optimized)}</div>
              <div className="text-xs text-muted">平均最深水下 {pct(wf!.oos_optimized_mae)}</div>
            </div>
            <div>
              <div className="text-xs text-muted">預設（樣本外）</div>
              <div className={`text-2xl font-semibold tabular-nums ${changeColor(wf!.oos_default)}`}>{pct(wf!.oos_default)}</div>
              <div className="text-xs text-muted">平均最深水下 {pct(wf!.oos_default_mae)}</div>
            </div>
            <div>
              <div className="text-xs text-muted">差距／筆</div>
              <div className={`text-2xl font-semibold tabular-nums ${changeColor(wf!.edge)}`}>{pct(wf!.edge)}</div>
            </div>
          </div>
          <p className="mt-3 text-sm text-gray-200">{wf!.verdict}</p>
        </div>
      )}

      {has && sw!.boundary && (
        <div className={`rounded-xl border p-4 ${sw!.boundary.is_runaway ? "border-amber-700/60 bg-amber-950/20" : "border-emerald-800/50 bg-panel"}`}>
          <div className="mb-1 font-semibold">
            {sw!.boundary.is_runaway ? "🚩 真甜蜜點 or 行情假象？→ 偏行情假象" : "✅ 真甜蜜點 or 行情假象？→ 偏真甜蜜點"}
          </div>
          <p className="text-sm text-gray-200">{sw!.boundary.message}</p>
          {best_full && (
            <p className="mt-2 text-xs text-muted">
              全期最佳：{fmtParams(best_full.params)}（期望值 {pct(best_full.expectancy)}）
              {sw!.boundary.at_max.length > 0 && `；仍貼最寬端的軸：${sw!.boundary.at_max.join("、")}`}
            </p>
          )}
          {!sw!.boundary.is_runaway && wf!.folds && wf!.folds[0] && (wf!.edge ?? 0) > 0.2 && (
            <p className="mt-2 text-xs text-sky-300/90">
              可試跑：到「出場提醒」把波段設成 停損{wf!.folds[0].picked.stop_cap}／啟動{wf!.folds[0].picked.trail_trigger}／回落{wf!.folds[0].picked.trail_pullback}
              {!wf!.folds[0].picked.break_ma && "、關閉「跌破月線即出場」"}，與預設並行觀察再定奪。
            </p>
          )}
        </div>
      )}

      {has && wf!.folds && wf!.folds.length > 0 && (
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 font-semibold">各折（前段挑參數 → 後段驗證）</div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">測試期</th>
                <th className="py-1 text-left font-normal">前段挑的參數</th>
                <th className="py-1 text-right font-normal">訓練</th>
                <th className="py-1 text-right font-normal">樣本外</th>
                <th className="py-1 text-right font-normal">預設樣本外</th>
              </tr>
            </thead>
            <tbody>
              {wf!.folds.map((f, i) => (
                <tr key={i} className="border-t border-edge/60">
                  <td className="py-1.5 text-xs tabular-nums">{f.test_from}~{f.test_to}</td>
                  <td className="py-1.5 text-xs">{fmtParams(f.picked)}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{pct(f.train_expectancy)}</td>
                  <td className={`py-1.5 text-right tabular-nums ${changeColor(f.oos_expectancy)}`}>{pct(f.oos_expectancy)}</td>
                  <td className={`py-1.5 text-right tabular-nums ${changeColor(f.default_oos_expectancy)}`}>{pct(f.default_oos_expectancy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {has && sw!.grid_top.length > 0 && (
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-1 font-semibold">全期排行</div>
          <p className="mb-2 text-xs text-amber-400/80">⚠ 樣本內排行，看起來最美的那組通常是過擬合——以上面的樣本外結論為準。</p>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">出場參數</th>
                <th className="py-1 text-right font-normal">筆數</th>
                <th className="py-1 text-right font-normal">勝率</th>
                <th className="py-1 text-right font-normal">賺賠比</th>
                <th className="py-1 text-right font-normal">期望值</th>
                <th className="py-1 text-right font-normal">最深水下</th>
              </tr>
            </thead>
            <tbody>
              {sw!.grid_top.map((r, i) => (
                <tr key={i} className="border-t border-edge/60">
                  <td className="py-1.5 text-xs">{fmtParams(r.params)}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{r.n}</td>
                  <td className="py-1.5 text-right tabular-nums">{rate(r.win_rate)}</td>
                  <td className="py-1.5 text-right tabular-nums">{r.payoff ?? "—"}</td>
                  <td className={`py-1.5 text-right tabular-nums ${changeColor(r.expectancy)}`}>{pct(r.expectancy)}</td>
                  <td className="py-1.5 text-right tabular-nums text-down">{pct(r.avg_mae)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {sw!.default && (
            <p className="mt-2 text-xs text-muted">
              預設（{fmtParams(sw!.default.params)}）：{sw!.default.n} 筆・期望值 {pct(sw!.default.expectancy)}
            </p>
          )}
        </div>
      )}

      {has && <p className="text-xs text-muted/80">{sw!.note}</p>}
    </div>
  );
}

function GeneralPanel() {
  const [theme, setTheme] = useState<Theme>(getStoredTheme());
  const choose = (t: Theme) => { setTheme(t); applyTheme(t); };
  return (
    <div className="rounded-xl border border-edge bg-panel p-4">
      <div className="mb-3 font-semibold">主題</div>
      <div className="flex gap-2">
        {(["dark", "light"] as Theme[]).map((t) => (
          <button key={t} onClick={() => choose(t)}
            className={`rounded-md px-4 py-2 text-sm ${theme === t ? "bg-sky-600 text-white" : "bg-panel2 text-gray-300 hover:bg-edge"}`}>
            {t === "dark" ? "🌙 深色" : "☀️ 淺色"}
          </button>
        ))}
      </div>
    </div>
  );
}
