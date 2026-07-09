"""AI 助手 + 個股健檢（P5，SSE 串流）。

健檢 = ask-only（詳情頁不自動生成，點按才串流）；助手 = 情境感知多輪。
DB 讀取在 handler 內完成（組好 system/facts），串流階段只跑 LLM（避免 session 已關閉）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..llm.assistant import assistant_system, brief_facts, brief_system, health_facts
from ..llm.client import HAIKU, SONNET, LLMClient
from ..llm.store import cache_key, get_cached, put_cached
from ..llm.tools import ASSISTANT_TOOLS, run_tool
from ..llm.translators import StockHealthTranslator
from ..storage import models
from ..storage.database import session_scope
from .deps import get_session

router = APIRouter(tags=["assistant"])
_client = LLMClient()


def _sse(chunks: Iterator[str]) -> Iterator[str]:
    for c in chunks:
        yield f"data: {json.dumps({'text': c}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


@router.get("/stocks/{stock_id}/health")
def stock_health(stock_id: str, session: Session = Depends(get_session)) -> StreamingResponse:
    td = session.execute(select(func.max(models.DailyPrice.date))).scalar()
    facts_dict = health_facts(session, stock_id, td) if td else None
    if facts_dict is None:
        raise HTTPException(404, f"找不到股票 {stock_id} 或尚無資料")
    tr = StockHealthTranslator()
    system, user = tr.system, tr.build_facts(**facts_dict)

    def gen():
        yield from _sse(_client.stream(system, [{"role": "user", "content": user}], model=HAIKU, max_tokens=700))

    return StreamingResponse(gen(), media_type="text/event-stream")


class BriefRequest(BaseModel):
    context: dict = {}  # {page, stock_id?, sector_id?}


@router.post("/assistant/brief")
def assistant_brief(body: BriefRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    """進頁今日重點（情境感知）。命中當日該頁快取直接吐、未命中跑 Haiku 串流並回寫快取。"""
    ctx = body.context or {}
    page = ctx.get("page") or "overview"
    td = session.execute(select(func.max(models.DailyPrice.date))).scalar()
    ref = (f"stock-{ctx['stock_id']}" if ctx.get("stock_id")
           else f"sector-{ctx['sector_id']}" if ctx.get("sector_id") else page)
    key = cache_key("brief", ref, td) if td else None

    cached = get_cached(session, key) if key else None
    if cached:
        return StreamingResponse(_sse(iter([cached])), media_type="text/event-stream")

    label, facts = brief_facts(session, ctx)
    if not facts:
        return StreamingResponse(_sse(iter(["目前尚無今日盤後資料，無法產生重點。"])), media_type="text/event-stream")

    system = brief_system(label, facts)
    user = f"請依我目前所在的「{label}」頁，給今日重點與注意事項。"

    def gen():
        acc: list[str] = []
        for c in _client.stream(system, [{"role": "user", "content": user}], model=HAIKU, max_tokens=600):
            acc.append(c)
            yield f"data: {json.dumps({'text': c}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        text = "".join(acc).strip()
        if text and key and _client.available:  # 只在 LLM 真有產出時回寫，省下同日同頁重跑
            with session_scope() as s:
                put_cached(s, key, "brief", ref, td, text, HAIKU)

    return StreamingResponse(gen(), media_type="text/event-stream")


class ChatMsg(BaseModel):
    role: str  # user / assistant
    content: str


class ChatRequest(BaseModel):
    context: dict = {}
    history: list[ChatMsg]


@router.post("/assistant/chat")
def assistant_chat(body: ChatRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    system = assistant_system(session, body.context or {})
    messages = [{"role": m.role, "content": m.content} for m in body.history if m.role in ("user", "assistant")]
    if not messages:
        raise HTTPException(400, "history 不可為空")

    def gen():
        # 串流期間另開一個活的 session 給工具用（請求用 session 在 handler 返回後即關閉）
        with session_scope() as tool_session:
            def executor(name: str, tool_input: dict) -> str:
                return run_tool(tool_session, name, tool_input)

            yield from _sse(_client.stream_tools(
                system, messages, tools=ASSISTANT_TOOLS, executor=executor,
                model=SONNET, max_tokens=900,
            ))

    return StreamingResponse(gen(), media_type="text/event-stream")
