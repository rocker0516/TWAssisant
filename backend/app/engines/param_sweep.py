"""ParamSweepEngine（L4++）：出場參數掃描 + walk-forward 樣本外驗證（僅波段軌）。

進場規則固定（分數達門檻），只掃出場參數（停損/移動停利/含不含跌破月線）。
最貴的 point-in-time evaluate 透過 ExpectancyEngine._precompute 只算一次，36 組參數各跑
便宜的 _simulate。**重點是 walk-forward**：前段挑最佳參數 → 套到沒看過的後段，比
「最佳化 vs 預設」在樣本外誰贏。若樣本外贏不了預設＝過擬合，照實講、不自欺。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..storage import models
from .base import BaseEngine
from .exit_signals import set_config
from .expectancy import ExpectancyEngine, _entry_passed, _stats

# 掃描網格（出場參數，百分比）。往更寬擴，看期望值是 plateau(真甜蜜點)還是 runaway(行情假象)。
_STOP_CAPS = [8, 12, 16, 20]
_TRAIL_TRIGGERS = [10, 16, 22]
_TRAIL_PULLBACKS = [10, 16, 22]
_BREAK_MA = [True, False]
_DEFAULT = {"stop_cap": 8, "trail_trigger": 10, "trail_pullback": 10, "break_ma": True}
_MIN_TRAIN_TRADES = 80  # 訓練段樣本太少不選，避免雜訊挑參數
_FOLDS = 3  # 時間切 3 段，walk-forward 2 折（擴張視窗）


def _grid() -> list[dict]:
    combos = []
    for bm in _BREAK_MA:
        for sc in _STOP_CAPS:
            for tt in _TRAIL_TRIGGERS:
                for tp in _TRAIL_PULLBACKS:
                    combos.append({"stop_cap": sc, "trail_trigger": tt, "trail_pullback": tp, "break_ma": bm})
    return combos


def _expectancy(trades: list[dict]) -> float | None:
    return (sum(t["ret"] for t in trades) / len(trades) * 100) if trades else None


class ParamSweepEngine(BaseEngine):
    name = "param_sweep"

    def __init__(self) -> None:
        self.exp = ExpectancyEngine()

    def run(self, session: Session, trading_date: date) -> dict:
        result = self.compute(session, generated_at=trading_date)
        row = session.get(models.Setting, "param_sweep")
        if row is None:
            session.add(models.Setting(key="param_sweep", value=result))
        else:
            row.value = result
        session.flush()
        return {"status": "ok", "combos": len(result.get("grid_top", [])),
                "oos_optimized": result.get("walkforward", {}).get("oos_optimized"),
                "oos_default": result.get("walkforward", {}).get("oos_default")}

    def _run_combo(self, sds: list, combo: dict) -> list[dict]:
        """套一組出場參數，跑全市場逐筆 → trades（含 entry_date）。"""
        set_config({"wave": {k: combo[k] for k in ("stop_cap", "trail_trigger", "trail_pullback")}})
        trades: list[dict] = []
        for sd in sds:
            trades += self.exp._simulate(sd, _entry_passed, faithful=combo["break_ma"])
        return trades

    def compute(self, session: Session, generated_at: date) -> dict:
        sc_cfg = session.get(models.Setting, "scoring")
        wave_cfg = (sc_cfg.value or {}).get("wave", {}) if sc_cfg and isinstance(sc_cfg.value, dict) else {}
        sds, target_dates = self.exp._precompute(session, wave_cfg)
        if not sds:
            return {"track": "wave", "grid_top": [], "walkforward": {}, "note": "歷史資料不足。"}

        grid = _grid()
        # 每組參數的全期 trades（含 entry_date），供分段取樣本外
        combo_trades: list[tuple[dict, list[dict]]] = [(c, self._run_combo(sds, c)) for c in grid]
        default_trades = self._run_combo(sds, _DEFAULT)

        # 時間切 _FOLDS 段（依進場日）
        d0, d1 = target_dates[0], target_dates[-1]
        span = (d1 - d0).days or 1
        cuts = [d0, d0 + (d1 - d0) * 1 // _FOLDS, d0 + (d1 - d0) * 2 // _FOLDS, d1]
        seg = lambda trades, lo, hi: [t for t in trades if lo <= t["entry_date"] <= hi]  # noqa: E731

        # 全期排行（樣本內，會誘人——拿來對照 walk-forward）
        full_rows = []
        for c, tr in combo_trades:
            st = _stats(tr)
            full_rows.append({"params": c, "n": st["n"], "expectancy": st["expectancy"],
                              "win_rate": st["win_rate"], "payoff": st["payoff"], "avg_mae": st["avg_mae"]})
        full_rows.sort(key=lambda r: (r["expectancy"] is not None, r["expectancy"] or -1e9), reverse=True)
        best_full = full_rows[0] if full_rows else None

        # 邊界診斷：最佳解是否仍貼網格最寬端 → 期望值還在隨放寬上升＝runaway/行情假象
        loosest = {"stop_cap": max(_STOP_CAPS), "trail_trigger": max(_TRAIL_TRIGGERS),
                   "trail_pullback": max(_TRAIL_PULLBACKS), "break_ma": False}
        at_max = []
        if best_full:
            bp = best_full["params"]
            for k in ("stop_cap", "trail_trigger", "trail_pullback"):
                if bp[k] == loosest[k]:
                    at_max.append(k)
            is_runaway = bp == loosest
        else:
            is_runaway = False
        if is_runaway:
            boundary_msg = ("最佳解仍卡在網格『最寬端的角落』——期望值還在隨放寬持續上升，沒有收斂。"
                            "這比較像『在這段行情裡越不出場越好』的 regime 假象（被存活者偏誤＋更長持有吃 beta 放大），"
                            "不是穩定的甜蜜點。別把放寬幅度當保證。")
        elif len(at_max) >= 2:
            boundary_msg = "最佳解多軸仍貼最寬端，放寬可能還沒到頂，偏 regime 效應，謹慎看待。"
        else:
            boundary_msg = "最佳解落在網格內部（非最寬角落）——較像真的甜蜜點，放寬到某處後不再更好。"

        # walk-forward 2 折：擴張視窗，前段挑最佳 → 套到後段
        folds = []
        oos_opt_trades: list[dict] = []
        oos_def_trades: list[dict] = []
        for k in range(1, _FOLDS):
            train_lo, train_hi = cuts[0], cuts[k]
            test_lo, test_hi = cuts[k], cuts[k + 1]
            # 在訓練段挑期望值最佳（樣本數夠）的參數
            best = None
            for c, tr in combo_trades:
                tr_train = seg(tr, train_lo, train_hi)
                if len(tr_train) < _MIN_TRAIN_TRADES:
                    continue
                exp = _expectancy(tr_train)
                if exp is not None and (best is None or exp > best[1]):
                    best = (c, exp, tr)
            if best is None:
                continue
            picked, train_exp, picked_trades = best
            test_opt = seg(picked_trades, test_lo, test_hi)
            test_def = seg(default_trades, test_lo, test_hi)
            oos_opt_trades += test_opt
            oos_def_trades += test_def
            folds.append({
                "test_from": test_lo.isoformat(), "test_to": test_hi.isoformat(),
                "picked": picked, "train_expectancy": round(train_exp, 2),
                "oos_expectancy": _expectancy(test_opt), "n_test": len(test_opt),
                "default_oos_expectancy": _expectancy(test_def),
            })

        oos_opt = _expectancy(oos_opt_trades)
        oos_def = _expectancy(oos_def_trades)
        oos_opt_mae = _stats(oos_opt_trades)["avg_mae"]
        oos_def_mae = _stats(oos_def_trades)["avg_mae"]
        edge = None if (oos_opt is None or oos_def is None) else round(oos_opt - oos_def, 2)
        if edge is None:
            verdict = "資料不足，無法下定論。"
        elif edge > 0.2:
            verdict = f"最佳化在樣本外勝預設 {edge:+.2f}%／筆——這個出場設定可能真有改善。"
        elif edge < -0.2:
            verdict = f"最佳化在樣本外輸預設 {edge:+.2f}%／筆——典型過擬合，訓練段挑的參數不通用。"
        else:
            verdict = "最佳化與預設樣本外幾乎打平——調參沒有可靠優勢，維持預設即可。"

        return {
            "generated_at": generated_at.isoformat(),
            "track": "wave",
            "window": {"from": d0.isoformat(), "to": d1.isoformat()},
            "grid_size": len(grid),
            "default": {"params": _DEFAULT, **{k: _stats(default_trades)[k] for k in ("n", "expectancy", "win_rate", "payoff")}},
            "best_full": best_full,
            "grid_top": full_rows[:8],
            "boundary": {"at_max": at_max, "is_runaway": is_runaway, "message": boundary_msg},
            "walkforward": {
                "folds": folds,
                "oos_optimized": oos_opt,
                "oos_default": oos_def,
                "oos_optimized_mae": oos_opt_mae,
                "oos_default_mae": oos_def_mae,
                "edge": edge,
                "verdict": verdict,
            },
            "note": (
                "僅波段軌。進場固定（分數達門檻），只掃出場參數。全期排行為樣本內（會誘人高估）；"
                "walk-forward 用前段挑參數、套沒看過的後段，才是誠實的樣本外成績。"
                "資料約 2 年(見 window)、僅 2 折，方向參考、非投資建議。"
            ),
        }
