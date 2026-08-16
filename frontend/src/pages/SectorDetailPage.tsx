import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useSectorDetail, type SectorConstituent, type SectorDetailExtra } from "../api/client";
import { Markdown } from "../components/Markdown";
import { changeColor, fmtNum, fmtPct, scoreColor, trendColor } from "../lib/format";

// ─── 細分（業務標籤）聚合：選中細分時的產業狀態卡 ───

function avg(xs: number[]): number | null {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
}

function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// 細分判讀：把成員數字合成「資金選中沒？齊漲還單騎？什麼階段？」的結論
type SegmentVerdict = {
  stage: { label: string; cls: string };
  sentence: string;          // 白話結論（一句話講完該不該進）
  chips: { label: string; value: string; color?: string }[];
  leaders: SectorConstituent[];
  n: number;
  recommended: number;
};

function segmentVerdict(
  members: SectorConstituent[], mktMom5: number | null, mktMom20: number | null,
): SegmentVerdict {
  const nums = (f: (c: SectorConstituent) => number | null | undefined) =>
    members.map(f).filter((v): v is number => v != null);
  const mom5 = avg(nums((c) => c.mom5_pct));
  const mom20 = avg(nums((c) => c.mom20_pct));
  const rel5 = mom5 != null && mktMom5 != null ? mom5 - mktMom5 : null;
  const rel20 = mom20 != null && mktMom20 != null ? mom20 - mktMom20 : null;
  const withMom = members.filter((c) => c.mom5_pct != null);
  const upCount = withMom.filter((c) => (c.mom5_pct ?? 0) > 0).length;
  const breadth = withMom.length ? upCount / withMom.length : null;
  const inst5 = nums((c) => c.inst_net5);
  const inst20 = nums((c) => c.inst_net20);
  const inst5Sum = inst5.length ? inst5.reduce((a, b) => a + b, 0) : null;
  const inst20Sum = inst20.length ? inst20.reduce((a, b) => a + b, 0) : null;
  const ma = members.filter((c) => c.above_ma20 != null);
  const maRatio = ma.length ? ma.filter((c) => c.above_ma20).length / ma.length : null;
  const revMed = median(nums((c) => c.rev_yoy));
  const leaders = [...withMom].sort((a, b) => (b.mom5_pct ?? 0) - (a.mom5_pct ?? 0)).slice(0, 3);
  const top = leaders[0];

  // ── 階段（相對大盤 5 日 / 20 日的四象限）──
  let stage = { label: "資料不足", cls: "bg-gray-500/10 text-gray-400" };
  if (rel5 != null && rel20 != null) {
    if (rel5 > 0 && rel20 > 0) stage = { label: "強勢延伸", cls: "bg-red-500/15 text-red-300" };
    else if (rel5 > 0) stage = { label: "起漲嘗試", cls: "bg-amber-500/15 text-amber-300" };
    else if (rel20 > 0) stage = { label: "高檔歇腳", cls: "bg-violet-500/15 text-violet-300" };
    else stage = { label: "資金不在這", cls: "bg-emerald-500/15 text-emerald-300" };
  }

  // ── 廣度：齊漲 / 分歧 / 單騎 ──
  const single = breadth != null && breadth <= 0.4 && top && (top.mom5_pct ?? 0) > 3;
  const broad = breadth != null && breadth >= 0.7;

  // ── 法人：5 日 vs 20 日日均 → 加速 / 續買 / 退潮 / 轉賣 ──
  let instText: string | null = null;
  if (inst5Sum != null && inst20Sum != null) {
    const pace = inst20Sum / 4; // 20 日換算成同樣 5 日的步調
    if (inst5Sum > 0 && inst5Sum > pace * 1.5) instText = "法人近5日加速買";
    else if (inst5Sum > 0 && inst20Sum > 0) instText = "法人續買";
    else if (inst5Sum < 0 && inst20Sum > 0) instText = "法人近5日轉賣（20日仍買超）";
    else if (inst5Sum < 0) instText = "法人賣超";
    else instText = "法人觀望";
  }

  // ── 白話結論 ──
  const parts: string[] = [];
  if (rel5 != null) {
    parts.push(rel5 > 0 ? `近5日跑贏大盤 ${rel5.toFixed(1)}%` : `近5日落後大盤 ${Math.abs(rel5).toFixed(1)}%`);
  }
  if (single && top) parts.push(`但只靠${top.name}一檔帶（${upCount}/${withMom.length} 家上漲）`);
  else if (broad) parts.push(`且${upCount}/${withMom.length} 家齊漲`);
  else if (breadth != null) parts.push(`漲跌分歧（${upCount}/${withMom.length} 家上漲）`);
  if (instText) parts.push(instText);

  let advice: string;
  if (stage.label === "強勢延伸" && broad) advice = "資金選中且全面，順勢在細分裡挑強者";
  else if (single) advice = "單騎行情，追高風險大，等第二、三檔跟上再確認";
  else if (stage.label === "起漲嘗試") advice = "剛開始轉強，可留意領漲股是否放量突破";
  else if (stage.label === "高檔歇腳") advice = "動能退潮中，持有者留意獲利保護，未進場者不追";
  else if (stage.label === "資金不在這") advice = "資金未選中，先觀望等輪動";
  else advice = "訊號混雜，暫不下結論";

  return {
    stage,
    sentence: `${parts.join("，")}——${advice}。`,
    chips: [
      { label: "相對大盤5日", value: rel5 != null ? `${rel5 > 0 ? "+" : ""}${rel5.toFixed(1)}%` : "—", color: changeColor(rel5) },
      { label: "相對大盤20日", value: rel20 != null ? `${rel20 > 0 ? "+" : ""}${rel20.toFixed(1)}%` : "—", color: changeColor(rel20) },
      { label: "上漲家數", value: breadth != null ? `${upCount}/${withMom.length}` : "—" },
      { label: "站上月線", value: maRatio != null ? `${Math.round(maRatio * 100)}%` : "—", color: maRatio != null && maRatio >= 0.5 ? "text-up" : "text-down" },
      { label: "法人5日/20日", value: inst5Sum != null ? `${fmtNum(inst5Sum, 0)} / ${fmtNum(inst20Sum, 0)} 張` : "—", color: changeColor(inst5Sum) },
      { label: "營收YoY中位", value: fmtPct(revMed), color: changeColor(revMed) },
    ],
    leaders,
    n: members.length,
    recommended: members.filter((c) => c.recommended).length,
  };
}

