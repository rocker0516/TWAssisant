"""mlentry feature 層：架構防線、registry 一致性、PIT 截斷不變性、決定性。"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from app.mlentry.config import FeatureConfig, UniverseConfig
from app.mlentry.data import pit
from app.mlentry.data.calendar import TradingCalendar
from app.mlentry.data.quality import limits_from_prev_close
from app.mlentry.data.universe import build_universe
from app.mlentry.features import registry
from app.mlentry.features.context import FeatureContext

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "mlentry"
FORBIDDEN_MODULES = {"sqlite3", "sqlalchemy", "app.storage", "app.research.level1.prices"}
FORBIDDEN_CALLS = {"read_sql", "read_sql_query", "connect"}


def _synthetic(n=80, cols=("A", "B", "C", "D"), seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.Index([f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)], name="date")
    cols = pd.Index(cols, name="stock_id")
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n, len(cols))), axis=0)),
                         index=dates, columns=cols)
    open_ = close.shift(1).fillna(100) * (1 + rng.normal(0, 0.005, close.shape))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, close.shape))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, close.shape))
    vol = pd.DataFrame(rng.integers(1000, 5000, close.shape), index=dates, columns=cols).astype(float)
    return {"open": open_, "high": high, "low": low, "close": close, "volume": vol,
            "turnover": vol * close}


def _ctx(m, as_of, ucfg=UniverseConfig(history_lookback=30)):
    cal = TradingCalendar(m["close"].index)
    elig, _ = build_universe(m, ucfg)
    up, dn = limits_from_prev_close(m["close"])
    mkt = m["close"].mean(axis=1)
    sector = pd.Series([1.0, 1.0, 2.0, np.nan], index=m["close"].columns)
    attn = pd.DataFrame(False, index=m["close"].index, columns=m["close"].columns)
    attn.iloc[10:20, 0] = True
    trunc = pit.truncate(m, cal, as_of)
    idx = trunc["close"].index
    return FeatureContext(as_of=as_of, calendar=cal, prices=trunc, market_close=mkt.loc[idx],
                          sector_map=sector, eligible=elig.loc[idx],
                          events={"attention": attn.loc[idx], "disposition": attn.loc[idx] & False},
                          limits={"up": up.loc[idx], "down": dn.loc[idx]})


def test_architecture_feature_and_label_modules_never_touch_db():
    for sub in ("features", "labels"):
        for path in (ROOT / sub).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for nm in names:
                        assert not any(nm == f or nm.startswith(f + ".") for f in FORBIDDEN_MODULES), \
                            f"{path.name} imports {nm}"
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in FORBIDDEN_CALLS, f"{path.name} calls {node.func.attr}"


def test_registry_declares_every_built_feature_and_lookback_bound():
    cfg = FeatureConfig()
    names = registry.feature_names(cfg)
    assert len(names) == len(set(names)) == 57
    assert registry.max_feature_lookback(cfg) <= UniverseConfig().history_lookback
    assert registry.feature_version(cfg).startswith("f_")
    assert registry.feature_version(cfg) != registry.feature_version(FeatureConfig(families=("price",)))


def test_context_rejects_untruncated_prices():
    m = _synthetic()
    cal = TradingCalendar(m["close"].index)
    elig, _ = build_universe(m, UniverseConfig(history_lookback=30))
    with pytest.raises(ValueError):
        FeatureContext(as_of=m["close"].index[-5], calendar=cal, prices=m,
                       market_close=m["close"].mean(axis=1), sector_map=pd.Series(dtype=float),
                       eligible=elig)


def test_pit_truncation_invariance_all_features():
    """整段歷史算 vs 截斷到 as_of 算，as_of 當日逐欄相等 → 沒有任何特徵向前看（§32-2）。"""
    m = _synthetic()
    cfg = FeatureConfig()
    as_of = m["close"].index[60]
    full = registry.build_all(_ctx(m, m["close"].index[-1]), cfg)
    trunc = registry.build_all(_ctx(m, as_of), cfg)
    for name in registry.feature_names(cfg):
        a = full[name].loc[as_of].to_numpy(dtype=float)
        b = trunc[name].loc[as_of].to_numpy(dtype=float)
        assert np.allclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-12), name


def test_snapshot_is_deterministic_and_u_t_only():
    m = _synthetic()
    cfg = FeatureConfig()
    as_of = m["close"].index[60]
    s1 = registry.snapshot(_ctx(m, as_of), cfg)
    s2 = registry.snapshot(_ctx(m, as_of), cfg)
    pd.testing.assert_frame_equal(s1, s2)
    assert list(s1.columns) == registry.feature_names(cfg)
    assert s1.dtypes.eq("float32").all()
    # 把 C 在 as_of 弄成品質壞 → 不在 snapshot
    m2 = {k: v.copy() for k, v in m.items()}
    m2["high"].loc[as_of, "C"] = 0.0
    s3 = registry.snapshot(_ctx(m2, as_of), cfg)
    assert "C" not in s3.index and "C" in s1.index


def test_event_and_limit_features_semantics():
    m = _synthetic()
    as_of = m["close"].index[60]
    f = registry.build_all(_ctx(m, as_of), FeatureConfig())
    d15 = m["close"].index[15]
    assert f["is_attention_stock"].loc[d15, "A"] == 1.0 and f["is_attention_stock"].loc[d15, "B"] == 0.0
    assert (f["dist_limit_up"].loc[as_of] > 0).all()          # 未漲停 → 距離為正
    assert f["industry_ret_5d"].loc[as_of, "A"] == pytest.approx(f["industry_ret_5d"].loc[as_of, "B"])
    assert np.isnan(f["industry_ret_5d"].loc[as_of, "D"])       # 無類股
    ranks = f["ret_5d_pct_rank"].loc[as_of].dropna()
    assert ranks.max() == 1.0 and 0 < ranks.min() <= 1
