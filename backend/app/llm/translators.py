"""翻譯員（架構④）：把已算好的結論翻白話。BaseTranslator 抽象，子類給 system + build_facts。

build_facts 只放『質化結論』（強/中/弱、偏多/偏空、買超/賣超），不放畫面數字（守規範①）。
system_prompt 固定（規範①~⑤ + 骨架），設為 cache_control 可快取（同日多檔命中）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .client import HAIKU, LLMClient

SHARED_RULES = """你是台股投資解讀助手，只把『系統已經算好的結論』翻成白話，幫助使用者理解整體方向。請嚴守以下規範：
① 只解讀結論與綜合方向，不要重述畫面上已經有的數字。
② 只談方向（偏多／偏空／中性）與觀察點，絕對不要說「買進」或「賣出」。
③ 固定骨架：先講結論，再講理由，最後講風險／觀察點。
④ 用完整、連貫的段落說明推論過程，不要只是乾話條列。
⑤ 結尾用一句話免責：本內容為輔助解讀、非投資建議，決策請自行評估。
全程使用繁體中文，語氣專業而精簡。"""


def _level(v: float | None) -> str:
    if v is None:
        return "未知"
    return "強勢" if v >= 60 else "弱勢" if v < 40 else "中性"


def _dim(v: float | None) -> str:
    if v is None:
        return "未知"
    return "強" if v >= 66 else "弱" if v < 40 else "中等"


def _mom(v: float | None) -> str:
    if v is None:
        return "未知"
    return "上揚" if v > 1 else "走弱" if v < -1 else "走平"


def _net(v: float | None) -> str:
    if v is None:
        return "未知"
    return "買超" if v > 0 else "賣超" if v < 0 else "持平"


class BaseTranslator(ABC):
    model: str = HAIKU
    role: str = ""

    @property
    def system(self) -> str:
        return f"{SHARED_RULES}\n\n{self.role}"

    @abstractmethod
    def build_facts(self, **data) -> str: ...

    def translate(self, client: LLMClient, **data) -> str | None:
        return client.complete(self.system, self.build_facts(**data), model=self.model)


class SectorTranslator(BaseTranslator):
    role = "任務：解讀一個『類股』目前的方向與輪動位置，幫使用者判斷該追、該抱、還是該觀望。"

    def build_facts(self, *, name, strength, trend_short, trend_long, rotation,
                    dim_momentum, dim_fund, dim_tech, m5, m20, foreign) -> str:
        return (
            f"類股：{name}\n強弱定位：{_level(strength)}\n短波段方向：{trend_short}\n"
            f"中長期方向：{trend_long}\n輪動階段：{rotation}\n"
            f"動能維度：{_dim(dim_momentum)}\n資金維度（法人）：{_dim(dim_fund)}\n"
            f"技術維度：{_dim(dim_tech)}\n近5日趨勢：{_mom(m5)}\n近20日趨勢：{_mom(m20)}\n"
            f"法人近5日：{_net(foreign)}"
        )


class MarketTranslator(BaseTranslator):
    role = "任務：根據盤後大盤概況做一段盤勢總結。"

    def build_facts(self, *, advancers, decliners, foreign_net, trust_net, turnover_billion) -> str:
        breadth = "上漲家數明顯居多" if advancers > decliners * 1.3 else (
            "下跌家數明顯居多" if decliners > advancers * 1.3 else "漲跌家數相當")
        return (
            f"市場廣度：{breadth}\n外資動向：{_net(foreign_net)}\n投信動向：{_net(trust_net)}\n"
            f"成交量能：{'相對活絡' if (turnover_billion or 0) > 3000 else '一般'}"
        )


class HoldingAlertTranslator(BaseTranslator):
    role = "任務：把一檔持股的出場狀態翻成白話提醒，幫使用者理解現在該留意什麼。"

    def build_facts(self, *, name, track, level, signals, profitable) -> str:
        light = {"red": "建議出場", "orange": "警戒", "yellow": "留意", "green": "續抱"}.get(level, level)
        return (
            f"持股：{name}（{'波段' if track == 'wave' else '長線'}軌）\n出場狀態：{light}\n"
            f"目前損益方向：{'獲利中' if profitable else '虧損中'}\n"
            f"觸發的訊號：{('、'.join(signals)) if signals else '無'}"
        )


class StockHealthTranslator(BaseTranslator):
    role = "任務：對一檔個股做『健檢』，綜合技術、籌碼、基本面與所屬類股，給出整體方向解讀。"

    def build_facts(self, *, name, wave_level, wave_passed, long_level, long_passed,
                    chip_net, revenue_trend, pe_level, sector_trend, has_risk) -> str:
        return (
            f"個股：{name}\n波段軌評分定位：{wave_level}（{'達進場門檻' if wave_passed else '未達門檻'}）\n"
            f"長線軌評分定位：{long_level}（{'達進場門檻' if long_passed else '未達門檻'}）\n"
            f"法人籌碼：{_net(chip_net)}\n月營收趨勢：{revenue_trend}\n估值水準：{pe_level}\n"
            f"所屬類股方向：{sector_trend}\n近期是否有重大利空：{'有' if has_risk else '無'}"
        )
