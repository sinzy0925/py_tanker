"""
ship_details_jp_prev.json と ship_details_jp.json を比較し、
各船の位置差分から移動判定を出す（cdp4_ship_details_filter 出力向け）。

使い方:
  python cdp5_diff_ship_positions.py
  python cdp5_diff_ship_positions.py --prev ship_data/ship_details_jp_prev.json --curr ship_data/ship_details_jp.json
  python cdp5_diff_ship_positions.py --min-distance-km 0.5 --min-speed-kn 1.0
"""

from __future__ import annotations

import argparse
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
    p.add_argument("--min-distance-km", type=float, default=1.0, help="Moved threshold by distance (km)")
    p.add_argument("--min-speed-kn", type=float, default=1.0, help="Moved threshold by speed (knots)")
    p.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="JSON report path (default: ship_moved/moved_report_YYYYMMDD_HHMMSS.json in JST)",
    )
    return p.parse_args()


def default_json_out_path() -> Path:
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    return Path("ship_moved") / f"moved_report_{ts}.json"


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


def main() -> None:
    args = parse_args()
    prev_root = load_json(args.prev)
    curr_root = load_json(args.curr)

    prev_idx = index_positions(prev_root)
    curr_idx = index_positions(curr_root)
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
        speed = c.get("speed") or 0.0
        moved_flag = (dist_km >= args.min_distance_km) or (speed >= args.min_speed_kn)
        if moved_flag:
            moved += 1
        rows.append(
            {
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
        )

    print(
        f"Compared={len(rows)} moved={moved} "
        f"(distance>={args.min_distance_km}km or speed>={args.min_speed_kn}kn)"
    )
    for r in rows:
        state = "MOVED" if r["moved"] else "STAY"
        print(
            f"{state}\t{r['ship_name']}\tSHIP_ID={r['ship_id']}\t"
            f"DIST_KM={r['distance_km']}\tSPEED={r['curr_speed_kn']}"
        )

    out_path = args.json_out if args.json_out is not None else default_json_out_path()
    payload = {
        "generated_at_jst": datetime.now(JST).isoformat(),
        "prev": str(args.prev),
        "curr": str(args.curr),
        "min_distance_km": args.min_distance_km,
        "min_speed_kn": args.min_speed_kn,
        "compared": len(rows),
        "moved": moved,
        "rows": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote report -> {out_path}")


if __name__ == "__main__":
    main()
