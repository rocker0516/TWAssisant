import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useStockSearch } from "../api/client";

export function StockSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { data: results = [] } = useStockSearch(open ? q : "");

  // 點擊外部關閉
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  useEffect(() => setActive(0), [q]);

  const go = (id: string) => {
    setQ("");
    setOpen(false);
    navigate(`/stocks/${id}`);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && results[active]) {
      go(results[active].stock_id);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={boxRef} className="relative">
      <input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        placeholder="🔍 股號 / 股名"
        className="w-full rounded-lg border border-edge bg-panel2 px-3 py-1.5 text-sm text-gray-200 placeholder:text-muted focus:border-sky-600 focus:outline-none"
      />
      {open && q.trim() && (
        <div className="absolute z-30 mt-1 max-h-80 w-full overflow-auto rounded-lg border border-edge bg-panel shadow-xl">
          {results.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted">查無符合的標的</div>
          ) : (
            results.map((r, i) => (
              <button
                key={r.stock_id}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(r.stock_id)}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
                  i === active ? "bg-sky-900/50 text-sky-100" : "text-gray-300 hover:bg-panel2"
                }`}
              >
                <span className="font-mono text-xs text-muted">{r.stock_id}</span>
                <span className="truncate">{r.name}</span>
                {r.is_etf && <span className="ml-auto rounded bg-panel2 px-1.5 py-0.5 text-[10px] text-muted">ETF</span>}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
