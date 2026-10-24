"""Chemins, configuration et état partagés par les scripts du pipeline.

Le code vit dans ce repo (reels-kb) ; les fiches et l'état vivent dans le repo de la KB,
cloné à côté (../kb-reels) ou désigné par KB_DIR.
"""
import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = Path(os.environ.get("KB_DIR", ROOT.parent / "kb-reels")).resolve()
CONFIG = json.loads((ROOT / "config.json").read_text())
WORK = ROOT / "work"
QUEUE_FILE = WORK / "queue.json"
STATE_FILE = KB / "state" / "processed.json"


def load_state():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    for key in ("processed", "skipped", "failures"):
        state.setdefault(key, {})
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def record_failure(state, msg_id, error):
    """Compte un échec propre au réel ; au-delà de max_attempts, le réel passe en ignoré.

    Les pannes globales (Instagram, Apify, réseau) ne passent pas par ici : elles arrêtent le lot
    sans pénaliser les réels.
    """
    today = date.today().isoformat()
    attempts = state["failures"].pop(msg_id, {}).get("attempts", 0) + 1
    if attempts >= CONFIG["max_attempts"]:
        state["skipped"][msg_id] = {"reason": f"{attempts} échecs, dernier : {error}", "date": today}
    else:
        state["failures"][msg_id] = {"attempts": attempts, "error": error, "date": today}
    return attempts


def load_queue():
    return json.loads(QUEUE_FILE.read_text()) if QUEUE_FILE.exists() else []


def save_queue(queue):
    WORK.mkdir(exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n")
