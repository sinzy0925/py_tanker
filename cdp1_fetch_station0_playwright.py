"""
MarineTraffic のページを Chrome で開き、リロード時に発生する
Fetch/XHR のうち `station:0` を含む JSON レスポンスを保存する。

使い方:
  python cdp1_fetch_station0_playwright.py
  python cdp1_fetch_station0_playwright.py --output ship_data/station0_all.json --show-all
  python cdp1_fetch_station0_playwright.py --cdp-url http://127.0.0.1:9222
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from chrome_cdp_paths import detect_chrome_executable

DEFAULT_URL = "https://www.marinetraffic.com/en/ais/home/centerx:51.5/centery:27.5/zoom:7"
SHIP_DATA_DIR = Path("ship_data")
DEFAULT_OUTPUT_JSON = SHIP_DATA_DIR / "station0_all.json"


def _default_chrome_headless() -> bool:
    """Linux（サーバ・Cloud Shell 等）ではヘッドレス既定。Windows ではウィンドウ表示。"""
    return sys.platform.startswith("linux")


def _default_post_reload_wait_ms() -> int:
    """クラウド・遅延回線では station:0 が遅れて返ることがある。"""
    return 22_000 if sys.platform.startswith("linux") else 12_000


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Capture MarineTraffic station:0 JSON by Playwright"
    )
    p.add_argument("--url", default=DEFAULT_URL, help="Target page URL")
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT_JSON})",
    )
    p.add_argument(
        "--show-all",
        action="store_true",
        help="Save all matched station:0 responses instead of best one",
    )
    p.add_argument(
        "--timeout-ms",
        type=int,
        default=120_000,
        help="Navigation timeout in ms",
    )
    p.add_argument(
        "--pre-reload-wait-ms",
        type=int,
        default=5_000,
        help="Wait before reload in ms",
    )
    p.add_argument(
        "--post-reload-wait-ms",
        type=int,
        default=None,
        help="Wait after reload in ms (default: 22000 on Linux, 12000 elsewhere)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="失敗時の調査用: marinetraffic.com への fetch/XHR の URL を stderr に列挙",
    )
    p.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9222",
        help="CDP endpoint URL (ex: http://127.0.0.1:9222)",
    )
    p.add_argument(
        "--chrome-path",
        default="",
        help="Chrome executable path (optional, auto-detect if omitted)",
    )
    p.add_argument(
        "--launch-cdp-chrome",
        action="store_true",
        default=True,
        help="Launch Chrome with remote debugging before connecting",
    )
    p.add_argument(
        "--no-launch-cdp-chrome",
        action="store_false",
        dest="launch_cdp_chrome",
        help="Do not launch Chrome; connect to existing CDP endpoint",
    )
    p.add_argument(
        "--keep-chrome-open",
        action="store_true",
        help="Keep auto-launched Chrome running after capture",
    )
    p.add_argument(
        "--chrome-headless",
        action=argparse.BooleanOptionalAction,
        default=_default_chrome_headless(),
        help="Pass --headless=new to auto-launched Chrome (default: on for Linux, off for Windows)",
    )
    ns = p.parse_args()
    if ns.post_reload_wait_ms is None:
        ns.post_reload_wait_ms = _default_post_reload_wait_ms()
    return ns


def _extract_port(cdp_url: str) -> int:
    parsed = urlparse(cdp_url)
    if parsed.port:
        return parsed.port
    if parsed.scheme in {"http", "https"}:
        return 9222
    raise ValueError(f"Invalid cdp-url: {cdp_url}")


def _wait_cdp_ready(cdp_url: str, timeout_sec: float = 15.0) -> None:
    deadline = time.time() + timeout_sec
    probe_url = cdp_url.rstrip("/") + "/json/version"
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(probe_url, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except (URLError, OSError) as exc:
            last_err = exc
            time.sleep(0.3)
    raise RuntimeError(f"CDP endpoint not ready: {probe_url}, error={last_err}")


def _launch_chrome_for_cdp(args: argparse.Namespace) -> subprocess.Popen[Any]:
    chrome_path = args.chrome_path.strip() or detect_chrome_executable()
    port = _extract_port(args.cdp_url)
    user_data_dir = Path(".chrome-cdp-profile").resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={str(user_data_dir)}",
        "--no-first-run",
        "--no-default-browser-check",
        # サーバ・Cloud Shell 向け: 共有メモリ不足と自動化フラグ緩和
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ]
    if sys.platform.startswith("linux"):
        cmd.append("--no-sandbox")
    if getattr(args, "chrome_headless", False):
        cmd.append("--headless=new")
    cmd.append("about:blank")
    proc = subprocess.Popen(cmd)
    _wait_cdp_ready(args.cdp_url)
    return proc


async def connect_browser_cdp(
    pw: Any, args: argparse.Namespace
) -> tuple[Browser, subprocess.Popen[Any] | None]:
    chrome_proc: subprocess.Popen[Any] | None = None
    if args.launch_cdp_chrome:
        chrome_proc = _launch_chrome_for_cdp(args)
    else:
        _wait_cdp_ready(args.cdp_url)

    browser = await pw.chromium.connect_over_cdp(args.cdp_url)
    return browser, chrome_proc


def _request_mentions_station0(url: str, post_data: str) -> bool:
    """URL エンコードされた station:0（station%3A0 等）も拾う。"""
    if "station:0" in post_data:
        return True
    if "station:0" in url:
        return True
    try:
        if "station:0" in unquote(url):
            return True
    except Exception:
        pass
    return "station%3a0" in url.lower()


def _score_payload(payload: Any) -> int:
    if isinstance(payload, dict):
        score = 10
        if "rows" in payload:
            score += 50
        if "data" in payload:
            score += 20
        return score
    if isinstance(payload, list):
        return 5
    return 0


async def capture_station0(page: Page, args: argparse.Namespace) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    parse_tasks: set[asyncio.Task[Any]] = set()
    debug_mt_urls: list[str] = []

    def on_response_debug(response: Any) -> None:
        if not args.verbose:
            return
        try:
            rt = response.request.resource_type
            if rt not in {"fetch", "xhr"}:
                return
            u = response.url
            if "marinetraffic.com" in u:
                debug_mt_urls.append(u)
        except Exception:
            pass

    async def process_response(response: Any) -> None:
        req = response.request
        if req.resource_type not in {"fetch", "xhr"}:
            return

        url = response.url
        post_data = req.post_data or ""
        if not _request_mentions_station0(url, post_data):
            return

        payload: Any
        try:
            payload = await response.json()
        except Exception:
            try:
                payload = json.loads(await response.text())
            except Exception:
                return

        matches.append(
            {
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "name_hint": "station:0",
                "url": url,
                "method": req.method,
                "resource_type": req.resource_type,
                "status": response.status,
                "payload": payload,
            }
        )

    def on_response(response: Any) -> None:
        task = asyncio.create_task(process_response(response))
        parse_tasks.add(task)
        task.add_done_callback(lambda t: parse_tasks.discard(t))

    page.on("response", on_response)
    if args.verbose:
        page.on("response", on_response_debug)

    await page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
    await page.wait_for_timeout(args.pre_reload_wait_ms)
    try:
        # MarineTraffic はバックグラウンド通信が多く networkidle になりにくい。
        await page.reload(wait_until="domcontentloaded", timeout=args.timeout_ms)
    except PlaywrightTimeoutError:
        # 画面更新トリガーだけ入っていれば、レスポンス監視は継続して行う。
        await page.evaluate("() => window.location.reload()")
    await page.wait_for_timeout(args.post_reload_wait_ms)

    if parse_tasks:
        await asyncio.gather(*parse_tasks, return_exceptions=True)

    if args.verbose and debug_mt_urls:
        print("[verbose] marinetraffic.com fetch/XHR URLs (dedup, max 100):", file=sys.stderr)
        for u in sorted(set(debug_mt_urls))[:100]:
            print(u, file=sys.stderr)

    return matches


def build_output(matches: list[dict[str, Any]], show_all: bool) -> dict[str, Any]:
    if not matches:
        return {
            "ok": False,
            "message": "station:0 を含む JSON レスポンスを取得できませんでした。",
            "matched_count": 0,
        }

    ordered = sorted(matches, key=lambda m: _score_payload(m.get("payload")), reverse=True)
    best = ordered[0]
    if show_all:
        return {
            "ok": True,
            "matched_count": len(matches),
            "best": best,
            "matches": ordered,
        }
    return {
        "ok": True,
        "matched_count": len(matches),
        "best": best,
    }


async def run(args: argparse.Namespace) -> int:
    async with async_playwright() as pw:
        browser, chrome_proc = await connect_browser_cdp(pw, args)
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = await browser.new_context()
        page = await context.new_page()

        try:
            matches = await capture_station0(page, args)
        finally:
            await browser.close()
            if chrome_proc and (not args.keep_chrome_open):
                chrome_proc.terminate()

    result = build_output(matches, args.show_all)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if result.get("ok"):
        best = result["best"]
        print(f"OK: station:0 JSON captured -> {args.output}")
        print(f"matched_count={result['matched_count']} status={best.get('status')} url={best.get('url')}")
        return 0

    print(f"NG: {result.get('message')}")
    print(f"Saved diagnostic JSON -> {args.output}")
    if not args.verbose:
        print(
            "ヒント: --verbose で marinetraffic への fetch/XHR URL を列挙。"
            " Linux では待ちを延ばす例: --post-reload-wait-ms 45000",
            file=sys.stderr,
        )
    return 1


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
