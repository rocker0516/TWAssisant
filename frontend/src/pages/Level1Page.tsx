import { useState } from "react";
import {
  useLevel1Board,
  useLevel1Performance,
  useLevel1Validation,
} from "../api/client";
import { Level1BoardTable } from "../components/Level1BoardTable";
import { Level1HealthPanel } from "../components/Level1HealthPanel";

// Level 1 ML 推薦軌（FRS §8）：純預測排序展示，Score/Rank 不是買賣指令。
// K 不固定——Ledger 存全排名，這裡只是視圖。5D 主軌（定義凍結）。
// U_t＝可交易 Universe（ADV20 ≥ 5,000 萬 ∧ 非處置）——與體檢籤驗證母體同源，
// 這是 Tradable Universe Redefinition 買回來的性質（設計 §1 同源原則）。

const HORIZONS: { key: number; label: string; note: string }[] = [
  { key: 1, label: "1 日", note: "隔日相對強弱" },
  { key: 5, label: "5 日", note: "主軌" },
  { key: 10, label: "10 日", note: "雙週相對強弱" },
];
const K_OPTIONS = [20, 50, 100];

export default function Level1Page() {
  const [tab, setTab] = useState<"board" | "health">("board");
  const [horizon, setHorizon] = useState(5);
  const [k, setK] = useState(20);
  const { data: board, isLoading } = useLevel1Board(horizon, k);
  const { data: perf } = useLevel1Performance(horizon, k);
  const { data: validation } = useLevel1Validation();

  const lgbmHoldout =
    validation?.horizons?.[String(horizon)]?.ladder?.lgbm?.holdout;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">ML 排序（Level 1）</h1>
        {board?.date && (
          <span className="text-sm text-gray-400">
            {board.date}・{board.model_version}・可交易 Universe {board.universe_size} 檔
          </span>
        )}
      </div>

      <p className="text-xs leading-relaxed text-gray-500">
        模型每日收盤後，對可交易 Universe（20 日均成交值 ≥ 5,000 萬、非處置股）預測
        「未來 N 日相對池內的強弱排名」。此處只做排序展示，分數不是買進指令；
        進出場、部位與風控屬後續交易層。
      </p>

      {lgbmHoldout && (
        <p className="text-xs text-gray-400">
          holdout 驗證：日勝率{" "}
          <b>{(lgbmHoldout.top20_day_win_rate * 100).toFixed(1)}%</b>・Top-20 平均超額{" "}
          <b>
            {lgbmHoldout.top20_excess_pct >= 0 ? "+" : ""}
            {lgbmHoldout.top20_excess_pct.toFixed(2)}pp
          </b>
          ／{horizon} 日（詳見模型體檢）
        </p>
      )}

      {/* 頁籤 */}
      <div className="flex gap-1 border-b border-gray-800">
        {(
          [
            ["board", "今日榜單"],
            ["health", "模型體檢"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-t px-4 py-2 text-sm ${
              tab === key
                ? "border border-b-0 border-gray-700 bg-gray-900 text-white"
                : "text-gray-400 hover:text-gray-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* horizon / K 控制列——兩籤共用（切籤不重置） */}
      <div className="flex flex-wrap items-center gap-2">
        {HORIZONS.map((h) => (
          <button
            key={h.key}
            onClick={() => setHorizon(h.key)}
            className={`rounded-full px-3 py-1 text-sm ${
              horizon === h.key
                ? "bg-sky-600 text-white"
                : "bg-gray-800 text-gray-300 hover:bg-gray-700"
            }`}
            title={h.note}
          >
            {h.label}
            {h.key === 5 && <span className="ml-1 text-[10px] opacity-75">主軌</span>}
          </button>
        ))}
        <span className="mx-2 text-gray-600">|</span>
        {K_OPTIONS.map((n) => (
          <button
            key={n}
            onClick={() => setK(n)}
            className={`rounded px-2 py-1 text-xs ${
              k === n
                ? "bg-gray-600 text-white"
                : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            }`}
          >
            Top {n}
          </button>
        ))}
      </div>

      {tab === "board" ? (
        <Level1BoardTable board={board} isLoading={isLoading} />
      ) : (
        <Level1HealthPanel
          validation={validation}
          perf={perf}
          horizon={horizon}
          k={k}
          modelVersion={board?.model_version ?? null}
        />
      )}
    </div>
  );
}
