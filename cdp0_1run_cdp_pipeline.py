"""
cdp1 -> cdp4_ship_details_filter -> cdp5_diff を順に実行する。

  python cdp0_1run_cdp_pipeline.py
  python cdp0_1run_cdp_pipeline.py --USA
  python cdp0_1run_cdp_pipeline.py --url "https://..."

cdp1 の地図 URL:
  --url 未指定かつ --USA なし → 従来どおり DEFAULT_CDP1_URLS（14本）。
  --url 未指定かつ --USA あり → USA_CDP1_URLS（米軍向け既定）。
  --url はいずれのモードでも上書き。cdp1 は複数 --url を 1 つの station0_all.json にマージ。

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

# 既定（--USA なし・--url なし）: 修正前と同じ 14 本
DEFAULT_CDP1_URLS = [
    "https://www.marinetraffic.com/en/ais/home/centerx:49.4/centery:28.6/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:51.5/centery:26.3/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:53.2/centery:24.7/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:53.0/centery:25.7/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:53.5/centery:25.7/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:52.8/centery:25.4/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:57.8/centery:25.5/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:37.5/centery:23.5/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:46.7/centery:18.7/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:54.9/centery:25.5/zoom:10",
    "https://www.marinetraffic.com/en/ais/home/centerx:53.9/centery:25.5/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:51.9/centery:26.3/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:50.6/centery:27.1/zoom:9",
    "https://www.marinetraffic.com/en/ais/home/centerx:54.5/centery:25.3/zoom:9",
]

# --USA 時のみ（--url なし）
USA_CDP1_URLS = [
    "https://www.marinetraffic.com/en/ais/home/centerx:61.8/centery:23.7/zoom:7",
    "https://www.marinetraffic.com/en/ais/home/centerx:62.1/centery:18.9/zoom:7",
    "https://www.marinetraffic.com/en/ais/home/centerx:59.2/centery:14.8/zoom:7",
    "https://www.marinetraffic.com/en/ais/home/centerx:68.2/centery:22.7/zoom:7",
    "https://www.marinetraffic.com/en/ais/home/centerx:69.1/centery:17.9/zoom:7",
    "https://www.marinetraffic.com/en/ais/home/centerx:70.8/centery:12.9/zoom:7",
    "https://www.marinetraffic.com/en/ais/home/centerx:68.4/centery:14.7/zoom:7",
]


def build_steps(*, cdp1_urls: list[str], cdp2_mode: str, shipname_contains: list[str]) -> list[list[str]]:
    cdp1_step = [
        "cdp1_fetch_station0_playwright.py",
        "--show-all",
        "--user-agent",
        "--output",
        "ship_data/station0_all.json",
    ]
    for u in cdp1_urls:
        cdp1_step += ["--url", u]

    cdp2_step: list[str] = [
        "cdp2_mt_snapshot_filter.py",
        "--mode",
        cdp2_mode,
        "--dedupe-by-ship-id",
        "--jsonl",
        "ship_data/out.jsonl",
    ]
    if cdp2_mode != "usa_military":
        # 日本向け（タンカー中心）: GT_SHIPTYPE 許可リストで絞る
        idx = cdp2_step.index("--dedupe-by-ship-id")
        cdp2_step[idx:idx] = ["--include-gt-shiptypes", "17,18,71,88"]
    for token in shipname_contains:
        cdp2_step += ["--shipname-contains", token]

    return [
        cdp1_step,
        cdp2_step,
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
        ["cdp4_ship_details_filter.py", "--include-all"],
        [
            "cdp5_diff_ship_positions.py",
            # "--mode",
            # "latlon_round",
            "--latlon-decimals",
            "1",
            "--latlon-quantize",
            "round",
            "--latlon-moved-if-speed-ge",
            "10",
        ],
        ["cdp6_google-maps.py", "--input", "ship_moved/moved_report_01.json"],
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
        [
            "cdp7_make_gif.py",
            "--pattern",
            "ship_moved/persian_*.png",
            "--output",
            "ship_moved/persian.gif",
        ],
        [
            "cdp7_make_gif.py",
            "--pattern",
            "ship_moved/red_*.png",
            "--output",
            "ship_moved/red.gif",
        ],
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run cdp pipeline (cdp1 -> cdp2 -> ...)")
    p.add_argument("--USA", action="store_true", help="Use cdp2 mode: usa_military")
    p.add_argument(
        "--url",
        action="append",
        default=[],
        metavar="URL",
        help="Override first cdp1 --url list (repeatable)",
    )
    p.add_argument(
        "--shipname-contains",
        action="append",
        default=[],
        metavar="TEXT",
        help="cdp2 の既存抽出結果に、SHIPNAME に TEXT を含む行を追加（複数指定可）",
    )
    args = p.parse_args(argv)

    if args.url:
        cdp1_urls = args.url
    elif args.USA:
        cdp1_urls = USA_CDP1_URLS
    else:
        cdp1_urls = DEFAULT_CDP1_URLS
    cdp2_mode = "usa_military" if args.USA else "japan_jp"
    steps = build_steps(
        cdp1_urls=cdp1_urls,
        cdp2_mode=cdp2_mode,
        shipname_contains=args.shipname_contains,
    )

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
