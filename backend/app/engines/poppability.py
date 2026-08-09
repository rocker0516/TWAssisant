"""PoppabilityEfficacyEngine：會噴清單成效回測（波段軌 poppable 風格）。

問對的問題：會噴清單到底準不準？取最近數個「已有 ≥H 個未來交易日」的歷史進場日，
point-in-time 跑**真引擎**的 poppable 風格，對過門檻的清單量後來 H 日的**實際結果**
（有沒有摸到 +10%、最高漲多少、最深回撤），並與「全市場(過會噴硬篩宇宙)」基準對比。

進場錨點 = **隔天(推薦日次日)**：實務上盤後看到推薦、隔天才能進；保守錨=隔天最高
(最壞情況追高)、一般錨=隔天開盤(較貼近實際)。摸+10%/回撤皆相對此進場價，只看隔天**之後**
的 H 根（不含隔天當日，避免用同日高作弊）。此為 2026-07 修改，先前錨點是推薦日當天，
不符實務（盤後才知推薦、當天已收盤）。

誠實定位：清單負責「給你一個停利點」(會噴)，**賺不賺看出場紀律**——故同時呈現
「摸+10%率」(清單的職責) 與「最深回撤 / 20日收盤」(提醒噴完可能吐回去)。
point-in-time 無類股修正(±5~8)，故 poppable 總分與線上推薦略有差異，僅供成效參考、非投資建議。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from ..storage import models
from .base import BaseEngine
from .calibration import _INST_COLS, _MIN_BARS, _iter_stock_groups
from .context import StockContext
from .rules.common import COMMON_FILTERS
from .rules.wave import (
    WAVE_FILTERS, ConsolidationScore, explosive_ok, pop_atr_pct, pop_ma_align,
    pop_pos_52w,
)
from .scoring import _DEFAULT_TOP_PCT, _pct_ranks
from .tracks import WaveTrack

# 回測只需：硬篩是否通過 + 會噴 rank 輸入(atr_pct/ma_align) + 低位盤整分(coil 濾網)。
# 故不跑完整 WaveTrack.evaluate(那會多算 9 個不影響會噴分數的規則+停損+evidence，極慢)，
# 改直接呼叫所需的少數規則；且 ctx 不帶法人/融資(這些規則用不到)，省逐日切片。
# 注意：此處用「當日原始硬篩」、不含線上的遲滯寬限（週頻取樣做不了連續日狀態機）。
# 寬限股實測命中率高於清單均值(scripts/pop_hysteresis_backtest.py)，故此統計略保守。
_EFF_FILTERS = COMMON_FILTERS + WAVE_FILTERS
_EFF_CONS = ConsolidationScore()
_EMPTY_INST = pd.DataFrame(columns=_INST_COLS)

_H = 30            # 未來交易日（會噴定義：30 日內碰到 +10%，2026-06 改 20→30 定版）
_POP_TARGET = 0.10  # 「會噴」門檻：持有期間摸到 +10%
_N_DATES = 200      # 統計窗口：取最近 N 個進場日算碰到率（~4年含空頭→可信度橫幅穩定、不被近期牛市虛高）
_PANEL_DATES = 12   # 面板 by_date 表只顯示最近幾列（避免整個統計窗口塞進表格）
_SAMPLE = 5         # 每 5 個交易日取一個（約週頻）
_TOPN = 30          # 明細列出檔數
_WARMUP_DAYS = 160  # 載入往前多抓的日曆天（確保 n_bars≥60）


def _f(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


def _jsonable(o):
    """把結果 dict 內殘留的 numpy 純量轉成原生型別，避免寫入 JSON 欄 TypeError。

    numpy 2.x 的 bool_/float64 比較與運算容易漏進來（且 numpy.bool_ 的型名就叫 'bool'，
    很難一眼看出），統一在存檔前收斂一次最省心。
    """
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    item = getattr(o, "item", None)  # numpy 純量 → python 原生
    if callable(item) and o.__class__.__module__ == "numpy":
        return o.item()
    return o


def _is_coil(c: dict) -> bool:
    """低位盤整＝低檔盤整打底(consolidation≥50)。

    低位已內建在 consolidation 分數裡（區間中位/高位被低位係數壓到不過門檻），故此處只需
    一個門檻、不再另外判 position。與前端『低位盤整』軟篩同門檻(consolidationMeta ≥50)。
    """
    # cons 來自 pandas/numpy 運算 → numpy 比較會產生 numpy.bool_（numpy 2.x 名為 'bool'），
    # 直接塞進 JSON 欄會 TypeError；強制轉回原生 bool。
    return bool((c.get("cons") or 0) >= 50)


class PoppabilityEfficacyEngine(BaseEngine):
    name = "poppability_efficacy"

    def __init__(self) -> None:
        self.track = WaveTrack()

    def run(self, session: Session, trading_date: date) -> dict:
        result = _jsonable(self.compute(session, generated_at=trading_date))
        row = session.get(models.Setting, "poppable_efficacy")
        if row is None:
            session.add(models.Setting(key="poppable_efficacy", value=result))
        else:
            row.value = result
        session.flush()
        return {"status": "ok", "entry_dates": len(result["by_date"]), "list_items": result["total_list"]}

    def _cutoff(self, session: Session) -> float:
        """會噴分數門檻 = 100 − 前N%（與線上 finalize_wave_pop 同一語意）。"""
        row = session.get(models.Setting, "scoring")
        cfg = row.value if row and isinstance(row.value, dict) else {}
        try:
            top_pct = float((cfg.get("wave") or {}).get("top_pct", _DEFAULT_TOP_PCT))
        except (TypeError, ValueError):
            top_pct = _DEFAULT_TOP_PCT
        return 100.0 - top_pct

    def compute(self, session: Session, generated_at: date) -> dict:
        cutoff = self._cutoff(session)
        axis = session.execute(
            select(distinct(models.DailyPrice.date)).order_by(models.DailyPrice.date)
        ).scalars().all()
        # 只取「有 ≥H 個未來交易日」的進場日，週頻取樣、留最近 _N_DATES 個
        eligible = axis[: len(axis) - _H]
        targets = sorted(eligible[::_SAMPLE][-_N_DATES:]) if eligible else []
        if not targets:
            return self._empty(generated_at, cutoff)

        stock_map = {s.id: s for s in session.execute(select(models.Stock)).scalars().all()}
        stock_ids = sorted(stock_map)
        date_lo = min(targets) - timedelta(days=_WARMUP_DAYS)

        # PB（rank 第四因子）：PIT=當日最近一筆 ≤ T（與線上 _load_latest 語意一致）
        from bisect import bisect_right as _br
        val_by_sid: dict[str, tuple[list, list]] = {}
        for vsid, vd, vpb in session.execute(
            select(models.Valuation.stock_id, models.Valuation.date, models.Valuation.pb)
            .where(models.Valuation.date >= date_lo)
            .order_by(models.Valuation.stock_id, models.Valuation.date)
        ):
            val_by_sid.setdefault(vsid, ([], []))
            val_by_sid[vsid][0].append(vd)
            val_by_sid[vsid][1].append(vpb)

        def _pb_at(sid_: str, T_: date):
            v = val_by_sid.get(sid_)
            if not v:
                return None
            i = _br(v[0], T_) - 1
            if i < 0:
                return None
            pb = v[1][i]
            return float(pb) if pb is not None and pb > 0 else None

        # per-date 累積候選（含 rank 原始值 + 實際結果）；排名在收齊全市場後逐日算。
        agg: dict[date, list] = {t: [] for t in targets}
        for sid, pdf, ind_g, inst_g, margin_g in _iter_stock_groups(session, stock_ids, date_lo):
            stock = stock_map.get(sid)
            if stock is None or ind_g is None:
                continue
            if inst_g is None:
                inst_g = pd.DataFrame(columns=_INST_COLS)
            pos_of = {d: i for i, d in enumerate(pdf["date"])}
            opens = [_f(x) for x in pdf["open"]]
            highs = [_f(x) for x in pdf["high"]]
            lows = [_f(x) for x in pdf["low"]]
            closes = [_f(x) for x in pdf["close"]]
            for T in targets:
                p = pos_of.get(T)
                if p is None or p < _MIN_BARS - 1:
                    continue
                ctx = StockContext(
                    stock=stock, date=T, prices=pdf.iloc[: p + 1],
                    inds=ind_g[ind_g["date"] <= T], inst=_EMPTY_INST,
                )
                atr_pct, ma_align = pop_atr_pct(ctx), pop_ma_align(ctx)
                if atr_pct is None or ma_align is None:  # 不可排名 → 不進當日宇宙
                    continue
                pos_52w = pop_pos_52w(ctx)
                pb = _pb_at(sid, T)
                common_ok = all(f.passes(ctx) for f in COMMON_FILTERS)
                passed_filter = common_ok and all(f.passes(ctx) for f in WAVE_FILTERS)
                # 爆發風格：極高波動+上揚月線、不看季線乖離（與線上 tracks.py 同規則）
                explosive = common_ok and explosive_ok(ctx)
                cons = _EFF_CONS.score(ctx)  # 低位盤整擇時濾網（point-in-time，與線上同算）
                # 實務：盤後看到推薦→隔天才能進。保守錨=隔天最高(最壞情況追高)；一般錨=隔天開盤。
                c0 = highs[p + 1] if p + 1 < len(highs) else None
                ce = opens[p + 1] if p + 1 < len(opens) else None
                mfe = dd = cret = None
                hit = hit_close = has_out = False
                if c0 and c0 > 0 and p + 1 + _H < len(closes):
                    fhis = [h for h in highs[p + 2 : p + 2 + _H] if h is not None]
                    flos = [lo for lo in lows[p + 2 : p + 2 + _H] if lo is not None]
                    if fhis and flos:
                        c20 = closes[p + 1 + _H]
                        mfe = max(fhis) / c0 - 1
                        dd = min(flos) / c0 - 1
                        cret = (c20 / c0 - 1) if c20 else None
                        hit = mfe >= _POP_TARGET
                        hit_close = bool(ce and ce > 0 and (max(fhis) / ce - 1) >= _POP_TARGET)
                        has_out = True
                agg[T].append({
                    "stock_id": sid, "name": stock.name,
                    "atr_pct": atr_pct, "ma_align": ma_align,
                    "pos_52w": pos_52w, "pb": pb,
                    "pos": None, "cons": cons,
                    "passed_filter": passed_filter,
                    "explosive": explosive,
                    "has_out": has_out, "mfe": mfe, "dd": dd, "cret": cret,
                    "hit": hit, "hit_close": hit_close,
                })

        by_date = []
        tot_n = tot_hit = tot_hit_close = 0
        tot_coil_n = tot_coil_hit = 0
        tot_exp_n = tot_exp_hit = tot_exp_hit_close = 0
        list_by_t: dict[date, list] = {}
        for t in targets:
            cands = agg[t]
            ar = _pct_ranks([c["atr_pct"] for c in cands])
            lr = _pct_ranks([c["ma_align"] for c in cands])
            pr = _pct_ranks([c["pos_52w"] for c in cands])
            br = _pct_ranks([c["pb"] for c in cands])
            comps = []
            for ra, rl, rp, rb in zip(ar, lr, pr, br):
                # 與線上 finalize_wave_pop 同式（四因子+重排名定版）：pos/pb 缺值中性 0.5
                if ra is None or rl is None:
                    comps.append(None)
                    continue
                rp = 0.5 if rp is None else rp
                rb = 0.5 if rb is None else rb
                comps.append((2.0 * ra + rl + rp + rb) / 5.0)
            for c, tr in zip(cands, _pct_ranks(comps)):  # 合成再重排名 → 真百分位
                c["pop"] = round(tr * 100.0, 1) if tr is not None else None
            uni = [c for c in cands if c["passed_filter"] and c["has_out"]]  # 過會噴硬篩宇宙(基準)
            uni_n = len(uni)
            uni_hit = sum(1 for c in uni if c["hit"])
            lst = [c for c in uni if c["pop"] is not None and c["pop"] >= cutoff]  # 過硬篩 + 前N%
            n = len(lst)
            hit = sum(1 for c in lst if c["hit"])
            hit_close = sum(1 for c in lst if c["hit_close"])
            base_rate = round(uni_hit / uni_n, 3) if uni_n else None
            coil = [c for c in lst if _is_coil(c)]  # 清單中再過「低位盤整」擇時濾網
            coil_n = len(coil)
            coil_hit = sum(1 for c in coil if c["hit"])
            exp = [c for c in cands if c["explosive"] and c["has_out"]]  # 爆發風格（獨立於會噴硬篩）
            exp_n = len(exp)
            exp_hit = sum(1 for c in exp if c["hit"])
            exp_hit_close = sum(1 for c in exp if c["hit_close"])
            tot_n += n
            tot_hit += hit
            tot_hit_close += hit_close
            tot_coil_n += coil_n
            tot_coil_hit += coil_hit
            tot_exp_n += exp_n
            tot_exp_hit += exp_hit
            tot_exp_hit_close += exp_hit_close
            list_by_t[t] = lst
            by_date.append({
                "date": t.isoformat(), "n": n,
                "list_hit_rate": round(hit / n, 3) if n else None,
                "base_hit_rate": base_rate,
                "lift": round((hit / n) / base_rate, 2) if (n and base_rate) else None,
                "avg_mfe": round(sum(c["mfe"] for c in lst) / n * 100, 1) if n else None,
                "avg_dd": round(sum(c["dd"] for c in lst) / n * 100, 1) if n else None,
                "coil_n": coil_n,
                "coil_hit_rate": round(coil_hit / coil_n, 3) if coil_n else None,
                "exp_n": exp_n,
                "exp_hit_rate": round(exp_hit / exp_n, 3) if exp_n else None,
            })

        latest = targets[-1]
        detail = [
            {
                "stock_id": c["stock_id"], "name": c["name"], "pop": c["pop"],
                "atr": round(c["atr_pct"] * 100, 1),
                "mfe": round(c["mfe"] * 100, 1), "dd": round(c["dd"] * 100, 1),
                "cret": round(c["cret"] * 100, 1) if c["cret"] is not None else None,
                "hit": c["hit"], "coil": _is_coil(c),
            }
            for c in sorted(list_by_t[latest], key=lambda r: -(r["pop"] or 0))[:_TOPN]
        ]
        return {
            "track": "wave", "style": "poppable",
            "generated_at": generated_at.isoformat(),
            "horizon": _H, "pop_target": _POP_TARGET, "threshold": cutoff,
            "window": {"from": targets[0].isoformat(), "to": latest.isoformat(), "entry_dates": len(targets)},
            "by_date": by_date[-_PANEL_DATES:],  # 表格只顯示最近數列；整體率仍以全統計窗口計
            "overall_hit_rate": round(tot_hit / tot_n, 3) if tot_n else None,
            "overall_hit_rate_close": round(tot_hit_close / tot_n, 3) if tot_n else None,
            "total_list": tot_n,
            "coil_total": tot_coil_n,
            "coil_overall_hit_rate": round(tot_coil_hit / tot_coil_n, 3) if tot_coil_n else None,
            # 爆發風格（atr>7%+上揚月線、無乖離帽）：同錨點/同窗口的碰到率，供風格對比
            "explosive_total": tot_exp_n,
            "overall_explosive_hit_rate": round(tot_exp_hit / tot_exp_n, 3) if tot_exp_n else None,
            "overall_explosive_hit_rate_close": round(tot_exp_hit_close / tot_exp_n, 3) if tot_exp_n else None,
            "detail_date": latest.isoformat(),
            "detail": detail,
            "note": (
                f"取最近 {len(targets)} 個已有 ≥{_H} 交易日未來的進場日，point-in-time 跑真引擎："
                f"當天全市場橫截面算會噴分數，取**前 {100 - cutoff:.0f}%**（分數≥{cutoff:.0f}）為清單。"
                f"「摸+10%」=之後 {_H} 交易日內持有期間最高價較進場再漲 +10%。同時給兩個進場價："
                "**買在當日最高**(保守、最差價位)=overall_hit_rate；**買在當日收盤**(較貼近實際進場)="
                "overall_hit_rate_close。基準=當日過會噴硬篩宇宙的摸+10%率。清單負責『給停利點』，賺不賺看出場"
                f"——故另列最深回撤/{_H}日收盤提醒噴完可能吐回。**低位盤整子集**＝清單中再過『低檔盤整"
                "打底(區間低位＋波動收斂)』的擇時濾網，比對其摸+10%率是否優於全清單，判這個進場濾網的生死。非投資建議。"
            ),
        }

    def _empty(self, generated_at: date, cutoff: float) -> dict:
        return {
            "track": "wave", "style": "poppable", "generated_at": generated_at.isoformat(),
            "horizon": _H, "pop_target": _POP_TARGET, "threshold": cutoff,
            "window": {"from": None, "to": None, "entry_dates": 0},
            "by_date": [], "overall_hit_rate": None, "overall_hit_rate_close": None, "total_list": 0,
            "coil_total": 0, "coil_overall_hit_rate": None,
            "detail_date": None, "detail": [], "note": "歷史資料不足以回測。",
        }


def recompute_latest() -> dict:
    """as-of 最新行情日跑一次成效回測並寫入 Setting。供獨立子行程呼叫（見 poppability_recompute）。

    刻意走獨立程序：此計算讀全市場×多年、約 3 分鐘，放 uvicorn 內 daemon 執行緒會與請求
    共用 SQLite 連線而鎖死；獨立程序完全隔離、與 standalone 同環境，可靠跑完。
    """
    from ..storage.database import session_scope

    with session_scope() as s:
        as_of = s.execute(select(func.max(models.DailyPrice.date))).scalar() or date.today()
        return PoppabilityEfficacyEngine().run(s, as_of)


if __name__ == "__main__":  # python -m app.engines.poppability
    recompute_latest()
