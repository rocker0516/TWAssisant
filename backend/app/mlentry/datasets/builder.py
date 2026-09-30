"""Dataset builder（§9、§10、§32-1~6）：DB → sample_index / features / outcomes / splits。

研究模式一次算整段歷史（rolling 只向後看，PIT 由 test_pit_truncation_invariance 釘住）；
生產模式（C）以 as_of 截斷後走同一條路。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import MLEntryConfig
from ..data import fundamentals as fund
from ..data import pit, prices, quality
from ..data.calendar import TradingCalendar, load_calendar
from ..data.universe import build_universe
from ..features import registry
from ..features.context import FeatureContext
from ..labels import canonical_outcome as co
from ..validation.walk_forward import Split, build_split, check_split_integrity
from .sample_index import build_sample_index, sample_id
from .store import DEFAULT_ROOT, Manifest, code_commit, write_dataset
from .weighting import day_normalized_weights

log = logging.getLogger(__name__)


@dataclass
class BuiltDataset:
    dataset_version: str
    manifest: Manifest
    sample_index: pd.DataFrame
    features: pd.DataFrame
    outcomes: pd.DataFrame
    split: Split


def load_inputs(con, cfg: MLEntryConfig, as_of: str | None = None):
    cal = load_calendar(con)
    if as_of is not None:
        cal = TradingCalendar(cal.truncate(as_of))
    cols = prices.coverage_ids(con)
    m = prices.load_matrices(con, cal, cols)
    mkt = prices.load_market_close(con, cal)
    sector = prices.load_sector_map(con)
    windows = prices.load_attention_windows(con)
    events = {"attention": pit.event_mask(windows, cal, cols, "notice"),
              "disposition": pit.event_mask(windows, cal, cols, "punish")}
    fundamentals = fund.load_fundamental_matrices(con, cal, cols) if "fundamentals" in cfg.features.families else None
    return cal, m, mkt, sector, events, fundamentals


def build(con, cfg: MLEntryConfig, as_of: str | None = None) -> BuiltDataset:
    cal, m, mkt, sector, events, fundamentals = load_inputs(con, cfg, as_of)
    as_of = cal.dates[-1]
    log.info("inputs loaded: %d dates × %d stocks, as_of=%s", len(cal), m["close"].shape[1], as_of)

    eligible, elig_flags = build_universe(m, cfg.universe)
    hard = quality.hard_flags(m)
    soft = quality.soft_flags(m)
    up, dn = quality.limits_from_prev_close(m["close"])
    versions = {**cfg.versions,
                "feature_version": registry.feature_version(cfg.features),
                "label_version": co.label_version(cfg.labels)}
    if registry.max_feature_lookback(cfg.features) > cfg.universe.history_lookback:
        raise ValueError("feature registry lookback exceeds universe.history_lookback")

    start = cfg.universe.research_start
    keep_dates = cal.dates[cal.dates >= start]
    mask = eligible.loc[keep_dates]
    rows, cs = np.nonzero(mask.to_numpy())
    sid = mask.columns.to_numpy()[cs]
    sd = keep_dates.to_numpy()[rows]
    ids = sample_id(sid, sd).to_numpy()
    log.info("U_t: %d eligible samples over %d dates", len(ids), len(keep_dates))

    # features：逐族轉長表
    ctx = FeatureContext(as_of=as_of, calendar=cal, prices=m, market_close=mkt, sector_map=sector,
                         eligible=eligible, events=events, limits={"up": up, "down": dn}, fundamentals=fundamentals)
    feat_cols: dict[str, np.ndarray] = {}
    for fam, got in registry.build_iter(ctx, cfg.features):
        for name, mat in got.items():
            feat_cols[name] = mat.loc[keep_dates].to_numpy(dtype="float32")[rows, cs]
        log.info("features: %s done", fam)
    names = registry.feature_names(cfg.features)
    features = pd.DataFrame({"sample_id": ids, "stock_id": sid, "signal_date": sd,
                             **{n: feat_cols[n] for n in names}})

    # outcomes
    mats = co.build_outcome_matrices(m, cfg.labels)
    outcomes = co.to_long({k: v.loc[keep_dates] for k, v in mats.items()},
                          mask, cal, cfg.labels)
    outcomes.insert(0, "sample_id", ids)
    soft_path = soft.loc[keep_dates].to_numpy()[rows, cs]
    outcomes["signal_soft_flags"] = soft_path.astype("uint16")

    # sample index + weights
    si = build_sample_index(eligible, elig_flags, hard, soft, cal, versions, start)
    si["weight"] = day_normalized_weights(si)

    sample_dates = pd.Index(np.unique(sd))              # 只用有合格樣本的日期
    split = build_split(sample_dates, cfg.validation, cfg.labels.max_horizon, as_of)
    check_split_integrity(split, sample_dates)
    versions["split_version"] = split.split_version
    si["is_holdout"] = split.is_holdout(si["signal_date"]).to_numpy()

    db_max = str(cal.dates[-1])
    ds_version = f"ds_{as_of}_{versions['universe_version']}{versions['feature_version'][2:]}{versions['label_version'][2:]}"
    ut = si.loc[si["eligible"]].groupby("signal_date").size()
    manifest = Manifest(
        dataset_version=ds_version, as_of=as_of, db_max_date=db_max,
        calendar_start=str(cal.dates[0]), calendar_end=db_max, versions=versions,
        feature_names=names, label_columns=[c for c in outcomes.columns if c != "sample_id"],
        row_counts={"sample_index": len(si), "features": len(features), "outcomes": len(outcomes)},
        feature_missing_rate={n: float(np.isnan(feat_cols[n]).mean()) for n in names},
        universe_daily_median=float(ut.median()) if len(ut) else 0.0,
        code_commit=code_commit(),
    )
    return BuiltDataset(ds_version, manifest, si, features, outcomes, split)


def build_and_write(con, cfg: MLEntryConfig, as_of: str | None = None,
                    root: Path = DEFAULT_ROOT) -> Path:
    ds = build(con, cfg, as_of)
    # holdout_start 起（含其後未成熟列）全部進 holdout 分區：dev 分區不得含任何晚於 dev_end 的列
    hs = ds.split.holdout_start
    hold = (ds.features["signal_date"].astype(str) >= hs) if hs else None
    d = write_dataset(root, ds.manifest, ds.sample_index, ds.features, ds.outcomes, hold)
    (d / "splits.json").write_text(ds.split.to_json(), encoding="utf-8")
    log.info("dataset written: %s", d)
    return d
