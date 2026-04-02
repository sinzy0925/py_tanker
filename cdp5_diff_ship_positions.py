"""
ship_details_jp_prev.json と ship_details_jp.json を比較し、
各船の位置差分から移動判定を出す（cdp4_ship_details_filter 出力向け）。

使い方:
  python cdp5_diff_ship_positions.py
  python cdp5_diff_ship_positions.py --prev ship_data/ship_details_jp_prev.json --curr ship_data/ship_details_jp.json
  python cdp5_diff_ship_positions.py --min-distance-km 0.5 --min-speed-kn 1.0
  python cdp5_diff_ship_positions.py --mode latlon_round --latlon-decimals 3
  python cdp5_diff_ship_positions.py --mode latlon_round --latlon-quantize truncate
  python cdp5_diff_ship_positions.py --latlon-moved-if-speed-ge 10
  python cdp5_diff_ship_positions.py --types-jsonl ship_data/out.jsonl
  # moved_report の各行に gt_shiptype / type_letter（O/L/P/C）を付与（JSONL 優先、無ければ ship_details の /general subtype）
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    JST = ZoneInfo("Asia/Tokyo")
except ZoneInfoNotFoundError:
    JST = timezone(timedelta(hours=9), name="JST")


SHIP_DATA_DIR = Path("ship_data")

# station0 / out.jsonl の GT_SHIPTYPE → 地図1文字（cdp6 用）
GT_SHIPTYPE_TO_LETTER: dict[str, str] = {
    "17": "O",  # Crude Oil Tanker
    "18": "L",  # LNG Tanker
    "71": "P",  # Oil Products Tanker
    "88": "C",  # Oil/Chemical Tanker
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Diff positions between two ship_details JSON files")
    p.add_argument(
        "--prev",
        type=Path,
        default=SHIP_DATA_DIR / "ship_details_jp_prev.json",
        help=f"Previous snapshot JSON (default: {SHIP_DATA_DIR / 'ship_details_jp_prev.json'})",
    )
    p.add_argument(
        "--curr",
        type=Path,
        default=SHIP_DATA_DIR / "ship_details_jp.json",
        help=f"Current snapshot JSON (default: {SHIP_DATA_DIR / 'ship_details_jp.json'})",
    )
    p.add_argument(
        "--mode",
        choices=("threshold", "latlon_round"),
        default="threshold",
        help="threshold: 距離・速度で判定（既定）; latlon_round: lat/lon を量子化して一致なら STAY、異なれば MOVED",
    )
    p.add_argument(
        "--latlon-decimals",
        type=int,
        default=3,
        help="--mode latlon_round 時の小数桁（round / truncate ともにこの桁で処理）",
    )
    p.add_argument(
        "--latlon-quantize",
        choices=("round", "truncate"),
        default="round",
        help="latlon_round 時: round=四捨五入、truncate=0 方向へ切り捨て（math.trunc）",
    )
    p.add_argument("--min-distance-km", type=float, default=1.0, help="Moved threshold by distance (km); threshold モードのみ")
    p.add_argument("--min-speed-kn", type=float, default=1.0, help="Moved threshold by speed (knots); threshold モードのみ")
    p.add_argument(
        "--latlon-moved-if-speed-ge",
        type=float,
        default=None,
        metavar="KN",
        help="指定時は自動で latlon_round になる。現在速力>=KN なら格子が同じでも MOVED。格子のみのときは --mode latlon_round のみ",
    )
    p.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="JSON report path (default: ship_moved/moved_report_01.json; 書き込み前に 01〜10 をローテーション)",
    )
    p.add_argument(
        "--types-jsonl",
        type=Path,
        default=SHIP_DATA_DIR / "out.jsonl",
        help="GT_SHIPTYPE を ship_id で引く JSONL（既定: ship_data/out.jsonl）。無い行は ship_details から推定",
    )
    ns = p.parse_args()
    if ns.latlon_moved_if_speed_ge is not None:
        ns.mode = "latlon_round"
    return ns


DEFAULT_MOVED_JSON = Path("ship_moved") / "moved_report_01.json"


def default_json_out_path() -> Path:
    return DEFAULT_MOVED_JSON


def rotate_moved_report_files(ship_moved_dir: Path) -> None:
    """moved_report_10 を削除し、09→10 … 01→02 とリネーム（新規 01 書き込み前に呼ぶ）。"""
    ship_moved_dir.mkdir(parents=True, exist_ok=True)
    names = [f"moved_report_{i:02d}.json" for i in range(1, 11)]
    p10 = ship_moved_dir / names[9]
    if p10.is_file():
        p10.unlink()
    for i in range(8, -1, -1):
        src = ship_moved_dir / names[i]
        dst = ship_moved_dir / names[i + 1]
        if src.is_file():
            src.replace(dst)


def index_report_rows_by_ship_id(report_path: Path) -> dict[str, dict[str, Any]]:
    """ローテーション前の moved_report_01.json などから ship_id → 行 dict。"""
    if not report_path.is_file():
        return {}
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    rows = raw.get("rows")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("ship_id") or "").strip()
        if sid:
            out[sid] = row
    return out


def copy_previous_report_row_flat(prev_row: dict[str, Any]) -> dict[str, Any]:
    """
    直前レポート1行分のコピー。previous_report_row は含めない（入れ子で JSON が太り続けるのを防ぐ）。
    他フィールドは deepcopy（共有参照で現在行を汚さないため）。
    """
    out = copy.deepcopy(prev_row)
    out.pop("previous_report_row", None)
    return out


def attach_previous_report_rows(
    rows: list[dict[str, Any]], prev_by_id: dict[str, dict[str, Any]]
) -> None:
    """moved が True の行に、直前レポートの同一 ship_id 行を previous_report_row で追記（1段のみ）。"""
    for row in rows:
        if not row.get("moved"):
            continue
        sid = str(row.get("ship_id") or "").strip()
        if not sid or sid not in prev_by_id:
            continue
        row["previous_report_row"] = copy_previous_report_row_flat(prev_by_id[sid])


def _normalize_latlon_pair(val: Any) -> list[float] | None:
    if not isinstance(val, list) or len(val) < 2:
        return None
    try:
        return [float(val[0]), float(val[1])]
    except (TypeError, ValueError):
        return None


def _oldest_latlon_from_prev_row(prev_row: dict[str, Any]) -> list[float] | None:
    """累積区間の最古点: 直前行の latlon_rounded_history[0]、無ければ prev_latlon_rounded。"""
    old_h = prev_row.get("latlon_rounded_history")
    if isinstance(old_h, list) and old_h:
        first = _normalize_latlon_pair(old_h[0])
        if first:
            return first
    return _normalize_latlon_pair(prev_row.get("prev_latlon_rounded"))


def apply_latlon_rounded_history(
    rows: list[dict[str, Any]],
    prev_by_id: dict[str, dict[str, Any]],
    mode: str,
) -> None:
    """latlon_round 時、latlon_rounded_history は常に2点 [最古, 最新]（量子化 lat/lon）のみ。"""
    if mode != "latlon_round":
        return
    for row in rows:
        sid = str(row.get("ship_id") or "").strip()
        prev_row = prev_by_id.get(sid) if sid else None
        p_pair = _normalize_latlon_pair(row.get("prev_latlon_rounded"))
        c_pair = _normalize_latlon_pair(row.get("curr_latlon_rounded"))
        if p_pair is None or c_pair is None:
            continue
        moved = bool(row.get("moved"))
        if moved:
            oldest = p_pair
            if prev_row:
                o = _oldest_latlon_from_prev_row(prev_row)
                if o is not None:
                    oldest = o
            row["latlon_rounded_history"] = [oldest, c_pair]
        elif prev_row:
            old_h = prev_row.get("latlon_rounded_history")
            if isinstance(old_h, list) and old_h:
                oldest = _normalize_latlon_pair(old_h[0])
                if oldest is not None:
                    row["latlon_rounded_history"] = [oldest, c_pair]


def quantize_latlon(
    lat: float, lon: float, ndigits: int, method: str
) -> tuple[float, float]:
    """method: round（四捨五入）または truncate（0 へ切り捨て、負の座標も trunc と同じ向き）。"""
    if method == "truncate":
        m = 10**ndigits
        return (math.trunc(lat * m) / m, math.trunc(lon * m) / m)
    return (round(lat, ndigits), round(lon, ndigits))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        print(f"ERROR: invalid JSON root (dict expected): {path}", file=sys.stderr)
        raise SystemExit(1)
    return raw


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_latest_position(one: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for m in one.get("matches", []) if isinstance(one.get("matches"), list) else []:
        if not isinstance(m, dict):
            continue
        payload = m.get("payload")
        if not isinstance(payload, dict):
            continue
        lat = _coerce_float(payload.get("lat"))
        lon = _coerce_float(payload.get("lon"))
        if lat is None or lon is None:
            continue
        ts = _coerce_float(payload.get("timestamp"))
        speed = _coerce_float(payload.get("speed"))
        candidates.append(
            {
                "lat": lat,
                "lon": lon,
                "timestamp": ts,
                "speed": speed,
                "captured_at_utc": m.get("captured_at_utc"),
                "captured_at_jst": m.get("captured_at_jst"),
                "url": m.get("url"),
            }
        )

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x.get("timestamp") or -1, x.get("captured_at_utc") or ""))
    return candidates[-1]


def index_positions(root: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    results = root.get("results")
    if not isinstance(results, list):
        return out
    for one in results:
        if not isinstance(one, dict):
            continue
        ship_id = str(one.get("ship_id") or "").strip()
        if not ship_id:
            continue
        pos = extract_latest_position(one)
        if not pos:
            continue
        out[ship_id] = {
            "ship_id": ship_id,
            "ship_name": one.get("ship_name"),
            **pos,
        }
    return out


def index_full_results(root: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """ship_id → ship_details の result 1件（型推定用）。"""
    out: dict[str, dict[str, Any]] = {}
    results = root.get("results")
    if not isinstance(results, list):
        return out
    for one in results:
        if not isinstance(one, dict):
            continue
        ship_id = str(one.get("ship_id") or "").strip()
        if not ship_id:
            continue
        out[ship_id] = one
    return out


def type_from_ship_details_general(one: dict[str, Any]) -> tuple[str | None, str | None]:
    """matches の /general の subtype から (gt_shiptype 風コード, type_letter) を推定。"""
    for m in one.get("matches", []) if isinstance(one.get("matches"), list) else []:
        if not isinstance(m, dict):
            continue
        url = str(m.get("url") or "")
        if "/general" not in url:
            continue
        pl = m.get("payload")
        if not isinstance(pl, dict):
            continue
        st = str(pl.get("subtype") or "")
        ul = st.upper()
        if "CRUDE" in ul:
            return ("17", "O")
        if "LNG" in ul:
            return ("18", "L")
        if "OIL PRODUCT" in ul or "PRODUCTS TANKER" in ul:
            return ("71", "P")
        if "CHEMICAL" in ul or "OIL/CHEMICAL" in ul:
            return ("88", "C")
    return (None, None)


def load_gt_shiptype_from_jsonl(path: Path) -> dict[str, tuple[str, str]]:
    """SHIP_ID → (GT_SHIPTYPE 文字列, type_letter)。"""
    out: dict[str, tuple[str, str]] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        sid = str(row.get("SHIP_ID") or "").strip()
        gt = str(row.get("GT_SHIPTYPE") or "").strip()
        if not sid or not gt:
            continue
        letter = GT_SHIPTYPE_TO_LETTER.get(gt, "")
        out[sid] = (gt, letter)
    return out


def resolve_ship_type(
    ship_id: str,
    jsonl_map: dict[str, tuple[str, str]],
    curr_result: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """gt_shiptype 文字列と type_letter（O/L/P/C）。"""
    if ship_id in jsonl_map:
        gt, letter = jsonl_map[ship_id]
        if letter:
            return (gt, letter)
        return (gt, None)
    if curr_result:
        gt, letter = type_from_ship_details_general(curr_result)
        if letter:
            return (gt, letter)
    return (None, None)


def main() -> None:
    args = parse_args()
    prev_root = load_json(args.prev)
    curr_root = load_json(args.curr)

    prev_idx = index_positions(prev_root)
    curr_idx = index_positions(curr_root)
    curr_by_id = index_full_results(curr_root)
    jsonl_types = load_gt_shiptype_from_jsonl(args.types_jsonl.resolve())
    common_ids = sorted(set(prev_idx) & set(curr_idx))

    if not common_ids:
        print("No comparable ships with position data.")
        raise SystemExit(0)

    rows: list[dict[str, Any]] = []
    moved = 0
    for ship_id in common_ids:
        p = prev_idx[ship_id]
        c = curr_idx[ship_id]
        dist_km = haversine_km(p["lat"], p["lon"], c["lat"], c["lon"])
        try:
            speed = float(c.get("speed") or 0.0)
        except (TypeError, ValueError):
            speed = 0.0
        if args.mode == "latlon_round":
            prev_r = quantize_latlon(
                p["lat"], p["lon"], args.latlon_decimals, args.latlon_quantize
            )
            curr_r = quantize_latlon(
                c["lat"], c["lon"], args.latlon_decimals, args.latlon_quantize
            )
            moved_flag = prev_r != curr_r
            if args.latlon_moved_if_speed_ge is not None and speed >= args.latlon_moved_if_speed_ge:
                moved_flag = True
        else:
            moved_flag = (dist_km >= args.min_distance_km) or (speed >= args.min_speed_kn)
        if moved_flag:
            moved += 1
        gt_s, type_letter = resolve_ship_type(ship_id, jsonl_types, curr_by_id.get(ship_id))
        row: dict[str, Any] = {
            "ship_id": ship_id,
            "ship_name": c.get("ship_name") or p.get("ship_name"),
            "moved": moved_flag,
            "distance_km": round(dist_km, 4),
            "prev_speed_kn": p.get("speed"),
            "curr_speed_kn": c.get("speed"),
            "prev_timestamp": p.get("timestamp"),
            "curr_timestamp": c.get("timestamp"),
            "prev_captured_at_jst": p.get("captured_at_jst"),
            "curr_captured_at_jst": c.get("captured_at_jst"),
        }
        if gt_s is not None:
            row["gt_shiptype"] = gt_s
        if type_letter:
            row["type_letter"] = type_letter
        if args.mode == "latlon_round":
            row["compare_mode"] = "latlon_round"
            row["latlon_decimals"] = args.latlon_decimals
            row["latlon_quantize"] = args.latlon_quantize
            row["prev_latlon_rounded"] = list(prev_r)
            row["curr_latlon_rounded"] = list(curr_r)
        rows.append(row)

    if args.mode == "latlon_round":
        q = "rounded" if args.latlon_quantize == "round" else "truncated"
        extra_sp = ""
        if args.latlon_moved_if_speed_ge is not None:
            extra_sp = f" OR curr_speed>={args.latlon_moved_if_speed_ge}kn"
        summary = (
            f"Compared={len(rows)} moved={moved} "
            f"(lat/lon {q} to {args.latlon_decimals} decimals: differ => MOVED{extra_sp})"
        )
    else:
        summary = (
            f"Compared={len(rows)} moved={moved} "
            f"(distance>={args.min_distance_km}km or speed>={args.min_speed_kn}kn)"
        )
    print(summary)
    for r in rows:
        state = "MOVED" if r["moved"] else "STAY"
        print(
            f"{state}\t{r['ship_name']}\tSHIP_ID={r['ship_id']}\t"
            f"DIST_KM={r['distance_km']}\tSPEED={r['curr_speed_kn']}"
        )

    out_path = (args.json_out if args.json_out is not None else default_json_out_path()).resolve()

    prev_by_id: dict[str, dict[str, Any]] = {}
    if out_path.name == "moved_report_01.json":
        prev_by_id = index_report_rows_by_ship_id(out_path)
        rotate_moved_report_files(out_path.parent)
        attach_previous_report_rows(rows, prev_by_id)
    apply_latlon_rounded_history(rows, prev_by_id, args.mode)

    payload: dict[str, Any] = {
        "generated_at_jst": datetime.now(JST).isoformat(),
        "prev": str(args.prev),
        "curr": str(args.curr),
        "compare_mode": args.mode,
        "compared": len(rows),
        "moved": moved,
        "rows": rows,
    }
    if args.mode == "latlon_round":
        payload["latlon_decimals"] = args.latlon_decimals
        payload["latlon_quantize"] = args.latlon_quantize
        if args.latlon_moved_if_speed_ge is not None:
            payload["latlon_moved_if_speed_ge"] = args.latlon_moved_if_speed_ge
    else:
        payload["min_distance_km"] = args.min_distance_km
        payload["min_speed_kn"] = args.min_speed_kn
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote report -> {out_path}")


if __name__ == "__main__":
    main()
