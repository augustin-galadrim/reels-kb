#!/usr/bin/env python3
"""Prépare chaque réel prêt de work/queue.json pour que Claude puisse le « regarder » :
- au plus max_frames images, choisies selon ce qui change à l'écran (voir frame_times), nommées par
  horodatage : frames/t0012.5s.jpg ;
- transcription horodatée de l'audio (faster-whisper, en local) : transcript.txt.

Le modèle Whisper n'est chargé que si au moins un réel a du son. Sans faster-whisper, ou sans
piste audio, la fiche reste possible à partir des images et de la légende.
Variables : WHISPER_MODEL (défaut "small").
"""
import os
import subprocess

from common import CONFIG, ROOT, load_queue, save_queue

# Sélection des images, réglée le 24/10/2026 sur 9 réels (schémas qui se construisent, animations sur fond
# sombre, plans alternés présentateur / interlocuteur, écran partagé schéma / visage). On mesure la nouveauté
# dans les zones fixes de l'image (fond, schéma, code), pas là où la personne bouge.
SAMPLE_FPS = 2      # échantillons analysés par seconde (miniatures 144x256 en niveaux de gris)
THUMB = (144, 256)
CUT = 0.18          # écart moyen entre deux échantillons consécutifs au-delà duquel on change de plan
STATIC = 0.02       # un pixel est « fixe » s'il bouge peu au moins 75 % du temps
STRONG = 0.15       # écart d'un pixel qui compte comme un vrai changement
NEW_CONTENT = 0.008 # part des pixels fixes changés, et restés changés 1 s, qui fait une nouvelle étape
MIN_GAP = 1.0       # images d'un même plan à moins d'une seconde l'une de l'autre : on garde la plus récente
STILL = 0.06        # écart sous lequel un pixel est immobile pendant la seconde qui précède une image
SAME_SHOT = 0.02    # part des pixels immobiles qui diffèrent sous laquelle deux plans montrent la même chose
                    # (mesuré : 0,5 à 1,6 % pour une même personne filmée, 2,6 % et plus entre deux schémas)


