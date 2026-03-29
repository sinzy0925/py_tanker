"""
日本向け原油タンカー「候補」を広く拾うためのヒューリスティック（AIS だけ・推定）。

- 原油かどうかは AIS では判別できないため「タンカー船種」のみ使用。
- 日本関連の目印: 目的地テキストの部分一致 + 日本 MID の MMSI（431/432）。
"""
from __future__ import annotations

import re

# ITU MID: 日本の船舶局 431, 432（便宜置籍でブレる前提で「目印」扱い）
JP_MMSI_PREFIXES: tuple[str, ...] = ("431", "432")

TANKER_TYPE_MIN = 80
TANKER_TYPE_MAX = 89

# 大文字小文字無視で部分一致。広めに取る（誤検知は後段で捨てる想定）。
JAPAN_DEST_KEYWORDS: tuple[str, ...] = (
    "JAPAN",
    "TOKYO",
    "YOKOHAMA",
    "CHIBA",
    "KAWASAKI",
    "YOKKAICHI",
    "MIZUSHIMA",
    "SAKAI",
    "KIIRE",
    "SHIBUSHI",
    "TOMAKOMAI",
    "NGO",
    "TYO",
    "NAGOYA",
    "OKINAWA",
    "NAHA",
    "KINWAN",
    "HAKATA",
    "MOJI",
    "MURORAN",
    "KASHIMA",
    "CHITA",
    "TSURUGA",
    "SHIMONOSEKI",
    "HIBIKI",
    "SHIMOTSU",
    "JP TYO",
    "JP YOK",
    "JP CHB",
    "JP NGO",
    "JP SBS",
    "JP KRE",
    "JP KII",
    "JP YKK",
    "JP SAK",
    "JP MIZ",
    "JP YOKOHAMA",
    "JP CHIBA",
    "JP TOKYO",
    "FOR ORDERS JP",  # 稀
)

# japan_hint より広く取る（誤検知増の代わりに取りこぼし減）。主に港名・略号・経由表現。
JAPAN_DEST_KEYWORDS_BROAD: tuple[str, ...] = (
    "JPN",
    "NIHON",
    "NIPPON",
    "OSAKA",
    "KOBE",
    "UKB",
    "IMABARI",
    "SASEBO",
    "SHIMIZU",
    "TOKUSHIMA",
    "TAKAMATSU",
    "MATSUYAMA",
    "FUKUOKA",
    "KAGOSHIMA",
    "MIYAZAKI",
    "HAKODATE",
    "AOMORI",
    "ISHINOMAKI",
    "SENDAI",
    "ONAHAMA",
    "HIMEJI",
    "KINUURA",
    "YAWATAHAMA",
    "SAIKAI",
    "YOKOSUKA",
    "VIA JAPAN",
    "VIA JP",
    "TO JAPAN",
    "FOR JAPAN",
    "JAPAN FOR",
    "E JAPAN",
    "W JAPAN",
    "SEA OF JAPAN",
    "EAST CHINA SEA",  # 日本寄港前の航路表記で出ることがある（誤検知あり）
    "RYUKYU",
    "AMAMI",
    "ISHIGAKI",
    "MIYAKO",
    "KEELUNG>JAPAN",
    "PUSAN>JAPAN",
    "BUSAN>JAPAN",
    "KOREA>JAPAN",
    "JP UKB",
    "JP OSA",
    "JP SMZ",
    "JP KIJ",
    "JP IMJ",
    "JP TKS",
    "JP TXD",
    "JP YKK",
    "JP YOS",
    "JP NAH",
    "JP OKA",
)


def clean_ais_string(s: str | None) -> str:
    if not s:
        return ""
    return str(s).replace("@", " ").strip().upper()


def is_tanker_type(type_code: int | None) -> bool:
    if type_code is None:
        return False
    try:
        t = int(type_code)
    except (TypeError, ValueError):
        return False
    if t == 0:
        return False
    return TANKER_TYPE_MIN <= t <= TANKER_TYPE_MAX


def mmsi_has_japan_mid(mmsi: str | None) -> bool:
    if not mmsi or len(mmsi) < 3:
        return False
    return mmsi.startswith(JP_MMSI_PREFIXES)


def destination_japan_hits(destination_raw: str | None) -> list[str]:
    d = clean_ais_string(destination_raw)
    if not d:
        return []
    padded = f" {d} "
    hits: list[str] = []
    seen: set[str] = set()
    for kw in JAPAN_DEST_KEYWORDS:
        ku = kw.upper()
        if ku in padded or ku in d:
            if ku not in seen:
                seen.add(ku)
                hits.append(kw)
    # UN/LOCODE 風 JP***
    if " JP" in padded:
        for token in d.replace(",", " ").split():
            if len(token) >= 4 and token.startswith("JP") and token[2:3].isalpha():
                tag = f"LOCODE_LIKE:{token[:5]}"
                if tag not in seen:
                    seen.add(tag)
                    hits.append(tag)
    return hits


# UN/LOCODE 日本: JP + 英字2〜4（AIS で JPXXX / JPXXXX 風に崩れることがある）
_RE_JP_LOCODE_TOKEN = re.compile(r"\bJP[A-Z]{2,4}\b", re.IGNORECASE)


def destination_japan_hits_broad(destination_raw: str | None) -> list[str]:
    """destination_japan_hits より広い。港名・JPN・JP 始まりトークン・経由表現など。"""
    base = destination_japan_hits(destination_raw)
    d = clean_ais_string(destination_raw)
    if not d:
        return list(base)
    padded = f" {d} "
    hits: list[str] = list(base)
    seen: set[str] = set(hits)
    for kw in JAPAN_DEST_KEYWORDS_BROAD:
        ku = kw.upper()
        if ku in padded or ku in d:
            if ku not in seen:
                seen.add(ku)
                hits.append(kw)
    for m in _RE_JP_LOCODE_TOKEN.finditer(d):
        token = m.group(0).upper()
        tag = f"JP_TOKEN:{token}"
        if tag not in seen:
            seen.add(tag)
            hits.append(tag)
    return hits


def japan_related_guess(
    *,
    ship_type: int | None,
    destination_raw: str | None,
    mmsi: str | None,
) -> tuple[bool, dict]:
    dest_hits = destination_japan_hits(destination_raw)
    mmsi_jp = mmsi_has_japan_mid(mmsi)
    detail = {
        "destination_hits": dest_hits,
        "mmsi_japan_mid": mmsi_jp,
    }
    return (bool(dest_hits) or mmsi_jp, detail)
