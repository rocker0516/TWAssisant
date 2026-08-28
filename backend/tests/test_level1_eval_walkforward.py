"""Level 1 評估框架與 walk-forward 測試：IC 正確性、分位單調、embargo 硬規則。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.research.level1 import evaluation as ev
from app.research.level1 import walkforward as wf
from app.research.level1.features import assemble_dataset


def _mats(n_days=60, n_stocks=40, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-01", periods=n_days).astype(str)
    cols = [f"{1000+i}" for i in range(n_stocks)]
    fwd = pd.DataFrame(rng.normal(0, 0.03, (n_days, n_stocks)), index=dates, columns=cols)
    return dates, cols, fwd


# ── Rank IC ──

def test_perfect_score_gives_ic_one():
    _, _, fwd = _mats()
    assert ev.daily_rank_ic(fwd.copy(), fwd).dropna().min() == pytest.approx(1.0)
    assert ev.daily_rank_ic(-fwd, fwd).dropna().max() == pytest.approx(-1.0)


def test_random_score_ic_near_zero():
    dates, cols, fwd = _mats(n_days=250, n_stocks=200)
    rng = np.random.default_rng(9)
    score = pd.DataFrame(rng.random(fwd.shape), index=dates, columns=cols)
    s = ev.ic_summary(ev.daily_rank_ic(score, fwd))
    assert abs(s["mean_ic"]) < 0.02
    assert abs(s["t_stat"]) < 3


def test_ic_skips_small_cross_sections():
    dates, cols, fwd = _mats(n_days=5, n_stocks=10)  # < 30 檔
    score = fwd.copy()
    assert ev.daily_rank_ic(score, fwd).dropna().empty


def test_ic_nan_alignment():
    """fwd 缺值（下市/停牌）格子不得進當日相關。"""
    _, _, fwd = _mats()
    score = fwd.copy()
    fwd_holed = fwd.copy()
    fwd_holed.iloc[:, :5] = np.nan  # 5 檔整段缺 fwd
    ic = ev.daily_rank_ic(score, fwd_holed).dropna()
    assert ic.min() == pytest.approx(1.0)  # 其餘仍完美對齊


# ── 分位分析 ──

def test_quantile_monotonic_for_perfect_score():
    _, _, fwd = _mats(n_days=120, n_stocks=100)
    out = ev.quantile_summary(fwd.copy(), fwd, n_q=10)
    assert out["monotonicity"] == pytest.approx(1.0)
    assert out["top_bottom_spread_pct"] > 0
    means = out["quantile_mean_pct"]
    assert means == sorted(means)  # Top 10% > … > Bottom 10%（§13）


def test_topk_summary_perfect_score():
    """完美 score 的 Top-20 超額必為正、日勝率 1.0。"""
    _, _, fwd = _mats(n_days=100, n_stocks=120)
    out = ev.topk_summary(fwd.copy(), fwd, ks=(20,))
    assert out["top20"]["excess_pct"] > 0
    assert out["top20"]["day_win_rate"] == pytest.approx(1.0)


def test_topk_excludes_missing_fwd():
    """fwd 缺值的股票不得占用 Top-K 名額。"""
    dates, cols, fwd = _mats(n_days=10, n_stocks=40)
    score = pd.DataFrame(0.0, index=dates, columns=cols)
    score[cols[0]] = 9.9          # 最高分
    fwd_holed = fwd.copy()
    fwd_holed[cols[0]] = np.nan   # 但 fwd 缺值
    out = ev.topk_summary(score, fwd_holed, ks=(5,))
    assert not np.isnan(out["top5"]["mean_ret_pct"])


# ── Walk-forward embargo ──

class _SpyModel:
    """記錄 fit 時拿到的列數；predict 回常數。"""

    def __init__(self, log):
        self.log = log

    def fit(self, x, y):
        self.log.append(len(y))

    def predict(self, x):
        return np.full(len(x), 0.5)


def test_train_slice_embargo():
    dates = pd.Index([f"d{i:03d}" for i in range(100)])
    tr = wf.train_slice(dates, test_start=50, embargo=10)
    assert list(tr) == list(dates[:40])          # 50 − 10
    assert wf.train_slice(dates, 5, 10).empty    # 不夠就空


def test_walk_forward_no_label_overlap():
    """訓練集最後一天 + horizon 必須早於測試起點（label 不與測試期重疊）。"""
    n_days, horizon = 80, 5
    dates = pd.date_range("2025-01-01", periods=n_days).astype(str)
    cols = [f"{1000+i}" for i in range(35)]
    rng = np.random.default_rng(1)
    pct = pd.DataFrame(rng.random((n_days, len(cols))), index=dates, columns=cols)
    ranked = {"f1": pd.DataFrame(rng.random((n_days, len(cols))),
                                 index=dates, columns=cols)}

    seen_train_dates: list[pd.Index] = []
    orig = wf.assemble_dataset

    def spy_assemble(rk, tp, ds):
        seen_train_dates.append(ds)
        return orig(rk, tp, ds)

    wf.assemble_dataset = spy_assemble
    try:
        score = wf.walk_forward_scores(
            lambda: _SpyModel([]), ranked, pct,
            horizon=horizon, first_test=str(dates[40]), step=20, min_train_days=10)
    finally:
        wf.assemble_dataset = orig

    # 奇數次呼叫是訓練集（train, test 交錯）：驗證 embargo
    starts = [40, 60]
    trains = seen_train_dates[0::2]
    for tr, s in zip(trains, starts):
        last_train_pos = list(dates).index(tr[-1])
        assert last_train_pos + horizon < s
    # OOS 區塊有預測、之前沒有
    assert score.loc[dates[39]].isna().all()
    assert score.loc[dates[41]].notna().any()


def test_assemble_dataset_fills_feature_nan_with_half():
    dates = pd.Index(["2025-01-01", "2025-01-02"])
    cols = ["1111", "2222"]
    pct = pd.DataFrame([[0.3, 0.9], [np.nan, 0.5]], index=dates, columns=cols)
    ranked = {"f1": pd.DataFrame([[0.1, np.nan], [0.2, 0.4]], index=dates, columns=cols)}
    x, y, meta = assemble_dataset(ranked, pct, dates)
    assert len(y) == 3            # pct 的 NaN 列被剔除
    assert x[1, 0] == pytest.approx(0.5)  # 特徵缺值補中性 0.5


# ── Production 訓練窗（設計 §6：P0 leakage 防線）──

def test_train_slice_for_date_excludes_pred_date_and_future():
    dates = pd.Index([f"2025-01-{d:02d}" for d in range(1, 21)])
    tr = wf.train_slice_for_date(dates, "2025-01-15", embargo=5)
    assert tr[-1] == "2025-01-09"          # index 14 − embargo 5 → dates[:9]
    assert "2025-01-15" not in tr
    assert not any(d > "2025-01-09" for d in tr)


def test_train_slice_for_date_matches_walk_forward_rule():
    """Production 與 OOS 必須是同一規則的同一函式，不是兩份等價邏輯。"""
    dates = pd.Index([f"2025-02-{d:02d}" for d in range(1, 29)])
    i = 20
    assert list(wf.train_slice_for_date(dates, dates[i], embargo=10)) == \
           list(wf.train_slice(dates, i, embargo=10))


def test_train_slice_for_date_rejects_unknown_date():
    dates = pd.Index(["2025-01-01", "2025-01-02"])
    with pytest.raises(KeyError):
        wf.train_slice_for_date(dates, "2025-01-03", embargo=1)
