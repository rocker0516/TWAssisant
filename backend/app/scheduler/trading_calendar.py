"""交易日判斷（pipeline 開頭用，休市跳過）。

P0 近似：週一~週五為交易日，扣掉已知國定假日。
精確休市表（颱風假等）P0 不處理，後續可接 TWSE 行事曆 API 補強。
"""

from __future__ import annotations

from datetime import date, timedelta

# 已知固定 / 重要休市日（逐年補；P0 先放近期）。月日近似不足以涵蓋全部，
# 但漏判只會多跑一次（冪等 upsert，無害）。
_HOLIDAYS: set[date] = set()


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _HOLIDAYS


def previous_trading_day(d: date) -> date:
    cur = d - timedelta(days=1)
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def resolve_trading_date(today: date) -> date:
    """盤後 pipeline 的目標交易日：今天是交易日即今天，否則往前找最近交易日。"""
    return today if is_trading_day(today) else previous_trading_day(today)