def duration_of(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(video)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def uniform_times(duration, n):
    return [round(duration * (i + 0.5) / n, 1) for i in range(n)]


def frame_times(video, duration, max_frames):
    """Instants à capturer : la fin de chaque état visuel distinct, là où il est le plus complet.
    - Une coupe de plan, ou un ajout qui reste à l'écran dans les zones fixes (diagramme qui se construit,
      code qui s'écrit), termine un état : on garde l'image juste avant.
    - Dans un même plan, deux fins d'état à moins d'une seconde n'en font qu'une ; jamais à travers une coupe.
    - Un plan qui montre la même chose qu'un plan précédent (la personne qui revient entre deux schémas,
      jump cuts) remplace celui-ci : une seule image par décor, la plus récente.
    - Au-delà de max_frames, on garde les états restés le plus longtemps à l'écran : les étapes qu'on
      explique plutôt que les animations de passage."""
    try:
        import numpy as np
        w, h = THUMB
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
                              f"fps={SAMPLE_FPS},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        s = np.frombuffer(raw, np.uint8).reshape(-1, w * h).astype(np.float32) / 255
    except (ImportError, subprocess.CalledProcessError, ValueError):
        return uniform_times(duration, max_frames)
    n = len(s)
    if n < 3:
        return uniform_times(duration, min(max_frames, 3))

    static = np.percentile(np.abs(np.diff(s, axis=0)), 75, axis=0) < STATIC
    if static.mean() < 0.15:  # tout l'écran bouge (enregistrement d'écran, défilement) : mesure globale
        static = np.ones_like(static)

    cuts, events, ref = {0}, [], 0
    for i in range(1, n):
        if np.abs(s[i] - s[i - 1]).mean() > CUT:
            cuts.add(i)
            events.append(i)
            ref = i
            continue
        changed = static.copy()
        for j in range(i, min(i + SAMPLE_FPS + 1, n)):  # le changement doit tenir une seconde
            changed &= np.abs(s[j] - s[ref]) > STRONG
        if changed.mean() > NEW_CONTENT:
            events.append(i)
            ref = i
    shot_start = np.maximum.accumulate([i if i in cuts else 0 for i in range(n)])

    candidates, anchor = [], None
    for i in sorted({e - 1 for e in events} | {n - 1}):
        if anchor is not None and (i - anchor) / SAMPLE_FPS < MIN_GAP and shot_start[i] == shot_start[anchor]:
            candidates[-1] = i  # la plus récente est la plus complète
        else:
            candidates.append(i)
            anchor = i
    shown = {c: c - p for p, c in zip([-1] + candidates, candidates)}  # durée à l'écran, en échantillons

    def still(i):  # pixels immobiles dans la seconde qui précède, sans remonter avant le début du plan
        lo = max(shot_start[i], i - SAMPLE_FPS)
        return np.abs(s[lo:i + 1] - s[i]).max(axis=0) < STILL

    def same_view(a, b):
        mask = still(a) & still(b)
        return mask.any() and (np.abs(s[a] - s[b]) > STRONG)[mask].mean() <= SAME_SHOT

    views = []  # [première image, plus récente, durée à l'écran] ; comparées à leur première image
    for c in candidates:
        for v in views:
            if shot_start[v[1]] != shot_start[c] and same_view(c, v[0]):
                v[1], v[2] = c, max(v[2], shown[c])
                break
        else:
            views.append([c, c, shown[c]])
    if len(views) > max_frames:
        views = sorted(views, key=lambda v: -v[2])[:max_frames]
    return [round(min(v[1] / SAMPLE_FPS, duration - 0.1), 1) for v in sorted(views, key=lambda v: v[1])]


def extract_frames(video, out_dir, duration):
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for t in frame_times(video, duration, CONFIG["max_frames"]):
        dest = out_dir / f"t{t:06.1f}s.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=720:-2,format=yuvj420p", "-q:v", "3", str(dest)],
                       check=False)
        if dest.exists():
            frames.append(str(dest.relative_to(ROOT)))
    return frames


def fmt(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def transcribe(model, video, dest):
    try:
        segments, info = model.transcribe(str(video), vad_filter=True)
        lines = [f"[{fmt(s.start)}] {s.text.strip()}" for s in segments]
    except Exception as exc:  # fichier illisible…
        dest.write_text(f"(transcription impossible : {exc})\n")
        return None
    text = "\n".join(lines) if lines else "(aucune parole détectée : musique seule ou vidéo muette)"
    dest.write_text(f"# langue détectée : {info.language}\n{text}\n")
    return info.language


def load_whisper():
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("AVERTISSEMENT: faster-whisper absent, fiches sans transcription (vérifie le setup script).")
        return None
    return WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu", compute_type="int8")


def main():
    queue = load_queue()
    ready = [q for q in queue if q["status"] == "ready"]
    model = load_whisper() if any(q.get("has_audio") for q in ready) else None

    for item in ready:
        work_dir = ROOT / item["work_dir"]
        video, transcript = work_dir / "video.mp4", work_dir / "transcript.txt"
        try:
            item["duration_s"] = round(duration_of(video), 1)
        except (subprocess.CalledProcessError, ValueError):
            item["status"], item["error"] = "failed", "vidéo illisible (ffprobe)"
            continue
        item["frames"] = extract_frames(video, work_dir / "frames", item["duration_s"])
        if not item.get("has_audio"):
            transcript.write_text("(pas de piste audio : fiche à partir des images et de la légende)\n")
        elif model is None:
            transcript.write_text("(transcription indisponible : faster-whisper absent)\n")
        else:
            item["language"] = transcribe(model, video, transcript)
        item["transcript"] = str(transcript.relative_to(ROOT))
        print(f"✓ {item['short_id']} : {item['duration_s']}s, {len(item['frames'])} images, "
              f"langue {item.get('language') or '—'}")

    save_queue(queue)


if __name__ == "__main__":
    main()
