# backend/scripts/level1_v3_report.py
"""Level 1 v3 findings 報表 CLI（設計 2026-09-28 §2.5／brief Task 10 Step 2）。

印出：
1. fill_report（level1_v3_targets.pkl meta，逐年漲停未成交率／停牌率）
2. 逐 horizon 主表（level1_v3_results.json，step → top20/win/cal_err/no_entry_diff/ic/Δ/floor/pass）
3. 逐 horizon 補充表（dev_oos 逐 seed 的 topk/no_entry 欄位取三種子平均：
   excess vs U_t、median net%、no_entry share_flagged 等）

所有 findings 數字由此輸出，禁手寫。

用法：cd backend && .venv/Scripts/python.exe -X utf8 -m scripts.level1_v3_report
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data"


def _fmt(v, spec: str) -> str:
    return "NA" if v is None else format(v, spec)


def _mean(vals: list) -> float | None:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def print_fill_report(meta: dict) -> None:
    print("## fill_report")
    for y, v in meta["fill_report"].items():
        print(y, v)


def print_main_tables(results: dict) -> None:
    """主表：與 brief Task 10 Step 2 的一次性指令輸出相同欄位。"""
    for h, steps in results["horizons"].items():
        print(f"\n## {h}D")
        print("| step | n_feat | top20 net% (dev, mean±std) | win | cal_err pp | "
              "no_entry diff pp | IC | Δ | floor | pass |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for s in results["steps"]:
            if s not in steps:
                continue
            e = steps[s]
            d = e["dev_summary"]
            dv = e.get("delta_vs_prev", {})
            t20, win = d["top20_mean_net_pct"], d["top20_day_win_rate"]
            cal, ic = d["calibration_abs_error_pp"], d["mean_ic"]
            print(f"| {s} | {len(e['features'])} | "
                  f"{_fmt(t20['mean'], '+.3f')}±{_fmt(t20['std'], '.3f')} | "
                  f"{_fmt(win['mean'], '.3f')} | {_fmt(cal['mean'], '.2f')} | "
                  f"{_fmt(d['no_entry_diff_pp']['mean'], '+.4f')} | "
                  f"{_fmt(ic['mean'], '+.4f')} | "
                  f"{dv.get('top20_mean_net_pct', '—')} | "
                  f"{dv.get('noise_floor', '—')} | {dv.get('passes', '—')} |")


def print_supplementary_tables(results: dict) -> None:
    """補充表（findings §2）：dev_oos 逐 seed 的 topk/no_entry 欄位取三種子平均。"""
    for h, steps in results["horizons"].items():
        print(f"\n## {h}D（dev_oos，三種子平均，補充表）")
        print("| step | top20 excess vs U_t pp | top20 median net% | "
              "no_entry share_flagged | U_t net% 旗標日 | U_t net% 其他日 | "
              "top20 net% 旗標日 | n_days |")
        print("|---|---|---|---|---|---|---|---|")
        for s in results["steps"]:
            if s not in steps:
                continue
            devs = [v["dev_oos"] for v in steps[s]["seeds"].values() if "dev_oos" in v]
            if not devs:
                continue
            excess = _mean([d["topk"]["top20"]["excess_pct"] for d in devs])
            median = _mean([d["topk"]["top20"]["median_net_pct"] for d in devs])
            share = _mean([d["no_entry"]["share_flagged"] for d in devs])
            flagged = _mean([d["no_entry"]["univ_net_pct_flagged"] for d in devs])
            other = _mean([d["no_entry"]["univ_net_pct_other"] for d in devs])
            top20_flagged = _mean([d["no_entry"]["top20_net_pct_flagged"] for d in devs])
            n_days = _mean([d["topk"]["top20"]["n_days"] for d in devs])
            print(f"| {s} | {_fmt(excess, '+.3f')} | {_fmt(median, '.3f')} | "
                  f"{_fmt(share, '.3f')} | {_fmt(flagged, '.3f')} | {_fmt(other, '.3f')} | "
                  f"{_fmt(top20_flagged, '.3f')} | {_fmt(n_days, '.0f')} |")


def main() -> None:
    results = json.loads((_DATA / "level1_v3_results.json").read_text(encoding="utf-8"))
    # pickle 為本專案 scripts.level1_targets_v3 自產的內部快取檔，非外部輸入，可信任
    # （與 scripts/level1_v3_ablation.py 同款用法）。
    meta = pickle.load(open(_DATA / "level1_v3_targets.pkl", "rb"))["meta"]  # noqa: S301
    print_fill_report(meta)
    print_main_tables(results)
    print_supplementary_tables(results)


if __name__ == "__main__":
    main()
