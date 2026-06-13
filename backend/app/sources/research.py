"""外部研報來源（可插拔、預設關閉）。

公開券商研報沒有穩定免費 API，爬蟲易壞。此處只留乾淨接口：實作 NewsProvider，
回傳對齊 schemas.EVENT_COLS 的 DataFrame（source 標「研報」）。預設 config.research_enabled=False，
CombinedNewsSource 不會把它納入；要啟用時再於此補上實際爬蟲，抓失敗 graceful 回空，
不可拖垮主新聞流程。
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import BaseSource
from .interfaces import NewsProvider
from . import schemas


class ResearchSource(BaseSource, NewsProvider):
    name = "research"
    requires_token = False

    def _probe(self) -> None:
        # 尚無實際端點，視為「無資料但連線正常」
        return None

    def fetch_events(self, start: date, end: date) -> pd.DataFrame:
        """TODO：接外部研報來源。目前回空（接口佔位，不影響主流程）。"""
        return pd.DataFrame(columns=schemas.EVENT_COLS)
