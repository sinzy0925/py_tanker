"""
moved_report_01.json の船位置を Google Maps Static API で地図画像にする。

  set GOOGLE_MAPS_API_KEY=...   （または .env に記載）
  python cdp6_google-maps.py
  python cdp6_google-maps.py --input ship_moved/moved_report_01.json
  （出力省略時は ship_moved/map_yyyymmdd_hhmmss.png。毎回上書きは --output ship_moved/map.png）

MOVED の行は、latlon_rounded_history（または prev→curr）の古い位置から現在まで線を引く（--no-tracks で無効）。

マーカーは API のピンではなく、取得した地図上にバッジを描画する。
cdp5 が付けた type_letter（O/L/P/C）があるときは上段に1文字、下段に船名の先頭 2 文字（例: TOWADA → TO）。
型が無いレポートでは従来どおり船名先頭2文字のみ。
同一座標が複数ある場合は円状にずらし、アンカー船の赤丸内に「O」「TO」「×3」のように複行で表示する。

ホルムズ海峡を右上付近に載せたい場合は --hormuz-frame（縮尺は広がる）。
左端（西側の枠）はデータのまま、右だけ海峡付近まで広げる場合は --extend-east-hormuz（--hormuz-frame より優先）。

出力 PNG の右下にローカル時刻の実行日時を入れる（--no-run-timestamp でオフ）。

Static Maps の利用制限・課金は Google のドキュメントを参照してください。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

DEFAULT_REPORT = Path("ship_moved") / "moved_report_01.json"
DEFAULT_OUTPUT_DIR = Path("ship_moved")
STATIC_MAP_BASE = "https://maps.googleapis.com/maps/api/staticmap"

# cdp5 / out.jsonl と同じ GT_SHIPTYPE → 1 文字（type_letter 無しの古いレポート用）
GT_SHIPTYPE_TO_LETTER: dict[str, str] = {
    "17": "O",
    "18": "L",
    "71": "P",
    "88": "C",
}

# MOVED 軌跡の線（path）。衛星図でも見やすい濃い黄色・やや太め（weight は API 上限内で調整可）
# color は必ず 0xRRGGBB（"FFFFFF" だけだと API が別解釈し色がずれる）
TRACK_PATH_WEIGHT = 6
TRACK_PATH_COLOR = "0xFFFFFF"  # 白。黄色の例: 0xFFEB3B

# ホルムズ海峡付近（枠取り用・概ね海峡東口）
HORMUZ_FRAMING_LAT = 26.55
HORMUZ_FRAMING_LON = 56.42
# --extend-east-hormuz 時の東端（経度）。海峡が枠に入るよう少し東へ
HORMUZ_EXTEND_EAST_LON = 56.58

# バンダレ・レンゲ付近（キャプション「ホルムズ海峡↗」の基準点）
BANDAR_LENGEH_LAT = 26.558
BANDAR_LENGEH_LON = 54.880

HORMUZ_CAPTION_TEXT = "ホルムズ海峡↗"

# 船名略号バッジ（船ごとに色分け / 似た色を避ける高コントラスト）
BADGE_COLOR_PALETTE: tuple[tuple[int, int, int], ...] = (
    (230, 57, 70),   # vivid red
    (29, 78, 216),   # vivid blue
    (5, 150, 105),   # vivid green
    (234, 88, 12),   # vivid orange
    (124, 58, 237),  # vivid violet
    (20, 184, 166),  # vivid teal
    (217, 119, 6),   # amber
    (190, 24, 93),   # magenta
    (14, 116, 144),  # cyan
    (132, 204, 22),  # lime
    (127, 29, 29),   # dark red
    (22, 101, 52),   # dark green
    (30, 64, 175),   # indigo
    (109, 40, 217),  # purple
    (8, 145, 178),   # sky
    (202, 138, 4),   # yellow brown
)

# 同一座標の船を円状に分離するときの半径（度）。n に応じてやや拡大
_OVERLAP_JITTER_BASE_DEG = 0.0032

# 海域フィルタ（概略の枠。必要なら後で微調整）
REGION_BOUNDS: dict[str, dict[str, float]] = {
    "persian_gulf": {"south": 22.0, "north": 31.5, "west": 47.0, "east": 59.5},
    "red_sea": {"south": 10.0, "north": 31.5, "west": 31.0, "east": 45.5},
}

# --region persian_gulf の表示枠（見た目調整用）
PERSIAN_GULF_VIEW_WEST_LON = 49
PERSIAN_GULF_VIEW_EAST_LON = 57.2#56.2
# --region persian_gulf のときに追加で寄せるズーム段数（+1 で少し拡大）
PERSIAN_GULF_ZOOM_DELTA = 1
# --region red_sea のときに追加で寄せるズーム段数（+1 で少し拡大）
RED_SEA_ZOOM_DELTA = 1


def _static_map_path_color(spec: str) -> str:
    """Static Maps の path color は 0xRRGGBB。6桁のみなら 0x を付与（付けないと意図しない色になる）。"""
    s = spec.strip()
    if s[:2].lower() == "0x":
        return s
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return "0x" + s.upper()
    return s


def _badge_colors(rgb: tuple[int, int, int]) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """RGB から塗り色/枠色を作る。"""
    r, g, b = rgb
    fill = (r, g, b, 255)
    outline = (max(0, int(r * 0.58)), max(0, int(g * 0.58)), max(0, int(b * 0.58)), 255)
    return fill, outline


def _build_ship_color_map(points: list[tuple[str, str, float, float, str]]) -> dict[str, tuple[int, int, int]]:
    """
    今回出現する船に対して、似た色が連続しにくい順で色を割り当てる。
    ship_id の同一性は維持しつつ、見た目の偏り（紫/緑が多い等）を抑える。
    """
    n = len(BADGE_COLOR_PALETTE)
    # パレットを飛び飛びで使う順序（16色に対して step=7 は互いに素）
    order = [(i * 7) % n for i in range(n)]
    ship_ids = sorted({(sid or "").strip() for sid, _nm, _la, _lo, _tl in points})
    out: dict[str, tuple[int, int, int]] = {}
    for i, sid in enumerate(ship_ids):
        out[sid] = BADGE_COLOR_PALETTE[order[i % n]]
    return out


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
        default=None,
        help=(
            f"出力 PNG（省略時: {DEFAULT_OUTPUT_DIR}/"
            "map_yyyymmdd_hhmmss.png, "
            "persian_yyyymmdd_hhmmss.png, "
            "red_yyyymmdd_hhmmss.png）"
        ),
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
        "--language",
        default="ja",
        help="地図ラベル言語（例: ja, en）",
    )
    p.add_argument(
        "--region",
        choices=("all", "persian_gulf", "red_sea"),
        default="all",
        help="海域で絞り込み（all: 全件 / persian_gulf: ペルシャ湾 / red_sea: 紅海）",
    )
    p.add_argument(
        "--region-west-lon",
        type=float,
        default=None,
        help="表示枠の西端経度を上書き（--region persian_gulf 時に有効）",
    )
    p.add_argument(
        "--region-east-lon",
        type=float,
        default=None,
        help="表示枠の東端経度を上書き（--region persian_gulf 時に有効）",
    )
    p.add_argument(
        "--hormuz-frame",
        action="store_true",
        help="ホルムズ海峡を枠に含め右上寄りに見せる（既定オフ＝先の縮尺・船データ中心）",
    )
    p.add_argument(
        "--extend-east-hormuz",
        action="store_true",
        help="西側の枠（データの西端＋余白）はそのまま、東側だけホルムズ海峡付近まで広げる（--hormuz-frame よりこちらを優先）",
    )
    p.add_argument(
        "--no-hormuz-caption",
        action="store_true",
        help="「ホルムズ海峡↗」注記を付けない",
    )
    p.add_argument(
        "--no-run-timestamp",
        action="store_true",
        help="画像右下への実行日時（ローカル）を入れない",
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


def _point_in_region(lat: float, lon: float, region: str) -> bool:
    if region == "all":
        return True
    b = REGION_BOUNDS.get(region)
    if not b:
        return True
    return b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]


def _forced_bounds_for_region_view(
    region: str,
    points: list[tuple[str, str, float, float, str]],
    tracks: list[tuple[tuple[float, float], tuple[float, float]]],
    *,
    west_override: float | None = None,
    east_override: float | None = None,
) -> dict[str, float] | None:
    """
    海域ごとの表示枠を返す。
    - red_sea: 海域固定枠で安定表示
    - persian_gulf: 左端をクウェート付近、右端を KM 右の半島先端の少し右に寄せる
    """
    if region == "red_sea":
        return REGION_BOUNDS["red_sea"].copy()
    if region == "persian_gulf":
        b = _compute_bounds_with_padding(
            points,
            tracks,
            pad=0.08,
            include_hormuz=False,
            bias_hormuz_top_right=False,
        )
        # 左右の見切り位置を固定（縦はデータに追従）
        b["west"] = west_override if west_override is not None else PERSIAN_GULF_VIEW_WEST_LON
        b["east"] = east_override if east_override is not None else PERSIAN_GULF_VIEW_EAST_LON
        return b
    return None


def type_letter_from_row(row: dict[str, Any]) -> str:
    """cdp5 の type_letter、または gt_shiptype から 1 文字（無ければ空）。"""
    tl = str(row.get("type_letter") or "").strip()
    if tl:
        return tl[:1]
    gt = str(row.get("gt_shiptype") or "").strip()
    if gt:
        return (GT_SHIPTYPE_TO_LETTER.get(gt) or "")[:1]
    return ""


def load_points_and_tracks(
    path: Path, moved_only: bool, draw_tracks: bool, region: str
) -> tuple[list[tuple[str, str, float, float, str]], list[tuple[tuple[float, float], tuple[float, float]]]]:
    """マーカー用 (ship_id, name, lat, lon, type_letter) と、軌跡 [(始点), (終点)] のリスト。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("rows")
    if not isinstance(rows, list):
        print("ERROR: JSON に rows 配列がありません", file=sys.stderr)
        raise SystemExit(1)
    points: list[tuple[str, str, float, float, str]] = []
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
        if not _point_in_region(lat, lon, region):
            continue
        sid = str(row.get("ship_id") or "").strip()
        name = str(row.get("ship_name") or "").strip() or sid
        tl = type_letter_from_row(row)
        points.append((sid, name, lat, lon, tl))
        if draw_tracks and row.get("moved"):
            seg = moved_track_endpoints(row)
            if seg is not None:
                (la1, lo1), (la2, lo2) = seg
                if _point_in_region(la1, lo1, region) or _point_in_region(la2, lo2, region):
                    tracks.append(seg)
    return points, tracks


