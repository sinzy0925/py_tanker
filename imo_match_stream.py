"""
AISstream を受信し、JSONL に記録する。

モード `--mode imo`（既定）
  - IMO リストに含まれる船舶のみ（ShipStaticData / PositionReport）

モード `--mode wide`
  - 矩形内の「タンカー船種（AIS 80–89）」を広く記録
  - 各行に `japan_related_guess`（目的地・日本 MMSI MID の目印）を付与
  - `--wide-only-likely` で「日本関連目印あり」に限定可能

使い方:
  python imo_match_stream.py --duration 120
  python imo_match_stream.py --mode wide --out wide_tankers.jsonl --duration 300
  python imo_match_stream.py --mode wide --wide-only-likely

定期実行（推奨）:
  - 1 ファイルに蓄積: 毎回同じ --out に --append（上書きしない）
  - 実行ごとに別ファイル: --stamp-out（UTC で matches_YYYYMMDD_HHMMSS.jsonl）

環境変数: .env に AISSTREAM_API_KEY
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import websockets

from config_aisstream import BOUNDING_BOXES, DEFAULT_FILTER_MESSAGE_TYPES
from japan_wide_signals import is_tanker_type, japan_related_guess

load_dotenv()
URL = "wss://stream.aisstream.io/v0/stream"


def normalize_imo(value: int | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        if value <= 0:
            return None
        s = str(value).zfill(7)
    else:
        digits = "".join(c for c in str(value).strip() if c.isdigit())
        if not digits:
            return None
        s = digits.zfill(7)[-7:]
    return s if len(s) == 7 else None


def normalize_mmsi(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return str(int(user_id)).zfill(9)[-9:]


def load_imo_set(path: Path) -> set[str]:
    if not path.is_file():
        print(f"ERROR: IMO file not found: {path}", file=sys.stderr)
        sys.exit(1)
    imos: set[str] = set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            cell = row[0].strip()
            if not cell or cell.startswith("#"):
                continue
            if cell.lower() == "imo":
                continue
            imo = normalize_imo(cell)
            if imo:
                imos.add(imo)
    if not imos:
        print(f"ERROR: No valid IMOs in {path}", file=sys.stderr)
        sys.exit(1)
    return imos


def resolve_output_path(*, mode: str, out_arg: Path | None, stamp_out: bool) -> Path:
    default = Path("wide_tankers.jsonl") if mode == "wide" else Path("matches.jsonl")
    base = out_arg if out_arg is not None else default
    if stamp_out:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = base.parent / f"{base.stem}_{ts}{base.suffix}"
    return base


def metadata_coords(meta: dict) -> tuple[float | None, float | None]:
    lat = meta.get("latitude")
    lon = meta.get("longitude")
    if lat is None:
        lat = meta.get("Latitude")
    if lon is None:
        lon = meta.get("Longitude")
    try:
        return (float(lat) if lat is not None else None, float(lon) if lon is not None else None)
    except (TypeError, ValueError):
        return None, None


async def run(
    *,
    imo_file: Path,
    duration_sec: float,
    outfile: Path,
    quiet: bool,
    append: bool,
) -> None:
    api_key = os.environ.get("AISSTREAM_API_KEY", "").strip()
    if not api_key:
        print("ERROR: AISSTREAM_API_KEY is empty (.env)", file=sys.stderr)
        sys.exit(1)

    imo_set = load_imo_set(imo_file)
    if not quiet:
        print(f"Loaded {len(imo_set)} IMO(s) from {imo_file}", file=sys.stderr)

    mmsi_to_imo: dict[str, str] = {}
    subscribe = {
        "APIKey": api_key,
        "BoundingBoxes": BOUNDING_BOXES,
        "FilterMessageTypes": list(DEFAULT_FILTER_MESSAGE_TYPES),
    }

    t_end = asyncio.get_event_loop().time() + duration_sec
    n_in = 0
    n_match = 0

    outfile.parent.mkdir(parents=True, exist_ok=True)

    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps(subscribe))
        mode_out = "a" if append else "w"
        if not quiet:
            print(
                f"Subscribed for {duration_sec}s -> {outfile} ({'append' if append else 'overwrite'})",
                file=sys.stderr,
            )

        with outfile.open(mode_out, encoding="utf-8") as out:
            while asyncio.get_event_loop().time() < t_end:
                try:
                    remaining = t_end - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 60.0))
                except asyncio.TimeoutError:
                    continue

                n_in += 1
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if obj.get("error"):
                    if not quiet:
                        print(f"Stream error: {obj}", file=sys.stderr)
                    continue

                meta = obj.get("MetaData") or obj.get("Metadata") or {}
                mt = obj.get("MessageType")
                inner = obj.get("Message") or {}
                rec_base = {
                    "received_at_utc": datetime.now(timezone.utc).isoformat(),
                    "MessageType": mt,
                }
                meta_lat, meta_lon = metadata_coords(meta)

                if mt == "ShipStaticData":
                    body = inner.get("ShipStaticData")
                    if not body:
                        continue
                    uid = body.get("UserID")
                    mmsi = normalize_mmsi(uid)
                    imo_raw = body.get("ImoNumber")
                    imo = normalize_imo(imo_raw)
                    if not mmsi or not imo:
                        continue
                    if imo_raw and int(imo_raw) <= 0:
                        continue
                    mmsi_to_imo[mmsi] = imo
                    if imo not in imo_set:
                        continue
                    rec = {
                        **rec_base,
                        "kind": "static",
                        "mmsi": mmsi,
                        "imo": imo,
                        "name": (body.get("Name") or "").strip(),
                        "ship_type": body.get("Type"),
                        "destination": (body.get("Destination") or "").strip(),
                        "valid": body.get("Valid"),
                        "latitude": meta_lat,
                        "longitude": meta_lon,
                    }
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_match += 1
                    if not quiet:
                        print(f"MATCH static IMO {imo} MMSI {mmsi}", file=sys.stderr)

                elif mt == "PositionReport":
                    body = inner.get("PositionReport")
                    if not body:
                        continue
                    if not body.get("Valid", True):
                        continue
                    mmsi = normalize_mmsi(body.get("UserID"))
                    if not mmsi:
                        continue
                    imo = mmsi_to_imo.get(mmsi)
                    if not imo or imo not in imo_set:
                        continue
                    rec = {
                        **rec_base,
                        "kind": "position",
                        "mmsi": mmsi,
                        "imo": imo,
                        "latitude": body.get("Latitude"),
                        "longitude": body.get("Longitude"),
                        "sog": body.get("Sog"),
                        "nav_status": body.get("NavigationalStatus"),
                    }
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_match += 1

    if not quiet:
        print(f"Done. messages_in={n_in} matches_written={n_match}", file=sys.stderr)


async def run_wide(
    *,
    duration_sec: float,
    outfile: Path,
    quiet: bool,
    wide_only_likely: bool,
    append: bool,
) -> None:
    """タンカーを広く記録し、日本向け候補の目印を付ける。"""
    api_key = os.environ.get("AISSTREAM_API_KEY", "").strip()
    if not api_key:
        print("ERROR: AISSTREAM_API_KEY is empty (.env)", file=sys.stderr)
        sys.exit(1)

    # mmsi -> 直近の静的情報ベースのキャッシュ
    cache: dict[str, dict] = {}

    subscribe = {
        "APIKey": api_key,
        "BoundingBoxes": BOUNDING_BOXES,
        "FilterMessageTypes": list(DEFAULT_FILTER_MESSAGE_TYPES),
    }

    t_end = asyncio.get_event_loop().time() + duration_sec
    n_in = 0
    n_match = 0

    outfile.parent.mkdir(parents=True, exist_ok=True)

    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps(subscribe))
        mode_out = "a" if append else "w"
        if not quiet:
            extra = " (only japan signals)" if wide_only_likely else " (all tankers in box)"
            print(
                f"Subscribed WIDE{extra} for {duration_sec}s -> {outfile} ({'append' if append else 'overwrite'})",
                file=sys.stderr,
            )

        with outfile.open(mode_out, encoding="utf-8") as out:
            while asyncio.get_event_loop().time() < t_end:
                try:
                    remaining = t_end - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 60.0))
                except asyncio.TimeoutError:
                    continue

                n_in += 1
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if obj.get("error"):
                    if not quiet:
                        print(f"Stream error: {obj}", file=sys.stderr)
                    continue

                meta = obj.get("MetaData") or obj.get("Metadata") or {}
                mt = obj.get("MessageType")
                inner = obj.get("Message") or {}
                rec_base = {
                    "received_at_utc": datetime.now(timezone.utc).isoformat(),
                    "MessageType": mt,
                    "filter_mode": "wide",
                }
                meta_lat, meta_lon = metadata_coords(meta)

                if mt == "ShipStaticData":
                    body = inner.get("ShipStaticData")
                    if not body or not body.get("Valid", True):
                        continue
                    mmsi = normalize_mmsi(body.get("UserID"))
                    if not mmsi:
                        continue
                    stype = body.get("Type")
                    if not is_tanker_type(stype):
                        continue
                    imo_raw = body.get("ImoNumber")
                    imo = normalize_imo(imo_raw) if imo_raw and int(imo_raw) > 0 else None
                    dest = (body.get("Destination") or "").strip()
                    likely, detail = japan_related_guess(
                        ship_type=stype,
                        destination_raw=dest,
                        mmsi=mmsi,
                    )
                    cache[mmsi] = {
                        "imo": imo,
                        "name": (body.get("Name") or "").strip(),
                        "ship_type": stype,
                        "destination": dest,
                        "japan_related_guess": likely,
                        "japan_signals": detail,
                    }
                    if wide_only_likely and not likely:
                        continue
                    rec = {
                        **rec_base,
                        "kind": "static",
                        "mmsi": mmsi,
                        "imo": imo,
                        "name": cache[mmsi]["name"],
                        "ship_type": stype,
                        "destination": dest,
                        "japan_related_guess": likely,
                        "japan_signals": detail,
                        "latitude": meta_lat,
                        "longitude": meta_lon,
                        "note": "AIS only; not proof of cargo or discharge port",
                    }
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_match += 1

                elif mt == "PositionReport":
                    body = inner.get("PositionReport")
                    if not body or not body.get("Valid", True):
                        continue
                    mmsi = normalize_mmsi(body.get("UserID"))
                    if not mmsi or mmsi not in cache:
                        continue
                    c = cache[mmsi]
                    if wide_only_likely and not c.get("japan_related_guess"):
                        continue
                    rec = {
                        **rec_base,
                        "kind": "position",
                        "mmsi": mmsi,
                        "imo": c.get("imo"),
                        "name": c.get("name"),
                        "ship_type": c.get("ship_type"),
                        "destination": c.get("destination"),
                        "japan_related_guess": c.get("japan_related_guess"),
                        "japan_signals": c.get("japan_signals"),
                        "latitude": body.get("Latitude"),
                        "longitude": body.get("Longitude"),
                        "sog": body.get("Sog"),
                        "nav_status": body.get("NavigationalStatus"),
                    }
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_match += 1

    if not quiet:
        print(f"Done. messages_in={n_in} lines_written={n_match}", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description="AISstream: IMO list or wide Japan-tanker heuristic")
    p.add_argument(
        "--mode",
        choices=("imo", "wide"),
        default="imo",
        help="imo: CSV IMO list only; wide: all tankers in box + japan hint fields",
    )
    p.add_argument(
        "--imo-file",
        type=Path,
        default=Path("data/imo_list.csv"),
        help="(mode=imo) CSV with IMO in first column",
    )
    p.add_argument(
        "--wide-only-likely",
        action="store_true",
        help="(mode=wide) output only rows with japan_related_guess=true",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=120.0,
        help="Receive loop duration in seconds (default: 120)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSONL path (default: matches.jsonl or wide_tankers.jsonl)",
    )
    p.add_argument(
        "--append",
        action="store_true",
        help="Append to output JSONL instead of truncating (scheduled runs to one log file)",
    )
    p.add_argument(
        "--stamp-out",
        action="store_true",
        help="Add UTC timestamp to filename, e.g. matches_20260329_143022.jsonl",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args()
    out = resolve_output_path(mode=args.mode, out_arg=args.out, stamp_out=args.stamp_out)

    if args.mode == "imo":
        asyncio.run(
            run(
                imo_file=args.imo_file,
                duration_sec=args.duration,
                outfile=out,
                quiet=args.quiet,
                append=args.append,
            )
        )
    else:
        asyncio.run(
            run_wide(
                duration_sec=args.duration,
                outfile=out,
                quiet=args.quiet,
                wide_only_likely=args.wide_only_likely,
                append=args.append,
            )
        )


if __name__ == "__main__":
    main()
