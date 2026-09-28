#!/bin/bash
# À coller dans le champ « Setup script » de l'environnement cloud Claude Code.
# Exécuté en root, une fois ; le résultat (ffmpeg, paquets, modèle Whisper) est mis en cache.
apt-get update -qq && apt-get install -y -qq ffmpeg || true
pip install -q requests faster-whisper || pip install -q --break-system-packages requests faster-whisper || true
python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')" || true
exit 0