def name_prefix2(name: str, ship_id: str) -> str:
    """船名の先頭 2 文字（短い名前はそのまま・空なら ship_id を利用）。"""
    s = (name or "").strip()
    if len(s) >= 2:
        return s[:2]
    if s:
        return s
    s2 = (ship_id or "").strip()
    if len(s2) >= 2:
        return s2[:2]
    return s2 or "?"


def badge_sort_key(sid: str, name: str, type_letter: str) -> tuple[str, str, str]:
    """同一座標クラスタのアンカー選定用（型→船名略→ship_id）。"""
    tl = (type_letter or "").strip()
    return (tl, name_prefix2(name, sid), sid)


def _badge_line_font_specs(
    type_letter: str,
    name: str,
    sid: str,
    cluster_n: int | None,
    font: Any,
    font_hint: Any,
) -> list[tuple[str, Any]]:
    """バッジの行 (テキスト, フォント)。上から順。"""
    specs: list[tuple[str, Any]] = []
    tl = (type_letter or "").strip()[:1]
    if tl:
        specs.append((tl, font))
    specs.append((name_prefix2(name, sid), font))
    if cluster_n is not None and cluster_n >= 2:
        specs.append((f"×{cluster_n}", font_hint))
    return specs


def split_overlapping_points(
    points: list[tuple[str, str, float, float, str]],
) -> tuple[list[tuple[str, str, float, float, str]], dict[int, int]]:
    """
    同一 (lat,lon) の船を円状にずらした表示用座標を返す。
    重複があるとき略号が辞書順で最小の船は index → 隻数 n（その1個の赤丸内に「略号」「×n」を縦に入れる）。
    """
    groups: dict[tuple[float, float], list[int]] = defaultdict(list)
    for i, p in enumerate(points):
        lat, lon = p[2], p[3]
        key = (round(lat, 6), round(lon, 6))
        groups[key].append(i)

    positions: list[tuple[float, float]] = [(p[2], p[3]) for p in points]  # lat, lon
    anchor_cluster: dict[int, int] = {}

    for _key, indices in groups.items():
        n = len(indices)
        if n < 2:
            continue
        lat0, lon0 = _key
        radius_deg = _OVERLAP_JITTER_BASE_DEG * (1.0 + 0.28 * max(0, n - 2))
        cos_lat = max(0.35, math.cos(math.radians(lat0)))
        order = sorted(indices, key=lambda i: (points[i][0], points[i][1]))
        for j, idx in enumerate(order):
            theta = 2.0 * math.pi * j / n - math.pi / 2.0
            dlat = radius_deg * math.cos(theta)
            dlon = radius_deg * math.sin(theta) / cos_lat
            positions[idx] = (lat0 + dlat, lon0 + dlon)

        anchor_idx = min(
            indices,
            key=lambda i: badge_sort_key(points[i][0], points[i][1], points[i][4]),
        )
        anchor_cluster[anchor_idx] = n

    display = [
        (points[i][0], points[i][1], positions[i][0], positions[i][1], points[i][4])
        for i in range(len(points))
    ]
    return display, anchor_cluster


