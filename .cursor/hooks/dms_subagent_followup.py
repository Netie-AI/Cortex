#!/usr/bin/env python3
"""After a subagent stops, remind to run gate verify if DMS files changed."""
import json
import sys


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    # Hook contract: respond with JSON on stdout
    status = payload.get("status", "")
    if status not in ("completed", "error"):
        sys.exit(0)

    follow_up = (
        "Subagent finished. If this was a DMS feature (F* or V*), run the "
        "dms-claude-gate skill and paste output to Claude per "
        "docs/dms/SUPERVISOR_GATE.md before starting the next feature."
    )

    print(json.dumps({"followup_message": follow_up}))


if __name__ == "__main__":
    main()
