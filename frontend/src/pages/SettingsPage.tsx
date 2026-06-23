import { useEffect, useRef, useState } from "react";
import {
  usePoppableEfficacy,
  useRecompute,
  useRecomputePoppableEfficacy,
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
  { key: "poppable_efficacy", label: "會噴成效" },
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
                <div className="mb-3 font-semibold">{tk === "wave" ? "波段軌（會噴）" : "長線軌"}</div>
                {tk === "wave" ? (
                  <>
                    <p className="mb-3 text-xs leading-relaxed text-muted">
                      進場推薦＝<b>會噴</b>：分數為當天全市場橫截面 <b>2×波動度 + 均線多排</b> 的百分位
                      （回測實證的會噴機率，無配分可調）。下方設定進推薦的「前 N%」；推薦頁也有橫桿可即時調整。
                    </p>
                    <label className="block w-40">
                      <span className="mb-1 block text-xs text-muted">推薦前 N%</span>
                      <input type="number" min={1} max={100} className={inputCls}
                        value={draft.wave?.top_pct ?? 20}
                        onChange={(e) => setDraft({ ...draft, wave: { ...draft.wave, top_pct: Number(e.target.value) } })} />
                    </label>
                  </>
                ) : (
                  <>
                    <NumGrid obj={draft[tk].weights} labels={CATEGORY_LABELS}
                      onChange={(k, v) => setDraft({ ...draft, [tk]: { ...draft[tk], weights: { ...draft[tk].weights, [k]: v } } })} />
                    <label className="mt-3 block w-40">
                      <span className="mb-1 block text-xs text-muted">推薦門檻</span>
                      <input type="number" className={inputCls} value={draft[tk].threshold}
                        onChange={(e) => setDraft({ ...draft, [tk]: { ...draft[tk], threshold: Number(e.target.value) } })} />
                    </label>
                  </>
                )}
              </div>
            ))}
            <p className="text-xs text-muted">長線軌配分自由給分、系統自動換算比例（不需加總 100）。儲存後當日重算、即時生效。</p>
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

        {/* 會噴清單成效回測 */}
        {section === "poppable_efficacy" && <PoppableEfficacyPanel />}

        {/* 一般（主題）*/}
        {section === "general" && <GeneralPanel />}

        {section !== "data" && section !== "sources" && section !== "general" && section !== "poppable_efficacy" && (
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
        <div className="mb-1 font-semibold">立即載入（補齊到最新）</div>
        <p className="text-sm text-muted">
          補齊「所有缺的交易日（含分數/推薦）」到最新——逐日跑盤後 pipeline（抓行情/籌碼 → 算指標 →
          類股 → 消息 → 評分 → 出場），最新那天才發通知/跑 AI（補多天不會重複通知）。冪等可重跑，
          台股盤後資料約 21:00 後才齊；缺多天會逐日跑、較久。
        </p>
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={() => trigger.mutate()}
            disabled={running || trigger.isPending}
            className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {running ? "載入中…" : trigger.isPending ? "啟動中…" : "補齊到最新"}
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

function PoppableEfficacyPanel() {
  const { data: eff, isLoading } = usePoppableEfficacy();
  const recompute = useRecomputePoppableEfficacy();
  const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
  const sign = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);

  if (isLoading) return <div className="text-muted">載入中…</div>;
  const has = eff && eff.by_date.length > 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-1 font-semibold">會噴清單成效（波段軌 · 會噴風格）</div>
        <p className="text-sm text-muted">
          會噴清單到底準不準？取最近幾個「已有完整未來」的歷史進場日，用<b className="text-gray-200">真引擎</b>
          重跑當時的會噴清單，看那些股票後來 {eff?.horizon ?? 20} 個交易日<b className="text-gray-200">有沒有摸到 +10%</b>，
          對比全市場基準。
        </p>
        <div className="mt-2 rounded-lg border border-amber-700/50 bg-amber-950/30 px-3 py-2 text-xs leading-relaxed text-amber-200/90">
          清單的職責是<b>「給你一個停利點」</b>，不是「會自動賺」。所以同時看<b>最深回撤 / 20 日收盤</b>
          ——噴完不賣可能吐回去，能不能入袋全看出場紀律。
        </div>
        {has && (
          <p className="mt-2 text-xs text-muted">
            {eff!.window.from} ~ {eff!.window.to}・{eff!.window.entry_dates} 個進場日・清單共 {eff!.total_list} 檔
            　|　整體摸+10% <b className="text-gray-200">{pct(eff!.overall_hit_rate)}</b>　|　計算於 {eff!.generated_at}
          </p>
        )}
        {has && (
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-emerald-800/50 bg-emerald-950/20 px-3 py-2 text-xs">
            <span className="font-medium text-emerald-300">低位盤整擇時濾網</span>
            <span className="text-muted">
              全清單 <b className="text-gray-200">{pct(eff!.overall_hit_rate)}</b>（{eff!.total_list} 檔）
              {" → "}低位盤整子集 <b className="text-emerald-300">{pct(eff!.coil_overall_hit_rate)}</b>（{eff!.coil_total ?? 0} 檔）
            </span>
            <span className="text-muted">
              {eff!.coil_overall_hit_rate != null && eff!.overall_hit_rate
                ? `lift ${(eff!.coil_overall_hit_rate / eff!.overall_hit_rate).toFixed(2)}x`
                : "子集樣本不足"}
            </span>
          </div>
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

      {!has && <p className="text-sm text-muted">尚無成效資料，按「重新計算」產生。</p>}

      {has && (
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 font-semibold">各進場日：清單 vs 全市場</div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">進場日</th>
                <th className="py-1 text-right font-normal">清單檔數</th>
                <th className="py-1 text-right font-normal">清單摸+10%</th>
                <th className="py-1 text-right font-normal">基準</th>
                <th className="py-1 text-right font-normal">lift</th>
                <th className="py-1 text-right font-normal">平均最高</th>
                <th className="py-1 text-right font-normal">平均回撤</th>
                <th className="py-1 text-right font-normal text-emerald-400/80">盤整檔</th>
                <th className="py-1 text-right font-normal text-emerald-400/80">盤整摸+10%</th>
              </tr>
            </thead>
            <tbody>
              {eff!.by_date.map((r) => (
                <tr key={r.date} className="border-t border-edge/60">
                  <td className="py-1.5 tabular-nums">{r.date}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{r.n}</td>
                  <td className="py-1.5 text-right tabular-nums font-medium text-sky-300">{pct(r.list_hit_rate)}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{pct(r.base_hit_rate)}</td>
                  <td className="py-1.5 text-right tabular-nums">{r.lift === null ? "—" : `${r.lift.toFixed(2)}x`}</td>
                  <td className="py-1.5 text-right tabular-nums text-up">{sign(r.avg_mfe)}</td>
                  <td className="py-1.5 text-right tabular-nums text-down">{sign(r.avg_dd)}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{r.coil_n ?? 0}</td>
                  <td className="py-1.5 text-right tabular-nums font-medium text-emerald-300">{pct(r.coil_hit_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {has && eff!.detail.length > 0 && (
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="font-semibold">{eff!.detail_date} 會噴清單明細</span>
            <span className="text-xs text-muted">後來 {eff!.horizon ?? 20} 交易日實際</span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">股票</th>
                <th className="py-1 text-right font-normal">會噴分</th>
                <th className="py-1 text-right font-normal">波幅%</th>
                <th className="py-1 text-right font-normal">最高漲</th>
                <th className="py-1 text-right font-normal">最深回撤</th>
                <th className="py-1 text-right font-normal">20日收盤</th>
                <th className="py-1 text-right font-normal">摸+10%</th>
              </tr>
            </thead>
            <tbody>
              {eff!.detail.map((r) => (
                <tr key={r.stock_id} className="border-t border-edge/60">
                  <td className="py-1.5">
                    <span className="tabular-nums text-muted">{r.stock_id}</span> {r.name}
                    {r.coil && <span className="ml-1.5 rounded bg-emerald-950/50 px-1 py-0.5 text-[10px] text-emerald-400">盤整</span>}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{r.pop}</td>
                  <td className="py-1.5 text-right tabular-nums text-muted">{r.atr}</td>
                  <td className="py-1.5 text-right tabular-nums text-up">{sign(r.mfe)}</td>
                  <td className="py-1.5 text-right tabular-nums text-down">{sign(r.dd)}</td>
                  <td className={`py-1.5 text-right tabular-nums ${changeColor(r.cret)}`}>{sign(r.cret)}</td>
                  <td className="py-1.5 text-right">{r.hit ? "✔" : "·"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {has && eff!.note && <p className="text-xs leading-relaxed text-muted">{eff!.note}</p>}
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
