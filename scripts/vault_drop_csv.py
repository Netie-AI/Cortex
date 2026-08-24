"""Drop a CSV of env_key,secret into OpenVault. Never prints values."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from CortexOS.crew.openvault import ingest_csv_drop

DEFAULT = Path(r"D:\Cortex-crew\data\crew\drops\vault.csv")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    os.environ.setdefault("CREW_OPENVAULT", "1")
    result = ingest_csv_drop(path)
    # labels only
    print("ok=" + str(result.get("ok")))
    print("n=" + str(result.get("n", 0)))
    print("pushed=" + ",".join(result.get("pushed") or []))
    if result.get("errors"):
        print("errors=" + str(len(result["errors"])))
        print("detail=" + str(result.get("detail") or ""))
        return 1
    if result.get("detail"):
        print("detail=" + str(result["detail"]))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