def _compute_bounds_with_padding(
    points: list[tuple[str, str, float, float, str]],
    tracks: list[tuple[tuple[float, float], tuple[float, float]]],
    pad: float,
    *,
    include_hormuz: bool = False,
    bias_hormuz_top_right: bool = False,
) -> dict[str, float]:
    lats: list[float] = []
    lons: list[float] = []
    for _a, _b, lat, lon, _tl in points:
        lats.append(lat)
        lons.append(lon)
    for (la1, lo1), (la2, lo2) in tracks:
        lats.extend([la1, la2])
        lons.extend([lo1, lo2])
    if include_hormuz:
        lats.append(HORMUZ_FRAMING_LAT)
        lons.append(HORMUZ_FRAMING_LON)
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    if lat_max - lat_min < 1e-6:
        lat_min -= 0.05
        lat_max += 0.05
    if lon_max - lon_min < 1e-6:
        lon_min -= 0.05
        lon_max += 0.05
    lat_span = lat_max - lat_min
    lon_span = lon_max - lon_min
    if bias_hormuz_top_right:
        # 南西に余白を多め＝視野中心がやや南西になり、海峡が北東（画面上で右上）寄りに見える
        return {
            "south": lat_min - lat_span * pad * 1.55,
            "north": lat_max + lat_span * pad * 0.5,
            "west": lon_min - lon_span * pad * 1.45,
            "east": lon_max + lon_span * pad * 0.42,
        }
    return {
        "south": lat_min - lat_span * pad,
        "north": lat_max + lat_span * pad,
        "west": lon_min - lon_span * pad,
        "east": lon_max + lon_span * pad,
    }


