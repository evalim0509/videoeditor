"""Run every analysis stage for a file (each one is cached in SQLite, so this is cheap to repeat)."""

from __future__ import annotations

from typing import Callable

STAGES = ["transcript", "audio", "shots", "visual", "speakers"]


def _speakers(file_hash: str) -> None:
    try:
        from .speakers import sentence_embeddings

        sentence_embeddings(file_hash)
    except Exception as e:  # never block an edit on this; captions then include every voice
        print(f"[autocut] voice recognition skipped: {e}")


def analyze_all(file_hash: str, progress: Callable[[str, float], None] | None = None) -> None:
    from .audio import analyze as audio_analyze
    from .shots import shots
    from .transcribe import transcribe
    from .visual import analyze as visual_analyze

    steps = [("transcribing speech", lambda: transcribe(file_hash)),
             ("listening for laughter & energy", lambda: audio_analyze(file_hash)),
             ("finding shot changes", lambda: shots(file_hash)),
             ("looking at the picture", lambda: visual_analyze(file_hash)),
             ("recognising your voice", lambda: _speakers(file_hash))]
    for i, (label, fn) in enumerate(steps):
        if progress:
            progress(label, i / len(steps))
        fn()
    if progress:
        progress("analysed", 1.0)
