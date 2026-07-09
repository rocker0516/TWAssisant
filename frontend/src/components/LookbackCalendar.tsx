import { useMemo, useState } from "react";
import type { LookbackDatePoint } from "../api/client";

type Props = {
  points: LookbackDatePoint[];        // 由舊到新
  todayDate: string | null;           // 資料截止日（月曆右上顯示）
  selectedDate: string | null;        // 目前選取的推薦日（null = 沒選 = 顯示今天推薦）
  onSelect: (date: string | null) => void;
};

const WEEK_LABELS = ["日", "一", "二", "三", "四", "五", "六"];

// 命中率配色：紅=高 / 灰=中 / 綠=低（照台股習慣，紅=強）
function rateColor(rate: number | null): string {
  if (rate == null) return "text-muted";
  const pct = rate * 100;
  if (pct >= 60) return "text-rose-400 font-medium";
  if (pct >= 40) return "text-orange-300";
  if (pct >= 20) return "text-gray-300";
  return "text-emerald-500/70";
}

function parseISO(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function fmtISO(y: number, m0: number, d: number): string {
  return `${y}-${String(m0 + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

export function LookbackCalendar({ points, todayDate, selectedDate, onSelect }: Props) {
  // 由日期 map（快查）
  const pointMap = useMemo(() => {
    const m = new Map<string, LookbackDatePoint>();
    for (const p of points) m.set(p.date, p);
    return m;
  }, [points]);

  // 預設檢視月份：優先選取日→今日→最新有推薦的月
  const initialMonth = useMemo(() => {
    const src = selectedDate ?? todayDate ?? points[points.length - 1]?.date;
    const d = src ? parseISO(src) : new Date();
    return { y: d.getFullYear(), m: d.getMonth() };
  }, [selectedDate, todayDate, points]);
  const [view, setView] = useState(initialMonth);

  const { y, m } = view;
  const firstDow = new Date(y, m, 1).getDay();      // 該月 1 號星期幾
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const cells: (number | null)[] = [];
  for (let i = 0; i < firstDow; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  const monthLabel = `${y} 年 ${m + 1} 月`;
  const canPrev = points.length > 0 ? parseISO(points[0].date) < new Date(y, m, 1) : true;
  const todayISO = todayDate;
  const canNext = todayDate ? parseISO(todayDate) > new Date(y, m + 1, 0) : true;

  const goto = (dy: number) => {
    const nm = m + dy;
    const ny = y + Math.floor(nm / 12);
    setView({ y: ny, m: ((nm % 12) + 12) % 12 });
  };

  return (
    <div className="rounded-xl border border-edge bg-panel/40 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            onClick={() => goto(-1)}
            disabled={!canPrev}
            className="rounded-md px-2 py-0.5 text-muted hover:bg-panel2 hover:text-gray-200 disabled:cursor-not-allowed disabled:opacity-30"
            aria-label="上個月"
          >
            ‹
          </button>
          <span className="text-sm font-medium">{monthLabel}</span>
          <button
            onClick={() => goto(1)}
            disabled={!canNext}
            className="rounded-md px-2 py-0.5 text-muted hover:bg-panel2 hover:text-gray-200 disabled:cursor-not-allowed disabled:opacity-30"
            aria-label="下個月"
          >
            ›
          </button>
        </div>
        <button
          onClick={() => onSelect(null)}
          className={`rounded-md px-2.5 py-1 text-xs ${
            selectedDate == null ? "bg-sky-600 text-white" : "text-muted hover:bg-panel2 hover:text-gray-200"
          }`}
        >
          今天
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1 text-center text-[10px] text-muted">
        {WEEK_LABELS.map((w) => (
          <div key={w} className="py-1">{w}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d, i) => {
          if (d == null) return <div key={i} className="h-14 rounded" />;
          const iso = fmtISO(y, m, d);
          const point = pointMap.get(iso);
          const isSelected = selectedDate === iso;
          const isToday = todayISO === iso;
          const hasData = !!point && point.n > 0;
          const rate = point?.hit_rate ?? null;
          const ratePct = rate != null ? Math.round(rate * 100) : null;
          return (
            <button
              key={i}
              onClick={() => hasData && onSelect(iso)}
              disabled={!hasData}
              className={`h-14 rounded border text-left transition ${
                isSelected
                  ? "border-sky-500 bg-sky-950/40"
                  : hasData
                  ? "border-edge hover:border-sky-600 hover:bg-panel2"
                  : "border-transparent"
              } ${!hasData ? "cursor-default opacity-40" : "cursor-pointer"}`}
              title={
                hasData
                  ? `${iso} · ${point!.n} 檔 · 命中 ${point!.hit_count} (${ratePct}%)`
                  : point
                  ? `${iso} · 無推薦（無過門檻標的）`
                  : iso
              }
            >
              <div className="flex flex-col px-1.5 py-0.5">
                <span className={`text-xs tabular-nums ${isToday ? "text-sky-300 font-medium" : "text-gray-300"}`}>
                  {d}{isToday ? "*" : ""}
                </span>
                {point ? (
                  hasData ? (
                    <>
                      <span className={`text-xs tabular-nums ${rateColor(rate)}`}>
                        {ratePct != null ? `${ratePct}%` : "—"}
                      </span>
                      <span className="text-[10px] text-muted tabular-nums">{point.n} 檔</span>
                    </>
                  ) : (
                    <span className="text-[10px] text-muted">無</span>
                  )
                ) : null}
              </div>
            </button>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted">
        <span>命中率＝那天推薦至今摸過 +10% 的比例</span>
        <span className="text-rose-400">≥60%</span>
        <span className="text-orange-300">40–60%</span>
        <span className="text-gray-300">20–40%</span>
        <span className="text-emerald-500/70">&lt;20%</span>
        <span>*＝今日</span>
      </div>
    </div>
  );
}