def _compute_bounds_extend_east_hormuz(
    points: list[tuple[str, str, float, float, str]],
    tracks: list[tuple[tuple[float, float], tuple[float, float]]],
    pad: float,
) -> dict[str, float]:
    """
    船データのみで通常の枠を作り、西・南・北はそのまま、東だけホルムズ付近まで延ばす。
    """
    b = _compute_bounds_with_padding(
        points,
        tracks,
        pad,
        include_hormuz=False,
        bias_hormuz_top_right=False,
    )
    b["east"] = max(b["east"], HORMUZ_EXTEND_EAST_LON)
    return b


def _zoom_for_bounds(bounds: dict[str, float], map_width: int, map_height: int) -> int:
    """複数 visible と近い枠取りのための zoom（論理サイズ map_width x map_height 基準）。"""
    world_dim = 256
    zoom_max = 21

    def lat_rad(lat: float) -> float:
        s = math.sin(lat * math.pi / 180.0)
        r = math.log((1.0 + s) / (1.0 - s)) / 2.0
        return max(min(r, math.pi), -math.pi) / 2.0

    def zoom_dim(map_px: float, fraction: float) -> int:
        if fraction <= 1e-12:
            return zoom_max
        z = math.floor(math.log(map_px / world_dim / fraction) / math.log(2.0))
        return max(0, min(z, zoom_max))

    lat_fr = (lat_rad(bounds["north"]) - lat_rad(bounds["south"])) / math.pi
    lon_d = bounds["east"] - bounds["west"]
    if lon_d < 0:
        lon_d += 360.0
    lon_fr = lon_d / 360.0

    return min(zoom_dim(map_height, lat_fr), zoom_dim(map_width, lon_fr))


