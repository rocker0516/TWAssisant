"""情境路由矩陣管線：組裝特徵 → 污染稽核閘 → 格子挖掘（context-routing-matrix task-7）。

三步：
  1. build(db_path)  → 組 chip+other+labels+context 成單一 frame → ctx_features.pkl
  2. audit(feat_path) → 對挖掘池特徵跑污染三判準（覆蓋率/ts_share/控ATR挖掘窗vs holdout
     spread，判準與常數照抄 backend/scripts/feature_contamination_audit.py:50-142，
     目標欄改用 exc_hit20）→ ctx_contamination.json；verdict=="必修" 記入 excluded_features
  3. mine(feat_path)  → KPI 定版（scan_thresholds）→ 群×情境×訊號跑 CellEvaluator →
     ctx_matrix.json（含 pass/watch/fail/insufficient 分級與 chain_audit）

用法：PYTHONIOENCODING=utf-8 python scripts/ctx_matrix_run.py build|audit|mine
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .chip import chip_feature_frame, chip_templates
from .context import chain_resonance, historical_regime, load_core_chains
from .evaluator import CellEvaluator, routing_delta
from .labels import build_excess_labels, scan_thresholds
from .other_families import other_feature_frame, other_templates
from .templates import build_masks, expand

_BACKEND_DIR = Path(__file__).resolve().parents[3]
_DATA_DIR = _BACKEND_DIR / "data"
_DEFAULT_DB = _DATA_DIR / "twa.db"
_FEAT_PATH = _DATA_DIR / "ctx_features.pkl"
_CONTAM_PATH = _DATA_DIR / "ctx_contamination.json"
_MATRIX_PATH = _DATA_DIR / "ctx_matrix.json"
_CORE_CHAINS_PATH = _DATA_DIR / "core_chains.json"

# 挖掘窗／holdout 邊界，照抄 backend/scripts/pop_condition_judge.py:41-42（既定裁決）
_MINE_LO, _MINE_HI = "2021-01-01", "2024-12-31"
_HOLD_LO = "2025-01-01"
_LABEL_HORIZON = 20
_KPI_GRID = (6, 8, 10, 12)
_N_TPEX_CHAINS = 15

# 稽核常數：照抄 backend/scripts/feature_contamination_audit.py:49-51
_MIN_COVER = 0.50
_HIGH_TS = 0.25
_N_Q = 5


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────────── Step 1: build ───────────────────────────────


def _bucket3(s: pd.Series) -> pd.Series:
    """同日橫斷面三分桶（rank(method="first") 去重後 qcut，缺值不足3檔則整組 NaN）。"""
    valid = s.notna()
    out = pd.Series(np.nan, index=s.index, dtype=float)
    if valid.sum() < 3:
        return out
    ranks = s[valid].rank(method="first")
    out.loc[valid] = pd.qcut(ranks, 3, labels=False, duplicates="drop").astype(float)
    return out


def build(db_path: str | None = None) -> None:
    db_path = str(db_path or _DEFAULT_DB)
    t0 = time.time()

    _log(f"讀取 chip 家族特徵（{db_path}）...")
    chip_df = chip_feature_frame(db_path)
    _log(f"chip: {len(chip_df):,} 列 / {chip_df.shape[1]} 欄")

    _log("讀取 momentum/fundamental/news/event 家族特徵 ...")
    other_df = other_feature_frame(db_path).drop(columns=["sector_id"])
    _log(f"other: {len(other_df):,} 列 / {other_df.shape[1]} 欄")

    feat = chip_df.merge(other_df, on=["stock_id", "date"], how="inner")
    _log(f"merge chip+other: {len(feat):,} 列 / {feat.shape[1]} 欄")

    pool_ids = feat["stock_id"].unique().tolist()

    con = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" * len(pool_ids))
        prices = pd.read_sql_query(
            f"SELECT stock_id, date, close FROM daily_prices WHERE stock_id IN ({placeholders})",
            con, params=pool_ids,
        )
        mkt = pd.read_sql_query("SELECT date, close FROM market_index ORDER BY date", con)
        indicators = pd.read_sql_query(
            f"SELECT stock_id, date, atr14 FROM indicators WHERE stock_id IN ({placeholders})",
            con, params=pool_ids,
        )
    finally:
        con.close()

    prices["date"] = pd.to_datetime(prices["date"])
    mkt["date"] = pd.to_datetime(mkt["date"])
    indicators["date"] = pd.to_datetime(indicators["date"])
    market = mkt.set_index("date")["close"].sort_index()

    _log("計算 20 日超額標籤（exc_mfe20）...")
    labels = build_excess_labels(prices, market, horizon=_LABEL_HORIZON)
    _log(f"labels: {len(labels):,} 列")
    feat = feat.merge(labels, on=["stock_id", "date"], how="inner")
    _log(f"merge labels: {len(feat):,} 列")

    # ATR% 橫斷面三分桶：個股 ATR%（atr14/close）；缺值時用 20 日報酬標準差代替
    # （brief 允許的替代口徑，此處以註解標明）
    feat = feat.merge(indicators, on=["stock_id", "date"], how="left")
    feat = feat.merge(prices, on=["stock_id", "date"], how="left")
    atr_pct = feat["atr14"].astype(float) / feat["close"].astype(float)
    ret_std20 = feat.groupby("stock_id", sort=False)["close"].transform(
        lambda s: s.pct_change(fill_method=None).rolling(20, min_periods=10).std()
    )
    atr_pct = atr_pct.where(atr_pct.notna(), ret_std20)  # 替代口徑：20日報酬標準差
    feat["_atr_pct"] = atr_pct
    _log("計算 ATR% 橫斷面三分桶 ...")
    feat["atr_bucket"] = feat.groupby("date")["_atr_pct"].transform(_bucket3)
    feat = feat.drop(columns=["_atr_pct", "atr14", "close"])

    # 歷史 regime（hold/defense），與 wave_market_regime 語義一致
    _log("計算歷史 regime（hold/defense）...")
    regime = historical_regime(market)
    regime_df = regime.reset_index()
    regime_df.columns = ["date", "regime"]
    feat = feat.merge(regime_df, on="date", how="left")

    # 鏈共振：核心鏈內成分股 rel_ret20 等權平均 → 橫斷面三分位 → 個股層級標籤
    _log("計算核心鏈共振（resonance）...")
    core_chains = load_core_chains(str(_CORE_CHAINS_PATH))
    stock_chains = core_chains[["stock_id", "chain_id"]].drop_duplicates()
    chain_mem = feat[["stock_id", "date", "rel_ret20"]].merge(stock_chains, on="stock_id", how="inner")
    chain_strength = chain_mem.groupby(["date", "chain_id"])["rel_ret20"].mean().unstack("chain_id")
    resonance = chain_resonance(chain_strength, stock_chains)
    feat = feat.merge(resonance, on=["stock_id", "date"], how="left")

    # fold（挖掘窗依日期切3段等分，fold∈{0,1,2}）／is_holdout（fold=-1）
    date_str = feat["date"].dt.strftime("%Y-%m-%d")
    feat = feat[date_str >= _MINE_LO].reset_index(drop=True)
    date_str = feat["date"].dt.strftime("%Y-%m-%d")
    is_holdout = (date_str >= _HOLD_LO).to_numpy()
    feat["is_holdout"] = is_holdout

    mine_dates = np.sort(feat.loc[~is_holdout, "date"].unique())
    n = len(mine_dates)
    edges = [0, n // 3, 2 * n // 3, n]
    fold_of_date = {}
    for fi in range(3):
        for d in mine_dates[edges[fi]:edges[fi + 1]]:
            fold_of_date[d] = fi
    feat["fold"] = feat["date"].map(fold_of_date)
    feat["fold"] = feat["fold"].fillna(-1).astype(np.int8)

    # 壓 dtype：識別欄轉 category，float64 轉 float32，省記憶體
    for c in ("stock_id", "sector_id", "regime", "resonance"):
        if c in feat.columns:
            feat[c] = feat[c].astype("category")
    for c in feat.columns:
        if feat[c].dtype == np.float64:
            feat[c] = feat[c].astype(np.float32)

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    feat.to_pickle(_FEAT_PATH)
    _log(
        f"寫入 {_FEAT_PATH.name}：{len(feat):,} 列 / {feat.shape[1]} 欄"
        f"（挖掘窗 {int((~is_holdout).sum()):,} 列，holdout {int(is_holdout.sum()):,} 列，"
        f"{time.time()-t0:.0f}s）"
    )


# ─────────────────────────────── Step 2: audit ───────────────────────────────


def _pool_columns() -> list[str]:
    """挖掘池特徵欄（chip_templates+other_templates 引用的 .col，去重、保序）。"""
    cols: list[str] = []
    seen: set[str] = set()
    for t in chip_templates() + other_templates():
        if t.col not in seen:
            seen.add(t.col)
            cols.append(t.col)
    return cols


def _kpi(feat: pd.DataFrame) -> dict:
    """KPI 定版：對挖掘窗（非 holdout）標籤跑 scan_thresholds。"""
    mine_labels = feat.loc[~feat["is_holdout"], ["exc_mfe20"]]
    return scan_thresholds(mine_labels, grid=_KPI_GRID)


def _spread(df: pd.DataFrame, col: str, target_col: str) -> float | None:
    """控 ATR 桶的高低組命中價差（pp）。照抄 feature_contamination_audit.py:61-89。"""
    s = df[[col, target_col, "atr_bucket"]].dropna()
    if len(s) < 20_000:
        return None
    v = s[col]
    if v.nunique() <= 3:
        hi = v > v.min()
        if hi.sum() < 2_000 or (~hi).sum() < 2_000:
            return None
        t = s.assign(hi=hi).groupby(["atr_bucket", "hi"], observed=True)[target_col].agg(
            ["mean", "size"]).unstack("hi")
        if t.shape[1] < 4:
            return None
        diff = (t["mean"][True] - t["mean"][False]) * 100
        w = t["size"][True]
        return float((diff * w).sum() / w.sum())
    q = s.groupby("atr_bucket", observed=True)[col].transform(
        lambda x: pd.qcut(x, _N_Q, labels=False, duplicates="drop"))
    t = s.assign(q=q).groupby("q", observed=True)[target_col].mean() * 100
    if len(t) < _N_Q:
        return None
    return float(t.iloc[-1] - t.iloc[0])


def audit(feat_path: str | None = None) -> dict:
    feat_path = str(feat_path or _FEAT_PATH)
    t0 = time.time()
    feat = pd.read_pickle(feat_path)
    _log(f"載入 {len(feat):,} 列 / {feat.shape[1]} 欄")

    kpi = _kpi(feat)
    chosen_x = kpi["chosen_x"]
    _log(f"KPI 定版：x=+{chosen_x}%（20日內曾達）；基率 {kpi['base_rates']}")

    feat = feat.copy()
    feat["exc_hit20"] = (feat["exc_mfe20"].astype(float) >= chosen_x).astype(np.float32)

    mine = feat[~feat["is_holdout"]]
    hold = feat[feat["is_holdout"]]

    cols = _pool_columns()
    rows = []
    for f in cols:
        if f not in feat.columns:
            continue
        v = feat[f]
        arr = pd.to_numeric(v, errors="coerce").to_numpy(dtype=float)
        cover = float(np.mean(~np.isnan(arr)))
        var_all = float(np.nanvar(arr))
        if cover < 0.02 or var_all <= 1e-12:
            ts_share = np.nan
        else:
            daily = pd.Series(arr, index=feat.index).groupby(feat["date"]).transform("mean").to_numpy(dtype=float)
            ts_share = float(np.nanvar(daily)) / var_all

        sm = _spread(mine, f, "exc_hit20") if cover >= 0.02 else None
        sh = _spread(hold, f, "exc_hit20") if cover >= 0.02 else None

        if cover < _MIN_COVER:
            health = "覆蓋不足"
        elif sm is None or sh is None:
            health = "無法評估"
        elif np.sign(sm) != np.sign(sh):
            health = "翻號"
        elif abs(sh) < abs(sm) * 0.4:
            health = "衰減>60%"
        else:
            health = "穩定"
        market = "市場層" if (not np.isnan(ts_share) and ts_share >= _HIGH_TS) else "選股層"
        verdict = ("必修" if health in ("覆蓋不足", "翻號", "衰減>60%") else
                   "可用" if health == "穩定" else "待查")

        rows.append({
            "feat": f, "cover": round(cover, 3),
            "ts_share": None if np.isnan(ts_share) else round(ts_share, 3),
            "spread_mine": round(sm, 2) if sm is not None else None,
            "spread_hold": round(sh, 2) if sh is not None else None,
            "market": market, "health": health, "verdict": verdict,
        })

    order = {"必修": 0, "待查": 1, "可用": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], r["cover"], -(r["ts_share"] or 0)))

    print(f"\n{'特徵':<22}{'覆蓋':>7}{'市場成分':>9}{'挖掘':>9}{'holdout':>10}  {'體質':<10}判定")
    cur = None
    for r in rows:
        if r["verdict"] != cur:
            cur = r["verdict"]
            print(f"  == {cur} " + "=" * 60)
        f2 = lambda x: f"{x:+.2f}pp" if x is not None else "-"
        print(f"{r['feat']:<22}{r['cover']*100:>6.1f}%"
              f"{(r['ts_share'] if r['ts_share'] is not None else 0):>9.3f}"
              f"{f2(r['spread_mine']):>9}{f2(r['spread_hold']):>10}  {r['health']:<10}{r['market']}")

    excluded = sorted(r["feat"] for r in rows if r["verdict"] == "必修")

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "kpi": kpi,
        "excluded_features": excluded,
        "audit": rows,
    }
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CONTAM_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    _log(f"稽核池內特徵 {len(cols)} 個；必修 {len(excluded)} 個：{', '.join(excluded) if excluded else '（無）'}")
    _log(f"完成（{time.time()-t0:.0f}s）→ {_CONTAM_PATH.name}")
    return out


# ─────────────────────────────── Step 3: mine ───────────────────────────────


def _top_tpex_chains(db_path: str, n: int = _N_TPEX_CHAINS) -> pd.DataFrame:
    """TPEX chain_name 前 n 大（依 industry_chain_members 成員數）。"""
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT DISTINCT stock_id, chain_name FROM industry_chain_members", con
        )
    finally:
        con.close()
    counts = df.groupby("chain_name")["stock_id"].nunique().sort_values(ascending=False)
    top = counts.head(n).index.tolist()
    return df[df["chain_name"].isin(top)]


def mine(feat_path: str | None = None, db_path: str | None = None) -> None:
    feat_path = str(feat_path or _FEAT_PATH)
    db_path = str(db_path or _DEFAULT_DB)
    t0 = time.time()

    feat = pd.read_pickle(feat_path).reset_index(drop=True)
    _log(f"載入 {len(feat):,} 列 / {feat.shape[1]} 欄")

    kpi = _kpi(feat)
    chosen_x = kpi["chosen_x"]
    feat["exc_hit20"] = (feat["exc_mfe20"].astype(float) >= chosen_x).astype(np.float32)
    _log(f"KPI 定版：x=+{chosen_x}%（20日內曾達）；基率 {kpi['base_rates']}")

    if _CONTAM_PATH.exists():
        with open(_CONTAM_PATH, "r", encoding="utf-8") as fh:
            contam = json.load(fh)
        excluded_features = set(contam.get("excluded_features", []))
    else:
        _log("找不到 ctx_contamination.json，本次不排除任何特徵（建議先跑 audit）")
        excluded_features = set()
    _log(f"稽核排除 {len(excluded_features)} 個特徵：{sorted(excluded_features)}")

    # 家族命名空間，避免 chip（F1~F4/E1~E2）與 other（F1~F6/E1~E2）id 撞號
    base_tpls = [
        replace(t, id=f"{t.family}__{t.id}")
        for t in (chip_templates() + other_templates())
        if t.col not in excluded_features
    ]
    tpls = expand(base_tpls)
    tpl_by_id = {t.id: t for t in tpls}
    _log(f"模板展開後（排除必修）{len(tpls)} 個")

    mine_df = feat[~feat["is_holdout"]]
    masks = build_masks(tpls, mine_df, feat)
    _log(f"訊號通過支持度過濾：{len(masks)} 個")

    # 群座標：全市場 ∪ 6 條核心鏈 ∪ TPEX chain_name 前15大
    core_chains = load_core_chains(str(_CORE_CHAINS_PATH))
    groups: list[tuple[str, str, np.ndarray]] = [
        ("ALL_MARKET", "all_market", np.ones(len(feat), dtype=bool)),
    ]
    for chain_id, g in core_chains.groupby("chain_id"):
        members = set(g["stock_id"])
        groups.append((chain_id, "core_chain", feat["stock_id"].isin(members).to_numpy()))

    tpex = _top_tpex_chains(db_path)
    for chain_name, g in tpex.groupby("chain_name"):
        members = set(g["stock_id"])
        groups.append((chain_name, "tpex_chain", feat["stock_id"].isin(members).to_numpy()))
    _log(
        f"群座標 {len(groups)} 個（全市場1 + 核心鏈{core_chains['chain_id'].nunique()} "
        f"+ TPEX前{_N_TPEX_CHAINS}大）"
    )

    # 情境座標
    regime = feat["regime"].astype(object)
    resonance = feat["resonance"].astype(object)
    contexts: list[tuple[str, np.ndarray]] = [
        ("all", np.ones(len(feat), dtype=bool)),
        ("hold", (regime == "hold").to_numpy()),
        ("defense", (regime == "defense").to_numpy()),
        ("resonance_strong", (resonance == "strong").to_numpy()),
        ("resonance_weak", (resonance == "weak").to_numpy()),
    ]

    n_signals = len(masks)
    n_cells_total = len(groups) * len(contexts) * n_signals
    gate = CellEvaluator.bonferroni_gate(n_cells_total) if n_cells_total > 0 else float("inf")
    _log(f"格子總數（群{len(groups)} x 情境{len(contexts)} x 訊號{n_signals}）= {n_cells_total:,}")
    _log(f"Bonferroni t 門檻（n_cells={n_cells_total:,}）= {gate:.3f}")

    evaluator = CellEvaluator(feat, target_col="exc_hit20", atr_col="atr_bucket")

    # baseline：(signal, context) → 全市場結果，供 routing_delta 用
    all_market_mask = groups[0][2]
    baseline: dict[tuple[str, str], dict | None] = {}
    for signal_id, sig_mask in masks:
        for ctx_id, ctx_mask in contexts:
            baseline[(signal_id, ctx_id)] = evaluator.run(sig_mask, all_market_mask & ctx_mask)

    cells: list[dict] = []
    t_scan = time.time()
    for group_id, group_kind, group_mask in groups:
        for ctx_id, ctx_mask in contexts:
            scope = group_mask & ctx_mask
            for signal_id, sig_mask in masks:
                base_res = baseline[(signal_id, ctx_id)]
                res = base_res if group_id == "ALL_MARKET" else evaluator.run(sig_mask, scope)

                if res is None:
                    cells.append({
                        "group": group_id, "group_kind": group_kind,
                        "signal": signal_id, "family": tpl_by_id[signal_id].family,
                        "context": ctx_id,
                        "n_picks": 0, "n_days": 0, "hit": 0.0, "ctrl": 0.0,
                        "t_ctrl": 0.0, "fold_ctrl": [], "holdout_ctrl": 0.0,
                        "routing_delta": 0.0, "tier": "insufficient",
                    })
                    continue

                fold_ctrl = res["fold_ctrl"]
                t_ctrl = res["t_ctrl"]
                all_fold_pos = len(fold_ctrl) == 3 and all(
                    fc is not None and fc > 0 for fc in fold_ctrl
                )
                if all_fold_pos and not np.isnan(t_ctrl) and t_ctrl >= gate:
                    tier = "pass"
                elif not np.isnan(t_ctrl) and t_ctrl >= 4.0:
                    tier = "watch"
                else:
                    tier = "fail"

                delta = 0.0
                if group_id != "ALL_MARKET" and base_res is not None:
                    delta = routing_delta(res, base_res)

                cells.append({
                    "group": group_id, "group_kind": group_kind,
                    "signal": signal_id, "family": tpl_by_id[signal_id].family,
                    "context": ctx_id,
                    "n_picks": res["n_picks"], "n_days": res["n_days"],
                    "hit": res["hit"], "ctrl": res["ctrl"],
                    "t_ctrl": None if np.isnan(t_ctrl) else t_ctrl,
                    "fold_ctrl": fold_ctrl,
                    "holdout_ctrl": res["holdout_ctrl"],
                    "routing_delta": round(delta, 2),
                    "tier": tier,
                })
        _log(f"  掃描完群 {group_id}（{group_kind}）... 累計 {len(cells):,}/{n_cells_total:,}")
    _log(f"掃格完成（{time.time()-t_scan:.0f}s）")

    tier_counts: dict[str, int] = {}
    for c in cells:
        tier_counts[c["tier"]] = tier_counts.get(c["tier"], 0) + 1
    _log(f"tier 統計：{tier_counts}")

    # chain_audit：鏈 pass 率 < 全體鏈 pass 率一半 且格子數≥20 → 建議檢討成員
    chain_cells = [c for c in cells if c["group_kind"] in ("core_chain", "tpex_chain")]
    overall_pass = sum(1 for c in chain_cells if c["tier"] == "pass")
    overall_rate = overall_pass / len(chain_cells) if chain_cells else 0.0

    chain_audit = []
    for group_id, group_kind, _ in groups:
        if group_kind == "all_market":
            continue
        gc = [c for c in chain_cells if c["group"] == group_id]
        n_cells_g = len(gc)
        n_pass = sum(1 for c in gc if c["tier"] == "pass")
        n_fail = sum(1 for c in gc if c["tier"] == "fail")
        rate = n_pass / n_cells_g if n_cells_g else 0.0
        verdict = "建議檢討成員" if (n_cells_g >= 20 and rate < overall_rate / 2) else "正常"
        chain_audit.append({
            "chain_id": group_id, "n_cells": n_cells_g,
            "n_pass": n_pass, "n_fail": n_fail, "verdict": verdict,
        })

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "kpi": {"horizon": _LABEL_HORIZON, "x": chosen_x, "base_rates": kpi["base_rates"]},
        "excluded_features": sorted(excluded_features),
        "cells": cells,
        "chain_audit": chain_audit,
    }
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_MATRIX_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    n_review = sum(1 for c in chain_audit if c["verdict"] == "建議檢討成員")
    _log(f"格子總數 {len(cells):,}；tier 統計 {tier_counts}")
    _log(f"chain_audit：{len(chain_audit)} 條鏈，{n_review} 條建議檢討成員")
    _log(f"完成（{time.time()-t0:.0f}s）→ {_MATRIX_PATH.name}")
