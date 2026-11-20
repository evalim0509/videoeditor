"""Stage: visual analysis on the 480p proxy.

- CLIP (open_clip ViT-B-32, LAION-2B) image embeddings at 1 fps: frame i is sampled at t = i + 0.5,
  so embedding i describes second [i, i+1). Stored as float16 blob (N x 512), L2-normalised.
- MediaPipe face detection at 4 fps: normalised boxes (x, y, w, h in 0..1 of the upright frame) for
  face-following crops and "has a person" scoring.
"""

from __future__ import annotations

import subprocess
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import db
from .models import CLIP_MODEL

CLIP_STAGE = "clip"
CLIP_VERSION = f"1:{CLIP_MODEL[0]}:{CLIP_MODEL[1]}"
FACE_STAGE = "faces"
FACE_VERSION = "1"
FACE_FPS = 4.0
_lock = threading.Lock()


def iter_frames(video: Path, fps: float, width: int, height: int, offset: float = 0.0):
    """Yield (t, HxWx3 uint8 RGB) frames sampled at `fps`, starting at `offset` seconds."""
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error"]
    if offset:
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += ["-i", str(video), "-vf", f"fps={fps},scale={width}:{height}:flags=bilinear",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = width * height * 3
    i = 0
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield offset + i / fps, np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()


def _proxy_dims(media: dict) -> tuple[int, int]:
    m = media["meta"]
    dw, dh = m["display_width"], m["display_height"]
    if dw >= dh:
        return int(round(dw * 480 / dh / 2) * 2), 480
    return 480, int(round(dh * 480 / dw / 2) * 2)


# ------------------------------------------------------------------ CLIP

@lru_cache(maxsize=1)
def _clip():
    import torch

    from .hardware import detect
    from .models import clip_model

    model, preprocess, tokenizer = clip_model()
    device = detect().torch_device
    model.to(device)
    torch.set_grad_enabled(False)
    return model, preprocess, tokenizer, device


def clip_embed_images(frames: list[np.ndarray]) -> np.ndarray:
    import torch
    from PIL import Image

    model, preprocess, _, device = _clip()
    batch = torch.stack([preprocess(Image.fromarray(f)) for f in frames]).to(device)
    with _lock, torch.no_grad():
        e = model.encode_image(batch).float()
    e = e / e.norm(dim=-1, keepdim=True)
    return e.cpu().numpy()


def clip_embed_text(texts: list[str]) -> np.ndarray:
    import torch

    model, _, tokenizer, device = _clip()
    with _lock, torch.no_grad():
        e = model.encode_text(tokenizer(texts).to(device)).float()
    e = e / e.norm(dim=-1, keepdim=True)
    return e.cpu().numpy()


def clip_embeddings(file_hash: str, *, force: bool = False) -> np.ndarray:
    if not force:
        blob = db.blob_get(file_hash, CLIP_STAGE, CLIP_VERSION)
        if blob is not None:
            return np.frombuffer(blob, np.float16).reshape(-1, 512).astype(np.float32)
    media = db.get_media(file_hash)
    if media is None:
        raise ValueError(f"{file_hash[:12]} is not ingested")
    w, h = _proxy_dims(media)
    # keep the full frame (no centre crop) by letting the CLIP transform resize the short side;
    # 1 fps sampled at the middle of each second
    embs, buf = [], []
    for _, frame in iter_frames(Path(media["proxy_path"]), 1.0, w, h, offset=0.5):
        buf.append(frame)
        if len(buf) == 32:
            embs.append(clip_embed_images(buf))
            buf = []
    if buf:
        embs.append(clip_embed_images(buf))
    arr = np.concatenate(embs) if embs else np.zeros((0, 512), np.float32)
    db.blob_put(file_hash, CLIP_STAGE, CLIP_VERSION, arr.astype(np.float16).tobytes())
    return arr


def search(query: str, hashes: list[str] | None = None, top: int = 10,
           min_gap: float = 3.0) -> list[dict]:
    """Rank seconds of footage by CLIP similarity to a text query."""
    from .moments import top_moments

    hashes = hashes or [m["hash"] for m in db.all_media()]
    q = clip_embed_text([query, f"a photo of {query}"]).mean(axis=0)
    q /= np.linalg.norm(q)
    results = []
    for h in hashes:
        e = clip_embeddings(h)
        if not len(e):
            continue
        sims = e @ q
        for mo in top_moments(list(sims), k=top, min_gap=min_gap, smooth_width=1):
            results.append({"hash": h, "t": mo["t"], "score": mo["score"]})
    results.sort(key=lambda r: -r["score"])
    return results[:top]


def similarity_series(file_hash: str, query: str) -> list[float]:
    q = clip_embed_text([query, f"a photo of {query}"]).mean(axis=0)
    q /= np.linalg.norm(q)
    return [round(float(x), 4) for x in clip_embeddings(file_hash) @ q]


# ------------------------------------------------------------------ faces

@lru_cache(maxsize=1)
def _face_detector():
    from mediapipe.tasks.python import BaseOptions, vision

    from .models import face_detector_model

    opts = vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(face_detector_model())),
        min_detection_confidence=0.5)
    return vision.FaceDetector.create_from_options(opts)


def detect_faces(file_hash: str, *, force: bool = False) -> dict:
    if not force:
        cached = db.cache_get(file_hash, FACE_STAGE, FACE_VERSION)
        if cached is not None:
            return cached
    import mediapipe as mp

    media = db.get_media(file_hash)
    if media is None:
        raise ValueError(f"{file_hash[:12]} is not ingested")
    w, h = _proxy_dims(media)
    det = _face_detector()
    samples = []
    for t, frame in iter_frames(Path(media["proxy_path"]), FACE_FPS, w, h, offset=0.5 / FACE_FPS):
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame))
        with _lock:
            res = det.detect(img)
        faces = []
        for d in res.detections:
            bb = d.bounding_box
            faces.append({"x": round(bb.origin_x / w, 4), "y": round(bb.origin_y / h, 4),
                          "w": round(bb.width / w, 4), "h": round(bb.height / h, 4),
                          "score": round(d.categories[0].score if d.categories else 0.0, 3)})
        faces.sort(key=lambda f: -f["w"] * f["h"])
        samples.append({"t": round(t, 3), "faces": faces})
    n_sec = int(np.ceil(media["meta"]["duration"]))
    per_sec_count = [0] * n_sec
    per_sec_size = [0.0] * n_sec
    for s in samples:
        i = min(n_sec - 1, int(s["t"]))
        per_sec_count[i] = max(per_sec_count[i], len(s["faces"]))
        if s["faces"]:
            per_sec_size[i] = max(per_sec_size[i], s["faces"][0]["w"] * s["faces"][0]["h"])
    result = {"fps": FACE_FPS, "frame_w": w, "frame_h": h, "samples": samples,
              "per_second": {"count": per_sec_count, "largest_area": [round(x, 4) for x in per_sec_size]}}
    db.cache_put(file_hash, FACE_STAGE, FACE_VERSION, result)
    return result


def analyze(file_hash: str, *, force: bool = False) -> dict:
    e = clip_embeddings(file_hash, force=force)
    f = detect_faces(file_hash, force=force)
    return {"clip_frames": len(e), "face_samples": len(f["samples"]),
            "seconds_with_face": sum(1 for c in f["per_second"]["count"] if c)}
