"""
cdp1 -> cdp4_ship_details_filter -> cdp5_diff を順に実行する。

  python cdp0_run_cdp_pipeline.py

cdp1 の地図 URL:
  STEPS 先頭の「--url」を増やすと海域を追加できる（cdp1 は複数 --url を 1 つの station0_all.json にマージ）。
  下記 2 本は例なので、座標・zoom は必要に応じて書き換え・追加してください。

cdp5:
  既定で lat/lon を小数第 3 位まで処理（切り捨て）、前回と同じ格子なら STAY・違えば MOVED。
  四捨五入にする場合は --latlon-quantize round。距離・速度しきい値は --mode threshold。

前提:
  - 作業ディレクトリはこのスクリプトと同じフォルダ（成果物は ship_data/ 以下）
  - 初回のみ cdp5_diff は ship_data/ship_details_jp_prev.json が無く失敗することがある（2回目以降はOK）
  - User-Agent は cdp1/cdp3 が `chrome_user_agent.txt` を読む（リポジトリ直下。手元の Chrome の chrome://version から貼り付け可）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


STEPS: list[list[str]] = [
    [
        "cdp2_mt_snapshot_filter.py",
        "--mode",
        #"all",
        "japan_jp",
        "--include-gt-shiptypes",
        "17,18,71,88",
        "--dedupe-by-ship-id",
        #"--filter-lat-lon-prefix",
        #"--exclude-lon-minus",
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
        "--user-agent",
        "--post-open-wait-ms",
        "7000",
    ],
    ["cdp4_ship_details_filter.py",
        "--include-all",
    ],
    [
        "cdp5_diff_ship_positions.py",
        #"--mode",
        #"latlon_round",
        "--latlon-decimals",
        "1",
        "--latlon-quantize",
        "round",
        "--latlon-moved-if-speed-ge",
        "10",
    ],
    [
        "cdp6_google-maps.py",
        "--input",
        "ship_moved/moved_report_01.json",
    ],
    [
        "cdp6_google-maps.py",
        "--input",
        "ship_moved/moved_report_01.json",
        "--region",
        "persian_gulf",
    ],
    [
        "cdp6_google-maps.py",
        "--input",
        "ship_moved/moved_report_01.json",
        "--region",
        "red_sea",
    ],
        ["cdp7_make_gif.py",
        "--pattern",
        "ship_moved/persian_*.png",
        "--output",
        "ship_moved/persian.gif",
    ],
    ["cdp7_make_gif.py",
        "--pattern",
        "ship_moved/red_*.png",
        "--output",
        "ship_moved/red.gif",
    ],


]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run cdp pipeline from cdp2 -> ...")
    p.add_argument("--USA", action="store_true", help="Use cdp2 mode: usa_military")
    p.add_argument(
        "--shipname-contains",
        action="append",
        default=[],
        metavar="TEXT",
        help="cdp2 の既存抽出結果に、SHIPNAME に TEXT を含む行を追加（複数指定可）",
    )
    args = p.parse_args(argv)

    cdp2_mode = "usa_military" if args.USA else "japan_jp"
    steps = [list(s) for s in STEPS]
    # STEPS[0] layout: ["cdp2_mt_snapshot_filter.py", "--mode", <mode>, ...]
    try:
        mode_i = steps[0].index("--mode")
        steps[0][mode_i + 1] = cdp2_mode
    except Exception:
        steps[0][2] = cdp2_mode

    # USA のときは タンカー縛り（GT_SHIPTYPE 許可リスト）を外す
    if args.USA and "--include-gt-shiptypes" in steps[0]:
        i = steps[0].index("--include-gt-shiptypes")
        del steps[0][i : i + 2]

    for token in args.shipname_contains:
        steps[0] += ["--shipname-contains", token]

    for step in steps:
        script = ROOT / step[0]
        if not script.is_file():
            print(f"ERROR: not found: {script}", file=sys.stderr)
            return 1
        cmd = [sys.executable, str(script), *step[1:]]
        print(f"+ {' '.join(cmd)}", flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print(f"ERROR: exit {r.returncode} from {step[0]}", file=sys.stderr)
            return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
