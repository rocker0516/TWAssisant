"""ML 共識確認器：訓練（GBM on hit10）＋每日推論 → data/ml_consensus.json。

實證依據（scripts/ml_synth.py，2026-08）：ML 與四因子選股僅重疊 ~52%，
「兩者皆選」的共識子集 holdout 命中 ≈34% vs 單獨 ~30-31%——ML 當確認器不當主帥。

流程：
  train  用研究特徵快取（mega_mine_features.pkl，有標籤列）訓練，存 joblib
  infer  對「最新交易日」重建同一套特徵（同一份程式碼；LEFT JOIN 標籤容許無標籤近期列），
         模型分在硬篩內每日排名前 20% ＝ ML 圈選 → 與四因子前 20% 的交集即共識
輸出 data/ml_consensus.json：{date, cutoff_pct, scores:{sid:prob}, picks:[sid…]}
serve 端（routes._attach_probabilities）以檔案 mtime 快取讀取，date 對得上才附旗標。

用法：
  python scripts/build_ml_consensus.py train    # 重訓（換目標/新特徵後手動跑）
  python scripts/build_ml_consensus.py infer    # 日更（pipeline MLConsensusStep 呼叫）
  python scripts/build_ml_consensus.py          # train(若無模型) + infer
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import joblib
import numpy as np
import pandas as pd

_BASE = __file__.replace("\\", "/").rsplit("/scripts/", 1)[0]
sys.path.insert(0, _BASE)
sys.path.insert(0, _BASE + "/scripts")

import pop_condition_judge as judge  # noqa: E402
import mega_mine as mm  # noqa: E402
from ml_synth import FEATS as _ALL_FEATS, hard_mask, four_factor, select_top  # noqa: E402

_MODEL = _BASE + "/data/ml_consensus_model.joblib"
_OUT = _BASE + "/data/ml_consensus.json"
_TOP = 0.80


def _log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def train() -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier

    df = pd.read_pickle(_BASE + "/data/mega_mine_features.pkl")
    df = df[df["hit10"].notna()].reset_index(drop=True)
    for c in ("att_notice5", "att_punish10"):
        df[c] = df[c].astype(float)
    feats = [f for f in _ALL_FEATS if f in df.columns and df[f].notna().mean() >= 0.05]
    _log(f"訓練列 {len(df):,}；特徵 {len(feats)}（hit10 基率 {df['hit10'].mean()*100:.1f}%）")
    model = HistGradientBoostingClassifier(
        max_iter=600, learning_rate=0.05, max_leaf_nodes=63,
        min_samples_leaf=200, l2_regularization=5.0,
        early_stopping=True, validation_fraction=0.1, n_iter_no_change=50,
        random_state=42, scoring="roc_auc",
    )
    model.fit(df[feats].to_numpy(np.float64), df["hit10"].to_numpy(np.int32))
    joblib.dump({"model": model, "feats": feats, "trained_at": time.strftime("%Y-%m-%d"),
                 "n_rows": len(df)}, _MODEL)
    _log(f"模型已存 → {_MODEL}（{model.n_iter_} 棵）")


def _build_recent_features() -> pd.DataFrame:
    """近 ~450 日、含無標籤近期列的特徵框（重用 mega_mine 同一套特徵程式碼）。"""
    con = sqlite3.connect(judge._DB)
    lo = pd.read_sql_query(
        "SELECT DATE(MAX(date), '-650 day') d FROM daily_prices", con)["d"].iloc[0]
    _log(f"重建近期特徵（{lo} 起，LEFT JOIN 標籤）…")
    df = pd.read_sql_query(f"""
        SELECT p.stock_id, p.date, p.open, p.high, p.low, p.close, p.volume,
               i.ma5, i.ma10, i.ma20, i.ma60, i.ma120, i.ma240,
               i.vol_ma5, i.vol_ma20, i.kd_k, i.kd_d,
               i.macd, i.macd_signal, i.macd_hist, i.atr14, i.bias_20, i.bias_60,
               f.mfe30, f.mae30, f.ret30, f.mfe10, f.mae10
        FROM daily_prices p
        LEFT JOIN indicators i ON i.stock_id = p.stock_id AND i.date = p.date
        LEFT JOIN forward_labels f ON f.stock_id = p.stock_id AND f.date = p.date
        WHERE p.date >= '{lo}' AND p.stock_id NOT LIKE '00%'
        ORDER BY p.stock_id, p.date""", con)
    con.close()

    # 與 judge._build_cache 相同的衍生欄（複製其邏輯；欄位齊了 mm.build_features 才能接手）
    df["atr_pct"] = df["atr14"] / df["close"]
    df["ma_align"] = ((df["ma5"] > df["ma10"]).astype(float)
                      + (df["ma10"] > df["ma20"]).astype(float)
                      + (df["ma20"] > df["ma60"]).astype(float))
    g = df.groupby("stock_id", sort=False)
    df["ret5"] = (df["close"] / g["close"].shift(5) - 1.0) * 100
    df["ret20"] = (df["close"] / g["close"].shift(20) - 1.0) * 100
    df["ma20_up5"] = df["ma20"] > g["ma20"].shift(5)
    df["vol_ratio"] = df["volume"] / df["vol_ma20"]
    df["vol_trend"] = df["vol_ma5"] / df["vol_ma20"]
    df["c_over_ma20"] = df["close"] / df["ma20"] - 1.0
    df["hit"] = (df["mfe30"] >= 10.0).astype(float)
    df["hit10"] = (df["mfe10"] >= 10.0).astype(float)
    df["pos_52w"] = ((df["close"] - g["low"].transform(lambda s: s.rolling(240, 60).min()))
                     / (g["high"].transform(lambda s: s.rolling(240, 60).max())
                        - g["low"].transform(lambda s: s.rolling(240, 60).min()) + 1e-9))
    df["dist_60d_high"] = (df["close"] / g["high"].transform(lambda s: s.rolling(60, 20).max()) - 1.0) * 100

    con = sqlite3.connect(judge._DB)
    inst = pd.read_sql_query(
        f"SELECT stock_id, date, foreign_net, trust_net, total_net FROM institutional WHERE date >= '{lo}'", con)
    marg = pd.read_sql_query(
        f"SELECT stock_id, date, margin_balance, short_balance FROM margin WHERE date >= '{lo}'", con)
    val = pd.read_sql_query(f"SELECT stock_id, date, pe, pb FROM valuation WHERE date >= '{lo}'", con)
    con.close()
    df = df.merge(inst, on=["stock_id", "date"], how="left")
    df = df.merge(marg, on=["stock_id", "date"], how="left")
    df = df.merge(val, on=["stock_id", "date"], how="left")
    g = df.groupby("stock_id", sort=False)
    for c in ("foreign_net", "trust_net", "total_net"):
        df[c] = df[c].fillna(0.0)
    for c in ("margin_balance", "short_balance", "pe", "pb"):
        df[c] = g[c].ffill()
    g = df.groupby("stock_id", sort=False)
    volsum5 = g["volume"].transform(lambda s: s.rolling(5, 3).sum())
    volsum10 = g["volume"].transform(lambda s: s.rolling(10, 5).sum())
    df["inst_f5"] = g["foreign_net"].transform(lambda s: s.rolling(5, 3).sum()) / (volsum5 + 1e-9)
    df["inst_t5"] = g["trust_net"].transform(lambda s: s.rolling(5, 3).sum()) / (volsum5 + 1e-9)
    df["inst_tot10"] = g["total_net"].transform(lambda s: s.rolling(10, 5).sum()) / (volsum10 + 1e-9)
    pos = (df["total_net"] > 0)
    df["inst_streak"] = pos.groupby([df["stock_id"], (~pos).groupby(df["stock_id"]).cumsum()]).cumsum()
    df["sq_ratio"] = (df["short_balance"] / (df["margin_balance"] + 1e-9)).clip(0, 5) * 100
    df["short_chg5"] = ((df["short_balance"] - g["short_balance"].shift(5))
                        / (g["short_balance"].shift(5) + 1e-9)).clip(-2, 5) * 100
    df["margin_chg5"] = ((df["margin_balance"] - g["margin_balance"].shift(5))
                         / (g["margin_balance"].shift(5) + 1e-9)).clip(-2, 5) * 100
    df = df.drop(columns=["foreign_net", "trust_net", "total_net",
                          "margin_balance", "short_balance"])

    con = sqlite3.connect(judge._DB)
    mkt = pd.read_sql_query("SELECT date, close AS mkt_close FROM market_index ORDER BY date", con)
    sec_map = pd.read_sql_query("SELECT id AS stock_id, sector_id FROM stocks", con)
    con.close()
    if len(mkt) > 0:
        mkt["mkt_bias60"] = (mkt["mkt_close"] / mkt["mkt_close"].rolling(60).mean() - 1.0) * 100
        mkt["mkt_ret20"] = (mkt["mkt_close"] / mkt["mkt_close"].shift(20) - 1.0) * 100
        df = df.merge(mkt[["date", "mkt_bias60", "mkt_ret20"]], on="date", how="left")
    else:
        df["mkt_bias60"] = np.nan
        df["mkt_ret20"] = np.nan

    df = df.merge(sec_map, on="stock_id", how="left")
    gs = df.groupby(["sector_id", "date"])
    sec = gs.agg(sec_ret20=("ret20", "median"),
                 sec_breadth=("c_over_ma20", lambda s: (s > 0).mean()),
                 peer_surge5=("ret5", lambda s: (s > 10).mean()),
                 sec_n=("ret20", "size")).reset_index()
    sec.loc[sec["sec_n"] < 5, ["sec_ret20", "sec_breadth", "peer_surge5"]] = np.nan
    df = df.merge(sec.drop(columns=["sec_n"]), on=["sector_id", "date"], how="left")
    df["rel_ret20"] = df["ret20"] - df["sec_ret20"]
    df["sec_breadth"] = df["sec_breadth"] * 100
    df["peer_surge5"] = df["peer_surge5"] * 100
    df["atr_bucket"] = (df.groupby("date")["atr_pct"]
                        .transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)))

    # 交給 mega_mine 的擴充（注意/處置、網絡、變化值、情緒）——monkeypatch 快取路徑重用同碼
    tmp = _BASE + "/data/_ml_infer_base.pkl"
    df.to_pickle(tmp)
    orig = judge._CACHE
    try:
        judge._CACHE = tmp
        out = mm.build_features()
    finally:
        judge._CACHE = orig
        os.remove(tmp)
    return out


def infer() -> None:
    bundle = joblib.load(_MODEL)
    model, feats = bundle["model"], bundle["feats"]
    df = _build_recent_features()
    latest = df["date"].max()
    d = df[df["date"] == latest].reset_index(drop=True)
    for c in ("att_notice5", "att_punish10"):
        d[c] = d[c].astype(float)
    d["_hard"] = hard_mask(d)
    prob = pd.Series(model.predict_proba(d[feats].to_numpy(np.float64))[:, 1], index=d.index)
    ml_top = select_top(d, prob)
    picks = sorted(d.loc[ml_top, "stock_id"].tolist())
    out = {
        "date": str(latest), "trained_at": bundle["trained_at"], "cutoff_pct": 20,
        "n_hard": int(d["_hard"].sum()), "picks": picks,
        "scores": {sid: round(float(p), 4) for sid, p in zip(d["stock_id"], prob)},
    }
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    _log(f"推論完成：{latest} 硬篩 {out['n_hard']} 檔 → ML 圈選 {len(picks)} 檔 → {_OUT}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if mode == "train" or (mode == "auto" and not os.path.exists(_MODEL)):
        train()
    if mode in ("infer", "auto"):
        infer()


if __name__ == "__main__":
    main()
