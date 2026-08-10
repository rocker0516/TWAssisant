// 讀 text/event-stream（data: {json}\n\n），逐段回呼。後端 /api 代理。
export async function streamSSE(
  path: string,
  opts: { method?: string; body?: unknown; signal?: AbortSignal },
  onChunk: (text: string) => void,
): Promise<void> {
  const res = await fetch(`/api${path}`, {
    method: opts.method ?? "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });
  if (res.status === 401 && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      if (line.startsWith("data: ")) {
        const d = line.slice(6);
        if (d === "[DONE]") return;
        try {
          onChunk(JSON.parse(d).text);
        } catch {
          /* ignore */
        }
      }
    }
  }
}
