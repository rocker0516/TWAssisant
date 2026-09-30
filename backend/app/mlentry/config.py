"""YAML config → frozen dataclass；每份 config 以內容 sha256 前 8 碼當版本（FRS §11、§30）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "mlentry"


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:8]


def load_yaml(name: str, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    with open(config_dir / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class _Versioned:
    @property
    def version(self) -> str:
        return _digest({f.name: getattr(self, f.name) for f in fields(self)})

    @classmethod
    def from_dict(cls, d: dict[str, Any]):
        names = {f.name for f in fields(cls)}
        unknown = set(d) - names
        if unknown:
            raise ValueError(f"{cls.__name__}: unknown config keys {sorted(unknown)}")
        vals = {k: (tuple(v) if isinstance(v, list) else v) for k, v in d.items()}
        return cls(**vals)


@dataclass(frozen=True)
class UniverseConfig(_Versioned):
    history_lookback: int = 120
    min_history_coverage: float = 0.95
    min_liquidity_twd: float | None = None
    research_start: str = "2020-01-02"


@dataclass(frozen=True)
class FeatureConfig(_Versioned):
    large_gap_threshold: float = 0.05
    families: tuple[str, ...] = ("price", "volume", "volatility", "cross_sectional", "regime", "event")


@dataclass(frozen=True)
class LabelConfig(_Versioned):
    target_pct: float = 0.10
    stop_pct: float = 0.05
    max_horizon: int = 10
    horizons: tuple[int, ...] = (3, 5, 10)
    return_horizons: tuple[int, ...] = (1, 3, 5, 10)
    limit_tolerance: float = 1e-6

    def __post_init__(self):
        if max(self.horizons) > self.max_horizon or max(self.return_horizons) > self.max_horizon:
            raise ValueError("horizons must be <= max_horizon")


@dataclass(frozen=True)
class ValidationConfig(_Versioned):
    train_window_days: int | None = 756
    fold_days: int = 126
    holdout_days: int = 252
    min_train_days: int = 250
    require_full_window: bool = True     # 第一個 fold 需有完整 train_window（§19.2 baseline）


@dataclass(frozen=True)
class MLEntryConfig:
    universe: UniverseConfig
    features: FeatureConfig
    labels: LabelConfig
    validation: ValidationConfig

    @property
    def versions(self) -> dict[str, str]:
        return {"universe_version": self.universe.version,
                "feature_config_version": self.features.version,
                "label_version": self.labels.version,
                "split_version": self.validation.version}


def load_config(config_dir: Path = CONFIG_DIR) -> MLEntryConfig:
    return MLEntryConfig(
        universe=UniverseConfig.from_dict(load_yaml("universe", config_dir)),
        features=FeatureConfig.from_dict(load_yaml("features", config_dir)),
        labels=LabelConfig.from_dict(load_yaml("labels", config_dir)),
        validation=ValidationConfig.from_dict(load_yaml("validation", config_dir)),
    )
