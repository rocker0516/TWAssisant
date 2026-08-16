import { useId } from "react";
import type { FearGreedResponse } from "../api/client";
import { fmtNum } from "../lib/format";

// 恐懼貪婪指數卡：儀器質感半圓儀表 + 漸層面積走勢 + 組件分解。
// 分數＝各組件對自身近一年歷史的百分位排名平均（0=極度恐懼、100=極度貪婪）。

const ZONES = [
  { to: 25, color: "#ef4444", label: "極度恐懼" },
  { to: 45, color: "#f97316", label: "恐懼" },
  { to: 55, color: "#94a3b8", label: "中性" },
  { to: 75, color: "#a3e635", label: "貪婪" },
  { to: 100, color: "#22c55e", label: "極度貪婪" },
];

function zoneColor(score: number): string {
  for (const z of ZONES) if (score < z.to) return z.color;
  return ZONES[ZONES.length - 1].color;
}

// ───── 半圓儀表：分區弧 + 刻度 + 發光錐形指針 ─────
function Gauge({ score }: { score: number }) {
  const uid = useId();
  const cx = 100, cy = 92, r = 74;
  const pt = (v: number, radius: number) => {
    const a = Math.PI * (1 - v / 100);
    return [cx + radius * Math.cos(a), cy - radius * Math.sin(a)] as const;
  };
  const arc = (from: number, to: number, radius: number) => {
    const [x1, y1] = pt(from, radius);
    const [x2, y2] = pt(to, radius);
    return `M ${x1.toFixed(1)} ${y1.toFixed(1)} A ${radius} ${radius} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`;
  };
  const color = zoneColor(score);
  // 指針：細長錐形
  const ang = Math.PI * (1 - score / 100);
  const tip = pt(score, r - 14);
  const baseL = [cx + 5 * Math.cos(ang + Math.PI / 2), cy - 5 * Math.sin(ang + Math.PI / 2)];
  const baseR = [cx + 5 * Math.cos(ang - Math.PI / 2), cy - 5 * Math.sin(ang - Math.PI / 2)];

  let from = 0;
  return (
    <svg viewBox="0 0 200 108" className="w-52">
      <defs>
        <filter id={`${uid}-glow`} x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.2" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      {/* 底軌 */}
      <path d={arc(0, 100, r)} fill="none" stroke="#1f2430" strokeWidth="13" />
      {/* 分區弧（區間之間留 1.2 分縫）*/}
      {ZONES.map((z) => {
        const seg = <path key={z.to} d={arc(from + 0.6, z.to - 0.6, r)} fill="none" stroke={z.color} strokeWidth="13" opacity={score >= from && score < z.to + (z.to === 100 ? 1 : 0) ? 1 : 0.28} />;
        from = z.to;
        return seg;
      })}
      {/* 主刻度 */}
      {[0, 25, 45, 55, 75, 100].map((v) => {
        const [x1, y1] = pt(v, r + 9);
        const [x2, y2] = pt(v, r + 13);
        return <line key={v} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#4b5563" strokeWidth="1" />;
      })}
      {/* 端點標籤 */}
      <text x={cx - r} y={cy + 13} textAnchor="middle" fontSize="8.5" fill="#6b7280">恐懼</text>
      <text x={cx + r} y={cy + 13} textAnchor="middle" fontSize="8.5" fill="#6b7280">貪婪</text>
      <text x={cx} y={cy - r - 17} textAnchor="middle" fontSize="7.5" fill="#4b5563">50</text>
      {/* 指針 */}
      <polygon
        points={`${tip[0].toFixed(1)},${tip[1].toFixed(1)} ${baseL[0].toFixed(1)},${baseL[1].toFixed(1)} ${baseR[0].toFixed(1)},${baseR[1].toFixed(1)}`}
        fill={color} filter={`url(#${uid}-glow)`}
      />
      <circle cx={cx} cy={cy} r="7" fill="#0d1017" stroke={color} strokeWidth="2" />
      <circle cx={cx} cy={cy} r="2.2" fill={color} />
    </svg>
  );
}

