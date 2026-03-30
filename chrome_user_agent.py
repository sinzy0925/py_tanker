"""Chrome CDP 起動時の User-Agent 解決（cdp1 / cdp3 共通）。"""

from __future__ import annotations

import sys
from pathlib import Path

# argparse の --user-agent 単独指定時に使う const（CLI から直接は使わない）
UA_FROM_FILE = ":from-file:"

DEFAULT_CHROME_USER_AGENT_FILE = Path("chrome_user_agent.txt")


def _first_non_comment_line(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        return s
    print(
        f"ERROR: User-Agent として使える行がありません（# で始まらない行を1行以上）: {path}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def resolve_chrome_user_agent(
    user_agent: str | None,
    user_agent_file: Path | None,
) -> str:
    """
    --user-agent 未指定: 空（Chrome 既定）。
    --user-agent のみ: user_agent_file または chrome_user_agent.txt を読む。
    --user-agent "文字列": その文字列を使う。
    """
    if user_agent is None:
        return ""
    if user_agent == UA_FROM_FILE:
        path = user_agent_file if user_agent_file is not None else DEFAULT_CHROME_USER_AGENT_FILE
        path = path.resolve()
        if not path.is_file():
            print(f"ERROR: User-Agent ファイルがありません: {path}", file=sys.stderr)
            raise SystemExit(1)
        return _first_non_comment_line(path)
    return user_agent.strip()
