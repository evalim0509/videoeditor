"""Stage: shot detection with PySceneDetect (on the 480p proxy).

Output (cached under stage "shots"):
{"cuts": [t, ...], "shots": [{"start", "end"}], "detector": "adaptive", "fps": 30.0}
"""

from __future__ import annotations

from pathlib import Path

from . import db

VERSION = "1"
STAGE = "shots"
MIN_SHOT_SECONDS = 0.6


def detect_shots(video_path: Path, duration: float) -> dict:
    from scenedetect import AdaptiveDetector, detect

    fps_guess = 30.0
    min_frames = int(MIN_SHOT_SECONDS * fps_guess)
    scenes = detect(str(video_path), AdaptiveDetector(min_scene_len=min_frames), show_progress=False)
    shots = [{"start": round(a.get_seconds(), 3), "end": round(b.get_seconds(), 3)}
             for a, b in scenes]
    if not shots:  # one continuous take
        shots = [{"start": 0.0, "end": round(duration, 3)}]
    cuts = [s["start"] for s in shots[1:]]
    return {"cuts": cuts, "shots": shots, "detector": "adaptive", "count": len(shots)}


def shots(file_hash: str, *, force: bool = False) -> dict:
    if not force:
        cached = db.cache_get(file_hash, STAGE, VERSION)
        if cached is not None:
            return cached
    media = db.get_media(file_hash)
    if media is None:
        raise ValueError(f"{file_hash[:12]} is not ingested")
    result = detect_shots(Path(media["proxy_path"]), media["meta"]["duration"])
    db.cache_put(file_hash, STAGE, VERSION, result)
    return result
