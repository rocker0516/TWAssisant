import { useState } from "react";
import { useMLEntryBoard, useMLEntryHealth, useMLEntryStatus, useMLEntryTracking } from "../api/client";
import { MLEntryBoardView } from "../components/MLEntryBoard";
import { MLEntryHealthView } from "../components/MLEntryHealth";
import { MLEntryTrackingView } from "../components/MLEntryTracking";
import { MLEntryStatusBanner } from "../components/MLEntryStatusBanner";
import { MLEntrySystemView } from "../components/MLEntrySystem";

// ML 進場推薦 FRS v1 頁面（/app/level1，附錄 C 三分頁）：今日榜單｜模型體檢｜系統狀態。
// 定位：Production Shadow / Research Recommendation。狀態橫幅固定顯示，不因分頁改變。

type Tab = "board" | "health" | "system";
const TABS: { key: Tab; label: string }[] = [
  { key: "board", label: "今日榜單" },
  { key: "health", label: "模型體檢" },
  { key: "system", label: "系統狀態" },
];

export default function MLEntryPage() {
  const [tab, setTab] = useState<Tab>("board");
  const [focusGate, setFocusGate] = useState<string | null>(null);
  const { data: board, isLoading: boardLoading } = useMLEntryBoard();
  const { data: health, isLoading: healthLoading } = useMLEntryHealth();
  const { data: status, isLoading: statusLoading } = useMLEntryStatus();
  const { data: tracking, isLoading: trackingLoading } = useMLEntryTracking();
  const stack = board?.stack ?? status?.stack;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">ML 進場推薦（Research Shadow）</h1>
        <span className="text-sm text-gray-400">
          +10% / −5% barrier・3D / 5D / 10D・全市場 U_t・Gate → Rank → Dynamic Top-K・允許 NO_TRADE
        </span>
      </div>
      <MLEntryStatusBanner stack={stack} run={board?.run ?? status?.last_run} progress={status?.live_progress}
                           onGateClick={(g) => { setFocusGate(g); setTab("system"); }} />
      <div className="flex gap-1 border-b border-gray-800">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
                  className={`px-4 py-2 text-sm ${tab === t.key ? "border-b-2 border-sky-400 text-sky-300" : "text-gray-400 hover:text-gray-200"}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "board" && (
        <div className="space-y-6">
          <MLEntryBoardView board={board} isLoading={boardLoading} />
          <MLEntryTrackingView data={tracking} isLoading={trackingLoading} />
        </div>
      )}
      {tab === "health" && <MLEntryHealthView health={health} isLoading={healthLoading} />}
      {tab === "system" && <MLEntrySystemView status={status} health={health} isLoading={statusLoading} focusGate={focusGate} />}
    </div>
  );
}
