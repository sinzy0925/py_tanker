"""
moved_report_01.json の船位置を Google Maps Static API で地図画像にする。

  set GOOGLE_MAPS_API_KEY=...   （または .env に記載）
  python cdp6_google-maps.py
  python cdp6_google-maps.py --input ship_moved/moved_report_01.json --output ship_moved/map.png

MOVED の行は、latlon_rounded_history（または prev→curr）の古い位置から現在まで線を引く（--no-tracks で無効）。

カスタムピン（例: ship_moved/tanker.png）を使うには、Google が取得できる公開 HTTPS URL が必要（--marker-icon-url）。
ローカルファイルのままでは不可。GitHub raw / 自サイト / オブジェクトストレージ等に置く。

Static Maps の利用制限・課金は Google のドキュメントを参照してください。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

DEFAULT_REPORT = Path("ship_moved") / "moved_report_01.json"
DEFAULT_OUTPUT = Path("ship_moved") / "static_map.png"
STATIC_MAP_BASE = "https://maps.googleapis.com/maps/api/staticmap"

# マーカーを色分け（Static API の color:0xRRGGBB）
MARKER_COLORS = (
    "0xE53935",
    "0x1E88E5",
    "0x43A047",
    "0xFB8C00",
    "0x8E24AA",
    "0x00ACC1",
    "0x6D4C41",
    "0x546E7A",
    "0xFDD835",
    "0x3949AB",
    "0xD81B60",
    "0x00897B",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="moved_report JSON → Google Static Map PNG")
    p.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"cdp5 のレポート JSON（既定: {DEFAULT_REPORT}）",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"出力 PNG（既定: {DEFAULT_OUTPUT}）",
    )
    p.add_argument(
        "--api-key",
        default="",
        help="Google Maps API キー（省略時は環境変数 GOOGLE_MAPS_API_KEY）",
    )
    p.add_argument("--width", type=int, default=640, help="画像幅（最大 640 の倍数推奨）")
    p.add_argument("--height", type=int, default=640, help="画像高さ")
    p.add_argument("--scale", type=int, default=2, choices=(1, 2), help="2 で高解像度（課金区分に注意）")
    p.add_argument(
        "--maptype",
        choices=("roadmap", "satellite", "hybrid", "terrain"),
        default="hybrid",
        help="地図タイプ",
    )
    p.add_argument(
        "--moved-only",
        action="store_true",
        help="moved が true の行だけマーカーする",
    )
    p.add_argument(
        "--no-tracks",
        action="store_true",
        help="MOVED の古い位置→現在の線を引かない（マーカーのみ）",
    )
    p.add_argument(
        "--marker-icon-url",
        default="",
        metavar="HTTPS_URL",
        help="全マーカーに使う画像の公開 HTTPS URL（PNG/JPG。Static API が取得可能なこと）",
    )
    p.add_argument(
        "--marker-icon-anchor",
        default="bottom",
        help="カスタムアイコンの anchor（例: bottom, center, 16,16）",
    )
    p.add_argument(
        "--language",
        default="ja",
        help="地図ラベル言語（例: ja, en）",
    )
    return p.parse_args()


def _pair_float(seq: Any) -> tuple[float, float] | None:
    if not isinstance(seq, (list, tuple)) or len(seq) < 2:
        return None
    try:
        return float(seq[0]), float(seq[1])
    except (TypeError, ValueError):
        return None


def row_position(row: dict[str, Any]) -> tuple[float, float] | None:
    """マーカー用座標: curr_latlon_rounded を優先、無ければ latlon_rounded_history の最終点。"""
    p = _pair_float(row.get("curr_latlon_rounded"))
    if p is not None:
        return p
    hist = row.get("latlon_rounded_history")
    if isinstance(hist, list) and hist:
        return _pair_float(hist[-1])
    return None


def moved_track_endpoints(row: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """
    MOVED 行について (古い点, 現在点)。latlon_rounded_history が [最古,最新] ならそれを使用。
    無ければ prev_latlon_rounded と curr_latlon_rounded。
    同一座標なら None。
    """
    hist = row.get("latlon_rounded_history")
    if isinstance(hist, list) and len(hist) >= 2:
        a = _pair_float(hist[0])
        b = _pair_float(hist[-1])
        if a and b and a != b:
            return (a, b)
    a = _pair_float(row.get("prev_latlon_rounded"))
    b = _pair_float(row.get("curr_latlon_rounded"))
    if a and b and a != b:
        return (a, b)
    return None


def load_points_and_tracks(
    path: Path, moved_only: bool, draw_tracks: bool
) -> tuple[list[tuple[str, str, float, float]], list[tuple[tuple[float, float], tuple[float, float]]]]:
    """マーカー用 (ship_id, name, lat, lon) と、軌跡 [(始点), (終点)] のリスト。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("rows")
    if not isinstance(rows, list):
        print("ERROR: JSON に rows 配列がありません", file=sys.stderr)
        raise SystemExit(1)
    points: list[tuple[str, str, float, float]] = []
    tracks: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if moved_only and not row.get("moved"):
            continue
        pos = row_position(row)
        if pos is None:
            continue
        lat, lon = pos
        sid = str(row.get("ship_id") or "").strip()
        name = str(row.get("ship_name") or "").strip() or sid
        points.append((sid, name, lat, lon))
        if draw_tracks and row.get("moved"):
            seg = moved_track_endpoints(row)
            if seg is not None:
                tracks.append(seg)
    return points, tracks