def _latlng_to_world_px(lat: float, lng: float, zoom: int) -> tuple[float, float]:
    zf = 2.0**zoom
    x = (lng + 180.0) / 360.0 * zf * 256.0
    s = math.sin(math.radians(lat))
    s = min(max(s, -0.9999), 0.9999)
    y = (0.5 - math.log((1.0 + s) / (1.0 - s)) / (4.0 * math.pi)) * zf * 256.0
    return x, y


def _latlng_to_screen_xy(
    lat: float,
    lng: float,
    center_lat: float,
    center_lng: float,
    zoom: int,
    width: int,
    height: int,
    scale: int,
) -> tuple[float, float]:
    wx, wy = _latlng_to_world_px(lat, lng, zoom)
    wcx, wcy = _latlng_to_world_px(center_lat, center_lng, zoom)
    dx, dy = wx - wcx, wy - wcy
    sx = (width / 2.0 + dx) * scale
    sy = (height / 2.0 + dy) * scale
    return sx, sy


def _load_font_cjk(size: int, *, bold: bool = False) -> Any:
    """日本語表示用フォント（環境ごとの候補を試す）。bold 時は太字ファイルを優先。"""
    from PIL import ImageFont

    win = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
    bold_candidates = [
        os.path.join(win, "meiryob.ttc"),
        os.path.join(win, "yugothb.ttc"),
        os.path.join(win, "YuGothB.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    regular_candidates = [
        os.path.join(win, "meiryo.ttc"),
        os.path.join(win, "msgothic.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    candidates = bold_candidates if bold else regular_candidates
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    if bold:
        return _load_font_cjk(size, bold=False)
    return ImageFont.load_default()


def _draw_hormuz_caption_on_map(
    im: Any,
    *,
    center_lat: float,
    center_lng: float,
    zoom: int,
    width: int,
    height: int,
    scale: int,
) -> None:
    """バンダレ・レンゲ付近にホルムズ海峡の注記を描く（右下寄せ・太字・やや大きめ）。"""
    from PIL import ImageDraw

    sx, sy = _latlng_to_screen_xy(
        BANDAR_LENGEH_LAT,
        BANDAR_LENGEH_LON,
        center_lat,
        center_lng,
        zoom,
        width,
        height,
        scale,
    )
    pw, ph = width * scale, height * scale
    if not (-pw * 0.05 <= sx <= pw * 1.05 and -ph * 0.05 <= sy <= ph * 1.05):
        return

    # 以前より 2 段階ほど大きく（scale=2 で約 +4pt 相当になるよう上限も拡張）
    font_px = max(18, min(34, int(22 * scale)))
    font = _load_font_cjk(font_px, bold=True)
    draw = ImageDraw.Draw(im)
    text = HORMUZ_CAPTION_TEXT
    bx, by = float(sx), float(sy)
    by += 22.0 * float(scale) #22.0
    bx -= 4.0 * float(scale) #4.0 
    # 右下へずらす
    bx += 70.0 * float(scale) #42.0
    by -= 10.0 * float(scale) #32.0
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = int(round(bx - tw / 2))
    y = int(round(by))
    x = max(2, min(x, pw - tw - 2))
    y = max(2, min(y, ph - th - 2))
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=max(2, 3 * scale // 2),
        stroke_fill=(20, 30, 40, 255),
    )


def _draw_run_timestamp_bottom_right(im: Any, *, scale: int) -> None:
    """画像右下に実行日時（ローカル時刻）を描く。"""
    from PIL import ImageDraw

    pw, ph = im.size
    text = datetime.now().strftime("生成 %Y-%m-%d %H:%M:%S")
    font_px = max(13, min(24, int(14 * scale)))
    font = _load_font_cjk(font_px, bold=False)
    draw = ImageDraw.Draw(im)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    margin = max(6, 4 * scale)
    x = int(pw - tw - margin)
    y = int(ph - th - margin) - max(8, 16 * scale)  # 右下基準から少し上へ
    x = max(2, x)
    y = max(2, y)
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=max(1, 3 * scale // 2),
        stroke_fill=(0, 0, 0, 220),
    )


def _draw_legend_above_timestamp_bottom_right(im: Any, *, scale: int) -> None:
    """右下の実行日時の上に凡例を描く。"""
    from PIL import ImageDraw

    pw, ph = im.size
    ts_text = datetime.now().strftime("生成 %Y-%m-%d %H:%M:%S")
    ts_font_px = max(13, min(24, int(14 * scale)))
    ts_font = _load_font_cjk(ts_font_px, bold=False)
    draw = ImageDraw.Draw(im)
    ts_bbox = draw.textbbox((0, 0), ts_text, font=ts_font)
    ts_tw = ts_bbox[2] - ts_bbox[0]
    ts_th = ts_bbox[3] - ts_bbox[1]
    margin = max(6, 4 * scale)
    ts_x = int(pw - ts_tw - margin)
    ts_y = int(ph - ts_th - margin) - max(8, 16 * scale)

    lines = [
        "凡例:",
        "〇は船種(O/L/P/C)＋船名先頭2文字（cdp5 の型が無いときは2文字のみ）",
        "O=Oil L=LNG P=OilProducts C=OilChemical",
        "白線は船が移動したことを示す",
    ]
    legend_font_px = max(12, min(20, int(11 * scale)))
    legend_font = _load_font_cjk(legend_font_px, bold=False)
    lh = int(round(legend_font_px * 1.28))
    line_widths = [draw.textbbox((0, 0), s, font=legend_font)[2] for s in lines]
    box_w = max(line_widths) + max(14, 10 * scale)
    box_h = lh * len(lines) + max(10, 8 * scale)
    gap = max(8, 8 * scale)
    x0 = max(2, int(ts_x + ts_tw - box_w))
    y0 = max(2, int(ts_y - gap - box_h))
    x1 = min(pw - 2, x0 + box_w)
    y1 = min(ph - 2, y0 + box_h)

    # 可読性を上げるため半透明の白背景を敷く（文字は黒）
    draw.rounded_rectangle(
        [x0, y0, x1, y1],
        radius=max(6, 5 * scale),
        fill=(255, 255, 255, 255),
        outline=(0, 0, 0, 110),
        width=1,
    )

    tx = x0 + max(7, 5 * scale)
    ty = y0 + max(5, 4 * scale)
    for s in lines:
        draw.text(
            (tx, ty),
            s,
            font=legend_font,
            fill=(0, 0, 0, 255),
            stroke_width=max(1, scale // 3),
            stroke_fill=(0, 0, 0, 210),
        )
        ty += lh


def build_static_map_url_overlay(
    *,
    api_key: str,
    points: list[tuple[str, str, float, float, str]],
    tracks: list[tuple[tuple[float, float], tuple[float, float]]],
    width: int,
    height: int,
    scale: int,
    maptype: str,
    language: str,
    frame_hormuz: bool = False,
    extend_east_hormuz: bool = False,
    forced_bounds: dict[str, float] | None = None,
    zoom_delta: int = 0,
) -> tuple[str, float, float, int]:
    """マーカーなし（後で PIL 合成）。center + zoom で枠取り。"""
    if forced_bounds is not None:
        bounds = forced_bounds.copy()
    elif extend_east_hormuz:
        bounds = _compute_bounds_extend_east_hormuz(points, tracks, pad=0.12)
    else:
        bounds = _compute_bounds_with_padding(
            points,
            tracks,
            pad=0.12,
            include_hormuz=frame_hormuz,
            bias_hormuz_top_right=frame_hormuz,
        )
    center_lat = (bounds["south"] + bounds["north"]) / 2.0
    center_lng = (bounds["west"] + bounds["east"]) / 2.0
    zoom = _zoom_for_bounds(bounds, width, height)
    if zoom_delta:
        zoom = max(0, min(21, zoom + zoom_delta))
    parts: list[tuple[str, str]] = [
        ("size", f"{width}x{height}"),
        ("scale", str(scale)),
        ("maptype", maptype),
        ("language", language),
        ("key", api_key),
        ("center", f"{center_lat:.6f},{center_lng:.6f}"),
        ("zoom", str(zoom)),
    ]
    for (la1, lo1), (la2, lo2) in tracks:
        path_spec = (
            f"weight:{TRACK_PATH_WEIGHT}|color:{_static_map_path_color(TRACK_PATH_COLOR)}|"
            f"{la1:.6f},{lo1:.6f}|{la2:.6f},{lo2:.6f}"
        )
        parts.append(("path", path_spec))
    return f"{STATIC_MAP_BASE}?{urlencode(parts)}", center_lat, center_lng, zoom


def _draw_name_labels_on_map(
    im: Any,
    points: list[tuple[str, str, float, float, str]],
    *,
    anchor_cluster: dict[int, int] | None = None,
    center_lat: float,
    center_lng: float,
    zoom: int,
    width: int,
    height: int,
    scale: int,
) -> None:
    """各船位置の中央にバッジを描く。type_letter があるときは上段に O/L/P/C、下段に船名先頭2文字。重複アンカーは ×n を最下段に。"""
    from PIL import ImageDraw

    anchor_cluster = anchor_cluster or {}
    # 以前より一段階小さめ（scale=2 で約 12〜13pt 相当）
    font_px = max(10, min(20, int(12 * scale)))
    font = _load_font_cjk(font_px, bold=True)
    hint_font_px = max(8, min(15, int(9 * scale)))
    font_hint = _load_font_cjk(hint_font_px, bold=True)
    draw = ImageDraw.Draw(im)
    ow = max(1, scale // 2)
    ship_color_map = _build_ship_color_map(points)
    gap_lines = max(1, scale // 2)
    for i, (sid, name, lat, lon, tl) in enumerate(points):
        sx, sy = _latlng_to_screen_xy(
            lat, lon, center_lat, center_lng, zoom, width, height, scale
        )
        rgb = ship_color_map.get((sid or "").strip(), BADGE_COLOR_PALETTE[0])
        fill_col, outline_col = _badge_colors(rgb)
        n_dup = anchor_cluster.get(i)
        pad = max(3.0, 2.5 * scale)
        line_specs = _badge_line_font_specs(tl, name, sid, n_dup, font, font_hint)
        heights: list[float] = []
        widths: list[float] = []
        for text, fnt in line_specs:
            bb = draw.textbbox((0, 0), text, font=fnt)
            heights.append(float(bb[3] - bb[1]))
            widths.append(float(bb[2] - bb[0]))
        total_h = sum(heights) + gap_lines * max(0, len(line_specs) - 1)
        total_w = max(widths) if widths else 0.0
        r = int(math.ceil(math.hypot(total_w / 2.0, total_h / 2.0) + pad))
        x0, y0 = int(round(sx - r)), int(round(sy - r))
        x1, y1 = int(round(sx + r)), int(round(sy + r))
        draw.ellipse(
            [x0, y0, x1, y1],
            fill=fill_col,
            outline=outline_col,
            width=ow,
        )
        y_top = sy - total_h / 2.0
        y_acc = y_top
        for j, (text, fnt) in enumerate(line_specs):
            h = heights[j]
            cy = y_acc + h / 2.0
            draw.text(
                (sx, cy),
                text,
                font=fnt,
                fill=(255, 255, 255, 255),
                anchor="mm",
            )
            y_acc += h + (gap_lines if j < len(line_specs) - 1 else 0.0)


def fetch_png(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "cdp6_google-maps.py (py_tanker)"})
    with urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> int:
    args = parse_args()
    if args.output is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.region == "persian_gulf":
            args.output = DEFAULT_OUTPUT_DIR / f"persian_{ts}.png"
        elif args.region == "red_sea":
            args.output = DEFAULT_OUTPUT_DIR / f"red_{ts}.png"
        else:
            args.output = DEFAULT_OUTPUT_DIR / f"map_{ts}.png"
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
    points, tracks = load_points_and_tracks(args.input, args.moved_only, draw_tracks, args.region)
    if not points:
        # region 指定や moved_only で対象が 0 件のときは「何もしない」。
        # 呼び出し側（cdp0 など）で try/無視しなくても処理が止まらないようにする。
        return 0

    display_points, anchor_cluster = split_overlapping_points(points)

    try:
        from PIL import Image
    except ImportError:
        print(
            "ERROR: 船名ラベル合成には Pillow が必要です: pip install pillow",
            file=sys.stderr,
        )
        return 1

    url, clat, clng, zoom = build_static_map_url_overlay(
        api_key=api_key,
        points=display_points,
        tracks=tracks,
        width=args.width,
        height=args.height,
        scale=args.scale,
        maptype=args.maptype,
        language=args.language,
        frame_hormuz=args.hormuz_frame,
        extend_east_hormuz=args.extend_east_hormuz,
        forced_bounds=_forced_bounds_for_region_view(
            args.region,
            display_points,
            tracks,
            west_override=args.region_west_lon,
            east_override=args.region_east_lon,
        ),
        zoom_delta=(
            PERSIAN_GULF_ZOOM_DELTA
            if args.region == "persian_gulf"
            else RED_SEA_ZOOM_DELTA if args.region == "red_sea" else 0
        ),
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

    im = Image.open(BytesIO(data)).convert("RGBA")
    _draw_name_labels_on_map(
        im,
        display_points,
        anchor_cluster=anchor_cluster,
        center_lat=clat,
        center_lng=clng,
        zoom=zoom,
        width=args.width,
        height=args.height,
        scale=args.scale,
    )
    if not args.no_hormuz_caption:
        _draw_hormuz_caption_on_map(
            im,
            center_lat=clat,
            center_lng=clng,
            zoom=zoom,
            width=args.width,
            height=args.height,
            scale=args.scale,
        )
    if not args.no_run_timestamp:
        _draw_legend_above_timestamp_bottom_right(im, scale=args.scale)
        _draw_run_timestamp_bottom_right(im, scale=args.scale)
    out_buf = BytesIO()
    im.save(out_buf, format="PNG")
    data = out_buf.getvalue()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    oc = len(anchor_cluster)
    extra_oc = f", same_spot_clusters={oc}" if oc else ""
    print(
        f"Wrote {args.output} ({len(data)} bytes), markers={len(points)}, tracks={len(tracks)}, name_labels=1{extra_oc}"
    )
    for sid, name, lat, lon, tl in sorted(points, key=lambda p: (p[2], p[3])):
        abbr = name_prefix2(name, sid)
        tag = f"{tl}+{abbr}" if (tl or "").strip() else abbr
        print(f"  [{tag}] {name} ({sid}) {lat:.5f},{lon:.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
