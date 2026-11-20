"""Stage: whose voice is it? — so captions only show what YOU say (offline speaker verification).

- Each transcript sentence gets a speaker embedding (SpeechBrain ECAPA-TDNN, VoxCeleb; 192-d),
  cached in SQLite by file hash.
- Your voice profile (data/voice_profile.json) is built automatically from the dominant speaker in
  your footage (in a vlog that's you), and refined with every voice instruction you record in the
  app (those are always you) and every "this is me / not me" you tap in the review screen.
- Each sentence is assigned to the nearer of two voices: your profile, or an "other voices" profile
  built from the lines least like you in that file (falls back to a fixed THRESHOLD when there is
  no second voice). Your taps ("me" / "not me") always win.
  Test clip: a distinct second voice ("Nice to meet you", "You guys", ...) scores 0.4–0.6 to the
  other-voice profile and ≤ 0.29 to you; your short lines ("Great.", "Oh my gosh.") go to you.
"""

from __future__ import annotations

import json
import threading
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import db
from .config import get_settings

STAGE = "speakers"
VERSION = "1"
MARKS = "speaker_marks"
THRESHOLD = 0.38      # used when there's no second voice to compare with
OTHER_SEED = 0.25     # lines below this similarity seed the "other voices" profile
MIN_WINDOW = 1.2
HF_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _encoder():
    from speechbrain.inference.speaker import EncoderClassifier

    save = get_settings().models_dir / "speechbrain" / "spkrec-ecapa-voxceleb"
    # once downloaded, load straight from the local folder (no network)
    source = str(save) if (save / "hyperparams.yaml").exists() else HF_SOURCE
    return EncoderClassifier.from_hparams(source=source, savedir=str(save), run_opts={"device": "cpu"})


def embed(samples: np.ndarray) -> np.ndarray:
    import torch

    with _lock, torch.no_grad():
        e = _encoder().encode_batch(torch.from_numpy(samples.astype(np.float32))[None]).squeeze().numpy()
    return e / (np.linalg.norm(e) + 1e-9)


def sentence_embeddings(file_hash: str, *, force: bool = False) -> dict:
    """{"ranges": [[start, end], ...], "emb": (n, 192) array} for the file's base sentences."""
    from .ffmpeg import load_audio
    from .transcribe import get_transcript

    tr = get_transcript(file_hash) or {"sentences": []}
    ranges = [[round(s["start"], 3), round(s["end"], 3)] for s in tr["sentences"]]
    meta = db.cache_get(file_hash, STAGE, VERSION)
    blob = db.blob_get(file_hash, STAGE, VERSION)
    if not force and meta and blob is not None and meta.get("ranges") == ranges:
        return {"ranges": ranges, "emb": np.frombuffer(blob, np.float32).reshape(-1, 192)}
    media = db.get_media(file_hash)
    if not ranges or not media.get("audio_path"):
        emb = np.zeros((0, 192), np.float32)
    else:
        y = load_audio(Path(media["audio_path"]), 16000)
        embs = []
        for a, b in ranges:
            if b - a < MIN_WINDOW:  # short lines: listen to a slightly longer window around them
                c = (a + b) / 2
                a, b = c - MIN_WINDOW / 2, c + MIN_WINDOW / 2
            seg = y[int(max(0.0, a) * 16000): int(b * 16000)]
            embs.append(embed(seg) if len(seg) > 1600 else np.zeros(192, np.float32))
        emb = np.array(embs, np.float32)
    db.cache_put(file_hash, STAGE, VERSION, {"ranges": ranges})
    db.blob_put(file_hash, STAGE, VERSION, emb.astype(np.float32).tobytes())
    return {"ranges": ranges, "emb": emb}


# ------------------------------------------------------------------ voice profile

def _profile_path() -> Path:
    return get_settings().home / "voice_profile.json"


def load_profile() -> dict | None:
    p = _profile_path()
    return json.loads(p.read_text()) if p.exists() else None


def _save_profile(vec: np.ndarray, source: str, n: float) -> dict:
    vec = vec / (np.linalg.norm(vec) + 1e-9)
    prof = {"embedding": [round(float(x), 6) for x in vec], "source": source, "weight": round(n, 2),
            "updated": time.time()}
    _profile_path().write_text(json.dumps(prof))
    return prof


