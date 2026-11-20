"""Stage: voice instructions → text (local faster-whisper) → EditSpec.

The browser records audio (WebM/Opus on Android/Chrome, MP4/AAC on iPhone Safari); FFmpeg decodes
whatever arrives, so any format works.
"""

from __future__ import annotations

import time
from pathlib import Path

from .editspec import EditSpec

# Bias Whisper towards our vocabulary (improves "tiktok", "captions", numbers, etc.)
VOCAB_PROMPT = ("Make a funny 60 second TikTok with captions. Hype reel, calm vertical YouTube "
                "Shorts, 30 seconds, 2 minutes, no music, without subtitles, in the style of.")
MAX_COMMAND_SECONDS = 120


def transcribe_command(audio_path: Path, language: str | None = None) -> dict:
    import os

    from .ffmpeg import load_audio
    from .hardware import detect
    from .transcribe import transcribe_audio

    dur = len(load_audio(audio_path, 16000)) / 16000
    if dur > MAX_COMMAND_SECONDS:
        raise ValueError(f"Voice note is {dur:.0f}s long; keep instructions under "
                         f"{MAX_COMMAND_SECONDS}s.")
    t0 = time.time()
    lang = language or os.environ.get("AUTOCUT_VOICE_LANGUAGE") or None  # None = auto-detect
    tr = transcribe_audio(audio_path, model_size=detect().voice_model, language=lang, vad=True,
                          initial_prompt=VOCAB_PROMPT, fallback_language="en")
    text = " ".join(s["text"] for s in tr["segments"]).strip()
    return {"text": text, "language": tr["language"], "duration": round(dur, 2),
            "seconds": round(time.time() - t0, 2)}


def voice_to_spec(audio_path: Path, available_presets: list[str] | None = None) -> dict:
    from .intent import parse

    tr = transcribe_command(audio_path)
    if not tr["text"]:
        spec = EditSpec(warnings=["I didn't hear an instruction in that recording. Try again a bit "
                                  "closer to the mic."])
    else:
        spec = parse(tr["text"], available_presets=available_presets)
    return {"transcript": tr, "spec": spec.model_dump()}
