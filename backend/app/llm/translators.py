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


def _levels_facts(levels: list | None) -> str:
    """技術支撐/壓力（量價客觀算出）。這是規範①的刻意例外：這些價位是分析主體，
    允許 LLM 引用，但不得自行新增或更動數字。回多行字串（空則回空字串）。"""
    if not levels:
        return ""
    lines = ["技術支撐/壓力（系統由均線、波段前低、量價套牢區客觀算出，可引用解讀但勿更改數字）："]
    for l in levels:
        side = "支撐" if l.kind == "support" else "壓力"
        m = "、".join(l.methods[:3])
        lines.append(f"- {side} {l.price:g}（{m}；距現價{l.distance_pct:+g}%、強度{l.strength}）")
    return "\n".join(lines) + "\n"


class BaseTranslator(ABC):
    model: str = HAIKU
    role: str = ""
    max_tokens: int = 700  # 單主題夠用；彙整多標的/多事件的長輸出 digest 各自覆寫調高

    @property
    def system(self) -> str:
        return f"{SHARED_RULES}\n\n{self.role}"

    @abstractmethod
    def build_facts(self, **data) -> str: ...

    def translate(self, client: LLMClient, **data) -> str | None:
        return client.complete(
            self.system, self.build_facts(**data), model=self.model, max_tokens=self.max_tokens
        )


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
    role = ("任務：根據盤後大盤概況做一段盤勢總結。廣度/分化請依下方『量化指標』陳述，"
            "不要憑漲跌家數臆測；站上均線占比低或投信買超集中＝廣度差/個股分化。")

    def build_facts(self, *, advancers, decliners, foreign_net, trust_net, turnover_billion,
                    breadth=None) -> str:
        ad = "上漲家數明顯居多" if advancers > decliners * 1.3 else (
            "下跌家數明顯居多" if decliners > advancers * 1.3 else "漲跌家數相當")
        lines = [
            f"漲跌家數：{ad}（漲 {advancers} / 跌 {decliners}）",
            f"外資動向：{_net(foreign_net)}",
            f"投信動向：{_net(trust_net)}",
            f"成交量能：{'相對活絡' if (turnover_billion or 0) > 3000 else '一般'}",
        ]
        b = breadth or {}
        if b.get("pct_above_ma20") is not None:
            lvl = "偏弱" if b["pct_above_ma20"] < 40 else ("偏強" if b["pct_above_ma20"] > 60 else "中性")
            lines.append(
                f"市場廣度（量化）：站上月線 {b['pct_above_ma20']}%、站上季線 {b.get('pct_above_ma60')}% → {lvl}"
            )
        if b.get("foreign_buy_count") is not None:
            lines.append(
                f"外資買賣超家數：買超 {b['foreign_buy_count']} 檔 / 賣超 {b['foreign_sell_count']} 檔"
            )
        if b.get("trust_buy_count") is not None:
            conc = b.get("trust_top10_concentration")
            conc_txt = f"，買超前10檔占 {conc}%（{'高度集中=護盤集中少數' if conc and conc > 50 else '相對分散'}）" if conc is not None else ""
            lines.append(
                f"投信買賣超家數：買超 {b['trust_buy_count']} 檔 / 賣超 {b['trust_sell_count']} 檔{conc_txt}"
            )
        return "\n".join(lines)


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
    role = ("任務：對一檔標的做『健檢』，綜合技術、籌碼、基本面與所屬類股，給出整體方向解讀。"
            "若提供了技術支撐/壓力，請點出『目前最關鍵的支撐與壓力各一』並說明為何（多來源重疊者較硬），"
            "以及跌破支撐或站上壓力分別代表的觀察意義；引用系統給的價位即可，勿自行編造數字。"
            "若為 ETF，則改以 ETF 角度解讀（追蹤標的、類型、規模、技術動能與法人籌碼），"
            "不要套用個股的月營收/本益比邏輯。")

    def build_facts(self, *, name, wave_level, wave_passed, long_level, long_passed,
                    chip_net, revenue_trend, pe_level, sector_trend, has_risk,
                    is_etf=False, etf=None, levels=None) -> str:
        lv = _levels_facts(levels)
        if is_etf:
            base = (
                f"ETF：{name}\n波段軌評分定位：{wave_level}（{'達進場門檻' if wave_passed else '未達門檻'}）\n"
                f"長線軌評分定位：{long_level}（{'達進場門檻' if long_passed else '未達門檻'}）\n"
                f"法人籌碼：{_net(chip_net)}\n所屬類股方向：{sector_trend}\n"
                f"近期是否有重大利空：{'有' if has_risk else '無'}\n"
                f"{lv}"
                f"（註：ETF 無個股月營收/本益比，請勿據此評論）"
            )
            if not etf:
                return f"{base}\n基本資料：未涵蓋（多為債券型 ETF，請以技術與籌碼面為主解讀）"
            idx = etf.get("track_index") or "（主動式／未對應指數）"
            foreign = etf.get("has_foreign")
            foreign_txt = "含國外成分股" if foreign else "純國內成分股" if foreign is False else "成分地區未知"
            scale = etf["scale_label"]
            scale_txt = scale if etf.get("scale_billion") is None else f"{scale}（約 {etf['scale_billion']:.0f} 億）"
            return f"{base}\n類型：{etf['kind']}\n追蹤標的：{idx}\n成分地區：{foreign_txt}\n規模：{scale_txt}"
        return (
            f"個股：{name}\n波段軌評分定位：{wave_level}（{'達進場門檻' if wave_passed else '未達門檻'}）\n"
            f"長線軌評分定位：{long_level}（{'達進場門檻' if long_passed else '未達門檻'}）\n"
            f"法人籌碼：{_net(chip_net)}\n月營收趨勢：{revenue_trend}\n估值水準：{pe_level}\n"
            f"所屬類股方向：{sector_trend}\n近期是否有重大利空：{'有' if has_risk else '無'}\n"
            f"{lv}"
        ).rstrip()


