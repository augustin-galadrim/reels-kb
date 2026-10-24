#!/usr/bin/env bash
# Point d'entrée de la routine cloud (et des tests en local).
#   bash scripts/run.sh prepare   nouveaux réels prêts dans work/ ; dernière ligne « STATUT: … »
#   bash scripts/run.sh publish   état, images, commit et push dans le repo de la KB
# Le repo de la KB (kb-reels) est cloné à côté de celui-ci, ou désigné par KB_DIR.
# PYTHON permet d'utiliser un venv en local (défaut : python3).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export KB_DIR="${KB_DIR:-$(cd "$ROOT/.." && pwd)/kb-reels}"
PY="${PYTHON:-python3}"
cd "$ROOT"

publish() {
  "$PY" scripts/finalize.py || return 1
  cd "$KB_DIR" || return 1
  [ -n "$(git status --porcelain -- fiches state)" ] || { echo "PUBLIE: rien à committer"; return 0; }
  git config user.email >/dev/null || git config user.email "noreply@anthropic.com"
  git config user.name >/dev/null || git config user.name "reels-kb"
  git add fiches state && git commit -q -F "$ROOT/work/commit_message.txt" || return 1
  if git push -q origin HEAD:main 2>/dev/null \
     || { git pull -q --rebase origin main && git push -q origin HEAD:main; }; then
    echo "PUBLIE: $(head -1 "$ROOT/work/commit_message.txt") sur main"
  else
    branch="claude/reels-$(date +%F)"
    git push -q origin "HEAD:$branch" && echo "PUBLIE: push sur main refusé, poussé sur $branch" \
      || { echo "PUBLIE: ÉCHEC du push"; return 1; }
  fi
}

case "${1:-}" in
  prepare)
    [ -d "$KB_DIR/.git" ] || { echo "STATUT: ALERTE repo de la KB introuvable ($KB_DIR)"; exit 2; }
    "$PY" scripts/fetch_reels.py
    code=$?
    case $code in
      0) "$PY" scripts/extract.py || { echo "STATUT: ERREUR extraction des images ou de la transcription"; exit 3; } ;;
      1) publish >/dev/null || echo "AVERTISSEMENT: l'état n'a pas pu être publié dans la KB" ;;
    esac
    exit $code
    ;;
  publish)
    publish
    ;;
  *)
    echo "usage : bash scripts/run.sh prepare|publish" >&2
    exit 64
    ;;
esac
