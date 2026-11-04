"""Local stdio peer that sends a benign notification at a host-controlled boundary."""

import json
import sys
import threading
import time
from pathlib import Path

from raw_result_server import response


def notify(marker, kind):
    deadline = time.monotonic() + 10
    while not marker.exists():
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)
    print(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "level": "info",
                    "data": "orchard" if kind == "credential" else "x" * 2048,
                },
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    marker, kind = sys.argv[1:]
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    **response(request["method"], request.get("params", {}), "valid", "clean"),
                }
            ),
            flush=True,
        )
        if request["method"] == "tools/list":
            threading.Thread(target=notify, args=(Path(marker), kind), daemon=True).start()
