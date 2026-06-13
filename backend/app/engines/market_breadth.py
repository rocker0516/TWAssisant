"""市場廣度 / 分化指標：把『廣度不佳、個股分化』從 LLM 推測變成量出來的事實。

盤勢總結（MarketTranslator）與首頁總覽共用。輕量查詢（單日 join / 掃描），不重算歷史。
- 參與度：站上月線(ma20)/季線(ma60) 的個股占比 → 真正的廣度
- 法人廣度：外資/投信 買超 vs 賣超『家數』（非只看全市場加總，加總會被少數權值股蓋過）
- 投信集中度：買超前 10 檔占投信總買超比例 → 高=護盤集中少數股=廣度差=分化
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage import models


def compute_breadth(session: Session, td: date) -> dict:
    """回市場廣度/分化指標（單日）。無資料時各欄回 None。"""
    rows = session.execute(
        select(models.DailyPrice.close, models.Indicator.ma20, models.Indicator.ma60)
        .join(
            models.Indicator,
            (models.DailyPrice.stock_id == models.Indicator.stock_id)
            & (models.DailyPrice.date == models.Indicator.date),
        )
        .where(models.DailyPrice.date == td)
    ).all()
    tot = a20 = a60 = 0
    for c, m20, m60 in rows:
        if c is None:
            continue
        tot += 1
        if m20 is not None and c > m20:
            a20 += 1
        if m60 is not None and c > m60:
            a60 += 1

    inst = session.execute(
        select(models.Institutional.foreign_net, models.Institutional.trust_net)
        .where(models.Institutional.date == td)
    ).all()
    f_buy = sum(1 for fn, _ in inst if fn and fn > 0)
    f_sell = sum(1 for fn, _ in inst if fn and fn < 0)
    t_buy = sum(1 for _, tn in inst if tn and tn > 0)
    t_sell = sum(1 for _, tn in inst if tn and tn < 0)
    t_pos = sorted((tn for _, tn in inst if tn and tn > 0), reverse=True)
    t_total = sum(t_pos)
    t_conc = round(sum(t_pos[:10]) / t_total * 100, 1) if t_total else None

    return {
        "total": tot or None,
        "pct_above_ma20": round(a20 / tot * 100, 1) if tot else None,
        "pct_above_ma60": round(a60 / tot * 100, 1) if tot else None,
        "foreign_buy_count": f_buy,
        "foreign_sell_count": f_sell,
        "trust_buy_count": t_buy,
        "trust_sell_count": t_sell,
        "trust_top10_concentration": t_conc,
    }