function SegmentCard({ tag, members, mktMom5, mktMom20 }: {
  tag: string; members: SectorConstituent[];
  mktMom5: number | null; mktMom20: number | null;
}) {
  const v = useMemo(() => segmentVerdict(members, mktMom5, mktMom20), [members, mktMom5, mktMom20]);
  return (
    <div className="mb-3 rounded-xl border border-edge bg-panel p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-semibold">{tag}</span>
        <span className={`rounded px-2 py-0.5 text-xs ${v.stage.cls}`}>{v.stage.label}</span>
        <span className="text-xs text-muted">{v.n} 家 · ★推薦 {v.recommended} 檔</span>
      </div>
      <p className="mb-3 text-sm text-gray-200">{v.sentence}</p>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
        {v.chips.map((c) => (
          <span key={c.label}>
            {c.label} <span className={`font-medium tabular-nums ${c.color ?? "text-gray-300"}`}>{c.value}</span>
          </span>
        ))}
      </div>
      {v.leaders.length > 0 && (
        <div className="mt-2 text-xs text-muted">
          近5日領漲：
          {v.leaders.map((c, i) => (
            <span key={c.stock_id}>
              {i > 0 && "、"}
              <Link to={`/stocks/${c.stock_id}`} className="text-sky-400 hover:underline">{c.name}</Link>
              <span className={changeColor(c.mom5_pct)}> {fmtPct(c.mom5_pct)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function DirCard({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="rounded-lg border border-edge bg-panel2 p-3 text-center">
      <div className="text-xs text-muted">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${trendColor(value)}`}>{value ?? "—"}</div>
    </div>
  );
}

function DimBar({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-10 text-muted">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-panel2">
        <div className="h-full rounded-full bg-sky-500" style={{ width: `${Math.min(100, value ?? 0)}%` }} />
      </div>
      <span className="w-8 text-right tabular-nums">{value?.toFixed(0) ?? "—"}</span>
    </div>
  );
}

export default function SectorDetailPage() {
  const { id } = useParams();
  const { data, isLoading, isError } = useSectorDetail(id);
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  // 業務標籤出現次數（≥2 家才值得當篩選鈕），依家數排序
  const tagCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of data?.constituents ?? []) for (const t of c.tags) m.set(t, (m.get(t) ?? 0) + 1);
    return [...m.entries()].filter(([, n]) => n >= 2).sort((a, b) => b[1] - a[1]);
  }, [data]);
  // 各細分的成員與近5日平均（籤上色用：紅=強、綠=弱，台股慣例）
  const tagMembers = useMemo(() => {
    const m = new Map<string, SectorConstituent[]>();
    for (const c of data?.constituents ?? []) for (const t of c.tags) {
      const arr = m.get(t) ?? [];
      arr.push(c);
      m.set(t, arr);
    }
    return m;
  }, [data]);
  const tagMom5 = useMemo(() => {
    const m = new Map<string, number | null>();
    for (const [t, arr] of tagMembers) {
      const xs = arr.map((c) => c.mom5_pct).filter((v): v is number => v != null);
      m.set(t, xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
    }
    return m;
  }, [tagMembers]);

  if (isLoading) return <div className="p-6 text-muted">載入中…</div>;
  if (isError || !data) return <div className="p-6 text-down">找不到類股</div>;
  const s = data.sector;
  // openapi 型別未重跑：SectorDetail.constituents 仍是舊 inline 型別，斷言成擴充後的別名
  const shown = (tagFilter
    ? data.constituents.filter((c) => c.tags.includes(tagFilter))
    : data.constituents) as SectorConstituent[];

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <Link to="/sectors" className="text-sm text-sky-400 hover:underline">← 類股行情</Link>

      <div className="mb-5 mt-3 flex items-center gap-3">
        <h1 className="text-2xl font-bold">{s.name}</h1>
        <span className={`text-lg font-semibold ${scoreColor(s.strength_score)}`}>強弱 {s.strength_score?.toFixed(0)}</span>
        <span className="rounded bg-panel2 px-2 py-0.5 text-sm text-gray-300">{s.rotation_stage}</span>
      </div>

      {/* 方向總結卡 */}
      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-3 text-sm font-semibold">方向判讀</div>
          <div className="grid grid-cols-3 gap-3">
            <DirCard label="短波段方向" value={s.trend_short} />
            <DirCard label="中長期方向" value={s.trend_long} />
            <DirCard label="輪動階段" value={s.rotation_stage} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-6 text-sm text-muted">
            <div>近5日動能 <span className={changeColor(s.momentum_5)}>{fmtPct(s.momentum_5)}</span></div>
            <div>近20日動能 <span className={changeColor(s.momentum_20)}>{fmtPct(s.momentum_20)}</span></div>
            <div>法人5日 <span className={changeColor(s.foreign_net)}>{fmtNum(s.foreign_net, 0)} 張</span></div>
            <div>成交佔比 {fmtNum(s.turnover_share)}%</div>
          </div>
        </div>
        <div className="rounded-xl border border-edge bg-panel p-4">
          <div className="mb-3 text-sm font-semibold">強弱三維度</div>
          <div className="flex flex-col gap-3">
            <DimBar label="動能" value={s.dim_momentum} />
            <DimBar label="資金" value={s.dim_fund} />
            <DimBar label="技術" value={s.dim_tech} />
          </div>
          <p className="mt-3 text-xs text-muted">方向與輪動為趨勢判讀，非預測保證。</p>
        </div>
      </div>

      {/* AI 類股方向解讀（盤後批次，進頁即顯示）*/}
      {data.interpretation && (
        <div className="mb-5 rounded-xl border border-edge bg-panel p-4">
          <div className="mb-2 text-sm font-semibold">🤖 AI 類股方向解讀</div>
          <Markdown>{data.interpretation}</Markdown>
        </div>
      )}

      {/* 業務標籤篩選（產業價值鏈細分）*/}
      {tagCounts.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs text-muted">細分：</span>
          <button
            onClick={() => setTagFilter(null)}
            className={`rounded-full px-2.5 py-1 text-xs ${tagFilter === null ? "bg-sky-600 text-white" : "bg-panel2 text-muted hover:text-gray-200"}`}
          >
            全部
          </button>
          {tagCounts.map(([t, n]) => {
            const m5 = tagMom5.get(t);
            return (
              <button
                key={t}
                onClick={() => setTagFilter(tagFilter === t ? null : t)}
                title={`${t} · 近5日平均 ${m5 != null ? `${m5 > 0 ? "+" : ""}${m5.toFixed(1)}%` : "—"}`}
                className={`inline-flex max-w-56 items-center gap-1 truncate rounded-full px-2.5 py-1 text-xs ${tagFilter === t ? "bg-sky-600 text-white" : "bg-panel2 text-muted hover:text-gray-200"}`}
              >
                <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                  m5 == null ? "bg-gray-600" : m5 > 1 ? "bg-red-400" : m5 < -1 ? "bg-emerald-400" : "bg-gray-400"
                }`} />
                {t}（{n}）
                {m5 != null && (
                  <span className={tagFilter === t ? "text-white/80" : changeColor(m5)}>
                    {m5 > 0 ? "+" : ""}{m5.toFixed(1)}%
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* 選中細分 → 產業判讀卡（結論句＋佐證數字）*/}
      {tagFilter && (
        <SegmentCard
          tag={tagFilter}
          members={tagMembers.get(tagFilter) ?? []}
          mktMom5={(data as SectorDetailExtra).market_mom5 ?? null}
          mktMom20={(data as SectorDetailExtra).market_mom20 ?? null}
        />
      )}

      {/* 成分股（領漲排序、★已推薦）*/}
      <div className="overflow-hidden rounded-xl border border-edge">
        <div className="bg-panel2 px-3 py-2 text-sm font-semibold">
          成分股（{shown.length}{tagFilter ? ` / ${data.constituents.length}` : ""}）· 領漲排序
          {tagFilter && <span className="ml-2 text-xs font-normal text-sky-400">篩選：{tagFilter}</span>}
        </div>
        <table className="w-full text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th className="px-3 py-2 text-left">股票</th>
              <th className="px-3 py-2 text-left">業務</th>
              <th className="px-3 py-2 text-right">現價漲跌</th>
              <th className="px-3 py-2 text-right">近5日</th>
              <th className="px-3 py-2 text-center">月線</th>
              <th className="px-3 py-2 text-right">法人5日</th>
              <th className="px-3 py-2 text-right">營收YoY</th>
              <th className="px-3 py-2 text-right">波段</th>
              <th className="px-3 py-2 text-right">長線</th>
              <th className="px-3 py-2 text-center">推薦</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.stock_id} className="border-t border-edge hover:bg-panel/60">
                <td className="px-3 py-2">
                  <Link to={`/stocks/${c.stock_id}`} className="hover:underline">
                    {c.name} <span className="text-xs text-muted">{c.stock_id}</span>
                  </Link>
                </td>
                <td className="max-w-56 px-3 py-2">
                  <span className="flex flex-wrap gap-1">
                    {c.tags.slice(0, 3).map((t) => (
                      <button
                        key={t}
                        onClick={() => setTagFilter(tagFilter === t ? null : t)}
                        title={t}
                        className="max-w-32 truncate rounded bg-panel2 px-1.5 py-0.5 text-[11px] text-muted hover:text-gray-200"
                      >
                        {t}
                      </button>
                    ))}
                    {c.tags.length > 3 && <span className="text-[11px] text-muted">+{c.tags.length - 3}</span>}
                  </span>
                </td>
                <td className={`px-3 py-2 text-right tabular-nums ${changeColor(c.change_pct)}`}>{fmtPct(c.change_pct)}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${changeColor(c.mom5_pct)}`}>{fmtPct(c.mom5_pct)}</td>
                <td className="px-3 py-2 text-center text-xs">{c.above_ma20 == null ? "—" : c.above_ma20 ? "✓" : <span className="text-muted">✗</span>}</td>
                <td className={`px-3 py-2 text-right text-xs tabular-nums ${changeColor(c.inst_net5)}`}>{c.inst_net5 != null ? fmtNum(c.inst_net5, 0) : "—"}</td>
                <td className={`px-3 py-2 text-right text-xs tabular-nums ${changeColor(c.rev_yoy)}`}>{fmtPct(c.rev_yoy)}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${scoreColor(c.wave_score)}`}>{c.wave_score?.toFixed(0) ?? "—"}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${scoreColor(c.long_score)}`}>{c.long_score?.toFixed(0) ?? "—"}</td>
                <td className="px-3 py-2 text-center">{c.recommended ? "★" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
