#!/usr/bin/env python3
"""Étape « préparer » : trouve les nouveaux réels partagés en DM et télécharge leur vidéo.

Fail fast, du moins cher au plus cher :
1. jeton Instagram (un appel) ;
2. messages récents : s'il n'y a rien de nouveau, on s'arrête là, sans Apify ni Claude ;
3. Apify, un seul appel pour tout le lot (max_reels_per_run) ;
4. téléchargement des vidéos, et du son séparé quand la vidéo n'en a pas.

L'API officielle ne donne que le permalien d'un réel (vérifié le 07/10/2026) ; Apify fournit la vidéo.
Seuls les 20 derniers messages d'une conversation sont lisibles : avec une exécution par jour,
au-delà de 20 réels partagés entre deux exécutions, les plus anciens sont perdus.

Authentification : dans le cloud, le proxy de l'environnement ajoute les jetons Instagram et Apify.
En local, IG_ACCESS_TOKEN et APIFY_TOKEN les fournissent.

Dernière ligne affichée : « STATUT: … ». Codes de retour : 0 = réels prêts, 1 = rien à faire,
2 = ALERTE (action humaine nécessaire), 3 = ERREUR temporaire (réessayée à la prochaine exécution).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import requests

from common import CONFIG, WORK, load_state, record_failure, save_queue, save_state

IG_API = "https://graph.instagram.com/v25.0"
APIFY_API = "https://api.apify.com/v2"
MSG_FIELDS = "id,created_time,from,message,attachments,shares"
TOKEN_ERROR_CODES = {102, 190, 2500}  # jeton absent, expiré ou invalide
PERMALINK = re.compile(r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")
MAX_VIDEO_BYTES = 300 * 1024 * 1024


class Stop(Exception):
    """Arrête le lot avec un code de retour et une ligne STATUT."""

    def __init__(self, code, statut):
        super().__init__(statut)
        self.code, self.statut = code, statut


def auth(env_var):
    token = os.environ.get(env_var)
    return {"Authorization": f"Bearer {token}"} if token else {}


def error_code(response):
    try:
        return (response.json().get("error") or {}).get("code")
    except ValueError:
        return None


def ig_get(path, **params):
    err = ""
    for attempt in range(3):
        try:
            r = requests.get(f"{IG_API}/{path}", params=params, headers=auth("IG_ACCESS_TOKEN"), timeout=30)
        except requests.RequestException as exc:
            err = f"réseau : {exc}"
        else:
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401 or error_code(r) in TOKEN_ERROR_CODES:
                raise Stop(2, "ALERTE Jeton Instagram expiré ou invalide : renouvelle-le (README, « Renouveler le jeton »).")
            err = f"HTTP {r.status_code} : {r.text[:200]}"
            if r.status_code < 500 and r.status_code != 429:
                break
        time.sleep(3 * (attempt + 1))
    raise Stop(3, f"ERREUR API Instagram ({path}) : {err}")


def warn_token_expiry():
    days_left = (date.fromisoformat(CONFIG["ig_token_expires"]) - date.today()).days
    if days_left <= CONFIG["warn_days_before_expiry"]:
        print(f"AVERTISSEMENT: le jeton Instagram expire le {CONFIG['ig_token_expires']} "
              f"(dans {days_left} jours) : renouvelle-le (README, « Renouveler le jeton »).")


def short_id(msg_id):
    return hashlib.sha1(msg_id.encode()).hexdigest()[:12]


def permalink_of(msg):
    urls = [s.get("link") or "" for s in (msg.get("shares") or {}).get("data", [])]
    urls += [(a.get("payload") or {}).get("url") or "" for a in (msg.get("attachments") or {}).get("data", [])]
    for url in urls:
        m = PERMALINK.search(url)
        if m:
            return m.group(0).rstrip("/") + "/", m.group(1)
    return None, None


def new_reels(state):
    """Nouveaux réels de l'expéditeur autorisé, du plus ancien au plus récent."""
    me = (ig_get("me", fields="user_id,username").get("username") or "").lower()
    allowed = {u.lower() for u in CONFIG["allowed_senders"]}
    known = set(state["processed"]) | set(state["skipped"])
    # Un même réel partagé deux fois ne donne qu'une fiche.
    done_codes = {v["shortcode"]: v["fiche"] for v in state["processed"].values() if v.get("shortcode")}
    today = date.today().isoformat()
    found, batch_codes = [], set()
    for conv in ig_get("me/conversations", platform="instagram", fields="id").get("data", []):
        msgs = ig_get(conv["id"], fields="messages").get("messages", {}).get("data", [])
        if len(msgs) > 20 and msgs[20]["id"] not in known:
            print("AVERTISSEMENT: plus de 20 messages non traités dans une conversation, les plus anciens sont perdus.")
        for stub in msgs[:20]:
            if stub["id"] in known:
                continue
            msg = ig_get(stub["id"], fields=MSG_FIELDS)
            sender = (msg.get("from") or {}).get("username", "").lower()
            permalink, code = permalink_of(msg)
            # On mémorise tout ce qui n'est pas à traiter, pour ne plus le relire aux exécutions suivantes.
            if sender == me:
                state["skipped"][msg["id"]] = {"reason": "message du compte dédié", "date": today}
            elif sender not in allowed:
                state["skipped"][msg["id"]] = {"reason": f"expéditeur non autorisé : {sender}", "date": today}
            elif not permalink:
                state["skipped"][msg["id"]] = {"reason": "pas de réel", "date": today}
            elif code in done_codes:
                state["skipped"][msg["id"]] = {"reason": f"doublon de {done_codes[code]}", "date": today}
            elif code in batch_codes:
                state["skipped"][msg["id"]] = {"reason": f"doublon du réel {code} dans le même lot", "date": today}
            else:
                batch_codes.add(code)
                found.append({"msg_id": msg["id"], "created_time": msg.get("created_time") or "",
                              "sender": sender, "permalink": permalink, "shortcode": code,
                              "message": msg.get("message") or ""})
    return sorted(found, key=lambda c: c["created_time"])


