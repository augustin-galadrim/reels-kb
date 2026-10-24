# reels-kb

Tu partages un réel en DM depuis `augustinlezinzin` au compte dédié `reels.gus`. Une fois par jour, une routine
Claude Code dans le cloud le récupère, le regarde, rédige une fiche technique et la pousse dans le repo de la KB,
[kb-reels](https://github.com/augustin-galadrim/kb-reels). Ce repo-ci ne contient que le code.

```text
routine cloud quotidienne (environnement « reels »)
  scripts/run.sh prepare
    ├─ fetch_reels.py   API Instagram (DM → permaliens) → Apify (vidéo, son, légende, auteur)
    └─ extract.py       ffmpeg (≤ 15 images, une par état visuel distinct) + faster-whisper (transcription)
  Claude               regarde, rédige kb-reels/fiches/<cat>/<date>-<slug>.md
  scripts/run.sh publish
    └─ finalize.py      images clés, état, message de commit → commit + push sur kb-reels/main
```

## Pourquoi Apify
L'API officielle d'Instagram ne donne jamais la vidéo d'un réel partagé en DM, seulement son permalien
(vérifié le 07/10/2026, API Conversations et webhook). L'acteur Apify `apify/instagram-reel-scraper` renvoie la vidéo,
le son séparé quand il le faut, la légende et l'auteur, sans compte Instagram (testé le 24/10/2026 : 15 réels sur 15,
14 avec le son ; environ 0,0025 $ par réel, dans le crédit gratuit de 5 $/mois).

## Garde-fous (quota Claude et Apify)
- **Une exécution par jour**, jamais relancée automatiquement.
- **Fail fast** : le jeton Instagram est vérifié en premier ; sans nouveau réel, la routine s'arrête avant Apify,
  Whisper et toute rédaction. Le modèle Whisper n'est chargé que si un réel a du son.
- **Au plus 5 réels par exécution** (`config.json`), le reste passe le lendemain.
- **Pas de retraitement sans fin** : chaque échec propre à un réel est compté ; au bout de 3, le réel est ignoré
  (`kb-reels/state/processed.json`). Une panne d'Instagram ou d'Apify arrête le lot sans pénaliser les réels.
- **Moins de tokens d'images** : au plus 15 images par réel, une par état visuel distinct (les doublons des jump cuts sont fusionnés).
- **Notifications push** : jeton bientôt expiré (10 jours avant), jeton expiré, Apify refusé (jeton ou quota),
  erreur temporaire, réels en échec, fiches publiées.
- **Côté Apify** : fixe une limite mensuelle à 5 $ dans la console (*Billing → Limits*) pour ne jamais payer.

## Réglages de l'environnement cloud « reels »
- **Réseau** : *Limité*, avec « Autoriser les gestionnaires de paquets » coché, et les domaines
  `graph.instagram.com`, `*.cdninstagram.com`, `*.fbcdn.net`, `huggingface.co`, `*.huggingface.co`, `*.hf.co`.
- **Secrets réseau** (le jeton n'entre jamais dans la machine, le proxy l'ajoute) :
  `graph.instagram.com` → jeton Instagram de `reels.gus` ; `api.apify.com` → jeton Apify (Bearer).
- **Setup script** (mis en cache entre les exécutions) :
  ```bash
  #!/bin/bash
  exec > /tmp/setup.log 2>&1
  set -x
  PKGS="faster-whisper==1.2.1 av<19 requests"   # av 19 casse faster-whisper 1.2.1 (metadata_errors)
  python3 -m pip install $PKGS || python3 -m pip install --break-system-packages $PKGS
  python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
  exit 0
  ```
- **Routine** « Réels → fiches » : repos `reels-kb` et `kb-reels`, prompt = `ROUTINE_PROMPT.md`, push autorisé sur `main`.

## Renouveler le jeton
Le jeton longue durée de `reels.gus` vaut 60 jours (le prochain expire le **27/11/2026**).
1. Tableau de bord Meta, app `reels-kb-IG`, *API setup with Instagram Login* : génère un nouveau jeton pour `reels.gus`.
2. claude.ai/code, environnement `reels`, secrets réseau : remplace la valeur pour `graph.instagram.com`.
3. Mets à jour `ig_token_expires` dans `config.json` (date du jour + 60 jours) et pousse sur `main`.

## Tester en local
```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt   # + ffmpeg (brew install ffmpeg)
git clone https://github.com/augustin-galadrim/kb-reels ../kb-reels
IG_ACCESS_TOKEN=... APIFY_TOKEN=... PYTHON=venv/bin/python bash scripts/run.sh prepare
```

## Limites
- L'API ne lit que les 20 derniers messages d'une conversation : plus de 20 partages entre deux exécutions → les plus anciens sont perdus.
- Certains réels n'ont pas de son accessible sans compte : la fiche est alors faite à partir des images et de la légende.
- Les réels appartiennent à leurs créateurs : la KB est privée et chaque fiche cite sa source.
