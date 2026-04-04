"""
persian_*.png / red_*.png を時系列順に読み込み、GIF を出力する（任意で MP4 も）。

例:
  python cdp7_make_gif.py --pattern ship_moved/persian_*.png --output ship_moved/persian.gif
  python cdp7_make_gif.py --pattern ship_moved/red_*.png     --output ship_moved/red.gif
  python cdp7_make_gif.py --region persian_gulf --duration-ms 800
  python cdp7_make_gif.py --pattern ship_moved/persian_*.png --output ship_moved/persian.gif --also-mp4
  python cdp7_make_gif.py ... --also-mp4 --mp4-duration-ms 1200

MP4 は PATH 上の ffmpeg で生成する（未インストール時は SKIP ログのみ）。
--mp4-duration-ms を省略すると MP4 も GIF と同じ --duration-ms を使う。
GIF/MP4 とも、既定で画像上部に白背景・黒文字のタイトル帯を付ける（--no-title でオフ）。
タイトルがあるとき、最後の行に PNG ファイル名の _YYYYMMDD_ から求めた YYYY/MM/DD ～ YYYY/MM/DD を自動追記する。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

# 上部タイトル帯の既定文（\n で改行）
DEFAULT_TITLE = (
    "ペルシャ湾（ホルムズ海峡）付近\n"
    "日本向けと推定される原油・LNGタンカーの位置推移"
)


def parse_dt_from_filename(path: Path) -> datetime:
    # map_YYYYMMDD_HHMMSS.png / persian_YYYYMMDD_HHMMSS.png の想定
    m = re.search(r"_(\d{8})_(\d{6})", path.name)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    # タイムスタンプが取れなければファイル更新時刻にフォールバック
    return datetime.fromtimestamp(path.stat().st_mtime)


def sorted_paths_by_timestamp(paths: Iterable[Path]) -> list[Path]:
    return sorted(paths, key=parse_dt_from_filename)


def date_range_line_from_png_paths(paths: list[Path]) -> str | None:
    """ファイル名の _YYYYMMDD_HHMMSS_ から日付を集め、YYYY/MM/DD ～ YYYY/MM/DD を返す。"""
    dates: list[datetime] = []
    for p in paths:
        m = re.search(r"_(\d{8})_\d{6}", p.name)
        if not m:
            continue
        try:
            dates.append(datetime.strptime(m.group(1), "%Y%m%d"))
        except ValueError:
            continue
    if not dates:
        return None
    d0 = min(dates)
    d1 = max(dates)
    return (
        f"{d0.year:04d}/{d0.month:02d}/{d0.day:02d} ～ "
        f"{d1.year:04d}/{d1.month:02d}/{d1.day:02d}"
    )


def augment_title_with_png_date_range(title: str, paths: list[Path]) -> str:
    """タイトルが空でなければ、最後の行に PNG 名由来の日付範囲を追加する。"""
    title = (title or "").strip()
    if not title:
        return ""
    line = date_range_line_from_png_paths(paths)
    if not line:
        return title
    return title.rstrip() + "\n" + line


def _load_title_font(size: int):
    from PIL import ImageFont

    win = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
    for name in ("meiryob.ttc", "meiryo.ttc", "YuGothB.ttc", "msgothic.ttc"):
        p = os.path.join(win, name)
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def add_top_title_banner(im, title: str):
    """画像上に白い帯を付け、その中央に黒文字でタイトルを描く（RGBA を返す）。"""
    from PIL import Image, ImageDraw

    im = im.convert("RGBA")
    title = (title or "").strip()
    if not title:
        return im

    w, _h = im.size
    lines = [ln.strip() for ln in title.split("\n") if ln.strip()]
    if not lines:
        return im

    font_size = max(16, min(52, int(w * 0.038)))
    font = _load_title_font(font_size)
    meas = ImageDraw.Draw(Image.new("RGB", (max(w, 400), 200)))
    line_heights: list[int] = []
    for line in lines:
        bb = meas.textbbox((0, 0), line, font=font)
        line_heights.append(bb[3] - bb[1])
    gap = max(4, font_size // 8)
    pad_v = max(12, font_size // 2)
    banner_h = pad_v * 2 + sum(line_heights) + gap * max(0, len(lines) - 1)

    out = Image.new("RGBA", (w, im.size[1] + banner_h), (255, 255, 255, 255))
    out.paste(im, (0, banner_h))
    draw = ImageDraw.Draw(out)
    y = pad_v
    for i, line in enumerate(lines):
        bb = draw.textbbox((0, 0), line, font=font)
        lh = bb[3] - bb[1]
        cy = y + lh / 2.0
        draw.text(
            (w / 2.0, cy),
            line,
            font=font,
            fill=(0, 0, 0, 255),
            anchor="mm",
        )
        y += lh + (gap if i < len(lines) - 1 else 0)
    return out


def convert_for_gif(pil_image):
    # GIF は基本的にパレット画像。まず変換してから保存する。
    # colors=255 のままだと色が足りない場合があるが、ここでは簡便に ADAPTIVE。
    from PIL import Image

    img_rgb = pil_image.convert("RGB")
    return img_rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=255)


def write_mp4_ffmpeg(
    matches: list[Path],
    out_mp4: Path,
    duration_ms: int,
    *,
    title: str | None,
) -> None:
    """ffmpeg で H.264 MP4。無い／失敗時は SKIP ログのみ（例外は出さない）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("SKIP: ffmpeg not found in PATH (install ffmpeg to write MP4)")
        return
    if duration_ms <= 0:
        print("SKIP: duration-ms must be positive for MP4")
        return
    duration_sec = duration_ms / 1000.0
    out_mp4 = out_mp4.resolve()
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        def _save_frame_for_mp4(src: Path, dest: Path) -> None:
            im = Image.open(src).convert("RGBA")
            if (title or "").strip():
                im = add_top_title_banner(im, title or "")
            im.save(dest)

        if len(matches) == 1:
            with tempfile.TemporaryDirectory() as td:
                one = Path(td) / "frame.png"
                _save_frame_for_mp4(matches[0], one)
                cmd = [
                    ffmpeg,
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(one),
                    "-t",
                    str(duration_sec),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(out_mp4),
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        else:
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                for i, src in enumerate(matches):
                    _save_frame_for_mp4(src, td_path / f"frame_{i:05d}.png")
                fps = 1000.0 / duration_ms
                cmd = [
                    ffmpeg,
                    "-y",
                    "-framerate",
                    str(fps),
                    "-i",
                    str(td_path / "frame_%05d.png"),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(out_mp4),
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            tail = err[-800:] if err else "(no stderr)"
            print(f"SKIP: ffmpeg failed to write mp4: {tail}")
            return
        print(f"Wrote MP4 -> {out_mp4} (frames={len(matches)}, duration_ms={duration_ms})")
    except subprocess.TimeoutExpired:
        print("SKIP: ffmpeg timed out while writing mp4")
    except Exception as e:
        print(f"SKIP: failed to generate mp4: {e}")


def make_gif(
    *,
    pattern: str,
    output: Path,
    duration_ms: int,
    loop: int,
    also_mp4: bool,
    output_mp4: Path | None,
    mp4_duration_ms: int | None,
    title: str,
) -> int:
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

    matches = sorted_paths_by_timestamp(matches)
    display_title = augment_title_with_png_date_range(title, matches)

    try:
        first = Image.open(matches[0]).convert("RGBA")
        frames = [first]
        for p in matches[1:]:
            img = Image.open(p).convert("RGBA")
            frames.append(img)

        if (display_title or "").strip():
            frames = [add_top_title_banner(im, display_title) for im in frames]

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
    except Exception as e:
        # 失敗しても落とさない（ログだけ出して 0）
        print(f"SKIP: failed to generate gif: {e}")

    if also_mp4:
        mp4_path = (output_mp4 or output.with_suffix(".mp4")).resolve()
        mp4_ms = mp4_duration_ms if mp4_duration_ms is not None else duration_ms
        write_mp4_ffmpeg(matches, mp4_path, mp4_ms, title=display_title)

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
    p.add_argument(
        "--also-mp4",
        action="store_true",
        help="GIF に加え、同じフレーム列で MP4 も書く（ffmpeg 必須。無ければ SKIP）",
    )
    p.add_argument(
        "--output-mp4",
        type=Path,
        default=None,
        help="MP4 の出力パス（省略時は GIF と同じ stem の .mp4）",
    )
    p.add_argument(
        "--mp4-duration-ms",
        type=int,
        default=None,
        metavar="MS",
        help="MP4 の 1 フレームあたりの表示時間(ms)。省略時は --duration-ms と同じ",
    )
    p.add_argument(
        "--no-title",
        action="store_true",
        help="上部の白帯タイトルを付けない（GIF/MP4 とも）",
    )
    p.add_argument(
        "--title",
        type=str,
        default=None,
        metavar="TEXT",
        help="上部タイトル（改行は \\n）。省略時はペルシャ湾向けの既定文",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.no_title:
        title_str = ""
    elif args.title is not None:
        title_str = args.title.replace("\\n", "\n")
    else:
        title_str = DEFAULT_TITLE

    if args.pattern.strip():
        pattern = args.pattern.strip()
        if args.output is None:
            # 例: ship_moved/persian_*.png -> ship_moved/persian.gif
            stem = Path(pattern).name.replace("*", "").replace(".png", "")
            output = Path(pattern).parent / f"{stem}.gif"
        else:
            output = args.output
        return make_gif(
            pattern=pattern,
            output=output,
            duration_ms=args.duration_ms,
            loop=args.loop,
            also_mp4=args.also_mp4,
            output_mp4=args.output_mp4,
            mp4_duration_ms=args.mp4_duration_ms,
            title=title_str,
        )

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
        rc = make_gif(
            pattern=pattern,
            output=output,
            duration_ms=args.duration_ms,
            loop=args.loop,
            also_mp4=args.also_mp4,
            output_mp4=None,
            mp4_duration_ms=args.mp4_duration_ms,
            title=title_str,
        )
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