// ───── 走勢線：分區底色 + 漸層面積 + 末端光點 ─────
function Sparkline({ history, tall }: { history: { date: string; score: number }[]; tall?: boolean }) {
  const uid = useId();
  if (history.length < 2) return null;
  const w = 600, h = tall ? 64 : 56, padT = 4;
  const y = (s: number) => padT + (1 - s / 100) * (h - padT - 2);
  const xs = history.map((_, i) => (i / (history.length - 1)) * w);
  const pts = xs.map((x, i) => `${x.toFixed(1)},${y(history[i].score).toFixed(1)}`);
  const last = history[history.length - 1];
  const color = zoneColor(last.score);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className={`${tall ? "h-16" : "h-14"} w-full`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={`${uid}-area`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* 極端區底色提示 */}
      <rect x="0" y={y(100)} width={w} height={y(75) - y(100)} fill="#22c55e" opacity="0.05" />
      <rect x="0" y={y(25)} width={w} height={y(0) - y(25)} fill="#ef4444" opacity="0.05" />
      {[25, 50, 75].map((lv) => (
        <line key={lv} x1="0" x2={w} y1={y(lv)} y2={y(lv)} stroke="#2a3040" strokeWidth="0.7" strokeDasharray="4 5" />
      ))}
      {/* 面積 + 線 */}
      <polygon points={`0,${h} ${pts.join(" ")} ${w},${h}`} fill={`url(#${uid}-area)`} />
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
      {/* 末端光點 */}
      <circle cx={w} cy={y(last.score)} r="5" fill={color} opacity="0.25" />
      <circle cx={w} cy={y(last.score)} r="2.4" fill={color} />
    </svg>
  );
}

function SparkCaption({ history, center }: { history: { date: string; score: number }[]; center: string }) {
  return (
    <div className="flex justify-between text-[10px] tracking-wide text-muted">
      <span className="tabular-nums">{history[0]?.date}</span>
      <span>{center}</span>
      <span className="tabular-nums">{history[history.length - 1]?.date}</span>
    </div>
  );
}

// 各組件原始值的顯示格式
function fmtValue(key: string, v: number | null | undefined): string {
  if (v == null) return "—";
  switch (key) {
    case "momentum": return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
    case "breadth":
    case "advancers": return `${v.toFixed(0)}%`;
    case "pc_ratio": return v.toFixed(0);
    case "fut_oi": return `${fmtNum(v, 0)} 口`;
    case "inst_flow": return `${fmtNum(v, 0)} 億`;
    case "volatility": return `${(v * 100).toFixed(0)}%`;
    case "margin": return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
    default: return String(v);
  }
}

function PrevChip({ label, v }: { label: string; v: number | null | undefined }) {
  if (v == null) return null;
  return (
    <span className="flex items-center gap-1 rounded-full border border-edge bg-panel2/70 px-2 py-0.5 text-[11px] text-muted">
      {label}
      <b className="tabular-nums" style={{ color: zoneColor(v) }}>{v.toFixed(0)}</b>
    </span>
  );
}

