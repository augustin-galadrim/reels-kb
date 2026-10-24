#!/usr/bin/env bash
# Point d'entrée de la routine cloud (et des tests en local).
#   bash scripts/run.sh all       prepare, draft puis publish : ce que lance la routine
#   bash scripts/run.sh prepare   nouveaux réels prêts dans work/ ; dernière ligne « STATUT: … »
#   bash scripts/run.sh draft     rédaction confinée des fiches (claude -p sans shell ni réseau)
#   bash scripts/run.sh publish   état, images, commit et push dans le repo de la KB
# La session de la routine ne lit jamais le contenu des réels : seul le claude -p confiné le lit, et il ne
# peut qu'écrire dans les fiches. Une consigne cachée dans un réel n'a donc ni shell, ni réseau, ni jeton.
# Le repo de la KB (kb-reels) est cloné à côté de celui-ci, ou désigné par KB_DIR.
# PYTHON permet d'utiliser un venv en local (défaut : python3).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export KB_DIR="${KB_DIR:-$(cd "$ROOT/.." && pwd)/kb-reels}"
PY="${PYTHON:-python3}"
cd "$ROOT"

# Contrôles avant commit des changements faits par le rédacteur (qui a lu du contenu non fiable) :
# ajouts seulement sur l'existant, nommage attendu des nouveaux fichiers, aucun jeton dans le texte ajouté.
check_fiches() {
  local shrunk new_bad secret
  shrunk=$(git diff --cached --numstat --diff-filter=MD -- fiches | awk '$2 != "0" {print $3}')
  new_bad=$(git diff --cached --name-only --diff-filter=A -- fiches | grep -vE \
    '^fiches/(INDEX\.md|themes/[a-z0-9-]{1,60}\.md|reels/([0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]{1,60}\.md|assets/[0-9a-f]{12}-t[0-9.]+s(-y[0-9]{1,3}-[0-9]{1,3})?\.jpg))$')
  secret=$(git diff --cached -U0 -- fiches | grep -E '^\+' \
    | grep -cE '(ghp_|gho_|ghs_|ghu_|github_pat_|sk-ant-|apify_api_|IGAA|EAA[A-Za-z0-9]{20})[A-Za-z0-9_-]{10,}')
  [ -z "$shrunk" ] && [ -z "$new_bad" ] && [ "$secret" = "0" ] && return 0
  echo "contrôle refusé : réécrit/supprimé=[${shrunk//$'\n'/ }] nommage=[${new_bad//$'\n'/ }] jetons=$secret" >&2
  return 1
}

publish() (  # sous-shell : le cd vers la KB ne doit pas changer le dossier de l'appelant
  "$PY" scripts/finalize.py || return 1
  cd "$KB_DIR" || return 1
  [ -n "$(git status --porcelain -- fiches state)" ] || { echo "PUBLIE: rien à committer"; return 0; }
  git config user.email >/dev/null || git config user.email "noreply@anthropic.com"
  git config user.name >/dev/null || git config user.name "reels-kb"
  git add -A fiches state || return 1
  if ! check_fiches 2>>"$ROOT/work/draft.log"; then
    # On jette tout ce qu'a fait le rédacteur ; les réels du lot comptent un échec (publié), pour qu'un réel
    # piégé ne soit pas retraité sans fin.
    git reset -q && git checkout -q -- fiches state && git clean -fdq fiches
    (cd "$ROOT" && "$PY" scripts/finalize.py --refused) || return 1
    git add state && git commit -q -m "reels: publication refusée par le contrôle de sécurité" \
      && git push -q origin HEAD:main
    echo "PUBLIE: REFUSÉ par le contrôle de sécurité (détail dans work/draft.log)"
    return 1
  fi
  git commit -q -F "$ROOT/work/commit_message.txt" || return 1
  if git push -q origin HEAD:main 2>/dev/null \
     || { git pull -q --rebase origin main && git push -q origin HEAD:main; }; then
    echo "PUBLIE: $(head -1 "$ROOT/work/commit_message.txt") sur main"
  else
    branch="claude/reels-$(date +%F)"
    git push -q origin "HEAD:$branch" && echo "PUBLIE: push sur main refusé, poussé sur $branch" \
      || { echo "PUBLIE: ÉCHEC du push"; return 1; }
  fi
)

prepare() {
  [ -d "$KB_DIR/.git" ] || { echo "STATUT: ALERTE repo de la KB introuvable ($KB_DIR)"; return 2; }
  "$PY" scripts/fetch_reels.py
  local code=$?
  case $code in
    0) "$PY" scripts/extract.py || { echo "STATUT: ERREUR extraction des images ou de la transcription"; return 3; } ;;
    1) publish >/dev/null || echo "AVERTISSEMENT: l'état n'a pas pu être publié dans la KB" ;;
  esac
  return $code
}

draft() {
  command -v claude >/dev/null || { echo "STATUT: ERREUR claude introuvable pour la rédaction confinée"; return 3; }
  claude -p "$(cat DRAFT_PROMPT.md)
Repo de la KB : $KB_DIR" \
    --model sonnet \
    --permission-mode dontAsk \
    --add-dir "$KB_DIR" \
    --allowedTools "Edit(/$KB_DIR/fiches/**)" \
    --disallowedTools Bash WebFetch WebSearch Agent Task NotebookEdit \
      "Read(/$KB_DIR/.git/**)" "Read(/$ROOT/.git/**)" "Read(/$KB_DIR/state/**)" \
    --setting-sources project \
    --strict-mcp-config \
    --no-session-persistence \
    > work/draft.log 2>&1
  local rc=$?
  # Seul le code de retour remonte : le journal contient du texte dérivé des réels.
  [ $rc -eq 0 ] || { echo "STATUT: ERREUR rédaction confinée en échec (code $rc, voir work/draft.log)"; return 3; }
}

case "${1:-}" in
  all)
    prepare; code=$?
    [ $code -eq 0 ] || exit $code
    draft || exit 3
    publish >/dev/null || { echo "STATUT: ERREUR publication dans la KB"; exit 3; }
    # Seules des données structurelles remontent à la session : les titres, rédigés à partir des réels,
    # restent dans le message de commit. Noms de fichiers filtrés sur [a-z0-9-].
    n=0
    while IFS= read -r path; do
      slug="$(basename "$path" .md)"
      [[ "$path" == fiches/reels/* && "$slug" =~ ^[0-9a-z-]{1,72}$ ]] && echo "FICHE: $slug"
      n=$((n + 1))
    done < <(cat work/published.txt 2>/dev/null)
    echo "STATUT: PUBLIE $n fiche(s), commit $(git -C "$KB_DIR" rev-parse --short HEAD)"
    ;;
  prepare) prepare ;;
  draft) draft ;;
  publish) publish ;;
  *)
    echo "usage : bash scripts/run.sh all|prepare|draft|publish" >&2
    exit 64
    ;;
esac
