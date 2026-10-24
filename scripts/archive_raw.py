#!/usr/bin/env python3
"""Archive les données brutes des réels du lot dans le repo kb-reels-raw (RAW_DIR, à côté de celui-ci par défaut),
pour pouvoir réanalyser plus tard sans repayer Apify ni retélécharger une vidéo peut-être supprimée.

Un dossier par réel, nommé par son code : reels/<shortcode>/
  video.mp4        la vidéo, avec le son fusionné quand il était séparé
  apify.json       la réponse Apify complète, URL signées retirées (elles expirent et ne doivent pas être publiées)
  meta.json        le message partagé, la légende, l'auteur, la durée, la langue
  transcript.txt   la transcription horodatée
  frames/          les images retenues par extract.py
Un réel déjà archivé n'est pas réécrit.
"""
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

from common import ROOT, load_queue

RAW = Path(os.environ.get("RAW_DIR", ROOT.parent / "kb-reels-raw"))
URL = re.compile(r"https?://[^\s\"']+")
SIGNED = re.compile(r"cdninstagram\.com|fbcdn\.net|fbsbx\.com|[?&](?:oh|oe|_nc_[a-z]+)=")
META_FIELDS = ("msg_id", "created_time", "sender", "permalink", "message", "caption", "author",
               "duration_s", "has_audio", "language", "status", "error")


def without_signed_urls(value):
    if isinstance(value, dict):
        return {k: without_signed_urls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [without_signed_urls(v) for v in value]
    if isinstance(value, str):
        return URL.sub(lambda m: "(URL signée retirée)" if SIGNED.search(m.group(0)) else m.group(0), value)
    return value


def archive(item):
    work = ROOT / item["work_dir"]
    code = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", item.get("permalink") or "")
    if not code:
        return False
    dest = RAW / "reels" / code.group(1)
    if (dest / "meta.json").exists():
        return False
    dest.mkdir(parents=True, exist_ok=True)
    if (work / "apify.json").exists():
        apify = without_signed_urls(json.loads((work / "apify.json").read_text()))
        (dest / "apify.json").write_text(json.dumps(apify, ensure_ascii=False, indent=1))
    for name in ("video.mp4", "transcript.txt"):
        if (work / name).exists():
            shutil.copyfile(work / name, dest / name)
    if (work / "frames").is_dir():
        shutil.copytree(work / "frames", dest / "frames", dirs_exist_ok=True)
    meta = {k: item[k] for k in META_FIELDS if k in item}
    meta.update(shortcode=code.group(1), archived=date.today().isoformat())
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return True


def main():
    if not (RAW / ".git").is_dir():
        print(f"AVERTISSEMENT: repo des données brutes introuvable ({RAW.name}), réels non archivés.")
        return
    n = sum(archive(item) for item in load_queue())
    print(f"{n} réel(s) archivé(s)")


if __name__ == "__main__":
    main()
