"""§12–§13 估計器：prevalence / logistic regression / LightGBM，統一 fit(X, y, w) / predict(X) 介面。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MODEL_NAMES = ("prevalence", "logreg", "lgbm")


class Prevalence:
    """歷史盛行率 / 平均值常數模型（§13-1）。"""

    def __init__(self, kind: str = "binary"):
        self.kind = kind
        self.value_ = float("nan")

    def fit(self, X, y, w=None):
        w = np.ones(len(y)) if w is None else np.asarray(w, dtype=float)
        y = np.asarray(y, dtype=float)
        if self.kind == "multiclass":
            k = int(y.max()) + 1
            self.value_ = np.array([np.sum(w[y == c]) / w.sum() for c in range(k)], dtype="float32")
        else:
            self.value_ = float(np.average(y, weights=w))
        return self

    def predict(self, X):
        if self.kind == "multiclass":
            return np.tile(self.value_, (len(X), 1))
        return np.full(len(X), self.value_, dtype="float32")


class _SkWrap:
    def __init__(self, pipe: Pipeline, kind: str):
        self.pipe, self.kind = pipe, kind

    def fit(self, X, y, w=None):
        # 雙 NaN 保護：中位數補值後再標準化；線性模型不吃 NaN
        self.pipe.fit(X, y, **({"est__sample_weight": w} if w is not None else {}))
        return self

    def predict(self, X):
        if self.kind == "binary":
            return self.pipe.predict_proba(X)[:, 1].astype("float32")
        return self.pipe.predict(X).astype("float32")


def _linear(kind: str, params: dict[str, Any]):
    est = (LogisticRegression(C=params.get("C", 1.0), max_iter=params.get("max_iter", 200))
           if kind == "binary" else Ridge(alpha=params.get("alpha", 1.0)))
    pipe = Pipeline([("impute", SimpleImputer(strategy="median")),
                     ("scale", StandardScaler()), ("est", est)])
    return _SkWrap(pipe, kind)


class _LGBM:
    def __init__(self, kind: str, params: dict[str, Any]):
        import lightgbm as lgb
        self.kind = kind
        if kind == "multiclass":
            params = {**params, "objective": "multiclass"}
        self.model = (lgb.LGBMClassifier(**params) if kind in ("binary", "multiclass") else lgb.LGBMRegressor(**params))

    def fit(self, X, y, w=None):
        self.model.fit(X, y, sample_weight=w)
        return self

    def predict(self, X):
        if self.kind == "binary":
            return self.model.predict_proba(X)[:, 1].astype("float32")
        if self.kind == "multiclass":
            return self.model.predict_proba(X).astype("float32")          # (n, n_class)
        return self.model.predict(X).astype("float32")

    def feature_importance(self, names) -> pd.Series:
        return pd.Series(self.model.booster_.feature_importance("gain"), index=names)


def make_model(name: str, kind: str, cfg: dict[str, Any]):
    if name == "prevalence":
        return Prevalence(kind)
    if name == "logreg":
        if kind == "multiclass":
            raise ValueError("logreg multiclass not supported in v1")
        return _linear(kind, cfg.get("logreg", {}))
    if name == "lgbm":
        return _LGBM(kind, dict(cfg["lgbm_regression" if kind == "regression" else "lgbm_binary"]))
    raise ValueError(f"unknown model {name}")
