"""Feature Registry（FRS §8、§11）：宣告式特徵清單、feature_version、max_feature_lookback。

各族模組只接受 FeatureContext（PIT 截斷後），回傳 {name: 矩陣}。
registry 驗證回傳名稱 == 宣告名稱，避免「偷加特徵」繞過版本。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pandas as pd

from ..config import FeatureConfig
from . import cross_sectional, event, flows, fundamentals, price, regime, relative, volatility, volume
from .context import FeatureContext


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    family: str
    lookback: int


FAMILY_FEATURES: dict[str, tuple[str, ...]] = {
    "price": ("ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "gap_open", "close_location",
              "distance_from_20d_high", "distance_from_20d_low"),
    "volume": ("turnover_1d", "turnover_5d_mean", "turnover_20d_mean", "volume_ratio_5d",
               "volume_ratio_20d", "turnover_change", "amihud_20d"),
    "volatility": ("realized_vol_5d", "realized_vol_10d", "realized_vol_20d", "atr_pct",
                   "intraday_range", "downside_vol", "positive_day_ratio", "negative_day_ratio",
                   "max_drawdown_5d", "max_drawdown_10d", "max_drawdown_20d"),
    "cross_sectional": tuple(f"{n}_pct_rank" for n in cross_sectional.RANKED)
                       + tuple(f"{n}_mktrel" for n in cross_sectional.MARKET_RELATIVE),
    "regime": ("market_ret_1d", "market_ret_5d", "market_ret_20d", "market_volatility",
               "industry_ret_5d", "industry_ret_20d", "industry_strength_rank",
               "stock_excess_return_vs_market", "stock_excess_return_vs_industry"),
    "relative": ("rel_ret_5d_vs_industry", "rel_vol_vs_industry", "turnover_rank_in_industry", "ret_20d_rank_in_industry",
                 "industry_breadth_ma20", "industry_momentum_60d", "x_strength_market_breadth", "x_strength_industry_breadth",
                 "x_vol_breadth", "x_turnover_accel_rel_strength", "x_rel_ret5_market_ret5"),
    "fundamentals": ("rev_yoy", "rev_yoy_3m", "rev_accel", "rev_mom", "rev_surprise", "net_margin", "gross_margin_chg",
                     "op_margin_chg", "eps_ttm_growth", "pe_rank_in_industry", "pb_rank_in_industry", "dividend_yield",
                     "earnings_yield"),
    "flows": ("foreign_net_1d_v20", "foreign_net_5d_v20", "foreign_net_20d_v20", "trust_net_5d_v20", "trust_net_20d_v20",
              "total_net_5d_v20", "foreign_buy_streak", "foreign_sell_streak", "margin_chg_5d_rel", "margin_balance_days",
              "short_chg_5d_rel", "short_to_margin"),
    "event": ("is_attention_stock", "is_disposition_stock", "limit_up_today", "limit_down_today",
              "limit_up_count_20d", "limit_down_count_20d", "large_gap", "consecutive_up_days",
              "consecutive_down_days", "dist_limit_up", "breadth_ma20"),
}

_MODULES = {"price": price, "volume": volume, "volatility": volatility, "relative": relative, "fundamentals": fundamentals,
            "flows": flows, "cross_sectional": cross_sectional, "regime": regime, "event": event}
_ORDER = ("price", "volume", "volatility", "regime", "event", "relative", "fundamentals", "flows", "cross_sectional")   # relative/cs 依賴前者


def specs(cfg: FeatureConfig) -> list[FeatureSpec]:
    out = []
    for fam in _ORDER:
        if fam in cfg.families:
            for n in FAMILY_FEATURES[fam]:
                out.append(FeatureSpec(n, fam, _MODULES[fam].LOOKBACK))
    return out


def feature_names(cfg: FeatureConfig) -> list[str]:
    return [s.name for s in specs(cfg)]


def max_feature_lookback(cfg: FeatureConfig) -> int:
    return max((s.lookback for s in specs(cfg)), default=0)


def feature_version(cfg: FeatureConfig) -> str:
    payload = {"specs": [(s.name, s.family, s.lookback) for s in specs(cfg)],
               "config": cfg.version}
    return "f_" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:8]


_CS_INPUTS = tuple(dict.fromkeys(cross_sectional.RANKED + cross_sectional.MARKET_RELATIVE + relative.INPUTS))


def build_iter(ctx: FeatureContext, cfg: FeatureConfig):
    """逐族產生 (family, {name: 矩陣})。呼叫端可即刻轉長表釋放記憶體；只保留 cs 需要的 base。"""
    base: dict[str, pd.DataFrame] = {}
    for fam in _ORDER:
        if fam not in cfg.families:
            continue
        mod = _MODULES[fam]
        if fam in ("cross_sectional", "relative"):
            got = mod.build(ctx, base)
        elif fam == "event":
            got = mod.build(ctx, large_gap_threshold=cfg.large_gap_threshold)
        else:
            got = mod.build(ctx)
        declared = set(FAMILY_FEATURES[fam])
        if set(got) != declared:
            raise RuntimeError(f"{fam}: built {sorted(set(got) ^ declared)} not matching registry")
        for n in _CS_INPUTS:
            if n in got:
                base[n] = got[n]
        yield fam, got


def build_all(ctx: FeatureContext, cfg: FeatureConfig) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for _, got in build_iter(ctx, cfg):
        out.update(got)
    return out


def snapshot(ctx: FeatureContext, cfg: FeatureConfig) -> pd.DataFrame:
    """as_of 當日、U_t 內每檔股票一列的特徵快照（columns = registry 順序）。"""
    feats = build_all(ctx, cfg)
    names = feature_names(cfg)
    row = pd.DataFrame({n: feats[n].loc[ctx.as_of] for n in names})
    row.index.name = "stock_id"
    return row[ctx.eligible.loc[ctx.as_of].reindex(row.index).fillna(False).astype(bool)].astype("float32")
