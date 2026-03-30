"""
cdp1 -> cdp4_ship_details_filter -> cdp5_diff を順に実行する。

  python cdp0_run_cdp_pipeline.py

前提:
  - 作業ディレクトリはこのスクリプトと同じフォルダ（成果物は ship_data/ 以下）
  - 初回のみ cdp5_diff は ship_data/ship_details_jp_prev.json が無く失敗することがある（2回目以降はOK）
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
        "ship_data/station0_all.json",
    ],
    [
        "cdp2_mt_snapshot_filter.py",
        "--mode",
        "all",
        "--dedupe-by-ship-id",
        "--jsonl",
        "ship_data/out.jsonl",
    ],
    [
        "cdp3_fetch_ship_details.py",
        "--input",
        "ship_data/out.jsonl",
        "--output",
        "ship_data/ship_details.json",
        "--show-all",
    ],
    ["cdp4_ship_details_filter.py"],
    ["cdp5_diff_ship_positions.py"],
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
