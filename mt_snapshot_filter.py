"""
MarineTraffic 地図の DevTools などで保存したスナップショット JSON（data.json 形式）を読み、絞り込む。

想定フォーマット:
  {"type": 1, "data": {"rows": [ {...}, ... ], "areaShips": N }}

例:
  python mt_snapshot_filter.py
      → 既定で data0.json … data5.json を読み、行をまとめて処理（存在するファイルのみ）
  python mt_snapshot_filter.py data.json
  python mt_snapshot_filter.py data.json --mode japan_hint --csv out.csv
  python mt_snapshot_filter.py data.json --mode tanker --jsonl
  python mt_snapshot_filter.py --mode japan_broad

注意:
  - SHIP_ID は多くの場合 MarineTraffic 内部 ID（IMO ではない）。
  - 利用規約上、サイトからの自動・大量取得は行わないこと。手元に保存した 1 ファイルの解析用。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from japan_wide_signals import destination_japan_hits, destination_japan_hits_broad

# 引数なしのときに読む既定スナップショット（複数時刻の比較用）
DEFAULT_INPUT_JSONS: tuple[Path, ...] = tuple(Path(f"data{i}.json") for i in range(6))


def load_rows(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "data" in raw:
        data = raw["data"]
        if isinstance(data, dict) and "rows" in data:
            rows = data["rows"]
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    raise ValueError("想定外の JSON: type/data/rows または配列を期待します")


def resolve_input_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        cand = Path(p)
        if cand.is_file():
            out.append(cand.resolve())
        else:
            print(f"WARN: skip (not found): {p}", file=sys.stderr)
    return out


def load_rows_multi(paths: list[Path], *, tag_source: bool) -> tuple[list[dict], dict[Path, int]]:
    """複数ファイルの rows を連結。tag_source なら各行に _source_json（ファイル名）を付与。"""
    all_rows: list[dict] = []
    per_file_counts: dict[Path, int] = {}
    for path in paths:
        chunk = load_rows(path)
        per_file_counts[path] = len(chunk)
        for r in chunk:
            if tag_source:
                rr = dict(r)
                rr["_source_json"] = path.name
                all_rows.append(rr)
            else:
                all_rows.append(dict(r))
    return all_rows, per_file_counts


def row_destination(row: dict) -> str:
    return (row.get("DESTINATION") or "").strip()


def row_flag(row: dict) -> str:
    return (row.get("FLAG") or "").strip().upper()


def is_japan_flag(row: dict) -> bool:
    return row_flag(row) == "JP"


def is_japan_destination_hint(row: dict) -> bool:
    return bool(destination_japan_hits(row_destination(row) or None))


def is_japan_destination_hint_broad(row: dict) -> bool:
    return bool(destination_japan_hits_broad(row_destination(row) or None))


def is_tanker_heuristic(row: dict) -> bool:
    """MarineTraffic スナップショットの SHIPTYPE は内部コード。8 がタンカーに多い。"""
    tn = (row.get("TYPE_NAME") or "").upper()
    if "TANKER" in tn:
        return True
    st = str(row.get("SHIPTYPE") or "")
    if st == "8":
        return True
    return False


def match_mode(row: dict, mode: str) -> bool:
    jf = is_japan_flag(row)
    jd = is_japan_destination_hint(row)
    jdb = is_japan_destination_hint_broad(row)
    tank = is_tanker_heuristic(row)
    if mode == "all":
        return True
    if mode == "japan_hint":
        return jf or jd
    if mode == "japan_broad":
        return jf or jdb
    if mode == "tanker":
        return tank
    if mode == "japan_tanker":
        return tank and (jf or jd)
    if mode == "japan_tanker_broad":
        return tank and (jf or jdb)
    raise ValueError(f"unknown mode: {mode}")


def enrich_row(row: dict, *, broad_dest_hits: bool = False) -> dict:
    dest = row_destination(row)
    hits = (
        destination_japan_hits_broad(dest or None)
        if broad_dest_hits
        else destination_japan_hits(dest or None)
    )
    out = dict(row)
    out["_filter_japan_flag"] = is_japan_flag(row)
    out["_filter_japan_dest_hits"] = hits
    out["_filter_tanker_guess"] = is_tanker_heuristic(row)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Filter MarineTraffic-style snapshot JSON")
    p.add_argument(
        "input",
        type=Path,
        nargs="*",
        help="Input JSON path(s). Omit to use data0.json … data5.json",
    )
    p.add_argument(
        "--mode",
        choices=(
            "all",
            "japan_hint",
            "japan_broad",
            "tanker",
            "japan_tanker",
            "japan_tanker_broad",
        ),
        default="japan_hint",
        help="japan_hint: JP+目的地(標準); japan_broad: より広い港名・JP略号; *_tanker*: タンカー推定も併用",
    )
    p.add_argument("--csv", type=Path, metavar="FILE", help="Write UTF-8 CSV (Excel 向け BOM 付き)")
    p.add_argument("--jsonl", type=Path, metavar="FILE", help="Write JSON Lines")
    p.add_argument("--with-meta", action="store_true", help="Add _filter_* diagnostic fields")
    args = p.parse_args()

    paths = list(args.input) if args.input else list(DEFAULT_INPUT_JSONS)
    resolved = resolve_input_paths(paths)
    if not resolved:
        print("ERROR: no input JSON files found (check paths or data0.json … data5.json)", file=sys.stderr)
        sys.exit(1)

    tag_source = len(resolved) > 1
    rows, per_file_counts = load_rows_multi(resolved, tag_source=tag_source)
    matched = [r for r in rows if match_mode(r, args.mode)]

    if args.with_meta:
        meta_broad = args.mode in ("japan_broad", "japan_tanker_broad")
        matched = [enrich_row(r, broad_dest_hits=meta_broad) for r in matched]

    if args.csv:
        if not matched:
            print("No rows to write.", file=sys.stderr)
            args.csv.write_text("", encoding="utf-8-sig")
            return
        keys: list[str] = []
        for r in matched:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with args.csv.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in matched:
                w.writerow({k: r.get(k, "") for k in keys})
        print(f"Wrote {len(matched)} rows -> {args.csv}", file=sys.stderr)
        return

    if args.jsonl:
        with args.jsonl.open("w", encoding="utf-8") as f:
            for r in matched:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"Wrote {len(matched)} lines -> {args.jsonl}", file=sys.stderr)
        return

    # stdout: compact table
    src_summary = ",".join(p.name for p in resolved)
    per = " ".join(f"{p.name}:{per_file_counts[p]}" for p in resolved)
    print(
        f"# inputs={src_summary} mode={args.mode} total_rows={len(rows)} matched={len(matched)} ({per})",
        file=sys.stderr,
    )
    for r in matched:
        src = f"{r.get('_source_json', '')}\t" if tag_source else ""
        name = (r.get("SHIPNAME") or "").strip()
        flag = row_flag(r)
        dest = row_destination(r)
        lat = r.get("LAT")
        lon = r.get("LON")
        sid = r.get("SHIP_ID")
        print(f"{src}{name}\tFLAG={flag}\tDEST={dest}\tLAT={lat}\tLON={lon}\tSHIP_ID={sid}")


if __name__ == "__main__":
    main()
