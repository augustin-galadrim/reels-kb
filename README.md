# reels-kb

Tu partages un réel en DM à ton compte Instagram dédié. Plus tard, une routine Claude Code le récupère, le regarde, rédige une fiche technique et la commite ici.

Aucun serveur ni webhook : la routine tourne dans le cloud d'Anthropic et interroge la boîte de réception via l'API officielle d'Instagram.

```
DM Instagram ──► routine Claude Code (planifiée)
                   ├─ fetch_reels.py   API Conversations → vidéo
                   ├─ extract.py       ffmpeg (images) + faster-whisper (audio)
                   ├─ Claude           regarde, rédige fiches/<cat>/<date>-<slug>.md
                   └─ git commit + push
```

## 1. Compte Instagram dédié
1. Crée le compte, puis passe-le en **compte professionnel** (type Créateur, c'est gratuit).
2. Dans l'app Instagram de ce compte, va dans *Paramètres → Messages et réponses aux stories → Outils connectés* et active **Autoriser l'accès aux messages**.
3. Depuis ton compte perso, envoie-lui un premier message. L'API ne voit que les conversations initiées par l'autre personne.

## 2. App Meta et jeton
1. Sur [developers.facebook.com](https://developers.facebook.com), crée une app de type Business et ajoute le produit **Instagram**, en choisissant *API with Instagram Login*.
2. Ajoute le compte dédié comme testeur de l'app. En mode développement, la validation Meta n'est pas nécessaire pour un usage personnel.
3. Génère un jeton pour ce compte avec les permissions `instagram_business_basic` et `instagram_business_manage_messages`, puis échange-le contre un **jeton longue durée**, valable 60 jours.

Rafraîchis le jeton tous les 50 jours environ, puis mets à jour la variable dans l'environnement de la routine :
```bash
curl "https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=$IG_ACCESS_TOKEN"
```

## 3. Test en local (étape clé)
Partage un réel au compte dédié, puis lance :
```bash
pip install -r requirements.txt
export IG_ACCESS_TOKEN=...
python scripts/fetch_reels.py --debug
```
Examine le JSON brut. L'essentiel est de trouver une **URL vidéo directe** (`video_data.url`, `file_url` ou `payload.url`, qui pointe vers un domaine `cdninstagram` ou `fbcdn`), et pas seulement un lien `instagram.com/reel/...`.
- **Si l'URL directe est présente**, tout le pipeline fonctionne. Lance `python scripts/fetch_reels.py` puis `python scripts/extract.py`, et vérifie le contenu de `work/`.
- **Si seul le permalien apparaît**, la lecture par sondage ne suffit pas pour ton compte. Le plan B consiste à ajouter un webhook minimal (un Cloudflare Worker gratuit) qui reçoit l'`ig_reel` en temps réel. Le reste du pipeline ne change pas.

## 4. Repo et routine
1. Pousse ce dossier dans un **repo GitHub privé**.
2. Sur [claude.ai/code/routines](https://claude.ai/code/routines), clique sur *New routine* et configure :
   - **Repo** : celui-ci.
   - **Prompt** : le contenu de `ROUTINE_PROMPT.md`.
   - **Environnement**, variables : `IG_ACCESS_TOKEN` et `IG_ALLOWED_SENDERS=ton_compte_perso`. Ce filtre fait que seuls tes partages sont traités, et pas les DM d'inconnus.
   - **Environnement**, accès réseau : en plus des registres de paquets, autorise `graph.instagram.com`, `*.cdninstagram.com`, `*.fbcdn.net`, `huggingface.co` et `*.hf.co` (pour télécharger le modèle Whisper).
   - **Environnement**, setup script : `bash scripts/setup.sh`.
   - **Déclencheur** : planifié, toutes les 4 à 6 heures par exemple.
   - **Branches** : par défaut, une routine ne pousse que sur des branches `claude/...`. Autorise le push sur `main` si tu veux que les fiches arrivent directement, sinon fusionne les PR.
3. Lance la routine une première fois à la main et lis le résumé.

## Limites à connaître
- **20 messages** : l'API ne donne le détail que des 20 derniers messages d'une conversation. Si tu partages plus de 20 réels entre deux exécutions, les plus anciens sont perdus. Le script t'avertit dans ce cas ; il suffit d'augmenter la fréquence.
- **Liens temporaires** : les URL vidéo expirent, mais chaque exécution redemande des URL fraîches à l'API.
- **Coût** : chaque exécution consomme ton quota Claude. Quand il n'y a rien de nouveau, la routine s'arrête vite.
- **Whisper** : le modèle `small` est un bon compromis. Passe `WHISPER_MODEL=medium` si les transcriptions sont approximatives, ou `base` pour aller plus vite.
- **Droits** : les réels appartiennent à leurs créateurs. Garde le repo privé et cite la source dans chaque fiche, ce que le template fait déjà.
