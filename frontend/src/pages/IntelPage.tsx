import { useState } from "react";
import { Link } from "react-router-dom";
import { useIntel, type IntelEvent } from "../api/client";
import { Markdown } from "../components/Markdown";

const CATEGORIES = ["全部", "利空", "題材", "中性"] as const;
type Category = (typeof CATEGORIES)[number];

function catClass(category: string | null, isRisk: boolean): string {
  if (isRisk) return "bg-down/20 text-down";
  if (category === "題材") return "bg-up/20 text-up";
  return "bg-panel2 text-muted";
}

function DigestCard({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-edge bg-panel p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">{title}</h2>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function EventRow({ e }: { e: IntelEvent }) {
  return (
    <li className="flex items-start gap-2 border-b border-edge/50 py-2 text-sm last:border-0">
      <span className={`mt-0.5 shrink-0 rounded px-1 text-xs ${catClass(e.category, e.is_risk)}`}>
        {e.is_risk ? "⚠️ 利空" : e.category ?? "中性"}
      </span>
      <span className="shrink-0 text-xs tabular-nums text-muted">{e.date}</span>
      <Link to={`/stocks/${e.stock_id}`} className="shrink-0 font-medium hover:underline">
        {e.name}
      </Link>
      {e.url ? (
        <a href={e.url} target="_blank" rel="noreferrer" className="truncate text-muted hover:text-gray-200 hover:underline">
          {e.title}
        </a>
      ) : (
        <span className="truncate text-muted">{e.title}</span>
      )}
      {e.source && <span className="ml-auto shrink-0 text-[10px] text-gray-600">{e.source}</span>}
    </li>
  );
}

export default function IntelPage() {
  const [days, setDays] = useState(14);
  const [cat, setCat] = useState<Category>("全部");
  const [riskOnly, setRiskOnly] = useState(false);

  const { data, isLoading } = useIntel({
    days,
    category: cat === "全部" ? undefined : cat,
    riskOnly,
  });

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <h1 className="text-xl font-bold">📰 情報</h1>
      <p className="mb-4 text-sm text-muted">
        近期消息與研報重點 · 盤後資料：{data?.date ?? "—"}
        {data && (
          <>
            {" · "}近 7 日共 {data.total} 則（利空 {data.risk_count} 則）
          </>
        )}
      </p>

      {isLoading && <div className="text-muted">載入中…</div>}

      {data && !data.has_digest && (
        <div className="mb-5 rounded-xl border border-dashed border-edge bg-panel p-4 text-sm text-muted">
          尚無 AI 消息總結。請於設定頁填入 Anthropic API key 並完成一次盤後批次後再回來查看；
          以下仍可瀏覽近期原始事件。
        </div>
      )}

      {/* ① 全市場 + ③ 焦點 */}
      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {data?.market_digest && (
          <DigestCard title="🤖 全市場消息重點">
            <Markdown>{data.market_digest}</Markdown>
          </DigestCard>
        )}
        {data?.focus_digest && (
          <DigestCard title="⭐ 我的關注焦點" hint="持股 + 觀察清單">
            <Markdown>{data.focus_digest}</Markdown>
          </DigestCard>
        )}
      </div>

      {/* ② 依題材（類股）分群 */}
      {data && data.themes.length > 0 && (
        <div className="mb-5">
          <h2 className="mb-2 text-sm font-semibold">依題材分群</h2>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {data.themes.map((t) => (
              <DigestCard
                key={t.sector_id}
                title={t.sector_name}
                hint={`${t.event_count} 則${t.risk_count > 0 ? ` · ⚠️ ${t.risk_count}` : ""}`}
              >
                <Markdown>{t.digest}</Markdown>
              </DigestCard>
            ))}
          </div>
        </div>
      )}

      {/* ④ 近期事件列表 + 篩選 */}
      <div className="rounded-xl border border-edge bg-panel p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h2 className="mr-2 text-sm font-semibold">近期事件</h2>
          {CATEGORIES.map((c) => (
            <button
              key={c}
              onClick={() => setCat(c)}
              className={`rounded-md px-2 py-0.5 text-xs ${
                cat === c ? "bg-sky-900/60 text-sky-200" : "bg-panel2 text-muted hover:text-gray-200"
              }`}
            >
              {c}
            </button>
          ))}
          <label className="ml-2 flex items-center gap-1 text-xs text-muted">
            <input type="checkbox" checked={riskOnly} onChange={(e) => setRiskOnly(e.target.checked)} />
            只看利空
          </label>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="ml-auto rounded-md bg-panel2 px-2 py-0.5 text-xs text-gray-300"
          >
            {[7, 14, 30].map((d) => (
              <option key={d} value={d}>
                近 {d} 日
              </option>
            ))}
          </select>
        </div>
        {data && data.events.length === 0 ? (
          <p className="text-sm text-muted">此條件下近期無事件</p>
        ) : (
          <ul className="flex flex-col">{data?.events.map((e, i) => <EventRow key={i} e={e} />)}</ul>
        )}
      </div>
    </div>
  );
}
