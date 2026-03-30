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

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


STEPS: list[list[str]] = [
    [
        "cdp1_fetch_station0_playwright.py",
        "--show-all",
        "--user-agent",
        "--output",
        "ship_data/station0_all.json",
        "--url",
        "https://www.marinetraffic.com/",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:49.1/centery:29.9/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:49.6/centery:28.7/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:50.3/centery:27.5/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:51.4/centery:27.0/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:51.4/centery:26.3/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:51.6/centery:25.3/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:53.9/centery:26.3/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:53.0/centery:25.1/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:53.9/centery:24.7/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:54.4/centery:25.5/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:56.2/centery:26.5/zoom:9",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:55.0/centery:25.5/zoom:10",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:53.9/centery:25.8/zoom:10",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:52.1/centery:25.6/zoom:10",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:50.1/centery:27.3/zoom:10",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:49.0/centery:29.4/zoom:10",
        "--url",
        "https://www.marinetraffic.com/en/ais/home/centerx:48.7/centery:29.0/zoom:10",

    ],
    [
        "cdp2_mt_snapshot_filter.py",
        "--mode",
        #"all",
        "japan_jp",
        "--dedupe-by-ship-id",
        "--filter-lat-lon-prefix",
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
        "10000",
    ],
    ["cdp4_ship_details_filter.py",
        "--include-all",
    ],
    [
        "cdp5_diff_ship_positions.py",
        "--mode",
        "latlon_round",
        "--latlon-decimals",
        "3",
        "--latlon-quantize",
        "truncate",
    ],
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
