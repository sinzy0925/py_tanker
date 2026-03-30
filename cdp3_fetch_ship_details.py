"""
out.jsonl の SHIP_ID 一覧を使い、CDP 接続した Chrome で船舶詳細ページを開いて
Fetch/XHR の JSON レスポンスを回収する。

使い方:
  python cdp3_fetch_ship_details.py
  python cdp3_fetch_ship_details.py --input ship_data/out.jsonl --output ship_data/ship_details.json --show-all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

DEFAULT_DETAILS_URL_TEMPLATE = "https://www.marinetraffic.com/en/ais/details/ships/shipid:{ship_id}"
SHIP_DATA_DIR = Path("ship_data")
DEFAULT_SHIP_LIST_JSONL = SHIP_DATA_DIR / "out.jsonl"
DEFAULT_SHIP_DETAILS_JSON = SHIP_DATA_DIR / "ship_details.json"
try:
    JST = ZoneInfo("Asia/Tokyo")
except ZoneInfoNotFoundError:
    # Windows 等で tzdata 未導入でも日本時間変換できるようにする。
    JST = timezone(timedelta(hours=9), name="JST")


def _default_chrome_headless() -> bool:
    """Linux ではヘッドレス既定。Windows ではウィンドウ表示。"""
    return sys.platform.startswith("linux")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch ship detail JSONs with Playwright CDP")
    p.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_SHIP_LIST_JSONL,
        help=f"Input JSONL path (default: {DEFAULT_SHIP_LIST_JSONL})",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SHIP_DETAILS_JSON,
        help=f"Output JSON path (default: {DEFAULT_SHIP_DETAILS_JSON})",
    )
    p.add_argument("--cdp-url", default="http://127.0.0.1:9222", help="CDP endpoint URL")
    p.add_argument("--chrome-path", default="", help="Chrome executable path (optional)")
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
    p.add_argument("--keep-chrome-open", action="store_true", help="Keep auto-launched Chrome open")
    p.add_argument(
        "--chrome-headless",
        action=argparse.BooleanOptionalAction,
        default=_default_chrome_headless(),
        help="Pass --headless=new to auto-launched Chrome (default: on for Linux, off for Windows)",
    )
    p.add_argument("--timeout-ms", type=int, default=120_000, help="Navigation timeout in ms")
    p.add_argument("--post-open-wait-ms", type=int, default=7_000, help="Wait after opening details page")
    p.add_argument(
        "--details-url-template",
        default=DEFAULT_DETAILS_URL_TEMPLATE,
        help="Detail URL template (use {ship_id})",
    )
    p.add_argument("--limit", type=int, default=0, help="Process first N ships only (0=all)")
    p.add_argument("--show-all", action="store_true", help="Include all matches per ship in output")
    return p.parse_args()


def _extract_port(cdp_url: str) -> int:
    parsed = urlparse(cdp_url)
    if parsed.port:
        return parsed.port
    if parsed.scheme in {"http", "https"}:
        return 9222
    raise ValueError(f"Invalid cdp-url: {cdp_url}")


def _detect_chrome_path() -> str:
    candidates = [
        os.environ.get("PROGRAMFILES", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("PROGRAMFILES(X86)", "") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("LOCALAPPDATA", "") + r"\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    found = shutil.which("chrome")
    if found:
        return found
    raise FileNotFoundError("chrome.exe が見つかりません。--chrome-path を指定してください。")


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
    chrome_path = args.chrome_path.strip() or _detect_chrome_path()
    port = _extract_port(args.cdp_url)
    user_data_dir = Path(".chrome-cdp-profile").resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={str(user_data_dir)}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
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


def load_targets(path: Path, limit: int) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"input not found: {path}")
    out: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        ship_id = str(row.get("SHIP_ID") or "").strip()
        if not ship_id:
            continue
        out.append({"ship_id": ship_id, "ship_name": str(row.get("SHIPNAME") or "").strip()})
    if limit > 0:
        return out[:limit]
    return out


def _score_payload(payload: Any) -> int:
    if isinstance(payload, dict):
        score = 10
        for k in ("vessel", "ship", "imo", "mmsi", "destination", "lastPos", "positions", "port"):
            if k in payload:
                score += 20
        score += min(len(payload), 50)
        return score
    if isinstance(payload, list):
        return 5 + min(len(payload), 30)
    return 0


def _to_jst_from_iso(iso_utc: str) -> str | None:
    try:
        return datetime.fromisoformat(iso_utc).astimezone(JST).isoformat()
    except Exception:
        return None


def _to_jst_from_unix(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).astimezone(JST).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _extract_payload_time_jst(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    time_keys = (
        "timestamp",
        "eta",
        "etaCalc",
        "departureTimestamp",
        "arrivalTimestamp",
        "lastPortTime",
        "previousArrivalTimestamp",
    )
    out: dict[str, str] = {}
    for key in time_keys:
        jst = _to_jst_from_unix(payload.get(key))
        if jst:
            out[f"{key}_jst"] = jst
    return out


def _prev_output_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_prev{output.suffix}")


def rotate_output_if_exists(output: Path) -> Path | None:
    if not output.exists():
        return None
    prev_path = _prev_output_path(output)
    if prev_path.exists():
        prev_path.unlink()
    output.replace(prev_path)
    return prev_path


async def collect_detail_jsons(
    page: Page, ship_id: str, ship_name: str, args: argparse.Namespace
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    parse_tasks: set[asyncio.Task[Any]] = set()

    async def process_response(response: Any) -> None:
        req = response.request
        if req.resource_type not in {"fetch", "xhr"}:
            return

        payload: Any
        try:
            payload = await response.json()
        except Exception:
            try:
                payload = json.loads(await response.text())
            except Exception:
                return

        hay_url = response.url
        hay_post = req.post_data or ""
        try:
            hay_payload = json.dumps(payload, ensure_ascii=False)
        except Exception:
            hay_payload = str(payload)

        if ship_id not in hay_url and ship_id not in hay_post and ship_id not in hay_payload:
            return

        captured_at_utc = datetime.now(timezone.utc).isoformat()
        rec = {
            "captured_at_utc": captured_at_utc,
            "captured_at_jst": _to_jst_from_iso(captured_at_utc),
            "url": hay_url,
            "status": response.status,
            "method": req.method,
            "resource_type": req.resource_type,
            "payload": payload,
        }
        payload_time_jst = _extract_payload_time_jst(payload)
        if payload_time_jst:
            rec["payload_time_jst"] = payload_time_jst
        matches.append(rec)

    def on_response(response: Any) -> None:
        task = asyncio.create_task(process_response(response))
        parse_tasks.add(task)
        task.add_done_callback(lambda t: parse_tasks.discard(t))

    page.on("response", on_response)
    detail_url = args.details_url_template.format(ship_id=ship_id)
    try:
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            await page.goto(detail_url, wait_until="commit", timeout=args.timeout_ms)
        await page.wait_for_timeout(args.post_open_wait_ms)
    finally:
        page.remove_listener("response", on_response)

    if parse_tasks:
        await asyncio.gather(*parse_tasks, return_exceptions=True)

    if not matches:
        return {
            "ship_id": ship_id,
            "ship_name": ship_name,
            "detail_url": detail_url,
            "ok": False,
            "matched_count": 0,
            "message": "details JSON not captured",
        }

    ordered = sorted(matches, key=lambda m: _score_payload(m.get("payload")), reverse=True)
    out: dict[str, Any] = {
        "ship_id": ship_id,
        "ship_name": ship_name,
        "detail_url": detail_url,
        "ok": True,
        "matched_count": len(matches),
        "best": ordered[0],
    }
    if args.show_all:
        out["matches"] = ordered
    return out


async def run(args: argparse.Namespace) -> int:
    targets = load_targets(args.input, args.limit)
    if not targets:
        print(f"NG: no SHIP_ID targets in {args.input}")
        return 1

    async with async_playwright() as pw:
        browser, chrome_proc = await connect_browser_cdp(pw, args)
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = await browser.new_context()
        page = await context.new_page()
        results: list[dict[str, Any]] = []

        try:
            for idx, target in enumerate(targets, start=1):
                ship_id = target["ship_id"]
                ship_name = target["ship_name"]
                print(f"[{idx}/{len(targets)}] ship_id={ship_id} ship_name={ship_name}")
                one = await collect_detail_jsons(page, ship_id, ship_name, args)
                results.append(one)
        finally:
            await browser.close()
            if chrome_proc and (not args.keep_chrome_open):
                chrome_proc.terminate()

    ok_count = sum(1 for r in results if r.get("ok"))
    created_at_utc = datetime.now(timezone.utc).isoformat()
    payload = {
        "created_at_utc": created_at_utc,
        "created_at_jst": _to_jst_from_iso(created_at_utc),
        "input": str(args.input),
        "total_targets": len(targets),
        "ok_targets": ok_count,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rotated = rotate_output_if_exists(args.output)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if rotated:
        print(f"Rotated previous output -> {rotated}")
    print(f"Done: ok_targets={ok_count}/{len(targets)} -> {args.output}")
    return 0 if ok_count > 0 else 1


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
