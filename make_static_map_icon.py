"""
大きいマーカー用 PNG を Google Static Maps 向けに縮小する（外部 URL 取得失敗・赤ピンに戻るのを防ぐ）。

  pip install pillow
  python make_static_map_icon.py --source-url "https://raw.githubusercontent.com/.../tanker.png" --output png/tanker_map.png

長辺は既定 256px。数十〜200KB 程度になるよう optimize 付きで保存する。
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from PIL import Image
except ImportError as e:
    raise SystemExit("Pillow が必要です: pip install pillow") from e


def main() -> int:
    p = argparse.ArgumentParser(description="PNG を Static Maps 用に縮小")
    p.add_argument("--source-url", default="", help="元画像の HTTPS URL")
    p.add_argument("--source-path", type=Path, default=None, help="ローカル PNG")
    p.add_argument(
        "--output",
        type=Path,
        default=Path("png") / "tanker_map.png",
        help="出力パス（既定: png/tanker_map.png）",
    )
    p.add_argument("--max-side", type=int, default=256, help="長辺の最大ピクセル")
    args = p.parse_args()

    if bool(args.source_url) == bool(args.source_path):
        print("ERROR: --source-url と --source-path のどちらか一方を指定してください", flush=True)
        return 1

    if args.source_path is not None:
        raw = args.source_path.read_bytes()
    else:
        req = Request(args.source_url.strip(), headers={"User-Agent": "make_static_map_icon.py"})
        with urlopen(req, timeout=120) as resp:
            raw = resp.read()

    im = Image.open(BytesIO(raw)).convert("RGBA")
    w, h = im.size
    m = max(w, h)
    if m > args.max_side:
        r = args.max_side / m
        im = im.resize((int(w * r), int(h * r)), Image.Resampling.LANCZOS)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    im.save(args.output, "PNG", optimize=True)
    n = args.output.stat().st_size
    print(f"Wrote {args.output} size={im.size} bytes={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
