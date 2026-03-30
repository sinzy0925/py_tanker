"""Chrome 実行ファイルパス（CDP 起動用）。Windows / Linux 双方で自動検出。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def detect_chrome_executable() -> str:
    if sys.platform == "win32":
        candidates = [
            os.environ.get("PROGRAMFILES", "") + r"\Google\Chrome\Application\chrome.exe",
            os.environ.get("PROGRAMFILES(X86)", "") + r"\Google\Chrome\Application\chrome.exe",
            os.environ.get("LOCALAPPDATA", "") + r"\Google\Chrome\Application\chrome.exe",
        ]
        for path in candidates:
            if path and Path(path).is_file():
                return path
    else:
        for name in (
            "google-chrome-stable",
            "google-chrome",
            "chromium",
            "chromium-browser",
        ):
            found = shutil.which(name)
            if found:
                return found
        for path in (
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
        ):
            if Path(path).is_file():
                return path

    found = shutil.which("chrome")
    if found:
        return found
    raise FileNotFoundError(
        "Chrome が見つかりません。Google Chrome をインストールするか --chrome-path を指定してください。"
    )
