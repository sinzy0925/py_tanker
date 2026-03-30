"""
ship_details.json から、航路の reportedDestination 等で「日本向けっぽい」船だけを抽出し
ship_details_jp.json に書き出す。

既に ship_details_jp.json がある場合は、上書き前に ship_details_jp_prev.json へ退避する。

使い方:
  python cdp4_ship_details_filter.py
  python cdp4_ship_details_filter.py --also-japan-mid
  python cdp4_ship_details_filter.py --input ship_data/ship_details.json --output ship_data/ship_details_jp.json

--also-japan-mid:
  general の mmsi の先頭3桁（MID）が ITU の日本船舶向け割当 431–439 に入る船も含める（航路の「日本向け」とは別軸）。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from japan_wide_signals import destination_japan_hits_broad

try:
    JST = ZoneInfo("Asia/Tokyo")
except ZoneInfoNotFoundError:
    JST = timezone(timedelta(hours=9), name="JST")

SHIP_DATA_DIR = Path("ship_data")
DEFAULT_INPUT = SHIP_DATA_DIR / "ship_details.json"
DEFAULT_OUTPUT = SHIP_DATA_DIR / "ship_details_jp.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Filter ship_details.json for Japan-like voyages")
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT, help=f"Input JSON (default: {DEFAULT_INPUT})")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"Output JSON (default: {DEFAULT_OUTPUT})")
    p.add_argument(
        "--also-japan-mid",
        action="store_true",
        help="OR: include ships where general payload MMSI MID is 431–439 (Japanese ITU allocation; not voyage)",
    )
    return p.parse_args()


def _to_jst_iso(utc_iso: str) -> str | None:
    try:
        return datetime.fromisoformat(utc_iso).astimezone(JST).isoformat()
    except Exception:
        return None


def _prev_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_prev{output.suffix}")


def rotate_if_exists(output: Path) -> Path | None:
    if not output.exists():
        return None
    prev = _prev_path(output)
    if prev.exists():
        prev.unlink()
    output.replace(prev)
    return prev


def is_voyage_japan_like(result: dict[str, Any]) -> bool:
    """voyage.reportedDestination を japan_wide_signals でヒットすれば日本向けっぽいとみなす。"""
    for m in result.get("matches", []) if isinstance(result.get("matches"), list) else []:
        if not isinstance(m, dict):
            continue
        url = str(m.get("url", ""))
        if "/voyage" not in url:
            continue
        payload = m.get("payload")
        if not isinstance(payload, dict):
            continue
        dest = payload.get("reportedDestination")
        if dest is None:
            continue
        if destination_japan_hits_broad(str(dest).strip()):
            return True
    return False


def _mmsi_mid_int(mmsi: Any) -> int | None:
    if mmsi is None:
        return None
    try:
        s = str(int(mmsi))
    except (TypeError, ValueError):
        return None
    if len(s) < 3:
        return None
    return int(s[:3])


def is_japan_mid_from_general(result: dict[str, Any]) -> bool:
    """general の payload.mmsi の MID が ITU 日本船舶向け 431–439 なら真（航路とは無関係）。"""
    for m in result.get("matches", []) if isinstance(result.get("matches"), list) else []:
        if not isinstance(m, dict):
            continue
        url = str(m.get("url", ""))
        if "/general" not in url:
            continue
        payload = m.get("payload")
        if not isinstance(payload, dict):
            continue
        mid = _mmsi_mid_int(payload.get("mmsi"))
        if mid is not None and 431 <= mid <= 439:
            return True
    return False


def should_keep_result(result: dict[str, Any], *, also_japan_mid: bool) -> bool:
    if is_voyage_japan_like(result):
        return True
    if also_japan_mid and is_japan_mid_from_general(result):
        return True
    return False


def main() -> None:
    args = parse_args()
    in_path = args.input
    out_path = args.output

    if not in_path.is_file():
        raise SystemExit(f"ERROR: input not found: {in_path}")

    raw = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("ERROR: root must be a JSON object")

    results_in = raw.get("results")
    if not isinstance(results_in, list):
        raise SystemExit("ERROR: missing results array")

    filtered: list[dict[str, Any]] = []
    for one in results_in:
        if isinstance(one, dict) and one.get("ok") and should_keep_result(
            one, also_japan_mid=args.also_japan_mid
        ):
            filtered.append(one)

    created_at_utc = datetime.now(timezone.utc).isoformat()
    created_at_jst = _to_jst_iso(created_at_utc)

    filter_parts = [
        "voyage.reportedDestination matches japan_wide_signals (broad)",
    ]
    if args.also_japan_mid:
        filter_parts.append(
            "OR general.mmsi MID in 431–439 (ITU Japan ship allocation; not destination to Japan)"
        )
    filter_note = "Japan-like: " + "; ".join(filter_parts)

    out_doc: dict[str, Any] = {
        "created_at_utc": created_at_utc,
        "created_at_jst": created_at_jst,
        "source_file": str(in_path.resolve()),
        "also_japan_mid": args.also_japan_mid,
        "filter_note": filter_note,
        "total_results_in": len(results_in),
        "total_results_kept": len(filtered),
        "ok_targets_kept": sum(1 for r in filtered if r.get("ok")),
        "results": filtered,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    rotated = rotate_if_exists(out_path)
    out_path.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    if rotated:
        print(f"Rotated previous JP snapshot -> {rotated}")
    print(f"Wrote {len(filtered)} Japan-like ship result(s) -> {out_path}")


if __name__ == "__main__":
    main()