def marker_label_char(index: int) -> str:
    """Static API はラベル1文字（A-Z, 0-9）。A〜Z を循環。"""
    return chr(ord("A") + (index % 26))


def _encode_icon_url_for_marker(url: str) -> str:
    """icon: 以降に & 等が含まれても壊れないよう URL をエンコード。"""
    return quote(url, safe="")


def build_static_map_url(
    *,
    api_key: str,
    points: list[tuple[str, str, float, float]],
    tracks: list[tuple[tuple[float, float], tuple[float, float]]],
    width: int,
    height: int,
    scale: int,
    maptype: str,
    language: str,
    marker_icon_url: str,
    marker_icon_anchor: str,
) -> str:
    # path → markers → visible（範囲に線の端点も含める）
    parts: list[tuple[str, str]] = [
        ("size", f"{width}x{height}"),
        ("scale", str(scale)),
        ("maptype", maptype),
        ("language", language),
        ("key", api_key),
    ]
    for ti, ((la1, lo1), (la2, lo2)) in enumerate(tracks):
        col = MARKER_COLORS[ti % len(MARKER_COLORS)]
        # weight|color|lat1,lon1|lat2,lon2
        path_spec = (
            f"weight:3|color:{col}|{la1:.6f},{lo1:.6f}|{la2:.6f},{lo2:.6f}"
        )
        parts.append(("path", path_spec))
    use_icon = bool(marker_icon_url.strip())
    for i, (_sid, _name, lat, lon) in enumerate(points):
        if use_icon:
            enc = _encode_icon_url_for_marker(marker_icon_url.strip())
            anc = marker_icon_anchor.strip() or "bottom"
            marker_spec = f"anchor:{anc}|icon:{enc}|{lat:.6f},{lon:.6f}"
        else:
            col = MARKER_COLORS[i % len(MARKER_COLORS)]
            ch = marker_label_char(i)
            marker_spec = f"color:{col}|label:{ch}|{lat:.6f},{lon:.6f}"
        parts.append(("markers", marker_spec))
    vis: set[tuple[float, float]] = set()
    for _sid, _name, lat, lon in points:
        vis.add((round(lat, 6), round(lon, 6)))
    for (la1, lo1), (la2, lo2) in tracks:
        vis.add((round(la1, 6), round(lo1, 6)))
        vis.add((round(la2, 6), round(lo2, 6)))
    for lat, lon in vis:
        parts.append(("visible", f"{lat:.6f},{lon:.6f}"))

    return f"{STATIC_MAP_BASE}?{urlencode(parts)}"


def fetch_png(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "cdp6_google-maps.py (py_tanker)"})
    with urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> int:
    args = parse_args()
    api_key = (args.api_key or os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    if not api_key:
        print(
            "ERROR: Google Maps API キーがありません。"
            " 環境変数 GOOGLE_MAPS_API_KEY を設定するか --api-key を付けてください。",
            file=sys.stderr,
        )
        return 1

    if not args.input.is_file():
        print(f"ERROR: 入力がありません: {args.input}", file=sys.stderr)
        return 1

    draw_tracks = not args.no_tracks
    points, tracks = load_points_and_tracks(args.input, args.moved_only, draw_tracks)
    if not points:
        print("ERROR: マーカーする行がありません（--moved-only で絞りすぎていませんか）", file=sys.stderr)
        return 1

    icon_url = (args.marker_icon_url or "").strip()
    if icon_url and not (icon_url.startswith("https://") or icon_url.startswith("http://")):
        print(
            "ERROR: --marker-icon-url は http(s):// で始まる公開 URL を指定してください。"
            " ローカルの tanker.png はそのまま使えません（GitHub raw 等に置いて URL を渡す）。",
            file=sys.stderr,
        )
        return 1

    url = build_static_map_url(
        api_key=api_key,
        points=points,
        tracks=tracks,
        width=args.width,
        height=args.height,
        scale=args.scale,
        maptype=args.maptype,
        language=args.language,
        marker_icon_url=icon_url,
        marker_icon_anchor=args.marker_icon_anchor,
    )

    if len(url) > 8192:
        print(
            f"WARNING: URL が {len(url)} 文字です。マーカーが多いと Static API の上限に抵触する場合があります。",
            file=sys.stderr,
        )

    try:
        data = fetch_png(url)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        print(f"ERROR: HTTP {e.code} {e.reason}\n{body[:2000]}", file=sys.stderr)
        return 1
    except URLError as e:
        print(f"ERROR: {e.reason}", file=sys.stderr)
        return 1

    if not data.startswith(b"\x89PNG"):
        text = data.decode("utf-8", errors="replace")[:2000]
        print(f"ERROR: PNG でない応答です（API エラーの可能性）:\n{text}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    extra_icon = f", custom_icon=1" if icon_url else ""
    print(
        f"Wrote {args.output} ({len(data)} bytes), markers={len(points)}, tracks={len(tracks)}{extra_icon}"
    )
    for i, (sid, name, lat, lon) in enumerate(points):
        print(f"  [{marker_label_char(i)}] {name} ({sid}) {lat:.5f},{lon:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
