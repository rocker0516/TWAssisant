"""情境維度：歷史 regime（MA60 遲滯）與鏈共振（橫斷面三分位）。

historical_regime 是 app/engines/market_regime.py 中 wave_market_regime 的
「純函數化」版本——原引擎只回傳「當下最新一筆」狀態，這裡改成回傳整段歷史
每一天的狀態序列，供情境矩陣挖掘/回測沿時間軸切片使用。不改動原引擎檔。

遲滯規則與 wave_market_regime（app/engines/market_regime.py:28-54）**語義完全
一致、可互相對照**（原本 Step 3 簡報給的對稱 gap 版本已作廢，改為完全複製正式
引擎的不對稱遲滯帶）：
  - 先算 MA{ma_n}（簡單移動平均）。
  - 初始態：MA 第一個有效值當天，**一律視為 "hold"**（與引擎 `held = True` 的
    硬編碼假設一致，不看當天 close 與 ma 的大小關係——引擎本來就是這樣寫的：
    只要歷史夠長，這個起始假設的影響會被後續逐日遲滯迭代蓋掉）。
  - 之後逐日（`held` 對應 "hold"）：
      若目前 held（"hold"）：close > ma*(1-gap) → 維持 hold；
                              close <= ma*(1-gap) → 轉 defense。
      若目前非 held（"defense"）：close > ma → 轉 hold；
                                   close <= ma → 維持 defense。
    即：hold 出場門檻是跌破 ma 逾 gap（例如 2%），defense 進場（回到 hold）
    門檻只需站回 ma（無 gap）——上下門檻不對稱，這正是「遲滯」的來源。
  - MA 未滿 ma_n 根（尚無有效 MA）之前的日子，用第一個有效態（"hold"）回填。
"""
from __future__ import annotations

import pandas as pd

_RESONANCE_LABELS = ["weak", "neutral", "strong"]
_RESONANCE_PRIORITY = {"weak": 0, "neutral": 1, "strong": 2}


def historical_regime(index_close: pd.Series, ma_n: int = 60, gap: float = 0.02) -> pd.Series:
    """把 wave_market_regime 的 MA60 遲滯規則改寫成吃整段歷史序列的純函數。

    與 app/engines/market_regime.py:28-54 的 wave_market_regime 語義完全一致
    （同一套不對稱遲滯帶規則），差別只在於：原引擎只回傳「最新一筆」狀態，
    這裡回傳整段歷史每一天的狀態序列，兩者在任一共同日期上的狀態必然相同，
    可互相對照驗證。

    Args:
        index_close: index=date、值=大盤（或任一指數）收盤價的序列，須依日期由舊到新排序。
        ma_n: 移動平均天數，預設 60（對應 wave_market_regime 的 _MA_N）。
        gap: 遲滯帶寬度，預設 0.02（對應 wave_market_regime 的 _GAP）。

    Returns:
        index=date、值 ∈ {"hold", "defense"} 的 Series。
    """
    close = index_close.astype(float)
    ma = close.rolling(ma_n, min_periods=ma_n).mean()

    states: list[str] = [""] * len(close)
    valid_idx = ma.first_valid_index()

    if valid_idx is None:
        # 全序列都不足 ma_n 根：無法算出任何 MA，比照引擎的起始假設回填 "hold"
        # （此分支只在極短測試序列出現，正式資料歷史夠長不會走到）
        return pd.Series(["hold"] * len(close), index=close.index, name="regime")

    positions = close.index.get_indexer([valid_idx])
    start_pos = int(positions[0])

    # 初始態一律 "hold"，比照引擎 `held = True` 的硬編碼假設（不看當天價位）
    for i in range(start_pos):
        states[i] = "hold"

    held = True
    states[start_pos] = "hold"
    for i in range(start_pos + 1, len(close)):
        c = close.iloc[i]
        m = ma.iloc[i]
        held = (c > m * (1.0 - gap)) if held else (c > m)
        states[i] = "hold" if held else "defense"

    return pd.Series(states, index=close.index, name="regime")


def chain_resonance(sec_rel20_by_chain: pd.DataFrame, stock_chains: pd.DataFrame) -> pd.DataFrame:
    """把鏈層強度轉成個股層級的橫斷面共振標籤。

    Args:
        sec_rel20_by_chain: index=date、columns=chain_id 的鏈層中性化20日強度
            （鏈內成分股 rel_ret20 等權平均）。
        stock_chains: columns [stock_id, chain_id]，一股可能對映多條鏈。

    Returns:
        columns [stock_id, date, resonance]，resonance ∈ {"strong","neutral","weak"}——
        該股所屬鏈當日強度在全部鏈的當日橫斷面三分位。一股多鏈時取其最強鏈
        （strong > neutral > weak；理由：只要有一條鏈當下夠強，就值得標記共振）。
    """
    if sec_rel20_by_chain.empty or stock_chains.empty:
        return pd.DataFrame(columns=["stock_id", "date", "resonance"])

    resonance_rows = []
    for dt, row in sec_rel20_by_chain.iterrows():
        row = row.dropna()
        if row.empty:
            continue
        # 橫斷面三分位：用百分位排名切三段（比 pd.qcut 更耐重複值/鏈數少的情況）
        pct_rank = row.rank(pct=True, method="first")
        labels = pd.cut(
            pct_rank,
            bins=[0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0],
            labels=_RESONANCE_LABELS,
            include_lowest=True,
        )
        for chain_id, label in labels.items():
            resonance_rows.append({"date": dt, "chain_id": chain_id, "resonance": str(label)})

    if not resonance_rows:
        return pd.DataFrame(columns=["stock_id", "date", "resonance"])

    resonance_by_chain = pd.DataFrame(resonance_rows)
    merged = resonance_by_chain.merge(stock_chains, on="chain_id", how="inner")
    merged["priority"] = merged["resonance"].map(_RESONANCE_PRIORITY)

    # 一股多鏈時取最強鏈（strong>neutral>weak）
    best_idx = merged.groupby(["stock_id", "date"])["priority"].idxmax()
    out = merged.loc[best_idx, ["stock_id", "date", "resonance"]].reset_index(drop=True)
    return out


def load_core_chains(path: str) -> pd.DataFrame:
    """讀取手工核心鏈 json，回傳 columns [chain_id, chain_name, stock_id, role]。"""
    import json

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    for chain in data.get("chains", []):
        chain_id = chain["chain_id"]
        chain_name = chain["chain_name"]
        for member in chain.get("members", []):
            rows.append(
                {
                    "chain_id": chain_id,
                    "chain_name": chain_name,
                    "stock_id": member["stock_id"],
                    "role": member["role"],
                }
            )
    return pd.DataFrame(rows, columns=["chain_id", "chain_name", "stock_id", "role"])
