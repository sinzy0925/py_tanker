"""
Connect to AISstream, subscribe to Hormuz AOI (SPEC default), save first raw message to sample_aisstream.jsonl
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
import websockets

from config_aisstream import BOUNDING_BOXES, DEFAULT_FILTER_MESSAGE_TYPES

load_dotenv()
API_KEY = os.environ.get("AISSTREAM_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: AISSTREAM_API_KEY is empty. Set it in .env", file=sys.stderr)
    sys.exit(1)

URL = "wss://stream.aisstream.io/v0/stream"
SUBSCRIBE = {
    "APIKey": API_KEY,
    "BoundingBoxes": BOUNDING_BOXES,
    "FilterMessageTypes": list(DEFAULT_FILTER_MESSAGE_TYPES),
}
OUTFILE = "sample_aisstream.jsonl"


async def main() -> None:
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps(SUBSCRIBE))
        print(f"Subscribed. Writing first message to {OUTFILE} ...")
        raw = await asyncio.wait_for(ws.recv(), timeout=120.0)
        rec = {
            "received_at_utc": datetime.now(timezone.utc).isoformat(),
            "raw": json.loads(raw),
        }
        with open(OUTFILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("Done.")
        mt = rec["raw"].get("MessageType", "?")
        print(f"MessageType: {mt}")


if __name__ == "__main__":
    asyncio.run(main())
