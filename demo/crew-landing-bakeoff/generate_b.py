"""Live lane-B landing via OpenVault model=auto. No secrets. Not imported by pytest."""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from pathlib import Path

PROMPT = (
    "Write one complete HTML5 document for a Cortex Crew marketing landing page. "
    "Product: local agentic chat over the Cortex governed engine. Port 8020. "
    "Keys stay in OpenVault. Must include a 3D visual the visitor can tilt or orbit "
    "(CSS 3D or canvas). Do not use a pasted photo. Do not make the hero a grid of "
    "solid-color boxes. No markdown fences. Output HTML only starting with <!DOCTYPE html>."
)
ROOT = Path(__file__).resolve().parent / "b-freeroute-auto"
FALLBACK = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>B FreeRoute auto</title></head>
<body><p>Lane B: model did not return HTML. See META.json.</p></body></html>
"""


def main() -> None:
    payload = json.dumps(
        {
            "model": "auto",
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 2500,
        }
    ).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:5000/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    choice = (data.get("choices") or [{}])[0]
    text = str((choice.get("message") or {}).get("content") or "")
    html = text.strip()
    if html.startswith("```"):
        html = re.sub(r"^```(?:html)?\s*", "", html)
        html = re.sub(r"\s*```$", "", html)
    if "<html" not in html.lower():
        html = FALLBACK + "<!-- raw -->\n"
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "index.html").write_text(html, encoding="utf-8")
    meta = {
        "lane": "B",
        "model": str(data.get("model") or "unknown"),
        "prompt_tokens": (data.get("usage") or {}).get("prompt_tokens"),
        "completion_tokens": (data.get("usage") or {}).get("completion_tokens"),
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "chars": len(html),
    }
    (ROOT / "META.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({"model": meta["model"], "chars": meta["chars"]}))


if __name__ == "__main__":
    main()
