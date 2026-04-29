"""
MarineTraffic の station0 取得結果 JSON（station0_all.json）を読み、絞り込む。

想定フォーマット:
  1) 直接スナップショット:
     {"type": 1, "data": {"rows": [ {...}, ... ], "areaShips": N }}
  2) fetch_station0_playwright.py の出力:
     {
       "ok": true,
       "matched_count": 12,
       "best": {..., "payload": {"type":1, "data":{"rows":[...]}}},
       "matches": [{..., "payload": {"type":1, "data":{"rows":[...]}}}, ...]
     }

例:
  python cdp2_mt_snapshot_filter.py
      → 既定で ship_data/station0_all.json を読む
  python cdp2_mt_snapshot_filter.py ship_data/station0_all.json
  python cdp2_mt_snapshot_filter.py --mode japan_hint --jsonl ship_data/out.jsonl --dedupe-by-ship-id
  python cdp2_mt_snapshot_filter.py --mode japan_broad
  python cdp2_mt_snapshot_filter.py --mode japan_jp --jsonl ship_data/out.jsonl
  python cdp2_mt_snapshot_filter.py --mode japan_jp --dedupe-by-ship-id --filter-lat-lon-prefix --jsonl ship_data/out.jsonl

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

SHIP_DATA_DIR = Path("ship_data")
# 引数なしのときに読む既定ファイル
DEFAULT_INPUT_JSON = SHIP_DATA_DIR / "station0_all.json"


def load_rows(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))

    # 直接 rows を持つスナップショット JSON
    if isinstance(raw, dict) and "data" in raw:
        data = raw["data"]
        if isinstance(data, dict) and "rows" in data:
            rows = data["rows"]
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]

    # fetch_station0_playwright.py 出力（best / matches）
    if isinstance(raw, dict) and ("best" in raw or "matches" in raw):
        captures: list[dict] = []
        best = raw.get("best")
        matches = raw.get("matches")

        if isinstance(best, dict):
            captures.append(best)
        if isinstance(matches, list):
            captures.extend(m for m in matches if isinstance(m, dict))

        rows_all: list[dict] = []
        for idx, cap in enumerate(captures):
            payload = cap.get("payload")
            if not isinstance(payload, dict):
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            rows = data.get("rows")
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rr = dict(row)
                rr["_capture_index"] = idx
                rr["_capture_url"] = cap.get("url")
                rr["_capture_status"] = cap.get("status")
                rows_all.append(rr)
        if rows_all:
            return rows_all

    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    raise ValueError("想定外の JSON: data.rows / best.payload.data.rows / matches[].payload.data.rows / 配列を期待します")


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


def is_jp_substring_in_fields(row: dict) -> bool:
    """DESTINATION / FLAG / SHIPNAME のいずれかに 'JP' が含まれる（大文字小文字は区別しない）。"""
    for key in ("DESTINATION", "FLAG", "SHIPNAME"):
        s = str(row.get(key) or "").upper()
        if "JP" in s:
            return True
    return False


def is_usa_military(row: dict) -> bool:
    """
    アメリカの軍関連（米海軍・軍関連船）っぽい行だけを雑に拾う。
    目的地/フラグ/船名に 'USS'/'USNS'/'NAVY'/'NAVAL'/'MILITARY' 等が含まれるかで判定する。
    """
    shipname = str(row.get("SHIPNAME") or "").upper()
    destination = str(row.get("DESTINATION") or "").upper()
    flag = row_flag(row)

    # 船名の代表例: USS / USNS
    if "USS" in shipname or "USNS" in shipname:
        return True

    # 目的地/船名に軍関連キーワードがあれば採用
    for token in ("US NAVY", "NAVY", "NAVAL", "MILITARY", "ARMY", "DEFENSE"):
        if token in shipname or token in destination:
            return True

    # FLAG が米国で、かつ目的地側が軍っぽい場合も採用
    if flag in ("US", "USA") and any(t in destination for t in ("NAVY", "NAVAL", "MILITARY", "ARMY")):
        return True

    return False


def lat_lon_prefix_match(row: dict) -> bool:
    """
    LAT の文字列（符号を除く）の先頭2文字がともに数字で、かつ十の位が 2（= 20°台）。
    LON の同様の先頭2文字の十の位が 4 または 5（= 40°台・50°台）。
    例: LAT \"27.14\", LON \"50.26\" は一致。日本付近（LON 13x 台）は一致しない。
    """
    lat_s = str(row.get("LAT") or "").strip()
    lon_s = str(row.get("LON") or "").strip()
    if not lat_s or not lon_s:
        return False
    lat_s = lat_s.lstrip("-+")
    lon_s = lon_s.lstrip("-+")
    if len(lat_s) < 2 or len(lon_s) < 2:
        return False
    if not (lat_s[0].isdigit() and lat_s[1].isdigit()):
        return False
    if lat_s[0] != "2":
        return False
    if not (lon_s[0].isdigit() and lon_s[1].isdigit()):
        return False
    if lon_s[0] not in ("4", "5"):
        return False
    return True


def shipname_contains_any(row: dict, needles: list[str]) -> bool:
    """SHIPNAME に needles のいずれか（大文字小文字無視）を含むか。"""
    shipname = str(row.get("SHIPNAME") or "").upper()
    for needle in needles:
        if needle and needle in shipname:
            return True
    return False


def _row_identity(row: dict) -> tuple[str, str, str, str]:
    """SHIP_ID があればそれを優先し、無ければ主要項目で同一判定する。"""
    sid = str(row.get("SHIP_ID") or "").strip()
    if sid:
        return ("sid", sid, "", "")
    name = str(row.get("SHIPNAME") or "").strip().upper()
    lat = str(row.get("LAT") or "").strip()
    lon = str(row.get("LON") or "").strip()
    return ("fallback", name, lat, lon)


def append_shipname_matches(base_rows: list[dict], all_rows: list[dict], needles: list[str]) -> tuple[list[dict], int]:
    """
    base_rows に、SHIPNAME が needles を含む行を all_rows から追加する。
    すでに同一行（SHIP_ID優先）がある場合は重複追加しない。
    """
    out = list(base_rows)
    seen = {_row_identity(r) for r in out}
    added = 0
    for r in all_rows:
        if not shipname_contains_any(r, needles):
            continue
        rid = _row_identity(r)
        if rid in seen:
            continue
        out.append(r)
        seen.add(rid)
        added += 1
    return out, added


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
    if mode == "japan_jp":
        return is_jp_substring_in_fields(row)
    if mode == "usa_military":
        return is_usa_military(row)
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


def dedupe_by_ship_id(rows: list[dict]) -> tuple[list[dict], int]:
    """SHIP_ID 単位で先頭行を残して重複排除する。SHIP_ID 欠損行はそのまま残す。"""
    seen: set[str] = set()
    out: list[dict] = []
    dropped = 0
    for row in rows:
        ship_id = str(row.get("SHIP_ID") or "").strip()
        if not ship_id:
            out.append(row)
            continue
        if ship_id in seen:
            dropped += 1
            continue
        seen.add(ship_id)
        out.append(row)
    return out, dropped


def _normalize_type_code(value: object) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


def main() -> None:
    p = argparse.ArgumentParser(description="Filter MarineTraffic-style snapshot JSON")
    p.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=DEFAULT_INPUT_JSON,
        help=f"Input JSON path (default: {DEFAULT_INPUT_JSON})",
    )
    p.add_argument(
        "--mode",
        choices=(
            "all",
            "japan_hint",
            "japan_broad",
            "japan_jp",
            "tanker",
            "japan_tanker",
            "japan_tanker_broad",
            "usa_military",
        ),
        default="japan_hint",
        help="japan_hint: JP+目的地(標準); japan_broad: より広い港名・JP略号; japan_jp: DESTINATION/FLAG/SHIPNAME のいずれかに JP を含む; usa_military: USS/USNS/NAVY/NAVAL/MILITARY 等; *_tanker*: タンカー推定も併用",
    )
    p.add_argument("--csv", type=Path, metavar="FILE", help="Write UTF-8 CSV (Excel 向け BOM 付き)")
    p.add_argument("--jsonl", type=Path, metavar="FILE", help="Write JSON Lines")
    p.add_argument("--with-meta", action="store_true", help="Add _filter_* diagnostic fields")
    p.add_argument(
        "--dedupe-by-ship-id",
        action="store_true",
        help="Deduplicate matched rows by SHIP_ID (keep first occurrence)",
    )
    p.add_argument(
        "--filter-lat-lon-prefix",
        action="store_true",
        help="モード一致・重複除去のあと、LAT 先頭2桁が 2x（20°台）かつ LON 先頭2桁が 4x または 5x の行だけ残す",
    )
    p.add_argument(
        "--exclude-lon-minus",
        action="store_true",
        help="LON の文字列が先頭（空白除く）が '-' の行を除く（西経の負表記を落とす）",
    )
    p.add_argument(
        "--include-gt-shiptypes",
        default="",
        metavar="CODES",
        help="GT_SHIPTYPE の許可リスト（カンマ区切り例: 17,18,71,88）。指定時はこのコードのみ残す",
    )
    p.add_argument(
        "--shipname-contains",
        action="append",
        default=[],
        metavar="TEXT",
        help="既存の抽出結果に加えて、SHIPNAME に指定文字列を含む行を追加する（大文字小文字無視、複数指定可）",
    )
    args = p.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"ERROR: input JSON not found: {in_path}", file=sys.stderr)
        sys.exit(1)

    rows = load_rows(in_path)
    matched = [r for r in rows if match_mode(r, args.mode)]
    shipname_needles = [str(x).strip().upper() for x in args.shipname_contains if str(x).strip()]
    shipname_added = 0
    gt_before = len(matched)
    allowed_gt: set[str] = {
        _normalize_type_code(x) for x in args.include_gt_shiptypes.split(",") if _normalize_type_code(x)
    }
    if allowed_gt:
        matched = [r for r in matched if _normalize_type_code(r.get("GT_SHIPTYPE")) in allowed_gt]
    deduped_count = 0
    if args.dedupe_by_ship_id:
        matched, deduped_count = dedupe_by_ship_id(matched)
    prefix_before = len(matched)
    if args.filter_lat_lon_prefix:
        matched = [r for r in matched if lat_lon_prefix_match(r)]

    lon_minus_before = len(matched)
    if args.exclude_lon_minus:
        matched = [r for r in matched if not str(r.get("LON") or "").strip().startswith("-")]

    if shipname_needles:
        matched, shipname_added = append_shipname_matches(matched, rows, shipname_needles)

    if args.with_meta:
        meta_broad = args.mode in ("japan_broad", "japan_tanker_broad")
        matched = [enrich_row(r, broad_dest_hits=meta_broad) for r in matched]

    if args.csv:
        if not matched:
            print("No rows to write.", file=sys.stderr)
            args.csv.parent.mkdir(parents=True, exist_ok=True)
            args.csv.write_text("", encoding="utf-8-sig")
            return
        keys: list[str] = []
        for r in matched:
            for k in r:
                if k not in keys:
                    keys.append(k)
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in matched:
                w.writerow({k: r.get(k, "") for k in keys})
        print(f"Wrote {len(matched)} rows -> {args.csv}", file=sys.stderr)
        return

    if args.jsonl:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl.open("w", encoding="utf-8") as f:
            for r in matched:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"Wrote {len(matched)} lines -> {args.jsonl}", file=sys.stderr)
        return

    # stdout: compact table
    extra = ""
    if shipname_needles:
        extra += f" added_shipname={shipname_added}"
    if allowed_gt:
        extra += f" after_gt_shiptype={len(matched)}/{gt_before}"
    if args.filter_lat_lon_prefix:
        extra += f" after_lat_lon_prefix={len(matched)}/{prefix_before}"
    if args.exclude_lon_minus:
        extra += f" after_exclude_lon_minus={len(matched)}/{lon_minus_before}"
    print(
        f"# input={in_path.name} mode={args.mode} total_rows={len(rows)} "
        f"matched={len(matched)} deduped={deduped_count}{extra}",
        file=sys.stderr,
    )
    for r in matched:
        name = (r.get("SHIPNAME") or "").strip()
        flag = row_flag(r)
        dest = row_destination(r)
        lat = r.get("LAT")
        lon = r.get("LON")
        sid = r.get("SHIP_ID")
        cap = r.get("_capture_index")
        cap_txt = f"\tCAP={cap}" if cap is not None else ""
        print(f"{name}\tFLAG={flag}\tDEST={dest}\tLAT={lat}\tLON={lon}\tSHIP_ID={sid}{cap_txt}")


if __name__ == "__main__":
    main()
