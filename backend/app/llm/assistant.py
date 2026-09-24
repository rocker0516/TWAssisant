"""浮動助手 + 個股健檢的事實組裝（架構④ ask-only / AssistantAgent）。

接地原則：助手只依『App 內部已算好的結論』回答，不編造、不引用外部即時資訊。
context{page,stock_id?,sector_id?} → 後端組 facts 注入 system。健檢為 ask-only 串流。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..engines.exit_engine import ExitEngine
from ..services.holding_service import HoldingService
from ..storage import models
from .store import cache_key, get_cached
from .translators import _level, _mom, _net

ASSISTANT_SYSTEM_BASE = """你是台股操作助手 App 內的 AI 助手。請依據『App 內部已經算好的結論』回答使用者，不要編造畫面上沒有的數字，也不要引用外部即時行情或新聞。嚴守規範：只談方向（偏多／偏空／中性）與觀察點、不要說買進或賣出、結尾附一句免責。使用繁體中文，回答精簡、貼合使用者問題。

你有一組『查詢工具』可主動取用 App 資料庫裡已算好的結論——不要動不動就說「沒有資料、請去某頁看」，而是先用工具查：
- 使用者用名稱提到某檔股票（如「台積電」）→ 先用 find_stock 取得代號，再用 stock_detail 查健檢結論。
- 問某類股 → sector_detail；問今天有什麼可進場 → list_recommendations；問我的持股/觀察 → my_portfolio；問近期消息/利空 → recent_news。
能查就查、查到再答；同一輪可連續呼叫多個工具把事實湊齊。只有當工具也查不到時，才說明該資訊目前不在可用範圍。所有工具回的都是 App 已算好的質化結論，請据此解讀，切勿自行臆測數字或外部消息。

下方已先附上大盤、持股、推薦與類股的概況背景，常見問題可直接引用、不必再查。"""

BRIEF_SYSTEM_BASE = """你是台股操作助手 App 內的 AI 助手。使用者剛點進「{page}」這個頁面，請你主動用三言兩語報今天這個頁面的重點與該注意的事——不是回答問題，而是進頁時的開場提醒。

規範：
- 只根據下方『App 內部已算好的事實』講，不要編造畫面上沒有的數字、不引用外部即時行情或新聞。
- 緊扣「{page}」這個頁面最該關心的事；其他頁的背景僅在有助於理解時順帶一句。
- 只談方向（偏多／偏空／中性）、觀察點與風險，不要說買進或賣出。
- 用繁體中文，輸出兩個小段，各以條列呈現：
  **📌 今日重點**：2～4 條。
  **⚠️ 注意事項**：1～3 條，講風險或要留意處；若確實沒有，寫「今日無特別風險訊號」。
