"""Transcript corrections — the user always has the last word on what was said.

Three ways to fix a transcript (all fully offline), stored as an edit layer in SQLite
(stage "transcript_edits") on top of the cached Whisper transcript:

1. Manual: replace the words in a time range with your own text
   (or find-and-replace a phrase).
2. Re-listen: re-transcribe just that moment (short windows are much more accurate on noisy
   footage), optionally with a hint of what was said.
3. Context: give free-text context — names, places, or "quoted things you said". Quoted phrases are
   located in the transcript and re-listened with the quote as a hint; unclear stretches are
   re-listened with the whole context as a prompt.

`effective_transcript(hash)` = original words outside edited ranges + edited words; captions and
clip texts use it. Edits never change the timing span they replace, so cut safety is preserved.
"""

from __future__ import annotations

import difflib
import re
import time
from pathlib import Path

import numpy as np

from . import db

STAGE = "transcript_edits"
VERSION = "1"


# ------------------------------------------------------------------ storage

def get_edits(file_hash: str) -> list[dict]:
    return db.cache_get(file_hash, STAGE, VERSION) or []


def _save(file_hash: str, edits: list[dict]) -> None:
    db.cache_put(file_hash, STAGE, VERSION, sorted(edits, key=lambda e: e["start"]))


def clear_edits(file_hash: str, edit_id: str | None = None) -> int:
    edits = get_edits(file_hash)
    keep = [e for e in edits if edit_id is not None and e["id"] != edit_id]
    _save(file_hash, keep)
    return len(edits) - len(keep)


def _add_edit(file_hash: str, start: float, end: float, words: list[dict], source: str,
              note: str = "") -> dict:
    edits = [e for e in get_edits(file_hash) if e["end"] <= start or e["start"] >= end]  # replace overlaps
    e = {"id": f"e{int(time.time() * 1000) % 10**10}", "start": round(start, 3), "end": round(end, 3),
         "words": words, "text": "".join(w["word"] for w in words).strip(), "source": source,
         "note": note}
    edits.append(e)
    _save(file_hash, edits)
    return e


# ------------------------------------------------------------------ effective transcript

def effective_transcript(file_hash: str) -> dict | None:
    from .transcribe import _sentences, get_transcript

    base = get_transcript(file_hash)
    if base is None:
        return None
    edits = get_edits(file_hash)
    if not edits:
        return base
    words = []
    for w in base["words"]:
        mid = (w["start"] + w["end"]) / 2
        if not any(e["start"] <= mid < e["end"] for e in edits):
            words.append(w)
    for e in edits:
        words.extend(e["words"])
    words.sort(key=lambda w: w["start"])
    return {**base, "words": words, "sentences": _sentences(words) if words else [],
            "edited": True}


def words_in(file_hash: str, start: float, end: float) -> list[dict]:
    tr = effective_transcript(file_hash) or {"words": []}
    return [w for w in tr["words"] if start <= (w["start"] + w["end"]) / 2 < end]


# ------------------------------------------------------------------ 1. manual

def _spread(text: str, start: float, end: float) -> list[dict]:
    """Give typed words timings spread across [start, end] in proportion to their length."""
    toks = text.split()
    if not toks:
        return []
    weights = np.array([max(1, len(t)) for t in toks], float)
    edges = start + (end - start) * np.concatenate([[0], np.cumsum(weights) / weights.sum()])
    return [{"word": " " + t, "start": round(float(edges[i]), 3), "end": round(float(edges[i + 1]) - 0.01, 3),
             "prob": 1.0} for i, t in enumerate(toks)]


def correct_text(file_hash: str, start: float, end: float, text: str) -> dict:
    """Replace whatever was transcribed between start and end with `text` (the words' time span
    is kept, so cuts stay between words)."""
    old = words_in(file_hash, start, end)
    s = old[0]["start"] if old else start
    e = old[-1]["end"] if old else end
    return _add_edit(file_hash, s, e + 1e-3, _spread(text, s, e), "manual")


def find_phrase(file_hash: str, phrase: str, near: float | None = None,
                window: float = 6.0) -> tuple[float, float, float] | None:
    """Best fuzzy match of a phrase in the transcript → (start, end, similarity 0..1).
    With `near`, only words within ±window seconds of that time are considered."""
    tr = effective_transcript(file_hash) or {"words": []}
    ws = tr["words"]
    if near is not None:
        ws = [w for w in ws if abs((w["start"] + w["end"]) / 2 - near) <= window]
    target = re.sub(r"[^a-z0-9]", "", phrase.lower())
    if not ws or not target:
        return None
    n = len(phrase.split())
    best = None
    for L in range(max(1, n - 2), n + 3):
        for i in range(0, max(1, len(ws) - L + 1)):
            chunk = ws[i:i + L]
            cand = re.sub(r"[^a-z0-9]", "", "".join(w["word"] for w in chunk).lower())
            r = difflib.SequenceMatcher(None, target, cand).ratio()
            if best is None or r > best[2]:
                best = (chunk[0]["start"], chunk[-1]["end"], r)
    return best


def find_replace(file_hash: str, find: str, replace: str, min_similarity: float = 0.75) -> dict | None:
    hit = find_phrase(file_hash, find)
    if not hit or hit[2] < min_similarity:
        return None
    return correct_text(file_hash, hit[0], hit[1] + 1e-3, replace)


