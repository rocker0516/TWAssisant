import { useState } from "react";
import { useNavigate } from "react-router-dom";

/** 登入頁（網站模式）。成功後 cookie 由後端設好，直接進 App。 */
export default function LoginPage() {
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        nav("/overview", { replace: true });
      } else {
        setError(data.reason ?? `登入失敗（HTTP ${res.status}）`);
      }
    } catch {
      setError("無法連線到伺服器");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <form
        onSubmit={submit}
        className="w-80 rounded-2xl border border-edge bg-panel p-8 shadow-lg"
      >
        <div className="mb-6 text-center">
          <div className="text-xl font-bold">TWAssistant</div>
          <div className="text-xs text-muted">台股操作助手 — 請先登入</div>
        </div>
        <label className="mb-1 block text-xs text-muted">帳號</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          className="mb-3 w-full rounded-lg border border-edge bg-panel2 px-3 py-2 text-sm outline-none focus:border-sky-500"
        />
        <label className="mb-1 block text-xs text-muted">密碼</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          className="mb-4 w-full rounded-lg border border-edge bg-panel2 px-3 py-2 text-sm outline-none focus:border-sky-500"
        />
        {error && <div className="mb-3 text-xs text-red-400">{error}</div>}
        <button
          type="submit"
          disabled={busy || !username || !password}
          className="w-full rounded-lg bg-sky-600 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-50"
        >
          {busy ? "登入中…" : "登入"}
        </button>
      </form>
    </div>
  );
}
