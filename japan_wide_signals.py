"""
目的地文字列から「日本関連」の目印を返す（MarineTraffic スナップショット向けヒューリスティック）。

cdp2_mt_snapshot_filter から利用。
"""
from __future__ import annotations

import re

# 標準: JP 旗・日本港の略号・よくある日本向け表記
_JP_HINT_PATTERNS = [
    re.compile(r"\bJP\s+[A-Z]{2,5}\b", re.I),  # JP CHB, JP OIT 等
    re.compile(r"\bJPO[A-Z]{2,4}\b", re.I),  # JPOIT 等
    re.compile(r"\bJPK[A-Z]{2,4}\b", re.I),
    re.compile(r"\bJPN[A-Z]{2,4}\b", re.I),
    re.compile(r"\bOKINAWA\b", re.I),
    re.compile(r"\bTOKYO\b", re.I),
    re.compile(r"\bYOKOHAMA\b", re.I),
    re.compile(r"\bNAGOYA\b", re.I),
    re.compile(r"\bOSAKA\b", re.I),
    re.compile(r"\bKOBE\b", re.I),
    re.compile(r"\bMOJI\b", re.I),
    re.compile(r"\bHAKATA\b", re.I),
    re.compile(r"\bCHIBA\b", re.I),
    re.compile(r"\bJAPAN\b", re.I),
]

# 広め: 追加の略号・表記ゆれ
_BROAD_EXTRA = [
    re.compile(r"\bJP\b", re.I),
    re.compile(r"\bJPN\b", re.I),
    re.compile(r"\bJAP\b", re.I),
    re.compile(r"\bSAKAI\b", re.I),
    re.compile(r"\bMIZUSHIMA\b", re.I),
    re.compile(r"\bSHIMIZU\b", re.I),
    re.compile(r"\bMURORAN\b", re.I),
    re.compile(r"\bKII\b", re.I),  # JPKII の KII 単独は誤爆しやすいので broad のみに近い扱い
]


def destination_japan_hits(destination_raw: str | None) -> list[str]:
    if not destination_raw or not str(destination_raw).strip():
        return []
    s = str(destination_raw).strip().upper()
    hits: list[str] = []
    for pat in _JP_HINT_PATTERNS:
        if pat.search(s):
            hits.append(pat.pattern)
    return hits


def destination_japan_hits_broad(destination_raw: str | None) -> list[str]:
    base = destination_japan_hits(destination_raw)
    if not destination_raw or not str(destination_raw).strip():
        return base
    s = str(destination_raw).strip().upper()
    extra: list[str] = []
    for pat in _BROAD_EXTRA:
        if pat.search(s):
            extra.append(pat.pattern)
    # 重複除去（順序: 標準ヒット → 広め追加）
    seen: set[str] = set(base)
    out = list(base)
    for h in extra:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out
