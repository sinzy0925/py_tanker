"""
手元に保存した Equasis「Ship info」ページの HTML を解析し、船舶要目を JSON で出す。

使い方:
  ブラウザで Equasis にログインし、対象船の Ship info を開いた状態で
  「名前を付けて保存」で HTML のみ（または完全）を保存し、そのファイルを渡す。

  python equasis_parse_saved_html.py saved_ship.html
  python equasis_parse_saved_html.py saved_ship.html -o ship.json

注意:
  - 本スクリプトは「保存済み HTML」のパースのみ。サイトへの自動ログイン・大量取得は
    利用規約・技術的制約の両面で行わないこと。
  - Equasis のマークアップ変更で取りこぼしが出る場合は、保存 HTML を共有して調整する。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore[misc, assignment]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _imo_from_text(text: str) -> str | None:
    m = re.search(r"IMO\s*n[°o]?\s*(\d{7})", text, re.I)
    return m.group(1) if m else None


def _name_from_text(text: str, imo: str | None) -> str | None:
    # "AZUMASAN - IMO n° 9397157" 風
    m = re.search(r"^(.+?)\s*-\s*IMO\s*n", text, re.I | re.M)
    if m:
        return _norm(m.group(1))
    if imo:
        m2 = re.search(rf"^(.+?)\s*-\s*IMO\s*[°o]?\s*{imo}", text, re.I | re.M)
        if m2:
            return _norm(m2.group(1))
    return None


def _last_update(text: str) -> str | None:
    m = re.search(
        r"Last update of ship particulars\s+(\d{1,2}/\d{1,2}/\d{4})",
        text,
        re.I,
    )
    return m.group(1) if m else None


def _parse_key_value_tables(soup: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        k = _norm(cells[0].get_text(" ", strip=True))
        v = _norm(cells[1].get_text(" ", strip=True))
        if not k or len(k) > 80:
            continue
        if k not in out and v:
            out[k] = v
    return out


def _parse_dl(soup: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for dt in soup.find_all("dt"):
        key = _norm(dt.get_text(" ", strip=True))
        dd = dt.find_next_sibling("dd")
        if key and dd is not None:
            out[key] = _norm(dd.get_text(" ", strip=True))
    return out


_LABEL_MAP = {
    "flag": "Flag",
    "call sign": "CallSign",
    "mmsi": "MMSI",
    "gross tonnage": "GrossTonnage",
    "dwt": "DWT",
    "deadweight": "DWT",
    "type of ship": "ShipType",
    "year of build": "YearOfBuild",
    "status": "Status",
}


def _merge_kv(raw: dict[str, str]) -> dict[str, str]:
    flat: dict[str, str] = {}
    for label, val in raw.items():
        key = label.lower().split("(")[0].strip()
        for needle, outk in _LABEL_MAP.items():
            if needle in key:
                if outk not in flat:
                    flat[outk] = val
                break
    return flat


def parse_equasis_html(html: str) -> dict[str, Any]:
    if BeautifulSoup is None:
        raise SystemExit(
            "BeautifulSoup4 が必要です: pip install beautifulsoup4"
        )

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    imo = _imo_from_text(text)
    name = _name_from_text(text, imo) or None

    kv = {}
    kv.update(_parse_dl(soup))
    kv.update(_parse_key_value_tables(soup))

    merged = _merge_kv(kv)

    # テーブル見出しが現地語のとき用に、ラベル文字列も残す
    particulars: dict[str, str] = {}
    for k, v in kv.items():
        if any(x in k.lower() for x in ("flag", "mmsi", "call", "tonnage", "dwt", "type", "build", "status")):
            particulars[k] = v

    result: dict[str, Any] = {
        "IMONumber": imo,
        "ShipName": name,
        "LastUpdateShipParticulars": _last_update(text),
    }
    result.update({k: v for k, v in merged.items() if v})

    if particulars:
        result["_raw_particulars_labels"] = particulars

    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse saved Equasis Ship info HTML to JSON")
    ap.add_argument("html", type=Path, help="Path to saved .html file")
    ap.add_argument("-o", "--out", type=Path, help="Write JSON (UTF-8); default: stdout")
    args = ap.parse_args()

    if not args.html.is_file():
        print(f"ERROR: not found: {args.html}", file=sys.stderr)
        sys.exit(1)

    html = args.html.read_text(encoding="utf-8", errors="replace")
    data = parse_equasis_html(html)
    line = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    if args.out:
        args.out.write_text(line, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(line)


if __name__ == "__main__":
    main()
