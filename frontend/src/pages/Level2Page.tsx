import {
  useLevel2Orders,
  useLevel2Positions,
  useLevel2Summary,
} from "../api/client";
import { Level2NavChart } from "../components/Level2NavChart";

// Level 2 模擬帳戶（FRS v1.1）：主 KPI＝成本後絕對報酬；硬約束＝MDD ≤ 大盤。
// 「vs 加權指數」是診斷欄——照常揭露、不作及格線（§14 KPI 修訂，使用者核可）。
// 所有數字由後端計算（metrics 與回測共用），前端只 render。

const STATUS_LABEL: Record<string, string> = {
  pending: "待成交",
  filled: "已成交",
  rejected: "已放棄",
  deferred: "順延中",
};

function Kpi({ label, value, sub, tone }: {
  label: string; value: string; sub?: string;
  tone?: "good" | "bad" | "neutral";
}) {
  const color =
    tone === "good" ? "text-emerald-400"
    : tone === "bad" ? "text-rose-400"
    : "text-gray-100";
  return (
    <div className="rounded border border-gray-800 bg-gray-900 px-4 py-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`text-lg font-bold ${color}`}>{value}</div>
      {sub && <div className="text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

export default function Level2Page() {
  const { data: s, isLoading, isError } = useLevel2Summary();
  const { data: positions } = useLevel2Positions();
  const { data: orders } = useLevel2Orders();

  if (isLoading) return <div className="text-sm text-gray-400">載入中…</div>;
  if (isError || !s) {
    return (
      <div className="space-y-3">
        <h1 className="text-xl font-bold">模擬帳戶（Level 2）</h1>
        <p className="text-sm text-gray-400">
          帳戶尚未建立——首次 21:30 盤後排程（Level2PaperStep）執行後，
          帳戶自動以 100 萬 TWD 起跑，隔日開盤開始成交。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">模擬帳戶（Level 2）</h1>
        <span className="text-sm text-gray-400">
          {s.account}・{s.policy_version}
          {s.start_date && <>・{s.start_date} 起跑・{s.days} 個交易日</>}
        </span>
      </div>

      <p className="text-xs leading-relaxed text-gray-500">
        以 Level 1 每日排名驅動的 paper trading：20 日再平衡持有 Top-20 等權、
        1D 最弱分位防禦出場，次日開盤價成交、含手續費/證交稅/低消。
        主 KPI＝成本後絕對報酬，硬約束＝回撤不得大於大盤；「vs 大盤」為誠實
        揭露的診斷欄（帳戶職責是絕對報酬與回撤控制，指數報酬請持有 0050——
        FRS v1.1 §14）。
      </p>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="淨值" value={s.nav == null ? "—" : s.nav.toLocaleString()}
             sub={`現金 ${s.cash == null ? "—" : s.cash.toLocaleString()}`} />
        <Kpi label="絕對報酬（主 KPI）" value={pct(s.return_pct)}
             tone={s.return_pct == null ? "neutral"
                   : s.return_pct >= 0 ? "good" : "bad"} />
        <Kpi label="MDD vs 大盤（硬約束）"
             value={`${pct(s.mdd_pct)} / ${pct(s.bench_mdd_pct)}`}
             sub={s.mdd_within_bench == null ? undefined
                  : s.mdd_within_bench ? "約束內 ✓" : "破約束——紅色警報"}
             tone={s.mdd_within_bench === false ? "bad" : "neutral"} />
        <Kpi label="vs 大盤（診斷欄）" value={pct(s.excess_vs_bench_pct)}
             sub={`成本累計 ${s.total_costs.toLocaleString()}・成交 ${s.n_fills} 筆・防禦 ${s.n_defense_exits} 次`} />
      </div>

      <div className="rounded border border-gray-800 bg-gray-900 p-3">
        <Level2NavChart points={s.series} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <h2 className="mb-2 text-sm font-bold text-gray-300">
            目前持倉{positions && positions.length > 0 && `（${positions.length} 檔）`}
          </h2>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-1">股票</th>
                <th className="py-1 text-right">股數</th>
                <th className="py-1 text-right">收盤</th>
                <th className="py-1 text-right">市值</th>
                <th className="py-1 text-right">權重</th>
              </tr>
            </thead>
            <tbody>
              {(positions ?? []).map((p) => (
                <tr key={p.stock_id} className="border-b border-gray-800/50">
                  <td className="py-1">{p.stock_id} {p.name ?? ""}</td>
                  <td className="py-1 text-right">{p.qty.toLocaleString()}</td>
                  <td className="py-1 text-right">{p.close ?? "—"}</td>
                  <td className="py-1 text-right">
                    {p.market_value == null ? "—" : Math.round(p.market_value).toLocaleString()}
                  </td>
                  <td className="py-1 text-right">{p.weight_pct ?? "—"}%</td>
                </tr>
              ))}
              {(positions ?? []).length === 0 && (
                <tr><td colSpan={5} className="py-3 text-center text-gray-500">
                  空手（現金 100%）
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div>
          <h2 className="mb-2 text-sm font-bold text-gray-300">近期委託</h2>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="py-1">訊號日</th>
                <th className="py-1">股票</th>
                <th className="py-1">方向</th>
                <th className="py-1 text-right">股數</th>
                <th className="py-1 text-right">成交價</th>
                <th className="py-1">狀態</th>
              </tr>
            </thead>
            <tbody>
              {(orders ?? []).map((o, i) => (
                <tr key={i} className="border-b border-gray-800/50">
                  <td className="py-1">{o.created_date}</td>
                  <td className="py-1">{o.stock_id}</td>
                  <td className={`py-1 ${o.side === "buy" ? "text-rose-400" : "text-emerald-400"}`}>
                    {o.side === "buy" ? "買" : "賣"}
                    <span className="text-gray-500">
                      （{o.reason.startsWith("defense") ? "防禦" : "再平衡"}）
                    </span>
                  </td>
                  <td className="py-1 text-right">{o.qty.toLocaleString()}</td>
                  <td className="py-1 text-right">{o.price ?? "—"}</td>
                  <td className="py-1">{STATUS_LABEL[o.status] ?? o.status}</td>
                </tr>
              ))}
              {(orders ?? []).length === 0 && (
                <tr><td colSpan={6} className="py-3 text-center text-gray-500">
                  尚無委託
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
