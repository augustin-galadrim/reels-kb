#!/usr/bin/env python3
"""Récupère les réels partagés en DM au compte dédié, via l'API Instagram officielle
(Instagram API with Instagram Login -> Conversations API).

Pas de serveur ni de webhook : on interroge la boîte de réception à chaque exécution.
Limite Meta : seuls les 20 derniers messages d'une conversation sont lisibles en détail,
d'où l'intérêt de lancer la routine assez souvent.

Usage :
    python scripts/fetch_reels.py            # télécharge les nouveaux réels -> work/queue.json
    python scripts/fetch_reels.py --debug    # affiche le JSON brut des messages, sans rien télécharger
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state" / "processed.json"
WORK = ROOT / "work"
QUEUE_FILE = WORK / "queue.json"

API = f"https://graph.instagram.com/{os.environ.get('IG_API_VERSION', 'v25.0')}"
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
ALLOWED_SENDERS = {
    u.strip().lstrip("@").lower()
    for u in os.environ.get("IG_ALLOWED_SENDERS", "").split(",")
    if u.strip()
}
MSG_FIELDS = "id,created_time,from,message,attachments,shares"
MAX_VIDEO_BYTES = 300 * 1024 * 1024


def api_get(path, **params):
    # Jeton envoyé en en-tête. Sans IG_ACCESS_TOKEN (routine avec « API credential »),
    # aucun en-tête n'est mis : le proxy de Claude Code l'ajoute lui-même.
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    last = None
    for attempt in range(3):
        r = requests.get(f"{API}/{path}", params=params, headers=headers, timeout=30)
        if r.status_code == 200:
            return r.json()
        last = r
        if r.status_code >= 500 or r.status_code == 429:
            time.sleep(3 * (attempt + 1))
            continue
        break
    hint = " (jeton absent, invalide ou expiré ?)" if last.status_code in (400, 401, 403) else ""
    raise RuntimeError(f"GET {path} -> HTTP {last.status_code}{hint}: {last.text[:400]}")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"processed": {}, "skipped": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def short_id(msg_id):
    return hashlib.sha1(msg_id.encode()).hexdigest()[:12]


def is_permalink(url):
    return "instagram.com/" in url and any(p in url for p in ("/reel", "/p/", "/tv/"))


def media_candidates(msg):
    """Liste (source, url) des médias trouvés dans le message, vidéos directes d'abord."""
    direct, links = [], []
    for att in (msg.get("attachments") or {}).get("data", []):
        video = att.get("video_data") or {}
        if video.get("url"):
            direct.append(("attachments.video_data", video["url"]))
        if att.get("file_url"):
            direct.append(("attachments.file_url", att["file_url"]))
        payload = att.get("payload") or {}
        if payload.get("url"):
            direct.append(("attachments.payload", payload["url"]))
    for share in (msg.get("shares") or {}).get("data", []):
        if share.get("link"):
            links.append(("shares.link", share["link"]))
    direct = [c for c in direct if not is_permalink(c[1])]
    permalinks = [c for c in direct + links if is_permalink(c[1])]
    others = [c for c in links if not is_permalink(c[1])]
    return direct + others, permalinks


def caption_of(msg):
    for share in (msg.get("shares") or {}).get("data", []):
        for key in ("name", "description", "title"):
            if share.get(key):
                return share[key]
    for att in (msg.get("attachments") or {}).get("data", []):
        if att.get("name"):
            return att["name"]
    return msg.get("message") or ""


def download_video(url, dest):
    with requests.get(url, stream=True, timeout=60) as r:
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        ctype = r.headers.get("Content-Type", "")
        if not (ctype.startswith("video/") or ctype == "application/octet-stream"):
            return f"pas une vidéo (Content-Type: {ctype or 'inconnu'})"
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    return "vidéo trop lourde"
                f.write(chunk)
    return None if dest.stat().st_size > 10_000 else "fichier vide ou tronqué"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="affiche le JSON brut, sans téléchargement")
    args = parser.parse_args()

    me = api_get("me", fields="user_id,username")
    my_username = (me.get("username") or "").lower()
    state = load_state()
    known = set(state["processed"]) | set(state["skipped"])

    convs = api_get("me/conversations", platform="instagram", fields="id,updated_time")
    queue, skipped_now = [], 0

    for conv in convs.get("data", []):
        msgs = api_get(conv["id"], fields="messages").get("messages", {}).get("data", [])
        recent = msgs[:20]
        if len(msgs) > 20 and msgs[20]["id"] not in known:
            print(f"⚠️  Conversation {conv['id'][:10]}… : plus de 20 messages non traités, "
                  "les plus anciens ne sont plus lisibles. Lance la routine plus souvent.")

        for stub in recent:
            if stub["id"] in known:
                continue
            msg = api_get(stub["id"], fields=MSG_FIELDS)
            sender = (msg.get("from") or {}).get("username", "").lower()

            if args.debug:
                print(json.dumps(msg, indent=2, ensure_ascii=False))
                print("-" * 60)
                continue

            if sender == my_username:
                continue  # message envoyé par le compte dédié lui-même
            if ALLOWED_SENDERS and sender not in ALLOWED_SENDERS:
                state["skipped"][msg["id"]] = {"reason": f"expéditeur non autorisé: {sender}"}
                skipped_now += 1
                continue

            candidates, permalinks = media_candidates(msg)
            if not candidates and not permalinks:
                state["skipped"][msg["id"]] = {"reason": "pas de média"}
                skipped_now += 1
                continue

            sid = short_id(msg["id"])
            work_dir = WORK / sid
            item = {
                "msg_id": msg["id"],
                "short_id": sid,
                "created_time": msg.get("created_time"),
                "sender": sender,
                "caption": caption_of(msg),
                "permalink": permalinks[0][1] if permalinks else None,
                "work_dir": str(work_dir.relative_to(ROOT)),
            }

            error = "aucune URL de vidéo directe (seulement un permalien)"
            for source, url in candidates:
                error = download_video(url, work_dir / "video.mp4")
                if error is None:
                    item["video_source"] = source
                    break
            item["status"] = "ready" if error is None else "failed"
            if error:
                item["error"] = error
            work_dir.mkdir(parents=True, exist_ok=True)
            (work_dir / "raw_message.json").write_text(json.dumps(msg, indent=2, ensure_ascii=False))
            queue.append(item)

    if args.debug:
        return

    save_state(state)
    WORK.mkdir(exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n")
    ready = sum(1 for q in queue if q["status"] == "ready")
    print(f"Réels prêts : {ready} | en échec : {len(queue) - ready} | ignorés : {skipped_now}")


if __name__ == "__main__":
    main()
