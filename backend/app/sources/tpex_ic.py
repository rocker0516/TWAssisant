"""櫃買中心「產業價值鏈資訊平台」（ic.tpex.org.tw）→ 個股產業鏈定位。

官方整理的產業鏈資料：每條鏈（半導體/電動車…約 40 條）一頁，含
上游/中游/下游分組 → 主節點（IC設計/晶圓製造…）→ 子節點（LED驅動IC…）→
本國上市/上櫃公司代號。這是「這家公司在產業裡做什麼」的權威免費來源，
用來做個股業務標籤與類股內細分。

頁面為靜態 HTML，逐鏈解析（~40 請求/次）。解析靠序列掃描狀態機：
  - chain-title-panel 上游/中游/下游 → ic_link_XXXX 決定主節點所屬分組
  - companyList_XXXX title=主節點名；其內 sc_link_XXXX=子節點名
  - sc_company_XXXX 表（或主節點直掛表）內 company_basic.php?stk_code=NNNN
"""

from __future__ import annotations

import re

import pandas as pd

from .base import BaseSource
from .twse import _UA
from . import schemas

_BASE = "https://ic.tpex.org.tw"

# 首頁鏈結：<a href="introduce.php?ic=D000" class="link"><span…></span> 半導體 …</a>
_RE_CHAIN_LINK = re.compile(r'href="introduce\.php\?ic=([A-Z0-9]{4})"[^>]*>(.*?)</a>', re.S)
_RE_TOKEN = re.compile(
    r'class="chain-title-panel">(上游|中游|下游)<'
    r'|id="ic_link_([A-Z0-9]{4})"'
    r'|id="companyList_([A-Z0-9]{4})" title="([^"]+)"'
    r'|id="sc_link_([A-Z0-9]{4})"[^>]*><span>[^<]*</span>&nbsp;([^&<]+?)&nbsp;\(\d+家\)'
    r'|id="sc_company_([A-Z0-9]{4})"'
    r'|company_basic\.php\?stk_code=(\d{4,6})'
)


def parse_chain_page(chain_id: str, chain_name: str, html: str) -> list[dict]:
    """單一鏈頁 → 成員列（每公司×節點一列）。"""
    stream_of_main: dict[str, str] = {}
    main_names: dict[str, str] = {}
    sub_names: dict[str, str] = {}
    sub_main: dict[str, str] = {}

    cur_stream: str | None = None
    cur_main: str | None = None
    cur_node: str | None = None
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for m in _RE_TOKEN.finditer(html):
        stream, ic_link, cl_id, cl_title, sc_id, sc_name, scc_id, code = m.groups()
        if stream:
            cur_stream = stream
        elif ic_link:
            if cur_stream:
                stream_of_main[ic_link] = cur_stream
        elif cl_id:
            main_names[cl_id] = cl_title.strip()
            cur_main = cl_id
            cur_node = cl_id  # 主節點直掛公司（無子節點）時歸主節點
        elif sc_id:
            sub_names[sc_id] = sc_name.strip()
            if cur_main:
                sub_main[sc_id] = cur_main
        elif scc_id:
            cur_node = scc_id
        elif code and cur_node:
            key = (code, cur_node)
            if key in seen:
                continue
            seen.add(key)
            main_id = sub_main.get(cur_node, cur_node if cur_node in main_names else None)
            rows.append({
                "stock_id": code,
                "chain_id": chain_id,
                "chain_name": chain_name,
                "stream": stream_of_main.get(main_id) if main_id else None,
                "main_node": main_names.get(main_id) if main_id else None,
                "node_id": cur_node,
                "node_name": sub_names.get(cur_node) or main_names.get(cur_node),
            })
    return rows


class TpexIcSource(BaseSource):
    name = "tpex_ic"
    base_url = _BASE
    requires_token = False

    def _auth_headers(self) -> dict[str, str]:
        # 預設 UA 會被 CDN 給精簡版頁面（無公司表），需帶瀏覽器 UA
        return {"User-Agent": _UA, "Referer": f"{_BASE}/"}

    def _probe(self) -> None:
        self._request("/index.php")

    def fetch_chain_list(self) -> list[tuple[str, str]]:
        """首頁 → [(chain_id, 鏈名)]。"""
        html = self._request("/index.php").text
        out: dict[str, str] = {}
        for cid, inner in _RE_CHAIN_LINK.findall(html):
            name = re.sub(r"<[^>]+>|&nbsp;", " ", inner)
            name = re.sub(r"\s+", "", name)
            if name and cid not in out:
                out[cid] = name
        return sorted(out.items())

    def fetch_industry_chains(self) -> pd.DataFrame:
        """全部鏈 → 成員 DataFrame（INDUSTRY_CHAIN_COLS）。單鏈失敗跳過不中斷。"""
        rows: list[dict] = []
        for cid, cname in self.fetch_chain_list():
            try:
                html = self._request("/introduce.php", {"ic": cid}).text
            except Exception:  # noqa: BLE001 — 單鏈失敗不拖垮整批
                continue
            rows.extend(parse_chain_page(cid, cname, html))
        if not rows:
            return pd.DataFrame(columns=schemas.INDUSTRY_CHAIN_COLS)
        return pd.DataFrame(rows)[schemas.INDUSTRY_CHAIN_COLS]
