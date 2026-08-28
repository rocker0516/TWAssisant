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


# ── Production pipeline 接線測試（設計 §6：prevent train_slice_for_date 呼叫被改掉）──

def test_predict_calls_train_slice_for_date_with_correct_embargo(monkeypatch):
    """生產端 predict() 必須以 pred_date 為準的訓練窗——spy 防止被改回 close.index。"""
    import scripts.level1_predict as lp
    from datetime import date
    from contextlib import contextmanager

    # 建構 40 日 × 3 檔合成資料，避免 LGBM 訓練列太少導致退化
    dates = pd.date_range("2025-01-01", periods=40).astype(str)
    cols = ["1001", "1002", "1003"]
    rng = np.random.default_rng(42)

    close = pd.DataFrame(rng.normal(100, 5, (len(dates), len(cols))),
                         index=dates, columns=cols)
    mask = pd.DataFrame(True, index=dates, columns=cols)

    # 簡單特徵集（2 個特徵，足以訓練 LGBM）
    ranked = {
        "f1": pd.DataFrame(rng.random((len(dates), len(cols))),
                           index=dates, columns=cols),
        "f2": pd.DataFrame(rng.random((len(dates), len(cols))),
                           index=dates, columns=cols),
    }

    # Spy train_slice_for_date 的呼叫
    calls = []
    orig_train_slice_for_date = wf.train_slice_for_date

    def spy_train_slice_for_date(dates_arg, pred_date_arg, embargo=None):
        calls.append({"pred_date": pred_date_arg, "embargo": embargo})
        return orig_train_slice_for_date(dates_arg, pred_date_arg, embargo=embargo)

    monkeypatch.setattr(lp.wf, "train_slice_for_date", spy_train_slice_for_date)

    # Mock upsert_many 為 no-op，回傳列數
    upsert_calls = []

    def mock_upsert_many(self, s, rows):
        upsert_calls.append(rows)
        return len(rows)

    monkeypatch.setattr(lp.repo.Level1PredictionRepository, "upsert_many", mock_upsert_many)

    # Mock SessionLocal 為假 context manager
    class FakeSession:
        def commit(self):
            pass

    @contextmanager
    def fake_session_local():
        yield FakeSession()

    monkeypatch.setattr(lp, "SessionLocal", fake_session_local)

    # 呼叫 predict，用最後一個交易日為預測日
    pred_date = dates[-1]
    lp.predict(close, mask, ranked, pred_date)

    # 驗證：spy 被呼叫 3 次（HORIZONS: 1, 5, 10）
    assert len(calls) == 3, f"Expected 3 calls, got {len(calls)}"

    # 驗證：每次的 embargo 等於該 horizon
    expected_horizons = [1, 5, 10]
    for call, expected_h in zip(calls, expected_horizons):
        assert call["embargo"] == expected_h, \
            f"Expected embargo={expected_h}, got {call['embargo']}"
        assert call["pred_date"] == pred_date, \
            f"Expected pred_date={pred_date}, got {call['pred_date']}"

    # 驗證：upsert_many 的 rows 非空、prediction_date 全等於 pred_date
    assert len(upsert_calls) == 3, f"Expected 3 upsert calls, got {len(upsert_calls)}"
    pred_date_obj = date.fromisoformat(pred_date)
    for i, rows in enumerate(upsert_calls):
        assert len(rows) > 0, f"Upsert call {i} has empty rows"
        for row in rows:
            assert row["prediction_date"] == pred_date_obj, \
                f"Row prediction_date {row['prediction_date']} != {pred_date_obj}"


# ── 評估母體規模（設計 §7：universe_size 與 evaluation_n 不得混用）──

def test_daily_evaluation_n_counts_only_scored_and_realised():
    dates = pd.Index(["2025-01-01", "2025-01-02"])
    cols = pd.Index(["A", "B", "C"])
    score = pd.DataFrame([[0.1, 0.2, 0.3], [0.1, 0.2, np.nan]],
                         index=dates, columns=cols)
    fwd = pd.DataFrame([[0.01, np.nan, 0.03], [0.01, 0.02, 0.03]],
                       index=dates, columns=cols)
    assert ev.daily_evaluation_n(score, fwd).tolist() == [2, 2]
    # d1：B 無 fwd（T+N 已下市）；d2：C 無 score


def test_evaluate_reports_evaluation_n():
    _, _, fwd = _mats(n_days=40, n_stocks=50)
    score = fwd.copy()
    fwd.iloc[:, 0] = np.nan          # 一檔全期無實現報酬 → 評估母體 49
    out = ev.evaluate(score, fwd)
    assert out["evaluation_n_mean"] == pytest.approx(49.0)
    assert out["evaluation_n_min"] == 49
