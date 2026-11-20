"""Stage: transcription with faster-whisper (word-level timestamps).

Output (cached in SQLite under stage "transcript", keyed by file hash + model):
{
  "language": "en", "language_prob": 0.98, "model": "small", "duration": 149.4,
  "segments":  [{"start", "end", "text", "words": [{"start", "end", "word", "prob"}]}],
  "words":     flat list of all words,
  "sentences": [{"start", "end", "text", "first_word", "last_word"}]   # used for "never cut mid-word"
}
"""

from __future__ import annotations

import re
import threading
from functools import lru_cache
from pathlib import Path

from . import db
from .hardware import detect

VERSION = "2"
STAGE = "transcript"
SENTENCE_END = re.compile(r"[.!?。！？…]+[\"')\]]*$")
PAUSE_SPLIT = 0.8     # a silence this long also ends a sentence
MAX_SENTENCE = 15.0   # very long run-ons are split at the largest internal pause

_model_lock = threading.Lock()


@lru_cache(maxsize=2)
def load_model(size: str | None = None):
    from faster_whisper import WhisperModel

    from .models import whisper_model_path

    hw = detect()
    size = size or hw.whisper_model
    path = whisper_model_path(size)
    return WhisperModel(path, device=hw.whisper_device, compute_type=hw.whisper_compute,
                        cpu_threads=max(1, hw.cpu_cores))


def _sentences(words: list[dict]) -> list[dict]:
    sents: list[list[dict]] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 0.0
        if SENTENCE_END.search(w["word"].strip()) or gap >= PAUSE_SPLIT or nxt is None:
            sents.append(cur)
            cur = []
    # split overly long sentences at their biggest pause
    out: list[list[dict]] = []
    stack = sents[::-1]
    while stack:
        s = stack.pop()
        if s[-1]["end"] - s[0]["start"] > MAX_SENTENCE and len(s) > 3:
            gaps = [(s[j + 1]["start"] - s[j]["end"], j) for j in range(1, len(s) - 2)]
            _, j = max(gaps)
            stack.append(s[j + 1:])
            stack.append(s[: j + 1])
        else:
            out.append(s)
    return [{
        "start": s[0]["start"], "end": s[-1]["end"],
        "text": "".join(w["word"] for w in s).strip(),
        "first_word": words.index(s[0]), "last_word": words.index(s[-1]),
    } for s in out]


# Speech clean-up applied before Whisper (timing-neutral): remove rumble/hiss and lift quiet
# voices over background noise. Phone footage in restaurants/streets needs this badly.
SPEECH_FILTER = "highpass=f=90,lowpass=f=7500,dynaudnorm=f=150:g=15:p=0.9"
VAD_PARAMS = {"threshold": 0.25, "min_silence_duration_ms": 500, "speech_pad_ms": 400}


def detect_language(model, audio, sr: int = 16000) -> tuple[str, float]:
    """Vote over up to 5 windows spread across the file (one window is easily fooled by noise
    or accents)."""
    n = len(audio)
    win = 30 * sr
    starts = [0] if n <= win else [int(i * (n - win) / 4) for i in range(5)]
    totals: dict[str, float] = {}
    for st in starts:
        _, _, probs = model.detect_language(audio[st: st + win])
        for lang, pr in probs:
            totals[lang] = totals.get(lang, 0.0) + pr
    lang = max(totals, key=totals.get)
    return lang, totals[lang] / len(starts)


def _drop_hallucinations(segs: list) -> list:
    out, last_text, repeats = [], None, 0
    for s in segs:
        if s.compression_ratio > 2.4 or s.avg_logprob < -1.5:
            continue  # repetition loops / garbage
        if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
            continue  # text invented over silence
        text = s.text.strip().lower()
        repeats = repeats + 1 if text == last_text else 0
        last_text = text
        if repeats >= 2:
            continue  # same line 3+ times in a row
        out.append(s)
    return out


def transcribe_audio(audio_path: Path, *, model_size: str | None = None,
                     language: str | None = None, vad: bool = True) -> dict:
    """Transcribe any audio/video file without caching (also used for voice commands)."""
    from .ffmpeg import load_audio

    size = model_size or detect().whisper_model
    model = load_model(size)
    audio = load_audio(Path(audio_path), 16000, af=SPEECH_FILTER)
    empty = {"language": language, "language_prob": 0.0, "model": size,
             "duration": round(audio.size / 16000, 3), "segments": [], "words": [],
             "sentences": []}
    if audio.size < 8000:  # < 0.5 s
        return empty
    with _model_lock:  # CTranslate2 models are not safe to call concurrently on CPU
        lang_prob = 1.0
        if language is None:
            language, lang_prob = detect_language(model, audio)
        segments, info = model.transcribe(
            audio, language=language, word_timestamps=True, vad_filter=vad,
            vad_parameters=VAD_PARAMS, beam_size=5, condition_on_previous_text=False)
        raw = _drop_hallucinations(list(segments))
    segs = []
    for s in raw:
        words = [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word,
                  "prob": round(w.probability, 3)} for w in (s.words or [])]
        if not words:
            continue
        segs.append({"start": round(s.start, 3), "end": round(s.end, 3),
                     "text": s.text.strip(), "words": words,
                     "avg_logprob": round(s.avg_logprob, 3),
                     "no_speech_prob": round(s.no_speech_prob, 3)})
    words = [w for s in segs for w in s["words"]]
    return {
        "language": language, "language_prob": round(lang_prob, 3), "model": size,
        "duration": round(info.duration, 3),
        "segments": segs, "words": words, "sentences": _sentences(words) if words else [],
    }


def transcribe(file_hash: str, *, model_size: str | None = None, language: str | None = None,
               force: bool = False) -> dict:
    """Transcribe an ingested file (by hash), using the SQLite cache."""
    size = model_size or detect().whisper_model
    version = f"{VERSION}:{size}:{language or 'auto'}"
    if not force:
        cached = db.cache_get(file_hash, STAGE, version)
        if cached is not None:
            return cached
    media = db.get_media(file_hash)
    if media is None:
        raise ValueError(f"{file_hash[:12]} is not ingested")
    if not media["audio_path"]:
        result = {"language": None, "language_prob": 0.0, "model": size,
                  "duration": media["meta"]["duration"], "segments": [], "words": [],
                  "sentences": []}
    else:
        result = transcribe_audio(Path(media["audio_path"]), model_size=size, language=language)
    db.cache_put(file_hash, STAGE, version, result)
    return result


def get_transcript(file_hash: str) -> dict | None:
    """Return any cached transcript for this file (whichever model produced it)."""
    with db.connect() as c:
        row = c.execute("SELECT version FROM analysis WHERE hash=? AND stage=? "
                        "ORDER BY created_at DESC", (file_hash, STAGE)).fetchone()
    return db.cache_get(file_hash, STAGE, row["version"]) if row else None


def fmt_ts(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"
