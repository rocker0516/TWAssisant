"""為 frozen champion 產生 feature_reference.monitoring.json sidecar（Spec A §2.6）。

    python -m scripts.mlentry_feature_monitoring_sidecar [--model-version X] [--root DIR]

只寫 sidecar；artifact 目錄其他檔案不讀寫（feature_reference.json 只讀）。冪等：內容相同不重寫。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.mlentry.monitoring.monitor_modes import SIDECAR_NAME, build_sidecar  # noqa: E402
from app.mlentry.registry.versions import SERVING_ROOT, load_champion  # noqa: E402


def write_sidecar(stack_dir: Path) -> tuple[Path, dict]:
    ref_full = json.loads((stack_dir / "feature_reference.json").read_text(encoding="utf-8"))
    side = build_sidecar(ref_full)
    path = stack_dir / SIDECAR_NAME
    text = json.dumps(side, ensure_ascii=False, indent=2, sort_keys=True)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path, side


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-version", default=None); ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else SERVING_ROOT
    mv = args.model_version
    if mv is None:
        champ = load_champion(root)
        if champ is None:
            print("no champion; pass --model-version"); return 1
        mv = champ.model_version
    path, side = write_sidecar(root / mv)
    modes = [v["monitor_mode"] for v in side["features"].values()]
    print(f"wrote {path}")
    print(f"continuous={modes.count('continuous')} skip={modes.count('skip')}")
    for n, v in sorted(side["features"].items()):
        print(f"  {v['monitor_mode']:10s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
