#!/usr/bin/env python3
"""Prépare chaque réel de work/queue.json pour que Claude puisse le « regarder » :
- images clés (ffmpeg), nommées par horodatage : frames/t0012.5s.jpg
- transcription horodatée de l'audio (faster-whisper, en local) : transcript.txt

Variables : WHISPER_MODEL (défaut "small"), MAX_FRAMES (défaut 30).
"""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = ROOT / "work" / "queue.json"
MAX_FRAMES = int(os.environ.get("MAX_FRAMES", "30"))
MIN_INTERVAL = 2.0


def duration_of(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def extract_frames(video, out_dir, duration):
    out_dir.mkdir(parents=True, exist_ok=True)
    interval = max(MIN_INTERVAL, duration / MAX_FRAMES)
    frames, t = [], 0.5
    while t < duration:
        dest = out_dir / f"t{t:06.1f}s.jpg"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", str(dest)],
            check=False,
        )
        if dest.exists():
            frames.append(str(dest.relative_to(ROOT)))
        t += interval
    return frames


def fmt(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def transcribe(model, video, dest):
    try:
        segments, info = model.transcribe(str(video), vad_filter=True)
        lines = [f"[{fmt(s.start)}] {s.text.strip()}" for s in segments]
    except Exception as exc:  # pas de piste audio, fichier illisible…
        dest.write_text(f"(transcription impossible : {exc})\n")
        return None
    text = "\n".join(lines) if lines else "(aucune parole détectée : musique seule ou vidéo muette)"
    dest.write_text(f"# langue détectée : {info.language}\n{text}\n")
    return info.language


def main():
    if not QUEUE_FILE.exists():
        print("Pas de work/queue.json : lance d'abord fetch_reels.py")
        return
    queue = json.loads(QUEUE_FILE.read_text())
    ready = [q for q in queue if q["status"] == "ready"]
    if not ready:
        print("Aucun réel à préparer.")
        return

    from faster_whisper import WhisperModel
    model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu", compute_type="int8")

    for item in ready:
        work_dir = ROOT / item["work_dir"]
        video = work_dir / "video.mp4"
        try:
            item["duration_s"] = round(duration_of(video), 1)
        except subprocess.CalledProcessError:
            item["status"], item["error"] = "failed", "vidéo illisible (ffprobe)"
            continue
        item["frames"] = extract_frames(video, work_dir / "frames", item["duration_s"])
        item["language"] = transcribe(model, video, work_dir / "transcript.txt")
        item["transcript"] = str((work_dir / "transcript.txt").relative_to(ROOT))
        print(f"✓ {item['short_id']} : {item['duration_s']}s, {len(item['frames'])} images, "
              f"langue {item['language']}")

    QUEUE_FILE.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
