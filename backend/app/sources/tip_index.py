"""台灣指數公司（TIP）指數定審技術通知：成分股納入/刪除事件。

來源（實測 2026-08）：
  列表  backend.taiwanindex.com.tw/api/downloads/technical_notices（需 TI-Language: zh 標頭）
        category=定審結果 者為成分股審核；史料回溯 2002，總數 ~1,260 份
  附件  api/downloadFile/TechnicalNotices/{id}/tw → PDF，內文固定格式：
        「成分股納入(N)：」「成分股刪除(N)：」各接股票列（或「無」），
        及「自 YYYY年M月D日…起生效」

註：TIP 只涵蓋自編指數（00919/00929/00932 等所追蹤）；0050/0056 屬富時合編、
00878 屬 MSCI，不在此源。納入名單可能含上櫃與非普通股，落庫時過濾 universe。
"""

from __future__ import annotations

import io
import re
import time
from datetime import date

import httpx

_API = "https://backend.taiwanindex.com.tw/api/downloads/technical_notices"
_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
      "Referer": "https://taiwanindex.com.tw/", "TI-Language": "zh"}


def list_review_notices(max_pages: int = 60, pause: float = 0.25) -> list[dict]:
    """全部「定審結果」通知：[{title, url, publish_date(date)}]，新→舊。"""
    out: list[dict] = []
    with httpx.Client(headers={**_H, "Accept": "application/json"}, timeout=25) as c:
        page = 1
        while page <= max_pages:
            r = c.get(_API, params={"page": page, "per_page": 50})
            r.raise_for_status()
            d = r.json()
            for it in d.get("data", []):
                if it.get("category") != "定審結果":
                    continue
                y, m, dd = it["publish_date"].split("/")
                out.append({"title": it["title"].replace(" ", ""),
                            "url": it["url"],
                            "publish_date": date(int(y), int(m), int(dd))})
            if page >= d.get("meta", {}).get("last_page", 1):
                break
            page += 1
            time.sleep(pause)
    return out


_RE_EFFECTIVE = re.compile(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^。]{0,20}?起生效")
_RE_SECTION = re.compile(r"成分股(納入|刪除|新增|剔除)\s*[（(]\s*(\d+)\s*[)）]\s*[:：]")
_RE_CODE = re.compile(r"(?<!\d)(\d{4}[A-Z]?)(?!\d)")  # 4 碼股號（容許權證字尾字母後續過濾）


def parse_notice_pdf(content: bytes, title: str, publish: date) -> list[dict]:
    """PDF → 事件 records：[{index_name, action(add/remove), stock_id, announce_date, effective_date}]。"""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
        txt = "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception:
        return []
    txt = txt.replace("　", " ")

    m = _RE_EFFECTIVE.search(txt)
    effective = date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None

    # 指數名：標題引號內優先
    tm = re.search(r"[「\"](.+?)[」\"]", title)
    index_name = (tm.group(1) if tm else title)[:80]

    events: list[dict] = []
    sections = list(_RE_SECTION.finditer(txt))
    for i, sm in enumerate(sections):
        action = "add" if sm.group(1) in ("納入", "新增") else "remove"
        n_declared = int(sm.group(2))
        seg = txt[sm.end(): sections[i + 1].start() if i + 1 < len(sections) else len(txt)]
        seg = seg.split("註")[0]  # 截掉附註（內含資料日等數字）
        if n_declared == 0:
            continue
        codes = []
        for c in _RE_CODE.findall(seg):
            if c[:4].isdigit() and c[:4] not in codes:
                codes.append(c[:4])
        for c in codes[:n_declared] if n_declared else codes:
            events.append({"index_name": index_name, "action": action, "stock_id": c,
                           "announce_date": publish, "effective_date": effective,
                           "title": title[:120]})
    return events


