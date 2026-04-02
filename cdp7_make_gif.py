"""
persian_*.png / red_*.png を時系列順に読み込み、最後に GIF を出力する。

例:
  python cdp7_make_gif.py --pattern ship_moved/persian_*.png --output ship_moved/persian.gif
  python cdp7_make_gif.py --pattern ship_moved/red_*.png     --output ship_moved/red.gif
  python cdp7_make_gif.py --region persian_gulf --duration-ms 800
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable


def parse_dt_from_filename(path: Path) -> datetime:
    # map_YYYYMMDD_HHMMSS.png / persian_YYYYMMDD_HHMMSS.png の想定
    m = re.search(r"_(\d{8})_(\d{6})", path.name)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    # タイムスタンプが取れなければファイル更新時刻にフォールバック
    return datetime.fromtimestamp(path.stat().st_mtime)


def sorted_paths_by_timestamp(paths: Iterable[Path]) -> list[Path]:
    return sorted(paths, key=parse_dt_from_filename)


def convert_for_gif(pil_image):
    # GIF は基本的にパレット画像。まず変換してから保存する。
    # colors=255 のままだと色が足りない場合があるが、ここでは簡便に ADAPTIVE。
    from PIL import Image

    img_rgb = pil_image.convert("RGB")
    return img_rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=255)


def make_gif(*, pattern: str, output: Path, duration_ms: int, loop: int) -> int:
    try:
        from PIL import Image
    except ImportError:
        # エラーで止めない（ログだけ出して正常終了扱い）
        print("SKIP: Pillow is not installed. Run: pip install pillow")
        return 0

    matches = [Path(p) for p in Path().glob(pattern)]
    if not matches:
        print(f"SKIP: no files matched pattern: {pattern}")
        return 0

    try:
        matches = sorted_paths_by_timestamp(matches)

        first = Image.open(matches[0]).convert("RGBA")
        frames = [first]
        for p in matches[1:]:
            img = Image.open(p).convert("RGBA")
            frames.append(img)

        # 先頭フレームを P に、残りも同様に変換（size 一致前提）
        frames = [convert_for_gif(im) for im in frames]

        output.parent.mkdir(parents=True, exist_ok=True)
        out_path = output.resolve()

        first_p = frames[0]
        append_images = frames[1:]
        first_p.save(
            out_path,
            save_all=True,
            append_images=append_images,
            duration=duration_ms,
            loop=loop,
            optimize=False,
        )

        t0 = parse_dt_from_filename(matches[0]).strftime("%Y-%m-%d %H:%M:%S")
        t1 = parse_dt_from_filename(matches[-1]).strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"Wrote GIF -> {out_path} (frames={len(matches)}, {t0} .. {t1}, duration={duration_ms}ms)"
        )
        return 0
    except Exception as e:
        # 失敗しても落とさない（ログだけ出して 0）
        print(f"SKIP: failed to generate gif: {e}")
        return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="persian_*.png / red_*.png を GIF 化")
    p.add_argument("--pattern", type=str, default="", help="PNG の glob パターン（例: ship_moved/persian_*.png）")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="出力 GIF パス（省略時は pattern から推定）",
    )
    p.add_argument(
        "--region",
        choices=("persian_gulf", "red_sea", "both"),
        default=None,
        help="region 指定で persian/red の2本まとめて作る（--pattern が空のとき）",
    )
    p.add_argument("--duration-ms", type=int, default=800, help="1フレームあたりの表示時間(ms)")
    p.add_argument("--loop", type=int, default=0, help="GIF の繰り返し回数（0=無限）")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.pattern.strip():
        pattern = args.pattern.strip()
        if args.output is None:
            # 例: ship_moved/persian_*.png -> ship_moved/persian.gif
            stem = Path(pattern).name.replace("*", "").replace(".png", "")
            output = Path(pattern).parent / f"{stem}.gif"
        else:
            output = args.output
        return make_gif(pattern=pattern, output=output, duration_ms=args.duration_ms, loop=args.loop)

    # --pattern が空なら --region で決め打ち
    if args.region is None:
        raise SystemExit("ERROR: --pattern か --region のどちらかを指定してください")

    targets: list[tuple[str, Path]] = []
    if args.region in ("persian_gulf", "both"):
        targets.append(("ship_moved/persian_*.png", Path("ship_moved") / "persian.gif"))
    if args.region in ("red_sea", "both"):
        targets.append(("ship_moved/red_*.png", Path("ship_moved") / "red.gif"))

    rc = 0
    for pattern, output in targets:
        rc = make_gif(pattern=pattern, output=output, duration_ms=args.duration_ms, loop=args.loop)
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

