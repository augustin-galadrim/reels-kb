#!/usr/bin/env python3
"""Termine une exécution, après la rédaction des fiches par Claude :
- copie dans <KB>/fiches/<categorie>/assets/ les images clés que les fiches référencent
  (convention : assets/<short_id>-<image de frames>.jpg, 2 par fiche au plus) ;
- marque comme traité chaque réel dont la fiche existe (champ msg_id du frontmatter) ;
- compte un échec pour chaque réel prêt resté sans fiche, pour qu'il ne soit pas retraité sans fin ;
- écrit le message de commit dans work/commit_message.txt.
"""
import re
import shutil
from datetime import date

from common import CONFIG, KB, WORK, load_queue, load_state, record_failure, save_state

ASSET_RE = re.compile(r"\]\(assets/([0-9a-f]{12})-(t[0-9.]+s\.jpg)\)")
MAX_ASSETS = 2


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
    for sid, frame in ASSET_RE.findall(fiche.read_text())[:MAX_ASSETS]:
        src = WORK / sid / "frames" / frame
        dest = fiche.parent / "assets" / f"{sid}-{frame}"
        if sid == short_id and src.exists() and not dest.exists():
            dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(src, dest)


def main():
    queue = load_queue()
    state = load_state()
    ready = {q["msg_id"]: q for q in queue if q["status"] == "ready"}

    titles = []
    for fiche in sorted((KB / "fiches").glob("*/*.md")):
        meta = frontmatter(fiche)
        msg_id = meta.get("msg_id")
        if msg_id not in ready or msg_id in state["processed"]:
            continue
        copy_assets(fiche, ready[msg_id]["short_id"])
        state["processed"][msg_id] = {"fiche": str(fiche.relative_to(KB)), "date": date.today().isoformat()}
        state["failures"].pop(msg_id, None)
        titles.append(meta.get("titre") or fiche.stem)

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