def apify_items(batch):
    """Un seul appel Apify pour le lot ; renvoie les éléments par shortcode. Les résultats sont
    mis en cache dans work/<id>/apify.json pour qu'une relance locale ne repaye pas l'appel."""
    cached, todo = {}, []
    for c in batch:
        cache = WORK / short_id(c["msg_id"]) / "apify.json"
        if cache.exists():
            cached[c["shortcode"]] = json.loads(cache.read_text())
        else:
            todo.append(c)
    if not todo:
        return cached
    url = f"{APIFY_API}/acts/{CONFIG['apify_actor']}/run-sync-get-dataset-items"
    # Pas de maxItems : Apify refuse un plafond de coût sous 0,0073 $ par run. Avec des liens directs, il renvoie
    # au plus un élément par lien, et max_reels_per_run borne déjà le coût.
    params = {"timeout": 240, "clean": "true"}
    try:
        r = requests.post(url, params=params, json={"username": [c["permalink"] for c in todo]},
                          headers=auth("APIFY_TOKEN"), timeout=300)
    except requests.RequestException as exc:
        raise Stop(3, f"ERREUR Apify injoignable : {exc}")
    if r.status_code in (401, 402, 403):
        raise Stop(2, f"ALERTE Apify a refusé l'appel (HTTP {r.status_code}, jeton ou quota) : {r.text[:200]}")
    if not r.ok:
        raise Stop(3, f"ERREUR Apify HTTP {r.status_code} : {r.text[:200]}")
    items = {i.get("shortCode"): i for i in r.json() if isinstance(i, dict)}
    for c in todo:
        if c["shortcode"] in items:
            cache = WORK / short_id(c["msg_id"]) / "apify.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(items[c["shortcode"]], ensure_ascii=False))
    return {**items, **cached}


def download(url, dest):
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            if r.status_code != 200:
                return f"téléchargement HTTP {r.status_code}"
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        return "vidéo trop lourde"
                    f.write(chunk)
    except requests.RequestException as exc:
        return f"téléchargement : {exc}"
    return None if dest.stat().st_size > 10_000 else "fichier vide ou tronqué"


def has_audio(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout
    return bool(out.strip())


def prepare(c, item, work_dir):
    """Télécharge la vidéo (et le son séparé si besoin). Renvoie (entrée de file, erreur)."""
    video = work_dir / "video.mp4"
    entry = {"msg_id": c["msg_id"], "short_id": work_dir.name, "created_time": c["created_time"],
             "sender": c["sender"], "permalink": c["permalink"], "work_dir": f"work/{work_dir.name}",
             "caption": (item or {}).get("caption") or c["message"],
             "author": (item or {}).get("ownerUsername") or ""}
    if not item or not item.get("videoUrl"):
        return entry, (item or {}).get("error") or "absent de la réponse Apify (réel supprimé ou privé ?)"
    if not (video.exists() and video.stat().st_size > 10_000):  # cache local
        error = download(item["videoUrl"], video)
        if error:
            return entry, error
    entry["has_audio"] = has_audio(video)
    if not entry["has_audio"] and item.get("audioUrl"):
        audio, merged = work_dir / "audio.m4a", work_dir / "merged.mp4"
        if download(item["audioUrl"], audio) is None:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v:0",
                            "-map", "1:a:0", "-c", "copy", str(merged)], check=False)
            if merged.exists() and has_audio(merged):
                merged.replace(video)
                entry["has_audio"] = True
    return entry, None


def run():
    state = load_state()
    save_queue([])  # une file d'une exécution précédente ne doit pas être reprise
    try:
        warn_token_expiry()
        candidates = new_reels(state)
        if not candidates:
            raise Stop(1, "RIEN aucun nouveau réel")
        batch = candidates[:CONFIG["max_reels_per_run"]]
        if len(candidates) > len(batch):
            print(f"AVERTISSEMENT: {len(candidates) - len(batch)} réel(s) reporté(s) à la prochaine exécution "
                  f"(limite de {len(batch)} par exécution).")
        for c in batch:
            (WORK / short_id(c["msg_id"])).mkdir(parents=True, exist_ok=True)
        items = apify_items(batch)
        queue = []
        for c in batch:
            entry, error = prepare(c, items.get(c["shortcode"]), WORK / short_id(c["msg_id"]))
            if error:
                entry.update(status="failed", error=error, attempts=record_failure(state, c["msg_id"], error))
            else:
                entry["status"] = "ready"
            queue.append(entry)
        save_queue(queue)
        ready = sum(e["status"] == "ready" for e in queue)
        failed = [e for e in queue if e["status"] == "failed"]
        for e in failed:
            print(f"ÉCHEC: {e['permalink']} : {e['error'][:150]} (essai {e['attempts']}/{CONFIG['max_attempts']})")
        if not ready:
            raise Stop(1, f"RIEN {len(failed)} réel(s) en échec, aucun prêt")
        return 0, f"PRET {ready} réel(s)"
    except Stop as stop:
        return stop.code, stop.statut
    finally:
        save_state(state)


if __name__ == "__main__":
    code, statut = run()
    print(f"STATUT: {statut}")
    sys.exit(code)
