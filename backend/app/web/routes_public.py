"""公開頁（匿名、可被索引）：伺服器端 Jinja 模板，與 /app 的 SPA 完全分離。

命名空間約定（與 main.py 的登入牆互為前提）：
  /api/*  JSON API，需 session（AUTH_EXEMPT 除外）
  /app/*  SPA 殼，登入後的工具
  其餘    本模組的公開頁——只放全站共用的盤後資料，沒有任何 per-user 內容，
          所以整個命名空間可以匿名放行，不需要逐路徑登記。

內容邊界（法規考量，見 docs/superpowers/specs 的分層設計第 3 節）：
  公開頁只放「客觀事實」——收盤、漲跌、成交值、法人買賣超。不放買進區間、
  停損、推薦名單；那些屬「建議」，留在登入牆後。

效能：每頁 2~4 個對日期索引的查詢，當前流量下即時算即可。日後流量大了，
正確的下一步是 pipeline 加預生成 step（資料一天只變一次），不是加 cache header。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.deps import get_session
from ..storage import models

router = APIRouter(tags=["public"], include_in_schema=False)
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _latest_dates(session: Session, n: int = 2) -> list[date]:
    """最近 n 個有行情的交易日（降冪）。漲跌幅要兩天收盤才算得出來。"""
    rows = session.execute(
        select(models.DailyPrice.date).distinct()
        .order_by(models.DailyPrice.date.desc()).limit(n)
    ).scalars().all()
    return list(rows)


@dataclass
class _Row:
    stock_id: str
    name: str
    close: float | None
    value: float
    display: str
    signed: bool = True  # 值有方向性（漲跌/買賣超）才上紅綠色


_TOP_N = 20


@router.get("/rankings", response_class=HTMLResponse)
def rankings(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    dates = _latest_dates(session, 2)
    if not dates:
        return _templates.TemplateResponse(request, "rankings.html",
                                           {"cutoff": "尚無資料", "boards": []})
    d0 = dates[0]

    # 漲跌幅：兩日收盤自比。上市+上櫃全宇宙，一次查回來在 Python 配對——
    # 兩日各 ~2000 列，比 SQL 自 join 好讀且對 SQLite 更省查詢計畫。
    boards: list[dict] = []
    if len(dates) == 2:
        d1 = dates[1]
        prev = dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close)
            .where(models.DailyPrice.date == d1, models.DailyPrice.close.is_not(None))
        ).all())
        cur = session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close, models.Stock.name)
            .join(models.Stock, models.Stock.id == models.DailyPrice.stock_id)
            .where(models.DailyPrice.date == d0, models.DailyPrice.close.is_not(None))
        ).all()
        chg = [
            _Row(sid, name, c, pct, f"{pct:+.2f}%")
            for sid, c, name in cur
            if (p := prev.get(sid)) and p > 0
            for pct in [(c / p - 1) * 100]
        ]
        chg.sort(key=lambda r: -r.value)
        boards.append({"title": "漲幅排行", "value_label": "漲跌幅", "rows": chg[:_TOP_N]})
        boards.append({"title": "跌幅排行", "value_label": "漲跌幅",
                       "rows": list(reversed(chg[-_TOP_N:]))})

    # 成交值
    turn = session.execute(
        select(models.DailyPrice.stock_id, models.DailyPrice.close,
               models.DailyPrice.turnover, models.Stock.name)
        .join(models.Stock, models.Stock.id == models.DailyPrice.stock_id)
        .where(models.DailyPrice.date == d0, models.DailyPrice.turnover.is_not(None))
        .order_by(models.DailyPrice.turnover.desc()).limit(_TOP_N)
    ).all()
    boards.append({"title": "成交值排行", "value_label": "成交值(億)", "rows": [
        _Row(sid, name, c, t, f"{t / 1e8:.1f}", signed=False)
        for sid, c, t, name in turn
    ]})

    # 三大法人合計買超（張）
    inst = session.execute(
        select(models.Institutional.stock_id, models.Institutional.total_net,
               models.Stock.name, models.DailyPrice.close)
        .join(models.Stock, models.Stock.id == models.Institutional.stock_id)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Institutional.stock_id)
              & (models.DailyPrice.date == d0))
        .where(models.Institutional.date == d0, models.Institutional.total_net.is_not(None))
        .order_by(models.Institutional.total_net.desc()).limit(_TOP_N)
    ).all()
    boards.append({"title": "法人買超排行", "value_label": "買超(張)", "rows": [
        _Row(sid, name, c, n, f"{n:+,}") for sid, n, name, c in inst
    ]})

    return _templates.TemplateResponse(
        request, "rankings.html", {"cutoff": d0.isoformat(), "boards": boards})


@router.get("/stocks", response_class=HTMLResponse)
def stocks_index(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """全站個股索引。存在的理由是內鏈：沒有這頁，個股頁對爬蟲是孤兒。

    個股頁上線前先以純文字列出（模板內註記了改連結的位置）——本頁自己也是
    可索引的內容（產業×收盤快照）。
    """
    dates = _latest_dates(session, 1)
    d0 = dates[0] if dates else None

    rows = session.execute(
        select(models.Stock.id, models.Stock.name, models.Sector.name.label("sector"),
               models.DailyPrice.close)
        .join(models.Sector, models.Sector.id == models.Stock.sector_id, isouter=True)
        .join(models.DailyPrice,
              (models.DailyPrice.stock_id == models.Stock.id)
              & (models.DailyPrice.date == d0), isouter=True)
        .order_by(models.Sector.name, models.Stock.id)
    ).all()

    groups: list[dict] = []
    for sid, name, sector, close in rows:
        label = sector or "未分類"
        if not groups or groups[-1]["sector"] != label:
            groups.append({"sector": label, "stocks": []})
        groups[-1]["stocks"].append(
            {"stock_id": sid, "name": name, "close": close})

    return _templates.TemplateResponse(
        request, "stocks_index.html",
        {"cutoff": d0.isoformat() if d0 else "尚無資料",
         "total": len(rows), "groups": groups})
