import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { streamSSE } from "../lib/sse";
import { useAssistant } from "../store/assistant";
import { Markdown } from "./Markdown";

// 從目前路由推情境（情境感知）
function useContext() {
  const { pathname } = useLocation();
  const stock = pathname.match(/^\/stocks\/(\w+)/);
  const sector = pathname.match(/^\/sectors\/(\d+)/);
  if (stock) return { page: "stock", stock_id: stock[1] } as const;
  if (sector) return { page: "sector", sector_id: Number(sector[1]) } as const;
  return { page: pathname.slice(1) || "overview" } as const;
}

const HINT: Record<string, string> = {
  stock: "正在看個股",
  sector: "正在看類股",
  overview: "今日總覽",
  recommendations: "進場推薦",
  holdings: "我的持股",
  sectors: "類股行情",
  watchlists: "觀察清單",
};

export function FloatingAssistant() {
  const { open, busy, messages, toggle, addUser, startAssistant, appendAssistant, setBusy } = useAssistant();
  const ctx = useContext();
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, open]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    const history = [...messages, { role: "user" as const, content: text }];
    addUser(text);
    setInput("");
    startAssistant();
    setBusy(true);
    try {
      await streamSSE("/assistant/chat", { method: "POST", body: { context: ctx, history } }, (c) => appendAssistant(c));
    } catch {
      appendAssistant("（連線發生問題，請稍後再試）");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {!open && (
        <button onClick={toggle}
          className="fixed bottom-5 right-5 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-sky-600 text-2xl shadow-lg hover:bg-sky-500"
          title="AI 助手">🤖</button>
      )}
      {open && (
        <div className="fixed bottom-5 right-5 z-40 flex h-[560px] w-[380px] flex-col rounded-xl border border-edge bg-panel shadow-2xl">
          <div className="flex items-center justify-between border-b border-edge px-4 py-2">
            <div>
              <div className="text-sm font-semibold">🤖 AI 助手</div>
              <div className="text-xs text-muted">{HINT[ctx.page] ?? ctx.page}</div>
            </div>
            <button onClick={toggle} className="text-muted hover:text-gray-200">─</button>
          </div>

          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
            {messages.length === 0 && (
              <p className="mt-8 text-center text-sm text-muted">
                問我關於目前畫面的解讀，例如<br />「這檔現在怎麼看？」<br />「今天大盤如何？」
              </p>
            )}
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="ml-8 rounded-lg bg-sky-900/40 px-3 py-2 text-sm">{m.content}</div>
              ) : (
                <div key={i} className="mr-2 rounded-lg bg-panel2 px-3 py-2">
                  {m.content ? <Markdown>{m.content}</Markdown> : <span className="text-sm text-muted">思考中…</span>}
                </div>
              ),
            )}
          </div>

          <div className="flex gap-2 border-t border-edge p-3">
            <input
              className="flex-1 rounded-md border border-edge bg-panel2 px-3 py-2 text-sm outline-none focus:border-sky-600"
              placeholder="輸入問題…" value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()} />
            <button onClick={send} disabled={busy || !input.trim()}
              className="rounded-md bg-sky-600 px-3 text-sm font-medium disabled:opacity-50">送出</button>
          </div>
        </div>
      )}
    </>
  );
}