# ------------------------------------------------------------------ 2. re-listen

def relisten(file_hash: str, start: float, end: float, hint: str | None = None,
             pad: float = 2.0) -> dict:
    """Re-transcribe a short window around [start, end] (optionally with a hint of what was said)
    and replace the words there with the new result (real word timings)."""
    import subprocess

    from .hardware import detect
    from .transcribe import SPEECH_FILTER, _model_lock, load_model

    media = db.get_media(file_hash)
    a = max(0.0, start - pad)
    b = min(media["meta"]["duration"], end + pad)
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                        "-i", media["audio_path"], "-af", SPEECH_FILTER, "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True)
    audio = np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0
    model = load_model(detect().whisper_model)
    lang = (effective_transcript(file_hash) or {}).get("language")
    with _model_lock:
        segs, _ = model.transcribe(audio, language=lang, word_timestamps=True, vad_filter=False,
                                   beam_size=5, condition_on_previous_text=False,
                                   initial_prompt=hint or None)
        new = [{"word": w.word, "start": round(a + w.start, 3), "end": round(a + w.end, 3),
                "prob": round(w.probability, 3)} for s in segs for w in (s.words or [])]
    # keep only the words inside the requested range (the padding is just listening context)
    new = [w for w in new if start - 0.15 <= (w["start"] + w["end"]) / 2 < end + 0.15]
    old = words_in(file_hash, start, end)
    s = min([w["start"] for w in old + new] or [start])
    e = max([w["end"] for w in old + new] or [end])
    return _add_edit(file_hash, s, e + 1e-3, new, "relisten" if not hint else "relisten+hint",
                     note=hint or "")


# ------------------------------------------------------------------ 3. context

def apply_context(file_hash: str, context: str, clarity_below: float = 0.6) -> list[dict]:
    """Use free-text context to improve the transcript (offline):
    - each "quoted phrase" is located (fuzzy) and that moment re-listened with the quote as hint;
    - sentences Whisper was unsure about are re-listened with the whole context as a prompt."""
    tr = effective_transcript(file_hash)
    if tr is None:
        return []
    changes = []
    # quotes, each paired with the closest time mentioned in the text ("at 1:43 I said '...'")
    times = [(m.start(), int(m.group(1)) * 60 + float(m.group(2)))
             for m in re.finditer(r"(\d{1,2}):(\d{2}(?:\.\d+)?)", context)]
    done: list[tuple[float, float]] = []
    for qm in re.finditer(r"[\"“']([^\"”']{3,})[\"”']", context):
        q = qm.group(1)
        near = min(times, key=lambda x: abs(x[0] - qm.start()))[1] if times else None
        if near is not None:
            # only the words around that moment that best match the quote ("it is Ywansuk" for
            # "you want some?"); the rest of the sentence is left alone
            hit = find_phrase(file_hash, q, near=near + 0.5, window=3.0)
            if not hit:
                continue
            s, e = hit[0], hit[1]
            # re-listen the sentence that contains those words (keeps punctuation natural,
            # leaves neighbouring sentences untouched)
            for sent in tr["sentences"]:
                if sent["start"] - 0.05 <= s <= sent["end"] + 0.05 or sent["start"] <= e <= sent["end"]:
                    s, e = min(s, sent["start"]), max(e, sent["end"])
        else:
            hit = find_phrase(file_hash, q)
            if not hit or hit[2] < 0.6:
                continue
            s, e = hit[0], hit[1]
            for sent in tr["sentences"]:
                if sent["start"] <= s < sent["end"] + 0.5:
                    s, e = min(s, sent["start"]), max(e, sent["end"])
        key = re.sub(r"[^a-z]", "", q.lower())
        for pad in (2.0, 3.0, 1.5, 4.0):  # more surrounding audio usually helps; try a few
            ed = relisten(file_hash, s, e, hint=q, pad=pad)
            if key in re.sub(r"[^a-z]", "", ed["text"].lower()):
                break
            clear_edits(file_hash, ed["id"])
        if key not in re.sub(r"[^a-z]", "", ed["text"].lower()):
            # the model still didn't hear it: trust the user and write the quote over the closest
            # matching words in that sentence
            clear_edits(file_hash, ed["id"])
            rng = find_phrase(file_hash, q, near=(s + e) / 2, window=(e - s) / 2 + 0.5)
            ed = correct_text(file_hash, rng[0], rng[1] + 1e-3, q) if rng else correct_text(file_hash, s, e, q)
        changes.append(ed)
        done.append((ed["start"], ed["end"]))
    # general context (names, places, topic) beyond the quotes → re-listen unclear sentences with it
    general = re.sub(r"[\"“'][^\"”']{3,}[\"”']", " ", context)
    general = re.sub(r"\b(at|around)?\s*\d+:\d+(\.\d+)?\b|\bi said\b", " ", general, flags=re.I).strip(" ,.;")
    if len(general.split()) < 3:
        return changes
    for sent in tr["sentences"]:
        ws = tr["words"][sent["first_word"]: sent["last_word"] + 1]
        if not ws or any(a <= sent["start"] < b for a, b in done):
            continue
        if float(np.mean([w["prob"] for w in ws])) < clarity_below:
            ed = relisten(file_hash, sent["start"], sent["end"], hint=general)
            changes.append(ed)
    return changes
