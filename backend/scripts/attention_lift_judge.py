"""注意/處置 × 量能標籤：用 pop_condition_judge 同一把尺驗證動能假設。

事件旗標（皆 PIT：公告日盤後才知道，旗標從公告「次日」起算）：
  att_notice5      近 5 交易日內曾公告列注意
  att_punish_new10 處置公告後 10 交易日內
  att_punish_act   當日在處置執行期間（begin~end）

交叉條件：量增（vol_trend = vol_ma5/vol_ma20 > 1.5，與「⚡量增共振」徽章同口徑）、
上升結構（c_over_ma20>0 & ma20_up5）。

護欄與判官相同：日層級 lift、ATR 桶控波動、MAE 代價、挖掘窗 2021~2024 + 3 fold；
--holdout 才看 2025+（只准最後看一次）。

用法：
  python scripts/attention_lift_judge.py [--holdout]
"""
from __future__ import annotations

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import pop_condition_judge as judge  # noqa: E402


def _attach_attention(df: pd.DataFrame) -> pd.DataFrame:
    """把注意/處置事件展開成 PIT 旗標欄位（公告次日起生效）。"""
    con = sqlite3.connect(judge._DB)
    ev = pd.read_sql_query(
        "SELECT stock_id, date, kind, begin_date, end_date FROM attention_listings", con)
    con.close()
    if ev.empty:
        raise SystemExit("attention_listings 為空，先跑 scripts/backfill_attention.py")

    df = df.sort_values(["stock_id", "date"]).reset_index(drop=True)

    # 公告日旗標（merge 到 df 的交易日格）
    for kind, col in (("notice", "ev_notice"), ("punish", "ev_punish")):
        sub = ev[ev["kind"] == kind][["stock_id", "date"]].drop_duplicates()
        sub[col] = 1.0
        df = df.merge(sub, on=["stock_id", "date"], how="left")
        df[col] = df[col].fillna(0.0)

    g = df.groupby("stock_id", sort=False)
    # 公告次日起算：shift(1) 再 rolling window max
    df["att_notice5"] = (g["ev_notice"].transform(lambda s: s.shift(1).rolling(5, 1).max())
                         .fillna(0.0) > 0)
    df["att_punish_new10"] = (g["ev_punish"].transform(lambda s: s.shift(1).rolling(10, 1).max())
                              .fillna(0.0) > 0)

    # 處置執行期間：展開 begin~end 為逐日鍵（日曆日展開，join 交易日自然對齊）
    pun = ev[(ev["kind"] == "punish") & ev["begin_date"].notna() & ev["end_date"].notna()].copy()
    rows = []
    for _, r in pun.iterrows():
        for d in pd.date_range(r["begin_date"], r["end_date"]):
            rows.append((r["stock_id"], d.strftime("%Y-%m-%d")))
    act = pd.DataFrame(rows, columns=["stock_id", "date"]).drop_duplicates()
    act["att_punish_act"] = True
    df = df.merge(act, on=["stock_id", "date"], how="left")
    df["att_punish_act"] = df["att_punish_act"].fillna(False)

    n5 = int(df["att_notice5"].sum())
    p10 = int(df["att_punish_new10"].sum())
    pa = int(df["att_punish_act"].sum())
    print(f"旗標覆蓋：att_notice5={n5:,} 列  att_punish_new10={p10:,} 列  att_punish_act={pa:,} 列\n")
    return df


CONDS = [
    ("處置公告後10日", "att_punish_new10"),
    ("處置執行期間", "att_punish_act"),
    ("注意(5日內)", "att_notice5"),
    ("注意×量增", "att_notice5 & (vol_trend > 1.5)"),
    ("注意×上升結構", "att_notice5 & (c_over_ma20 > 0) & ma20_up5"),
    ("注意×量增×上升", "att_notice5 & (vol_trend > 1.5) & (c_over_ma20 > 0) & ma20_up5"),
    ("處置新×量增", "att_punish_new10 & (vol_trend > 1.5)"),
    ("處置新×上升結構", "att_punish_new10 & (c_over_ma20 > 0) & ma20_up5"),
]


def main() -> None:
    holdout = "--holdout" in sys.argv

    df = pd.read_pickle(judge._CACHE) if os.path.exists(judge._CACHE) else judge._build_cache()
    df = _attach_attention(df)
    mine = df[(df["date"] >= judge._MINE_LO) & (df["date"] <= judge._MINE_HI)]
    print(f"挖掘窗 {judge._MINE_LO}~{judge._MINE_HI}（基率 {mine['hit'].mean()*100:.1f}%）")

    env_m = {c: mine[c] for c in mine.columns}
    for name, expr in CONDS:
        mask = pd.Series(eval(expr, {"np": np, "__builtins__": {}}, env_m),
                         index=mine.index).fillna(False).astype(bool)
        print(f"\n### {name}: {expr}")
        judge._daily_stats(mine, mask, "2021~2024")
        # 3 fold 簡報（穩健性）
        dts = sorted(mine["date"].unique())
        for fi in range(3):
            fd = set(dts[fi * len(dts) // 3:(fi + 1) * len(dts) // 3])
            fm = mine["date"].isin(fd)
            judge._daily_stats(mine[fm], mask[fm], f"fold{fi+1}")

    if holdout:
        print("\n" + "=" * 30 + " HOLDOUT 2025+ " + "=" * 30)
        hold = df[df["date"] >= judge._HOLD_LO]
        env_h = {c: hold[c] for c in hold.columns}
        for name, expr in CONDS:
            mask = pd.Series(eval(expr, {"np": np, "__builtins__": {}}, env_h),
                             index=hold.index).fillna(False).astype(bool)
            print(f"\n### {name}")
            judge._daily_stats(hold, mask, "2025+")


if __name__ == "__main__":
    main()