- 全文精簡（約 200 字內），結尾附一句簡短免責。"""

# 各頁中文名（給 brief 的開場用語與情境感知）
_PAGE_LABEL = {
    "overview": "今日總覽",
    "intel": "情報",
    "recommendations": "進場推薦",
    "sectors": "類股行情",
    "flow": "籌碼動向",
    "holdings": "我的持股",
    "watchlists": "觀察清單",
    "stock": "個股詳情",
    "sector": "類股詳情",
}

_exit = ExitEngine()
_holding = HoldingService()
_LIGHT = {"red": "🔴 建議出場", "orange": "🟠 警戒", "yellow": "🟡 留意", "green": "🟢 續抱"}


def _market_date(session: Session) -> date | None:
    return session.execute(select(func.max(models.DailyPrice.date))).scalar()


def _market_facts(session: Session, td: date) -> str | None:
    """大盤概況（質化，沿用 MarketTranslator 口徑）：市場廣度 + 法人動向。"""
    dates = list(session.execute(
        select(models.DailyPrice.date).where(models.DailyPrice.date <= td)
        .distinct().order_by(models.DailyPrice.date.desc()).limit(2)
    ).scalars().all())
    prev = dates[1] if len(dates) > 1 else None
    if prev is None:
        return None

    def closes(d: date) -> dict:
        return dict(session.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.close).where(models.DailyPrice.date == d)
        ).all())

    cur, pv = closes(td), closes(prev)
    adv = dec = 0
    for sid, c in cur.items():
        p = pv.get(sid)
        if c is None or p is None:
            continue
        if c > p:
            adv += 1
        elif c < p:
            dec += 1
    if adv == 0 and dec == 0:
        return None
    breadth = ("上漲家數明顯居多" if adv > dec * 1.3 else
               "下跌家數明顯居多" if dec > adv * 1.3 else "漲跌家數相當")
    f, t = session.execute(
        select(func.sum(models.Institutional.foreign_net), func.sum(models.Institutional.trust_net))
        .where(models.Institutional.date == td)
    ).one()
    return f"大盤概況（{td}）：市場廣度{breadth}；外資{_net(f)}、投信{_net(t)}。"


def _holdings_facts(session: Session, td: date) -> str | None:
    """持股清單與出場燈號（質化）。"""
    rows = session.execute(
        select(models.Holding).where(models.Holding.status == "open")
    ).scalars().all()
    items: list[str] = []
    for h in rows:
        pos = _holding.position(session, h)
        if pos.shares <= 0 or pos.avg_cost is None:
            continue
        close = session.execute(
            select(models.DailyPrice.close)
            .where(models.DailyPrice.stock_id == h.stock_id, models.DailyPrice.date <= td)
            .order_by(models.DailyPrice.date.desc()).limit(1)
        ).scalar()
        if close is None:
            continue
        st = _exit.evaluate(session, h, td, avg_cost=pos.avg_cost, close=close)
        stock = session.get(models.Stock, h.stock_id)
        name = stock.name if stock else h.stock_id
        direction = "獲利中" if close >= pos.avg_cost else "虧損中"
        sig = ("，訊號：" + "、".join(st.signals[:2])) if st.signals else ""
        items.append(f"{name}（{_LIGHT.get(st.level, st.level)}、{direction}{sig}）")
    if not items:
        return None
    return "目前持股出場狀態：" + "；".join(items) + "。"


def _reco_facts(session: Session, td: date) -> str | None:
    """進場推薦：波段軌前幾名（名稱 + 質化定位）。長線軌已移除。"""
    parts: list[str] = []
    for track, label in (("wave", "波段軌"),):
        rows = session.execute(
            select(models.Stock.name, models.Score.total_score)
            .join(models.Stock, models.Score.stock_id == models.Stock.id)
            .where(models.Score.track == track, models.Score.date == td, models.Score.passed.is_(True))
            .order_by(models.Score.total_score.desc()).limit(5)
        ).all()
        if not rows:
            continue
        names = "、".join(f"{n}（{_level(s)}）" for n, s in rows)
        parts.append(f"{label}達門檻共 {len(rows)} 檔（含以上），前幾名：{names}")
    if not parts:
        return None
    return "今日進場推薦：" + "；".join(parts) + "。"


def _sector_rank_facts(session: Session, td: date) -> str | None:
    """類股強弱排行：最強與最弱各幾個。"""
    rows = session.execute(
        select(models.Sector.name, models.SectorDaily.strength_score, models.SectorDaily.trend_short)
        .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
        .where(models.SectorDaily.date == td)
        .order_by(models.SectorDaily.strength_score.desc())
    ).all()
    if not rows:
        return None
    strong = "、".join(f"{n}（{_level(s)}）" for n, s, _ in rows[:4])
    weak = "、".join(f"{n}（{_level(s)}）" for n, s, _ in rows[-3:][::-1])
    return f"類股強弱：較強為 {strong}；較弱為 {weak}。"


def _pe_level(pe: float | None) -> str:
    if pe is None or pe <= 0:
        return "未知"
    return "偏低" if pe < 15 else "偏高" if pe > 25 else "合理"


def _scale_label(billion: float | None) -> str:
    """ETF 規模（億元）→ 大/中/小型。"""
    if billion is None:
        return "未知"
    return "大型" if billion >= 1000 else "小型" if billion < 100 else "中型"


def _etf_kind(fund_type: str | None) -> str:
    """基金類型字串 → 精簡分類（債券/主動式/指數股票型）。"""
    s = fund_type or ""
    if "債" in s:
        return "債券型"
    if "主動" in s:
        return "主動式"
    if "指數" in s or "ETF" in s:
        return "指數型"
    return s or "未知"


def etf_facts(session: Session, stock_id: str, close: float | None) -> dict | None:
    """ETF 身分事實（無此檔回 None）。規模 = 發行單位數 × 收盤價。"""
    p = session.get(models.EtfProfile, stock_id)
    if p is None:
        return None
    billion = round(p.units * close / 1e8, 0) if (p.units and close) else None
    return {
        "kind": _etf_kind(p.fund_type),
        "track_index": p.track_index,
        "has_foreign": p.has_foreign,
        "scale_label": _scale_label(billion),
        "scale_billion": billion,
    }


def _latest_close(session: Session, stock_id: str, td: date) -> float | None:
    return session.execute(
        select(models.DailyPrice.close)
        .where(models.DailyPrice.stock_id == stock_id, models.DailyPrice.date <= td)
        .order_by(models.DailyPrice.date.desc()).limit(1)
    ).scalar()


def _levels_phrase(levels: list | None) -> str:
    """把客觀支撐/壓力位轉成接地的一句（給 LLM 引用，不讓它自己生數字）。"""
    if not levels:
        return ""
    sups = [l for l in levels if l.kind == "support"]
    res = [l for l in levels if l.kind == "resistance"]

    def fmt(l) -> str:
        m = "、".join(l.methods[:2])
        tag = "最強" if l.strength >= 90 else ""
        return f"{l.price:g}（{m}{('，' + tag) if tag else ''}）"

    parts = []
    if sups:
        parts.append("客觀支撐：" + "、".join(fmt(l) for l in sups[:3]))
    if res:
        parts.append("壓力：" + "、".join(fmt(l) for l in res[:2]))
    return "；".join(parts)


def health_facts(session: Session, stock_id: str, td: date) -> dict | None:
    stock = session.get(models.Stock, stock_id)
    if stock is None:
        return None
    w = session.get(models.Score, {"stock_id": stock_id, "date": td, "track": "wave"})
    inst = session.execute(
        select(models.Institutional).where(models.Institutional.stock_id == stock_id)
        .order_by(models.Institutional.date.desc()).limit(1)
    ).scalars().first()
    rev = session.execute(
        select(models.RevenueMonthly).where(models.RevenueMonthly.stock_id == stock_id)
        .order_by(models.RevenueMonthly.year.desc(), models.RevenueMonthly.month.desc()).limit(1)
    ).scalars().first()
    val = session.execute(
        select(models.Valuation).where(models.Valuation.stock_id == stock_id)
        .order_by(models.Valuation.date.desc()).limit(1)
    ).scalars().first()
    sd = None
    if stock.sector_id:
        sd = session.execute(
            select(models.SectorDaily).where(models.SectorDaily.sector_id == stock.sector_id)
            .order_by(models.SectorDaily.date.desc()).limit(1)
        ).scalars().first()
    has_risk = session.execute(
        select(func.count()).select_from(models.Event)
        .where(models.Event.stock_id == stock_id, models.Event.is_risk.is_(True))
    ).scalar_one() > 0

    etf = etf_facts(session, stock_id, _latest_close(session, stock_id, td)) if stock.is_etf else None

    from ..engines.support import levels_for_stock

    return {
        "name": stock.name,
        "levels": levels_for_stock(session, stock_id),
        "wave_level": _level(w.total_score) if w else "未知",
        "wave_passed": bool(w and w.passed),
        "chip_net": (inst.foreign_net or 0) + (inst.trust_net or 0) if inst else None,
        "revenue_trend": _mom(rev.yoy) if rev else "未知",
        "pe_level": _pe_level(val.pe if val else None),
        "sector_trend": (sd.trend_short or "未知") if sd else "未知",
        "has_risk": has_risk,
        "is_etf": stock.is_etf,
        "etf": etf,
    }


def _focus_facts(session: Session, context: dict, td: date) -> list[str]:
    """情境焦點：使用者正在看的個股／類股，組成接地事實句（可能為空）。"""
    focus: list[str] = []
    sid = context.get("stock_id")
    if sid:
        hf = health_facts(session, sid, td)
        if hf and hf.get("is_etf"):
            e = hf.get("etf")
            if e:
                idx = e.get("track_index") or "主動式／未對應指數"
                scale = e["scale_label"] if e.get("scale_billion") is None else f"{e['scale_label']}（約 {e['scale_billion']:.0f} 億）"
                ident = (f"類型{e['kind']}、追蹤{idx}、規模{scale}、"
                         f"{'含國外成分' if e.get('has_foreign') else '純國內成分' if e.get('has_foreign') is False else '成分地區未知'}")
            else:
                ident = "基本資料未涵蓋（多為債券型 ETF）"
            lv = _levels_phrase(hf.get("levels"))
            focus.append(
                f"使用者正在看 ETF「{hf['name']}」：{ident}；"
                f"波段定位{hf['wave_level']}；法人{_net(hf['chip_net'])}；"
                f"所屬類股方向{hf['sector_trend']}；近期重大利空：{'有' if hf['has_risk'] else '無'}。"
                + (f"{lv}。" if lv else "")
                + "（ETF 無月營收/本益比，勿套個股基本面）"
            )
        elif hf:
            lv = _levels_phrase(hf.get("levels"))
            focus.append(
                f"使用者正在看個股「{hf['name']}」：波段評分定位{hf['wave_level']}"
                f"（{'達門檻' if hf['wave_passed'] else '未達門檻'}）；法人{_net(hf['chip_net'])}；"
                f"月營收趨勢{hf['revenue_trend']}；估值{hf['pe_level']}；所屬類股方向{hf['sector_trend']}；"
                f"近期重大利空：{'有' if hf['has_risk'] else '無'}。"
                + (f"{lv}（此為量價客觀計算，可引用解讀，勿自行更動數字）。" if lv else "")
            )

    sec_id = context.get("sector_id")
    if sec_id:
        row = session.execute(
            select(models.SectorDaily, models.Sector.name)
            .join(models.Sector, models.SectorDaily.sector_id == models.Sector.id)
            .where(models.SectorDaily.sector_id == sec_id, models.SectorDaily.date == td)
        ).first()
        if row:
            sd, name = row
            focus.append(
                f"使用者正在看類股「{name}」：強弱{_level(sd.strength_score)}、"
                f"短波段{sd.trend_short}、中長期{sd.trend_long}、輪動階段{sd.rotation_stage}。"
            )
    return focus


def _news_brief(session: Session, td: date) -> str | None:
    """近期消息面摘要（重用每日盤後已快取的市場消息 digest）。"""
    txt = get_cached(session, cache_key("news_market", "tw", td))
    return f"近期消息面摘要：{txt.strip()}" if txt else None


def context_facts(session: Session, context: dict) -> str:
    """依情境組可用事實（接地）。

    分兩層：①情境焦點（使用者正在看的個股／類股）放最前面；
    ②全域背景（大盤、持股、推薦、類股排行）一律附上，讓助手有足夠事實可答，
    不會動輒回「沒有資料」。
    """
    td = _market_date(session)
    if not td:
        return "（目前尚無盤後資料）"

    focus = _focus_facts(session, context, td)

    # 全域背景：一律附上 App 已算好的結論
    background = [
        f for f in (
            _market_facts(session, td),
            _holdings_facts(session, td),
            _reco_facts(session, td),
            _sector_rank_facts(session, td),
        ) if f
    ]

    lines: list[str] = []
    if focus:
        lines.append("【目前焦點】")
        lines.extend(focus)
    if background:
        lines.append("【全盤背景】")
        lines.extend(background)
    return "\n".join(lines) if lines else "（目前情境無特定個股／類股）"


def assistant_system(session: Session, context: dict) -> str:
    return f"{ASSISTANT_SYSTEM_BASE}\n\n[目前情境與可用事實]\n{context_facts(session, context)}"


# 各頁今日重點要優先參考的事實（鍵對應下方 parts；焦點事實一律排最前）
_BRIEF_ORDER = {
    "overview": ["market", "news", "reco", "holdings", "secrank"],
    "intel": ["news", "market"],
    "recommendations": ["reco", "market", "secrank"],
    "sectors": ["secrank", "market"],
    "flow": ["market", "reco"],
    "holdings": ["holdings", "market"],
    "watchlists": ["market", "reco", "news"],
    "stock": ["market"],
    "sector": ["secrank", "market"],
}


def brief_facts(session: Session, context: dict) -> tuple[str, str]:
    """組『進頁今日重點』用的事實，與頁面相關者排前。回 (頁名, 事實字串)。"""
    page = context.get("page") or "overview"
    label = _PAGE_LABEL.get(page, page)
    td = _market_date(session)
    if not td:
        return label, ""

    focus = _focus_facts(session, context, td)  # 個股/類股焦點（可能空）
    parts = {
        "market": _market_facts(session, td),
        "holdings": _holdings_facts(session, td),
        "reco": _reco_facts(session, td),
        "secrank": _sector_rank_facts(session, td),
        "news": _news_brief(session, td),
    }
    order = _BRIEF_ORDER.get(page, ["market", "reco", "holdings", "secrank"])
    lines = list(focus) + [parts[k] for k in order if parts.get(k)]
    return label, "\n".join(l for l in lines if l)


def brief_system(page_label: str, facts: str) -> str:
    return BRIEF_SYSTEM_BASE.format(page=page_label) + f"\n\n[App 已算好的事實]\n{facts}"