def fetch_events_resumable(pause: float = 1.5, log=print,
                            registry_path: str | None = None,
                            wall_limit: int = 3) -> tuple[list[dict], bool]:
    """可斷點續傳版：逐份即回事件、429 硬牆偵測即停。

    registry（json set of "title|date"）記錄已處理通知（含零異動者），跑完/中停都保留。
    連續 wall_limit 份「4 次重試全 429」→ 視為配額牆，回傳 (events, wall_hit=True)
    讓呼叫端落庫後擇時再跑。
    """
    import json as _json
    import os as _os

    reg_file = registry_path or (__file__.replace("\\", "/").rsplit("/app/", 1)[0]
                                 + "/data/tip_notices_done.json")
    done: set[str] = set()
    if _os.path.exists(reg_file):
        try:
            done = set(_json.load(open(reg_file, encoding="utf-8")))
        except Exception:
            pass

    notices = list_review_notices()
    todo = [n for n in notices if f"{n['title']}|{n['publish_date']}" not in done]
    log(f"定審通知共 {len(notices)}，已處理 {len(notices)-len(todo)}，待抓 {len(todo)}")

    events: list[dict] = []
    wall_streak = 0
    wall_hit = False

    def _save_reg():
        _json.dump(sorted(done), open(reg_file, "w", encoding="utf-8"), ensure_ascii=False)

    with httpx.Client(headers=_H, timeout=40, follow_redirects=True) as c:
        for k, n in enumerate(todo):
            content = None
            rate_limited_all = True
            for attempt in range(4):
                try:
                    f = c.get(n["url"])
                except Exception:
                    rate_limited_all = False
                    time.sleep(5)
                    continue
                if f.status_code == 429:
                    time.sleep(15 * (attempt + 1))
                    continue
                rate_limited_all = False
                if f.status_code == 200 and f.content.startswith(b"%PDF"):
                    content = f.content
                break
            if content is not None:
                events.extend(parse_notice_pdf(content, n["title"], n["publish_date"]))
                done.add(f"{n['title']}|{n['publish_date']}")
                wall_streak = 0
            elif rate_limited_all:
                wall_streak += 1
                if wall_streak >= wall_limit:
                    log(f"連續 {wall_limit} 份撞 429 配額牆，中停（進度已保留，稍後續跑）")
                    wall_hit = True
                    break
            else:
                done.add(f"{n['title']}|{n['publish_date']}")  # 非限流失敗（壞檔）不再重試
            if k % 25 == 0:
                _save_reg()
                log(f"  {k}/{len(todo)}（本輪事件 {len(events)}）")
            time.sleep(pause)
    _save_reg()
    log(f"本輪結束：事件 {len(events)}，registry {len(done)} 份{'（撞牆中停）' if wall_hit else ''}")
    return events, wall_hit


def fetch_all_events(pause: float = 1.2, log=print,
                     skip: set[tuple[str, date]] | None = None) -> list[dict]:
    """列表 → 逐份下載解析 → 事件 records（呼叫端過濾 universe 後落庫）。

    伺服器有速率限制（429）：檔案間隔預設 1.2s，429 時指數退避重試（最多 4 次）。
    skip＝已處理 (title, publish_date) 集合（續跑用；零異動通知會重抓，無害）。
    """
    notices = list_review_notices()
    if skip:
        notices = [n for n in notices if (n["title"], n["publish_date"]) not in skip]
    log(f"定審結果通知待處理 {len(notices)} 份…")
    events: list[dict] = []
    fails = 0
    with httpx.Client(headers=_H, timeout=40, follow_redirects=True) as c:
        for k, n in enumerate(notices):
            content = None
            for attempt in range(4):
                try:
                    f = c.get(n["url"])
                except Exception:
                    time.sleep(5)
                    continue
                if f.status_code == 429:
                    wait = 20 * (attempt + 1)
                    log(f"  [{k}] 429 限流，等 {wait}s…")
                    time.sleep(wait)
                    continue
                if f.status_code == 200 and f.content.startswith(b"%PDF"):
                    content = f.content
                break
            if content is None:
                fails += 1
            else:
                events.extend(parse_notice_pdf(content, n["title"], n["publish_date"]))
            if k % 40 == 0:
                log(f"  {k}/{len(notices)}（事件 {len(events)}，失敗 {fails}）")
            time.sleep(pause)
    log(f"完成：事件 {len(events)}，失敗 {fails} 份（可重跑續補）")
    return events
