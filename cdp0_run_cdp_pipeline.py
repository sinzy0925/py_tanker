"""
cdp1 -> cdp4 を、会話で使った引数どおりに順に実行する。

  python run_cdp_pipeline.py

前提:
  - 作業ディレクトリはこのスクリプトと同じフォルダ（相対パス station0_all.json 等）
  - 初回のみ cdp4 は ship_details_prev.json が無く失敗することがある（2回目以降はOK）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

STEPS: list[list[str]] = [
    [
        "cdp1_fetch_station0_playwright.py",
        "--show-all",
        "--output",
        "station0_all.json",
    ],
    [
        "cdp2_mt_snapshot_filter.py",
        "--mode",
        "japan_hint",
        "--dedupe-by-ship-id",
        "--jsonl",
        "out.jsonl",
    ],
    [
        "cdp3_fetch_ship_details.py",
        "--input",
        "out.jsonl",
        "--output",
        "ship_details.json",
        "--show-all",
    ],
    ["cdp4_diff_ship_positions.py"],
]


def main() -> int:
    for argv in STEPS:
        script = ROOT / argv[0]
        if not script.is_file():
            print(f"ERROR: not found: {script}", file=sys.stderr)
            return 1
        cmd = [sys.executable, str(script), *argv[1:]]
        print(f"+ {' '.join(cmd)}", flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"ERROR: exit {r.returncode} from {argv[0]}", file=sys.stderr)
            return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
