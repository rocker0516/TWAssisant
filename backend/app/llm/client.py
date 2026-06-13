"""LLMClient（架構④橫切A底層）。

模型分層（Haiku 例行摘要 / Sonnet 助手追問）、prompt caching（固定 system 設
cache_control，同日多檔第2次起命中）、退避重試、失敗回 None（呼叫端 fallback 舊快取）。
鐵律由上層 prompt 控制：只翻譯已算好的結論、不餵原始股價財報。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

from ..credentials import get_token

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

_MAX_RETRIES = 3


class LLMClient:
    def __init__(self, token: str | None = None) -> None:
        self._token = token or get_token("anthropic")
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self._token)

    def _ensure(self):
        if self._client is None and self._token:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._token)
        return self._client

    def _system_blocks(self, system: str) -> list[dict]:
        # 固定 system 設 ephemeral cache_control → 同 system 連續呼叫第2次起命中快取
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def complete(
        self, system: str, user: str, *, model: str = HAIKU, max_tokens: int = 700,
        temperature: float = 0.4,
    ) -> str | None:
        """單次生成。失敗（耗盡重試）回 None。"""
        client = self._ensure()
        if client is None:
            return None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = client.messages.create(
                    model=model, max_tokens=max_tokens, temperature=temperature,
                    system=self._system_blocks(system),
                    messages=[{"role": "user", "content": user}],
                )
                return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            except Exception as exc:  # noqa: BLE001 — 失敗回 None 由呼叫端 fallback
                import anthropic

                if isinstance(exc, anthropic.APIStatusError) and exc.status_code and 400 <= exc.status_code < 500 and exc.status_code != 429:
                    return None  # 請求問題重試無用
                if attempt == _MAX_RETRIES - 1:
                    return None
                time.sleep(1.5 ** attempt)
        return None

    def stream(
        self, system: str, messages: list[dict], *, model: str = SONNET, max_tokens: int = 900,
        tools: list[dict] | None = None,
    ) -> Iterator[str]:
        """串流生成（助手/健檢 SSE 用）。逐段 yield 文字。"""
        client = self._ensure()
        if client is None:
            yield "（尚未設定 Claude API 金鑰，請至設定頁的資料來源填入）"
            return
        kwargs = {"model": model, "max_tokens": max_tokens, "system": self._system_blocks(system), "messages": messages}
        if tools:
            kwargs["tools"] = tools
        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text

    def stream_tools(
        self, system: str, messages: list[dict], *, tools: list[dict],
        executor: Callable[[str, dict], str], model: str = SONNET, max_tokens: int = 900,
        max_turns: int = 5,
    ) -> Iterator[str]:
        """帶工具的多輪串流（助手 tool-use 用）。

        每輪串流文字 → 若 stop_reason=tool_use，執行工具、把結果回灌再續，直到模型不再叫工具
        或達 max_turns。executor(name, input)->str 由呼叫端綁定一個活的 DB session。
        """
        client = self._ensure()
        if client is None:
            yield "（尚未設定 Claude API 金鑰，請至設定頁的資料來源填入）"
            return
        msgs = list(messages)
        sys_blocks = self._system_blocks(system)
        for _turn in range(max_turns):
            with client.messages.stream(
                model=model, max_tokens=max_tokens, system=sys_blocks, messages=msgs, tools=tools,
            ) as stream:
                for text in stream.text_stream:
                    yield text
                final = stream.get_final_message()
            if final.stop_reason != "tool_use":
                return
            results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": executor(b.name, b.input)}
                for b in final.content if getattr(b, "type", "") == "tool_use"
            ]
            msgs.append({"role": "assistant", "content": final.content})
            msgs.append({"role": "user", "content": results})
