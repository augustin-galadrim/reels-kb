#!/usr/bin/env python3
"""Marque un réel comme traité ou ignoré, pour ne jamais le retraiter.

    python scripts/state.py done <msg_id> <chemin/de/la/fiche.md>
    python scripts/state.py skip <msg_id> "<raison>"
"""
import json
import sys
from datetime import date
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "processed.json"


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("done", "skip"):
        sys.exit(__doc__)
    action, msg_id, value = sys.argv[1:]
    state = json.loads(STATE_FILE.read_text())
    if action == "done":
        state["processed"][msg_id] = {"fiche": value, "date": date.today().isoformat()}
    else:
        state["skipped"][msg_id] = {"reason": value, "date": date.today().isoformat()}
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    print(f"{action}: {msg_id[:16]}…")


if __name__ == "__main__":
    main()
