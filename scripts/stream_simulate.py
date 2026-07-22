"""S0 — stream simulator. Generates sensor/scan events into a DMS stream.

In-process by default (calls the buffer directly — no server needed); pass --url
to POST to a running API instead.

Run: python -m scripts.stream_simulate --stream sensors --rate 50 --seconds 10
"""
from __future__ import annotations

import argparse
import random
import time


def _event(stream: str, i: int) -> dict:
    kind = random.choice(["temperature", "humidity", "scan"])
    if kind == "scan":
        return {"event_id": f"{stream}-{i}", "kind": "scan",
                "sku": f"SKU-{random.randint(90001, 90050)}",
                "location_code": random.choice(["WH-A", "WH-B", "WH-D"])}
    return {"event_id": f"{stream}-{i}", "kind": kind,
            "location_code": random.choice(["WH-A", "WH-B", "WH-D"]),
            "value": round(random.uniform(-5, 40), 2)}


def run_inprocess(stream: str, rate: int, seconds: int) -> dict:
    from packs.dms.streams import buffer, registry

    registry.create_stream(stream, name=f"sim {stream}", created_by="simulator")
    sent = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        batch = [_event(stream, sent + j) for j in range(rate)]
        buffer.append_events(stream, batch)
        sent += rate
        time.sleep(1)
    flushed = buffer.flush(stream)
    return {"stream": stream, "sent": sent, "final_flush": flushed}


def run_http(stream: str, rate: int, seconds: int, url: str, api_key: str) -> dict:
    import urllib.request

    sent = 0
    deadline = time.time() + seconds
    endpoint = f"{url.rstrip('/')}/dms/streams/{stream}/events"
    while time.time() < deadline:
        import json

        body = json.dumps({"events": [_event(stream, sent + j) for j in range(rate)]}).encode()
        req = urllib.request.Request(endpoint, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-API-Key": api_key})
        urllib.request.urlopen(req, timeout=10).read()
        sent += rate
        time.sleep(1)
    return {"stream": stream, "sent": sent, "via": "http"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stream", default="sensors")
    ap.add_argument("--rate", type=int, default=50, help="events per second")
    ap.add_argument("--seconds", type=int, default=10)
    ap.add_argument("--url", default=None, help="POST to a running API instead of in-process")
    ap.add_argument("--api-key", default="dms-demo-steward-key")
    args = ap.parse_args()

    if args.url:
        result = run_http(args.stream, args.rate, args.seconds, args.url, args.api_key)
    else:
        result = run_inprocess(args.stream, args.rate, args.seconds)
    print(result)


if __name__ == "__main__":
    main()
