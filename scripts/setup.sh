#!/usr/bin/env bash
# Installe les dépendances (à mettre dans le "setup script" de l'environnement cloud,
# ou à lancer en début de routine).
set -euo pipefail
if ! command -v ffmpeg >/dev/null 2>&1; then
  (sudo -n apt-get update -qq && sudo -n apt-get install -y -qq ffmpeg) \
    || (apt-get update -qq && apt-get install -y -qq ffmpeg)
fi
python3 -m pip install -q --disable-pip-version-check -r "$(dirname "$0")/../requirements.txt" \
  || python3 -m pip install -q --break-system-packages -r "$(dirname "$0")/../requirements.txt"
echo "setup ok"
