import pandas as pd
import numpy as np
from app.research.ctx_matrix.context import historical_regime, chain_resonance


def test_regime_hysteresis():
    dates = pd.date_range("2024-01-01", periods=200, freq="B")
    # 前100天緩漲（站上MA60→hold），後100天急跌破 MA60×0.98 → defense
    close = np.concatenate([np.linspace(100, 120, 100), np.linspace(120, 80, 100)])
    s = historical_regime(pd.Series(close, index=dates))
    assert s.iloc[99] == "hold"
    assert s.iloc[-1] == "defense"
    assert set(s.unique()) <= {"hold", "defense"}


def test_resonance_terciles():
    dates = pd.date_range("2024-01-01", periods=2, freq="B")
    strength = pd.DataFrame({"C1": [3.0, 3.0], "C2": [0.0, 0.0], "C3": [-3.0, -3.0]}, index=dates)
    stock_chains = pd.DataFrame({"stock_id": ["a", "b", "c"], "chain_id": ["C1", "C2", "C3"]})
    out = chain_resonance(strength, stock_chains)
    got = out[out["date"] == dates[0]].set_index("stock_id")["resonance"]
    assert got["a"] == "strong" and got["b"] == "neutral" and got["c"] == "weak"