# ─────────────── 近期消息總結（情報頁，架構④）───────────────
# facts 只放『已蒐集到的事件標題 + 類別 + 質化定位』，要求 LLM 歸納主題/方向，
# 而非逐條複述標題；不喊單、附免責由 SHARED_RULES 控制。


def _event_lines(events: list[dict], limit: int) -> str:
    """事件條列：日期｜類別｜（個股）標題。category 為利空者標星。"""
    out: list[str] = []
    for e in events[:limit]:
        star = "⚠️" if e.get("is_risk") else ""
        who = f"{e['name']}｜" if e.get("name") else ""
        cat = e.get("category") or "中性"
        out.append(f"- {star}{who}[{cat}] {e['title']}")
    extra = len(events) - limit
    if extra > 0:
        out.append(f"（另有 {extra} 則未列出）")
    return "\n".join(out) if out else "（近期無顯著事件）"


class NewsMarketTranslator(BaseTranslator):
    max_tokens = 1200  # 彙整最多 30 則事件、多段落觀察，700 會截尾
    role = ("任務：根據近期全市場的重大訊息與新聞，整理一段『市場層級的消息重點』。"
            "請歸納出主要題材方向、值得留意的利空叢集，給出整體觀察，不要逐條複述標題。")

    def build_facts(self, *, days, total, risk_count, theme_count, events) -> str:
        return (
            f"統計窗口：近 {days} 日，共 {total} 則事件（其中重大利空 {risk_count} 則、題材 {theme_count} 則）\n"
            f"代表性事件：\n{_event_lines(events, 30)}"
        )


class NewsThemeTranslator(BaseTranslator):
    role = ("任務：整理某一『類股』近期的消息重點，幫使用者掌握該族群最近發生什麼。"
            "請歸納題材與風險方向，結合該類股目前的強弱方向，不要逐條複述標題。")

    def build_facts(self, *, sector, direction, total, risk_count, events) -> str:
        return (
            f"類股：{sector}\n目前方向定位：{direction}\n"
            f"近期事件數：{total}（重大利空 {risk_count} 則）\n"
            f"事件：\n{_event_lines(events, 15)}"
        )


class NewsStockTranslator(BaseTranslator):
    role = ("任務：整理某一檔個股近期消息重點，結合其基本面定位給出觀察。"
            "請歸納消息對營運/題材的意涵與風險，不要逐條複述標題。")

    def build_facts(self, *, name, events, revenue_trend, pe_level, has_risk) -> str:
        return (
            f"個股：{name}\n月營收趨勢：{revenue_trend}\n估值水準：{pe_level}\n"
            f"近期是否有重大利空：{'有' if has_risk else '無'}\n"
            f"近期事件：\n{_event_lines(events, 15)}"
        )


class NewsFocusTranslator(BaseTranslator):
    max_tokens = 1600  # 單篇涵蓋全部持股+觀察標的，逐檔一段，輸出最長
    role = ("任務：針對使用者『持股 + 觀察清單』的標的，整理近期相關消息重點，"
            "特別點出帶利空的標的提醒留意，再點出有題材的標的。請以標的為單位歸納，不要逐條複述標題。")

    def build_facts(self, *, stocks) -> str:
        # stocks: [{name, role, has_risk, events:[...]}]
        blocks: list[str] = []
        for s in stocks:
            tag = "（持股）" if s.get("role") == "holding" else "（觀察）"
            risk = "⚠️ 含利空" if s.get("has_risk") else ""
            titles = "；".join(e["title"] for e in s["events"][:4]) or "近期無顯著消息"
            blocks.append(f"- {s['name']}{tag}{risk}：{titles}")
        body = "\n".join(blocks) if blocks else "（持股與觀察清單近期無顯著消息）"
        return f"關注標的近期消息：\n{body}"
