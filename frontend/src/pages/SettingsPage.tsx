import { useEffect, useState } from "react";
import {
  useRecompute,
  useResetSettings,
  useSettings,
  useTestSource,
  useUpdateSettings,
} from "../api/client";
import { inputCls } from "../components/Modal";
import { CATEGORY_LABELS } from "../lib/format";
import { applyTheme, getStoredTheme, type Theme } from "../lib/theme";

const SECTIONS = [
  { key: "scoring", label: "評分與推薦" },
  { key: "sector", label: "類股方向" },
  { key: "exit", label: "出場提醒" },
  { key: "sources", label: "資料來源" },
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
      {Object.entries(obj).map(([k, v]) => (
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
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings) setDraft(structuredClone(settings[section]));
    setSaved(false);
  }, [settings, section]);

  const save = async () => {
    await update.mutateAsync({ key: section, partial: draft });
    if (section === "scoring" || section === "sector") await recompute.mutateAsync();
    setSaved(true);
  };
  const doReset = () => reset.mutate(section);

  if (!settings || !draft) return <div className="p-6 text-muted">載入中…</div>;

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
              </div>
            ))}
            <p className="text-xs text-muted">出場參數即時反映於持股頁的停損價與移動停利判斷。</p>
          </div>
        )}

        {/* 資料來源 */}
        {section === "sources" && <SourcesPanel />}

        {/* 一般（主題）*/}
        {section === "general" && <GeneralPanel />}

        {section !== "sources" && section !== "general" && (
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
