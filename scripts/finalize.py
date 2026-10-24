#!/usr/bin/env python3
"""Termine une exécution, après la rédaction des fiches par Claude :
- copie dans <KB>/fiches/reels/assets/ les images clés que les fiches référencent
  (convention : assets/<short_id>-<image de frames>[-y<début>-<fin>].jpg, max_assets par fiche au plus ;
  le suffixe -y garde la bande horizontale entre <début> et <fin> % de la hauteur) ;
- marque comme traité chaque réel dont la fiche existe (champ msg_id du frontmatter) ;
- compte un échec pour chaque réel prêt resté sans fiche, pour qu'il ne soit pas retraité sans fin ;
- écrit le message de commit dans work/commit_message.txt.
"""
import re
import shutil
import subprocess
import sys
from datetime import date

from common import CONFIG, KB, WORK, load_queue, load_state, record_failure, save_state

ASSET_RE = re.compile(r"\]\(assets/([0-9a-f]{12})-(t[0-9.]+s)(?:-y([0-9]{1,3})-([0-9]{1,3}))?\.jpg\)")
CROP_MARGIN = 3  # % ajoutés de part et d'autre de la bande demandée : le rédacteur l'estime à l'œil


def frontmatter(path):
    text = path.read_text()
    if not text.startswith("---"):
        return {}
    fields = {}
    for line in text.split("---", 2)[1].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip().strip('"')
    return fields


def copy_assets(fiche, short_id):
    for m in list(ASSET_RE.finditer(fiche.read_text()))[:CONFIG["max_assets"]]:
        sid, frame, top, bottom = m.groups()
        src = WORK / sid / "frames" / f"{frame}.jpg"
        dest = fiche.parent / m.group(0)[2:-1]
        if sid != short_id or not src.exists() or dest.exists():
            continue
        dest.parent.mkdir(exist_ok=True)
        if top is None:
            shutil.copyfile(src, dest)
            continue
        top, bottom = max(0, int(top) - CROP_MARGIN) / 100, min(100, int(bottom) + CROP_MARGIN) / 100
        if bottom - top < 0.15:  # bande absurde : on garde l'image entière
            top, bottom = 0, 1
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                        f"crop=iw:trunc(ih*{bottom - top:.2f}/2)*2:0:trunc(ih*{top:.2f}/2)*2",
                        "-q:v", "3", str(dest)], check=False)


def refused():
    """Publication refusée par le contrôle de sécurité : chaque réel prêt du lot compte un échec."""
    state = load_state()
    for q in load_queue():
        if q["status"] == "ready":
            state["processed"].pop(q["msg_id"], None)
            record_failure(state, q["msg_id"], "publication refusée par le contrôle de sécurité")
    save_state(state)


def main():
    if "--refused" in sys.argv:
        return refused()
    queue = load_queue()
    state = load_state()
    ready = {q["msg_id"]: q for q in queue if q["status"] == "ready"}

    titles, published = [], []
    for fiche in sorted((KB / "fiches" / "reels").glob("*.md")):
        meta = frontmatter(fiche)
        msg_id = meta.get("msg_id")
        if msg_id not in ready or msg_id in state["processed"]:
            continue
        copy_assets(fiche, ready[msg_id]["short_id"])
        code = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", ready[msg_id].get("permalink") or "")
        state["processed"][msg_id] = {"fiche": str(fiche.relative_to(KB)), "date": date.today().isoformat(),
                                      "shortcode": code.group(1) if code else None}
        state["failures"].pop(msg_id, None)
        titles.append(meta.get("titre") or fiche.stem)
        published.append(str(fiche.relative_to(KB)))
    (WORK / "published.txt").write_text("".join(f"{p}\n" for p in published))

    missing = [q for q in ready.values() if q["msg_id"] not in state["processed"]]
    for q in missing:
        q["attempts"] = record_failure(state, q["msg_id"], "fiche non rédigée")
    save_state(state)

    failed = [q for q in queue if q["status"] == "failed"] + missing
    lines = [f"reels: +{len(titles)} fiches", ""] + [f"- {t}" for t in titles]
    if failed:
        lines += ["", f"Échecs (retentés aux exécutions suivantes, ignorés après {CONFIG['max_attempts']} essais) :"]
        lines += [f"- {q['permalink']} : {q.get('error', 'fiche non rédigée')[:150]} (essai {q.get('attempts')})"
                  for q in failed]
    (WORK / "commit_message.txt").write_text("\n".join(lines).strip() + "\n")
    print(f"{len(titles)} fiche(s), {len(failed)} échec(s)")


if __name__ == "__main__":
    main()
