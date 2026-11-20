"""Stage: audio analysis.

- PANNs (Cnn14, AudioSet 527 classes) per-second scores for laughter, cheering, applause, music,
  speech and shouting. Each second i is scored from a 2 s window centred on [i, i+1).
- librosa loudness / energy per second: RMS level (dBFS), onset strength (how "eventful"),
  dynamics (dB swing within ±1 s: punchy/expressive vs steady narration) and a combined 0..1
  ``energy`` = 0.5·rank(onset) + 0.5·rank(dynamics). Raw loudness is deliberately NOT part of
  energy: tuned on user feedback, loud-but-flat narration was being picked as "high energy".
- Beat tracking (tempo + beat times) for beat-synced cuts later.

Output (cached under stage "audio"):
{"seconds": N, "scores": {"laughter": [...], ..., "rms_db": [...], "onset": [...], "energy": [...]},
 "tempo": 120.0, "beats": [...], "integrated_lufs": -23.1, "peak_dbfs": -1.2}
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import db
from .config import ANALYSIS_SAMPLE_RATE

VERSION = "3"
STAGE = "audio"

# AudioSet display names → our event categories (score = max over the member classes)
CATEGORIES: dict[str, list[str]] = {
    "laughter": ["Laughter", "Baby laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle"],
    "cheering": ["Cheering", "Crowd"],
    "applause": ["Applause", "Clapping"],
    "music": ["Music"],
    "speech": ["Speech", "Conversation", "Narration, monologue"],
    "shouting": ["Shout", "Yell", "Screaming", "Bellow", "Whoop", "Children shouting"],
}
EVENT_NAMES = list(CATEGORIES)
WINDOW_SECONDS = 2.0
EDGE_SECONDS = 1.5
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _panns():
    from .hardware import detect
    from .models import ensure_panns_labels, panns_checkpoint

    ensure_panns_labels()
    import torch
    from panns_inference.models import Cnn14

    from panns_inference import labels  # noqa: F401  (import after labels exist)

    model = Cnn14(sample_rate=32000, window_size=1024, hop_size=320, mel_bins=64, fmin=50,
                  fmax=14000, classes_num=527)
    ckpt = torch.load(panns_checkpoint(), map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["model"])
    device = detect().torch_device
    model.to(device).eval()
    return model, device


@lru_cache(maxsize=1)
def _label_index() -> dict[str, int]:
    import csv

    from .config import ASSETS_DIR

    with open(ASSETS_DIR / "audioset_class_labels.csv", newline="") as f:
        rows = list(csv.reader(f))[1:]
    return {r[2]: int(r[0]) for r in rows}


def panns_scores(y: np.ndarray, sr: int = ANALYSIS_SAMPLE_RATE, batch: int = 32) -> dict[str, list[float]]:
    import torch

    model, device = _panns()
    n_sec = int(np.ceil(len(y) / sr))
    half_pad = int((WINDOW_SECONDS - 1.0) / 2 * sr)
    padded = np.concatenate([np.zeros(half_pad, np.float32), y,
                             np.zeros(half_pad + sr, np.float32)])
    win = int(WINDOW_SECONDS * sr)
    idx = _label_index()
    cat_idx = {c: [idx[n] for n in names] for c, names in CATEGORIES.items()}
    out = {c: [] for c in CATEGORIES}
    with _lock, torch.no_grad():
        for b0 in range(0, n_sec, batch):
            chunk = np.stack([padded[i * sr: i * sr + win] for i in range(b0, min(n_sec, b0 + batch))])
            probs = model(torch.from_numpy(chunk).to(device))["clipwise_output"].cpu().numpy()
            for c, ids in cat_idx.items():
                out[c] += [round(float(v), 4) for v in probs[:, ids].max(axis=1)]
    return out


def _rank01(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return x
    order = x.argsort().argsort()
    return order / max(1, len(x) - 1)


def loudness_energy(y: np.ndarray, sr: int = ANALYSIS_SAMPLE_RATE) -> dict:
    import librosa
    import pyloudnorm as pyln

    n_sec = int(np.ceil(len(y) / sr))
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    fps = sr / hop
    frame_db = 20 * np.log10(rms + 1e-9)
    rms_db, onset_s, dyn = [], [], []
    for i in range(n_sec):
        a, b = int(i * fps), max(int(i * fps) + 1, int((i + 1) * fps))
        r = rms[a:b]
        rms_db.append(float(20 * np.log10(np.sqrt(np.mean(r ** 2)) + 1e-9)) if len(r) else -90.0)
        onset_s.append(float(np.mean(onset[a:b])) if len(onset[a:b]) else 0.0)
        w = frame_db[max(0, int((i - 1) * fps)): int((i + 2) * fps)]
        dyn.append(float(np.percentile(w, 95) - np.percentile(w, 10)) if len(w) > 4 else 0.0)
    rms_db_a, onset_a, dyn_a = np.array(rms_db), np.array(onset_s), np.array(dyn)
    energy = 0.5 * _rank01(onset_a) + 0.5 * _rank01(dyn_a)
    # Phone clips start/end with handling noise (picking up / putting down the phone): damp the
    # first and last 1.5 s so they don't masquerade as lively moments.
    edge = int(np.ceil(EDGE_SECONDS))
    if len(energy) > 4 * edge:
        energy[:edge] *= 0.5
        energy[-edge:] *= 0.5

    meter = pyln.Meter(sr)
    try:
        integrated = float(meter.integrated_loudness(y.astype(np.float64)))
    except ValueError:
        integrated = float("nan")
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop, units="time")
    return {
        "rms_db": [round(v, 2) for v in rms_db_a],
        "onset": [round(v, 3) for v in onset_a],
        "dynamics": [round(v, 2) for v in dyn_a],
        "energy": [round(float(v), 3) for v in energy],
        "integrated_lufs": round(integrated, 2) if np.isfinite(integrated) else None,
        "peak_dbfs": round(float(20 * np.log10(np.max(np.abs(y)) + 1e-9)), 2),
        "tempo": round(float(np.atleast_1d(tempo)[0]), 1),
        "beats": [round(float(t), 3) for t in beats],
    }


def analyze_wav(path: Path) -> dict:
    import soundfile as sf

    y, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != ANALYSIS_SAMPLE_RATE:
        import librosa

        y = librosa.resample(y, orig_sr=sr, target_sr=ANALYSIS_SAMPLE_RATE)
    le = loudness_energy(y)
    scores = panns_scores(y)
    n = len(le["energy"])
    scores = {k: v[:n] for k, v in scores.items()}
    scores.update({"rms_db": le["rms_db"], "onset": le["onset"], "dynamics": le["dynamics"],
                   "energy": le["energy"]})
    return {"seconds": n, "scores": scores, "tempo": le["tempo"], "beats": le["beats"],
            "integrated_lufs": le["integrated_lufs"], "peak_dbfs": le["peak_dbfs"]}


def analyze(file_hash: str, *, force: bool = False) -> dict:
    if not force:
        cached = db.cache_get(file_hash, STAGE, VERSION)
        if cached is not None:
            return cached
    media = db.get_media(file_hash)
    if media is None:
        raise ValueError(f"{file_hash[:12]} is not ingested")
    if not media["audio_path"]:
        n = int(np.ceil(media["meta"]["duration"]))
        zeros = [0.0] * n
        result = {"seconds": n, "scores": {**{c: zeros for c in CATEGORIES}, "rms_db": [-90.0] * n,
                                           "onset": zeros, "dynamics": zeros,
                                           "energy": zeros},
                  "tempo": 0.0, "beats": [], "integrated_lufs": None, "peak_dbfs": None}
    else:
        result = analyze_wav(Path(media["audio_path"]))
    db.cache_put(file_hash, STAGE, VERSION, result)
    return result