def dominant_speaker(embs: list[np.ndarray], weights: list[float]) -> np.ndarray | None:
    """Weighted centroid of the main speaker (iteratively ignoring the least similar 40 %)."""
    if not embs:
        return None
    E, w = np.array(embs), np.array(weights)
    ok = np.linalg.norm(E, axis=1) > 0
    E, w = E[ok], w[ok]
    if not len(E):
        return None
    cent = (E * w[:, None]).sum(0)
    for _ in range(3):
        cent /= np.linalg.norm(cent) + 1e-9
        sims = E @ cent
        keep = sims >= np.percentile(sims, 40)
        cent = (E[keep] * w[keep, None]).sum(0)
    return cent / (np.linalg.norm(cent) + 1e-9)


def ensure_profile(hashes: list[str]) -> dict | None:
    """Use the saved profile; if there's none yet, learn it from the main speaker in this footage."""
    prof = load_profile()
    if prof:
        return prof
    embs, weights = [], []
    for h in hashes:
        se = sentence_embeddings(h)
        for (a, b), e in zip(se["ranges"], se["emb"]):
            embs.append(e)
            weights.append(b - a)
    cent = dominant_speaker(embs, weights)
    return _save_profile(cent, "auto: main speaker in your footage", 1.0) if cent is not None else None


def add_voice_sample(samples: np.ndarray, weight: float = 1.0, source: str = "voice instruction") -> dict:
    """Your recorded voice (e.g. a voice instruction) refines — or replaces an automatic — profile."""
    e = embed(samples)
    prof = load_profile()
    if not prof or prof["source"].startswith("auto"):
        old = np.array(prof["embedding"]) if prof else None
        vec = e if old is None else 0.7 * e + 0.3 * old
        return _save_profile(vec, source, weight)
    old, n = np.array(prof["embedding"]), prof.get("weight", 1.0)
    return _save_profile((old * n + e * weight) / (n + weight), prof["source"], min(50.0, n + weight))


# ------------------------------------------------------------------ who spoke when

def get_marks(file_hash: str) -> list[dict]:
    return db.cache_get(file_hash, MARKS, VERSION) or []


def mark(file_hash: str, start: float, end: float, is_me: bool) -> None:
    """Manual override from the review screen; 'me' marks also teach the voice profile."""
    marks = [m for m in get_marks(file_hash) if m["end"] <= start or m["start"] >= end]
    marks.append({"start": round(start, 3), "end": round(end, 3), "me": bool(is_me)})
    db.cache_put(file_hash, MARKS, VERSION, marks)
    if is_me:
        se = sentence_embeddings(file_hash)
        for (a, b), e in zip(se["ranges"], se["emb"]):
            if a < end and start < b and np.linalg.norm(e) > 0:
                prof = load_profile()
                if prof:
                    old, n = np.array(prof["embedding"]), prof.get("weight", 1.0)
                    _save_profile((old * n + e * 0.5) / (n + 0.5), prof["source"], min(50.0, n + 0.5))


def speaker_ranges(file_hash: str) -> list[dict]:
    """[{start, end, sim, me, marked}] per sentence."""
    prof = ensure_profile([file_hash])
    se = sentence_embeddings(file_hash)
    vec = np.array(prof["embedding"]) if prof else None
    marks = get_marks(file_hash)
    E = se["emb"]
    valid = np.linalg.norm(E, axis=1) > 0 if len(E) else np.zeros(0, bool)
    sims = E @ vec if (vec is not None and len(E)) else np.ones(len(E))
    # "other voices" profile for this file: the lines least like you. Each line is then given to
    # whichever voice it's closer to (clearer than one fixed cut-off for short, noisy lines).
    other = None
    low = valid & (sims < OTHER_SEED)
    if vec is not None and low.sum() >= 2:
        other = E[low].mean(0)
        other /= np.linalg.norm(other) + 1e-9
    out = []
    for (a, b), e, sim, ok in zip(se["ranges"], E, sims, valid):
        sim = float(sim) if ok else 1.0
        if other is not None and ok:
            me = sim - float(e @ other) > 0
        else:
            me = sim >= THRESHOLD
        marked = next((m for m in marks if m["start"] < b and a < m["end"]), None)
        if marked:
            me = marked["me"]
        out.append({"start": a, "end": b, "sim": round(sim, 3), "me": me, "marked": bool(marked)})
    return out


def is_me_fn(file_hash: str):
    """Fast lookup: t → is this moment spoken by you? (times outside any sentence → True)."""
    rs = speaker_ranges(file_hash)
    marks = get_marks(file_hash)

    def f(t: float) -> bool:
        for m in marks:
            if m["start"] - 0.05 <= t <= m["end"] + 0.05:
                return m["me"]
        for r in rs:
            if r["start"] - 0.05 <= t <= r["end"] + 0.05:
                return r["me"]
        return True
    return f
