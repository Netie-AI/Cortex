#!/usr/bin/env python3
"""Run act recorders 1,2,3,4,6 then concat with ffmpeg into yc_multiplayer_demo.mp4."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _common import OUT

SCRIPTS = [
    "record_act1_mesh_alive.py",
    "record_act2_vault_keys.py",
    "record_act3_share_lan.py",
    "record_act4_cortex_brain.py",
    "record_act6_multiplayer_gate.py",
]


def main() -> int:
    here = Path(__file__).resolve().parent
    env_python = sys.executable
    for name in SCRIPTS:
        print("==>", name)
        subprocess.check_call([env_python, str(here / name)], cwd=str(here))

    clips = [
        OUT / "act1_mesh_alive.webm",
        OUT / "act2_vault_keys.webm",
        OUT / "act3_share_lan.webm",
        OUT / "act4_cortex_brain.webm",
        OUT / "act6_multiplayer_gate.webm",
    ]
    missing = [c for c in clips if not c.exists()]
    if missing:
        print("missing clips:", missing, file=sys.stderr)
        return 1

    listing = OUT / "concat.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    mp4 = OUT / "yc_multiplayer_demo.mp4"
    # Re-encode for broad playback; keep resolution
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(mp4),
    ]
    subprocess.check_call(cmd, cwd=str(OUT))
    # Also copy to Cursor artifacts for easy download
    artifacts = Path("/opt/cursor/artifacts")
    if artifacts.is_dir():
        dest = artifacts / "yc_multiplayer_demo.mp4"
        dest.write_bytes(mp4.read_bytes())
        print("artifact", dest)
    print("final", mp4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
