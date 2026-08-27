from pathlib import Path

import pandas as pd
import numpy as np
from app.research.ctx_matrix.context import historical_regime, chain_resonance, load_core_chains

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def test_regime_hysteresis():
    dates = pd.date_range("2024-01-01", periods=200, freq="B")
    # 前100天緩漲（站上MA60→hold），後100天急跌破 MA60×0.98 → defense
    close = np.concatenate([np.linspace(100, 120, 100), np.linspace(120, 80, 100)])
    s = historical_regime(pd.Series(close, index=dates))
    assert s.iloc[99] == "hold"
    assert s.iloc[-1] == "defense"
    assert set(s.unique()) <= {"hold", "defense"}


def test_regime_hysteresis_has_memory():
    """驗證遲滯狀態機真的有記憶：同一個 (close, ma) 落點，因前態不同而給出不同結果。

    ma_n=5, gap=0.02。兩條序列的最後 5 筆收盤都是 [100,100,100,100,99]（因此當日
    close=99、ma=99.8 完全相同），差別只在更早之前第 6 筆（index=6）：一條全程
    持平 100（前態維持 hold），另一條在該處插入 111 造成遲滯機制轉為 defense、
    此後一路維持 defense 直到終點——同樣的終點價位，因前態不同而給出不同結果。
    """
    dates = pd.date_range("2024-01-01", periods=12, freq="B")

    hold_close = [100.0] * 11 + [99.0]
    s_hold = historical_regime(pd.Series(hold_close, index=dates), ma_n=5, gap=0.02)
    assert s_hold.iloc[-1] == "hold"

    defense_close = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 111.0, 100.0, 100.0, 100.0, 100.0, 99.0]
    s_defense = historical_regime(pd.Series(defense_close, index=dates), ma_n=5, gap=0.02)
    assert s_defense.iloc[-1] == "defense"

    # 兩序列最後一筆的 close 相同（同樣價位），結果卻不同：證明狀態機有記憶
    assert hold_close[-1] == defense_close[-1]
    assert s_hold.iloc[-1] != s_defense.iloc[-1]


def test_resonance_terciles():
    dates = pd.date_range("2024-01-01", periods=2, freq="B")
    strength = pd.DataFrame({"C1": [3.0, 3.0], "C2": [0.0, 0.0], "C3": [-3.0, -3.0]}, index=dates)
    stock_chains = pd.DataFrame({"stock_id": ["a", "b", "c"], "chain_id": ["C1", "C2", "C3"]})
    out = chain_resonance(strength, stock_chains)
    got = out[out["date"] == dates[0]].set_index("stock_id")["resonance"]
    assert got["a"] == "strong" and got["b"] == "neutral" and got["c"] == "weak"


def test_load_core_chains():
    df = load_core_chains(str(_DATA_DIR / "core_chains.json"))

    assert list(df.columns) == ["chain_id", "chain_name", "stock_id", "role"]
    assert df["chain_id"].nunique() == 6

    sizes = df.groupby("chain_id").size()
    assert (sizes >= 8).all() and (sizes <= 15).all()

    assert set(df["role"].unique()) <= {"上游", "中游", "下游"}
    assert df["stock_id"].str.len().gt(0).all()
