"""角落試跑：用**線上引擎的特徵路徑**在真 OOS 區間結算指定角落。

為什麼不直接看挖掘統計
  挖掘統計來自研究快取（condition_judge_cache_v4，止於 2026-07-01），而線上訊號走
  app/engines/corners.py::build_features 另一條路徑。兩者刻意共用 corner_defs 的原子
  定義，但特徵計算是兩份程式碼——本腳本同時驗證「定義沒漂移」與「OOS 表現」。

真 OOS 的界線
  研究快取最後一天 2026-07-01，之後的日子挖掘器沒看過。結算需要訊號後 10 個交易日，
  故可結算的訊號日 = [2026-07-02, 最新行情日 − 10]。

用法：
  .venv/Scripts/python scripts/corner_trial_oos.py S01 S02
  .venv/Scripts/python scripts/corner_trial_oos.py S01 S02 --from 2026-07-02
  .venv/Scripts/python scripts/corner_trial_oos.py S01 S02 --write   # 併寫入 corner_signals
"""
from __future__ import annotations

import sys
from datetime import date

sys.path.insert(0, __file__.replace("\\", "/").rsplit("/scripts/", 1)[0])

from sqlalchemy import distinct, select  # noqa: E402

from app.engines.corner_defs import eval_corner, load_corners  # noqa: E402
from app.engines.corners import CornerEngine, build_features  # noqa: E402
from app.storage import models  # noqa: E402
from app.storage.database import session_scope  # noqa: E402

_H = 10          # 結算窗（交易日），與 corners.json target=hit10 同口徑
_TARGET = 0.10
_OOS_FROM = date(2026, 7, 2)   # 研究快取止於 2026-07-01


def main() -> None:
    ids = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not ids:
        print(__doc__)
        return
    lo = _OOS_FROM
    if "--from" in sys.argv:
        lo = date.fromisoformat(sys.argv[sys.argv.index("--from") + 1])
    write = "--write" in sys.argv

    with session_scope() as s:
        corners = {c["id"]: c for c in load_corners()}
        missing = [i for i in ids if i not in corners]
        if missing:
            print(f"corners.json 找不到：{missing}")
            return
        axis = s.execute(select(distinct(models.DailyPrice.date))
                         .order_by(models.DailyPrice.date)).scalars().all()
        pos = {d: k for k, d in enumerate(axis)}
        # 只取「訊號後還有 ≥_H 個交易日」的日子，否則無法滿窗結算
        targets = [d for d in axis if d >= lo and pos[d] + _H < len(axis)]
        if not targets:
            print(f"{lo} 之後沒有可滿窗結算的交易日（最新行情 {axis[-1]}）")
            return
        print(f"OOS 區間 {targets[0]} ~ {targets[-1]}（{len(targets)} 個交易日）"
              f"；結算＝訊號隔日最高價進場、之後 {_H} 日內摸 +{_TARGET:.0%}\n")

        # 錨池＝角落的 atr 錨單獨成立者。同日對照必須用它，不是全市場：
        # atr>8 池在任何時候的基率都遠高於市場，不控錨就分不出「選對股」與「挑對日」。
        anchors = {cid: next((a for a in corners[cid]["atoms"] if a.startswith("atr>")), None)
                   for cid in ids}
        rows: dict[str, list] = {i: [] for i in ids}
        pool: dict[str, dict[date, list]] = {i: {} for i in ids}
        for t in targets:
            day = build_features(s, t)
            if day.empty:
                continue
            for cid in ids:
                mask = eval_corner(day, corners[cid]["atoms"])
                for _, r in day[mask].iterrows():
                    rows[cid].append((t, r["stock_id"]))
                if anchors[cid]:
                    pm = eval_corner(day, [anchors[cid]])
                    pool[cid][t] = list(day[pm]["stock_id"])

        sids = {sid for v in rows.values() for _, sid in v}
        sids |= {sid for v in pool.values() for lst in v.values() for sid in lst}
        if not sids:
            print("整個 OOS 區間兩條角落都沒有亮燈。")
            return
        px: dict[str, tuple[list, list]] = {}
        for sid, d_, hi in s.execute(
            select(models.DailyPrice.stock_id, models.DailyPrice.date, models.DailyPrice.high)
            .where(models.DailyPrice.stock_id.in_(sids))
            .order_by(models.DailyPrice.stock_id, models.DailyPrice.date)
        ):
            px.setdefault(sid, ([], []))
            px[sid][0].append(d_)
            px[sid][1].append(hi)
        names = dict(s.execute(select(models.Stock.id, models.Stock.name)).all())

    def settle(t: date, sid: str):
        """回 (是否命中, 最大漲幅%)；未滿窗回 None。"""
        dates, highs = px.get(sid, ([], []))
        k = {d: j for j, d in enumerate(dates)}.get(t)
        if k is None or k + 1 >= len(dates):
            return None
        entry = highs[k + 1]
        win = [h for h in highs[k + 1:k + 1 + _H] if h is not None]
        if not entry or len(win) < _H:
            return None
        mfe = max(win) / entry - 1
        return mfe >= _TARGET, round(mfe * 100, 1)

    print(f"{'角落':<6}{'亮燈':>6}{'日數':>6}{'滿窗':>6}{'命中':>6}{'命中率':>8}"
          f"{'同日錨池':>9}{'增量':>9}   挖掘/holdout 增量")
    detail = {}
    for cid in ids:
        c = corners[cid]
        hits = mat = 0
        picks = []
        by_day: dict[date, int] = {}
        for t, sid in rows[cid]:
            r = settle(t, sid)
            if r is None:
                continue
            hit, mfe = r
            mat += 1
            hits += hit
            by_day[t] = by_day.get(t, 0) + 1
            picks.append((t, sid, names.get(sid, ""), mfe, hit))
        # 同日同錨對照：權重＝角落當日亮燈檔數，讓對照的日期分布與角落一致
        num = den = 0.0
        for t, w in by_day.items():
            rs = [settle(t, sid) for sid in pool[cid].get(t, [])]
            rs = [x for x in rs if x is not None]
            if rs:
                num += w * (sum(h for h, _ in rs) / len(rs))
                den += w
        ctrl = (num / den * 100) if den else None
        hr = hits / mat * 100 if mat else None
        print(f"{cid:<6}{len(rows[cid]):>6}{len({t for t, _ in rows[cid]}):>6}"
              f"{mat:>6}{hits:>6}"
              f"{(f'{hr:.1f}%' if hr is not None else '—'):>8}"
              f"{(f'{ctrl:.1f}%' if ctrl is not None else '—'):>9}"
              f"{(f'{hr-ctrl:+.1f}pp' if (hr is not None and ctrl is not None) else '—'):>9}"
              f"   +{c.get('edge_mine_pp')}/+{c.get('edge_holdout_pp')}pp")
        detail[cid] = picks

    for cid, picks in detail.items():
        if not picks:
            continue
        print(f"\n[{cid}] {' ∧ '.join(corners[cid]['atoms'])}")
        for t, sid, nm, mfe, hit in sorted(picks):
            print(f"   {t}  {sid:<6}{nm:<8}最大漲幅 {mfe:>6.1f}%  {'✓命中' if hit else '未達'}")

    if write:
        eng = CornerEngine()
        with session_scope() as s:
            days = s.execute(select(distinct(models.CornerSignal.date))).scalars().all()
            for t in sorted(days):
                eng.run(s, t, only_ids=set(ids))
        print(f"\n已把 {ids} 補寫進既有 {len(days)} 個影子訊號日（其餘角落未動）")


if __name__ == "__main__":
    main()
