import { NavLink, Outlet } from "react-router-dom";
import { useMe } from "../api/client";
import { FloatingAssistant } from "./FloatingAssistant";
import { StockSearch } from "./StockSearch";

type NavItem = { to: string; label: string; icon: string; enabled: boolean };

// 6 入口（設計定案）。P1 只開「進場推薦」，其餘標建置中。
const NAV: NavItem[] = [
  { to: "/overview", label: "今日總覽", icon: "🏠", enabled: true },
  { to: "/intel", label: "情報", icon: "📰", enabled: true },
  { to: "/recommendations", label: "進場推薦", icon: "🎯", enabled: true },
  { to: "/sectors", label: "類股行情", icon: "📊", enabled: true },
  { to: "/flow", label: "籌碼動向", icon: "💰", enabled: true },
  { to: "/holdings", label: "我的持股", icon: "💼", enabled: true },
  { to: "/watchlists", label: "觀察清單", icon: "⭐", enabled: true },
  { to: "/lab", label: "策略室", icon: "🧪", enabled: true },
  { to: "/ctx-matrix", label: "情境矩陣", icon: "🗺️", enabled: true },
  { to: "/level1", label: "ML 排序", icon: "🤖", enabled: true },
];

// 側欄底部帳號卡＝設定入口（帳號與偏好同在設定頁）。
// 無登入牆的本機模式沒有 user，退化成單純「設定」入口。
function AccountCard() {
  const { data: me } = useMe();
  const authed = me?.auth_enabled && me?.authenticated;
  return (
    <NavLink
      to="/settings"
      title="帳號與設定"
      className={({ isActive }) =>
        `flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition ${
          isActive
            ? "border-sky-800 bg-sky-900/50 text-sky-200"
            : "border-edge bg-panel2/50 text-gray-300 hover:border-sky-800/60 hover:bg-panel2"
        }`
      }
    >
      <span>{authed ? "👤" : "⚙️"}</span>
      {authed ? (
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate leading-tight" title={me!.email ?? undefined}>{me!.email}</span>
          <span className="flex items-center gap-1.5 text-[10px] leading-tight text-muted">
            <span
              className={`rounded px-1 font-medium ${
                me!.tier === "pro" ? "bg-amber-900/60 text-amber-300" : "bg-edge/60 text-gray-400"
              }`}
            >
              {me!.tier === "pro" ? "Pro" : "Free"}
            </span>
            帳號與設定
          </span>
        </span>
      ) : (
        <span className="flex-1">設定</span>
      )}
      {authed && <span aria-hidden className="shrink-0 text-muted">⚙️</span>}
    </NavLink>
  );
}

export default function Layout() {
  return (
    <div className="flex min-h-screen">
      {/* sticky＋h-screen：內容頁再長，側欄都只占一屏、帳號卡固定在視窗左下。 */}
      <aside className="sticky top-0 flex h-screen w-52 shrink-0 flex-col overflow-y-auto border-r border-edge bg-panel">
        <div className="px-4 py-5">
          <div className="text-lg font-bold">TWAssistant</div>
          <div className="text-xs text-muted">台股操作助手</div>
        </div>
        <div className="px-3 pb-3">
          <StockSearch />
        </div>
        <nav className="flex flex-col gap-0.5 px-2">
          {NAV.map((n) =>
            n.enabled ? (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  `flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${
                    isActive ? "bg-sky-900/50 text-sky-200" : "text-gray-300 hover:bg-panel2"
                  }`
                }
              >
                <span>{n.icon}</span>
                {n.label}
              </NavLink>
            ) : (
              <div
                key={n.to}
                className="flex cursor-not-allowed items-center gap-2 rounded-lg px-3 py-2 text-sm text-gray-600"
                title="建置中"
              >
                <span>{n.icon}</span>
                {n.label}
                <span className="ml-auto text-[10px] text-gray-700">建置中</span>
              </div>
            ),
          )}
        </nav>
        <div className="mt-auto px-2 pb-4">
          <AccountCard />
        </div>
      </aside>
      <main className="flex-1 overflow-x-hidden">
        <Outlet />
      </main>
      <FloatingAssistant />
    </div>
  );
}
