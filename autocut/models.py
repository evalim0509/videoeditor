"""One-time model weight downloads (the only network access autocut ever needs).

After `autocut models` has run once, everything works fully offline: Hugging Face is switched to
offline mode whenever the weights are already present.
"""

from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path

from .config import ASSETS_DIR, get_settings

PANNS_URL = "https://zenodo.org/records/3987831/files/Cnn14_mAP%3D0.431.pth?download=1"
PANNS_FILE = "Cnn14_mAP=0.431.pth"
PANNS_MIN_BYTES = 300_000_000

CLIP_MODEL = ("ViT-B-32", "laion2b_s34b_b79k")  # open_clip (arch, pretrained tag)
CLIP_HF_REPO = "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"
CLIP_WEIGHTS = "open_clip_model.safetensors"
FACE_DETECTOR_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
                     "blaze_face_short_range/float16/1/blaze_face_short_range.tflite")


def _download(url: str, dst: Path, min_bytes: int = 1) -> Path:
    if dst.exists() and dst.stat().st_size >= min_bytes:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1024 * 1024)
    if tmp.stat().st_size < min_bytes:
        raise RuntimeError(f"Download of {url} looks truncated ({tmp.stat().st_size} bytes)")
    tmp.replace(dst)
    return dst


def panns_checkpoint() -> Path:
    return _download(PANNS_URL, get_settings().models_dir / "panns" / PANNS_FILE, PANNS_MIN_BYTES)


def ensure_panns_labels() -> None:
    """panns_inference reads labels from ~/panns_data and would otherwise shell out to wget."""
    dst = Path.home() / "panns_data" / "class_labels_indices.csv"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ASSETS_DIR / "audioset_class_labels.csv", dst)


def face_detector_model() -> Path:
    return _download(FACE_DETECTOR_URL, get_settings().models_dir / "mediapipe" /
                     "blaze_face_short_range.tflite", 100_000)


def _hf_offline_if_cached(repo_marker: Path) -> None:
    if repo_marker.exists():
        os.environ["HF_HUB_OFFLINE"] = "1"


def whisper_model_path(size: str) -> str:
    """Return a local path for a faster-whisper model, downloading it once if needed."""
    from huggingface_hub import snapshot_download

    get_settings()
    repo = f"Systran/faster-whisper-{size}"
    if size in ("large-v3-turbo", "turbo"):
        repo = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    try:
        return snapshot_download(repo, local_files_only=True)
    except Exception:
        return snapshot_download(repo)


def offline_first(fn):
    """Run fn() with the Hugging Face hub forced offline (use the local copy); only if the weights
    aren't downloaded yet, retry online once. Keeps every normal run fully offline."""
    import huggingface_hub.constants as hfc

    prev = hfc.HF_HUB_OFFLINE
    hfc.HF_HUB_OFFLINE = True
    try:
        return fn()
    except Exception:
        hfc.HF_HUB_OFFLINE = prev
        return fn()
    finally:
        hfc.HF_HUB_OFFLINE = prev


def clip_model():
    """Load (model, preprocess, tokenizer) for open_clip, downloading weights once."""
    import open_clip

    from huggingface_hub import hf_hub_download

    get_settings()
    arch, _tag = CLIP_MODEL
    try:  # local copy first: no network at all
        weights = hf_hub_download(CLIP_HF_REPO, CLIP_WEIGHTS, local_files_only=True)
    except Exception:
        weights = hf_hub_download(CLIP_HF_REPO, CLIP_WEIGHTS)
    model, _, preprocess = open_clip.create_model_and_transforms(arch, pretrained=weights)
    tokenizer = open_clip.get_tokenizer(arch)  # bundled BPE vocab, no download
    model.eval()
    return model, preprocess, tokenizer


def download_all(whisper_sizes: list[str]) -> list[str]:
    done = []
    for s in whisper_sizes:
        done.append(f"whisper-{s}: {whisper_model_path(s)}")
    done.append(f"panns: {panns_checkpoint()}")
    ensure_panns_labels()
    done.append(f"face detector: {face_detector_model()}")
    clip_model()
    done.append(f"clip: {CLIP_MODEL[0]} / {CLIP_MODEL[1]}")
    return done
