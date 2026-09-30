"""§12 任務定義：每個模型吃哪些樣本、哪個標籤、什麼權重（附錄 B 硬規則 2、3）。

- execution：全樣本（entry_status ≠ PENDING），學 P(blocked)（blocked ≈ 0.5%，診斷直觀）。
- target_H / stop_H：只用 entry_executable == 1 且成熟的樣本；STOP_AMBIGUOUS 列 weight = 0。
- mfe_H：同上樣本；winsorization 由 training fold 自估（oof.py），這裡只定義標籤。
- direction_H（challenger）：P(TARGET | TARGET or STOP)，只用事件已解決（TARGET/STOP）的列。
- event_H（challenger）：三分類 competing-risk P(TARGET/STOP/TIMEOUT)；STOP_AMBIGUOUS weight 0。
所有任務再疊 day-normalized 權重 w = 1/|樣本日 n|（§9.4）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..labels.barriers import EntryStatus, Event

EXEC_STATUS_COLS = ("entry_status",)


@dataclass(frozen=True)
class TaskSpec:
    name: str
    kind: str                 # binary | regression
    label_col: str            # outcome 欄；execution 用 entry_status 衍生
    horizon: int | None

    @property
    def outcome_columns(self) -> list[str]:
        base = ["entry_status", "entry_executable", "event_type", "matured"]
        if self.name.startswith(("direction_", "event_")):
            base += ["target_first_hit_day", "stop_first_hit_day"]
        elif self.name != "execution":
            base.append(self.label_col)
        return base


def all_tasks(horizons=(3, 5, 10)) -> dict[str, TaskSpec]:
    t = {"execution": TaskSpec("execution", "binary", "blocked", None)}
    for h in horizons:
        t[f"target_{h}d"] = TaskSpec(f"target_{h}d", "binary", f"target_hit_{h}d", h)
        t[f"stop_{h}d"] = TaskSpec(f"stop_{h}d", "binary", f"stop_hit_{h}d", h)
        t[f"mfe_{h}d"] = TaskSpec(f"mfe_{h}d", "regression", f"mfe_{h}d", h)
        t[f"direction_{h}d"] = TaskSpec(f"direction_{h}d", "binary", "event_type", h)
        t[f"event_{h}d"] = TaskSpec(f"event_{h}d", "multiclass", "event_type", h)
    return t


EVENT_CLASSES = {int(Event.TARGET): 0, int(Event.STOP): 1, int(Event.TIMEOUT): 2}   # multiclass 類別順序
EVENT_CLASS_NAMES = ("target", "stop", "timeout")


def task_frame(task: TaskSpec, outcomes: pd.DataFrame) -> pd.DataFrame:
    """→ DataFrame(sample_id, signal_date, y, w_task)。只含該任務合法樣本。"""
    o = outcomes
    if task.name.startswith("direction_"):
        h = task.horizon
        ev, tday, sday = o["event_type"], o[f"target_first_hit_day"], o[f"stop_first_hit_day"]
        # 在 horizon h 內已解決：TARGET 且 day<=h，或 STOP 且 day<=h（10D 即全體 TARGET/STOP）
        is_t = (ev == int(Event.TARGET)) & (tday <= h)
        is_s = (ev == int(Event.STOP)) & (sday <= h)
        ok = (o["entry_executable"] == 1) & (o["matured"] == 1) & (is_t | is_s)
        y = is_t.astype("float32")
        w = pd.Series(1.0, index=o.index, dtype="float32")
    elif task.name.startswith("event_"):
        h = task.horizon
        ev, tday, sday = o["event_type"], o[f"target_first_hit_day"], o[f"stop_first_hit_day"]
        cls = pd.Series(2.0, index=o.index)                                   # 預設 TIMEOUT（h 內未觸）
        cls = cls.mask((ev == int(Event.TARGET)) & (tday <= h), 0.0)
        cls = cls.mask((ev == int(Event.STOP)) & (sday <= h), 1.0)
        ok = (o["entry_executable"] == 1) & (o["matured"] == 1)
        y = cls.astype("float32")
        w = pd.Series(1.0, index=o.index, dtype="float32").mask(ev == int(Event.STOP_AMBIGUOUS), 0.0)
    elif task.name == "execution":
        ok = o["entry_status"] != int(EntryStatus.PENDING)
        y = (o["entry_status"].isin([int(EntryStatus.PRICE_LIMIT_CONSTRAINT),
                                     int(EntryStatus.NO_MARKET_DATA)])).astype("float32")
        w = pd.Series(1.0, index=o.index, dtype="float32")
    else:
        ok = (o["entry_executable"] == 1) & (o["matured"] == 1) & o[task.label_col].notna()
        y = o[task.label_col].astype("float32")
        w = pd.Series(1.0, index=o.index, dtype="float32")
        if task.kind == "binary":
            w = w.mask(o["event_type"] == int(Event.STOP_AMBIGUOUS), 0.0)
    df = pd.DataFrame({"sample_id": o["sample_id"], "signal_date": o["signal_date"],
                       "y": y, "w_task": w})
    return df.loc[ok.to_numpy()].reset_index(drop=True)


def day_weights(signal_date: pd.Series) -> np.ndarray:
    n = signal_date.groupby(signal_date).transform("size").to_numpy(dtype="float64")
    return (1.0 / n).astype("float32")