// CNN 官方美股區塊（直抓 CNN，非自算）
function UsBlock({ us }: { us: NonNullable<FearGreedResponse["us"]> }) {
  const color = zoneColor(us.score);
  return (
    <div className="mt-4 border-t border-edge/70 pt-3.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="text-sm font-semibold">🇺🇸 美股 Fear &amp; Greed</span>
        <span className="rounded-full border border-sky-800/60 bg-sky-950/40 px-2 py-0.5 text-[10px] tracking-wide text-sky-300">CNN 官方</span>
        <span className="ml-1 flex items-baseline gap-1.5">
          <span className="text-2xl font-bold tabular-nums" style={{ color, textShadow: `0 0 14px ${color}55` }}>{us.score.toFixed(0)}</span>
          <span className="text-sm font-medium" style={{ color }}>{us.label}</span>
        </span>
        <span className="ml-auto flex flex-wrap gap-1.5">
          <PrevChip label="前收" v={us.prev_close} />
          <PrevChip label="週前" v={us.prev_week} />
          <PrevChip label="月前" v={us.prev_month} />
          <PrevChip label="年前" v={us.prev_year} />
        </span>
      </div>
      {us.history.length >= 2 && (
        <div className="mt-2.5">
          <Sparkline history={us.history} />
          <SparkCaption history={us.history} center="近一年（CNN 官方）" />
        </div>
      )}
    </div>
  );
}

export function FearGreedCard({ data }: { data: FearGreedResponse }) {
  if (data.score == null && !data.us) return null;
  const color = data.score != null ? zoneColor(data.score) : "#94a3b8";
  return (
    <div className="relative mb-5 overflow-hidden rounded-xl border border-edge bg-panel p-4">
      {/* 依當前情緒色暈染的氛圍光（極淡，僅提供層次）*/}
      <div
        aria-hidden
        className="pointer-events-none absolute -left-24 -top-28 h-72 w-72 rounded-full opacity-[0.13] blur-3xl"
        style={{ background: `radial-gradient(circle, ${color}, transparent 70%)` }}
      />
      <div className="relative">
        <div className="mb-3 flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold">
            😨 恐懼貪婪指數 — 台股
            <span className="rounded-full border border-edge bg-panel2/70 px-2 py-0.5 text-[10px] font-normal tracking-wide text-muted">本站以官方數據自算</span>
          </span>
          <span className="text-xs tabular-nums text-muted">{data.date}</span>
        </div>

        {data.score != null && (
          <>
            <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
              {/* 儀表 */}
              <div className="flex shrink-0 flex-col items-center sm:w-60">
                <Gauge score={data.score} />
                <div className="-mt-7 text-center">
                  <div className="text-4xl font-bold tabular-nums" style={{ color, textShadow: `0 0 18px ${color}66` }}>
                    {data.score.toFixed(0)}
                  </div>
                  <div className="text-sm font-medium tracking-wide" style={{ color }}>{data.label}</div>
                </div>
              </div>
              {/* 組件分解 */}
              <div className="grid flex-1 grid-cols-1 content-center gap-x-8 gap-y-2 md:grid-cols-2">
                {data.components.map((c) => (
                  <div key={c.key} className="flex items-center gap-2.5 text-sm" title={c.desc ?? undefined}>
                    <span className="w-20 shrink-0 text-muted">{c.label}</span>
                    <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-panel2">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${c.score}%`, background: `linear-gradient(90deg, ${zoneColor(c.score)}88, ${zoneColor(c.score)})` }}
                      />
                    </div>
                    <span className="w-8 shrink-0 text-right font-semibold tabular-nums" style={{ color: zoneColor(c.score) }}>
                      {c.score.toFixed(0)}
                    </span>
                    <span className="w-20 shrink-0 text-right text-xs tabular-nums text-muted">{fmtValue(c.key, c.value)}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* 台股走勢：全寬自適應 */}
            {data.history.length >= 2 && (
              <div className="mt-4">
                <Sparkline history={data.history} tall />
                <SparkCaption history={data.history} center="台股近半年走勢" />
              </div>
            )}
          </>
        )}

        {data.us && <UsBlock us={data.us} />}

        <p className="mt-3.5 text-[11px] leading-relaxed text-muted">
          台股無官方恐懼貪婪指數：台股分數為本站以官方市場數據（法人/期權/融資/廣度等 8 組件）對近一年歷史做百分位排名之平均；
          美股為 CNN 官方指數直取。兩者為情緒溫度計，非買賣訊號。
        </p>
      </div>
    </div>
  );
}
