"""Stage: ingest.

For every video file: hash it (SHA-256), probe it, build a 480p constant-frame-rate SDR proxy
(H.264, keyframe every second so the web preview seeks well) and a 32 kHz mono WAV for audio
analysis, then record everything in SQLite. Files already ingested (same content hash) are skipped;
unchanged paths are not even re-hashed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import db
from .config import ANALYSIS_SAMPLE_RATE, PROXY_SHORT_SIDE, VIDEO_EXTS, get_settings
from .ffmpeg import require_ffmpeg, run_ffmpeg
from .media import AUDIO_SYNC_FILTER, normalize_video_filters, probe

VERSION = "1"
PROXY_FPS_CAP = 30.0  # analysis never needs more than 30 fps


@dataclass
class IngestResult:
    path: Path
    hash: str
    skipped: bool
    meta: dict
    proxy_path: Path | None
    audio_path: Path | None


def file_hash(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def hash_cached(path: Path) -> str:
    """SHA-256 of the file, reusing the stored hash if size+mtime are unchanged."""
    path = path.resolve()
    h = db.lookup_path(path)
    if h is None:
        h = file_hash(path)
        db.remember_path(path, h)
    return h


def iter_media(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file()
                          and q.suffix.lower() in VIDEO_EXTS and not q.name.startswith("."))
        elif p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            out.append(p)
    return out


def make_proxy(src: Path, meta: dict, dst: Path,
               progress: Callable[[float], None] | None = None) -> None:
    fps = min(meta.get("fps") or 30.0, PROXY_FPS_CAP)
    vf = ",".join(normalize_video_filters(meta, fps=fps, short_side=PROXY_SHORT_SIDE))
    args = ["-i", str(src), "-map", "0:v:0", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-g", str(int(round(fps))), "-keyint_min", str(int(round(fps))),
            "-fps_mode", "cfr",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    if meta.get("has_audio"):
        args += ["-map", "0:a:0", "-af", AUDIO_SYNC_FILTER, "-c:a", "aac", "-b:a", "96k", "-ac", "2"]
    args += ["-map_metadata", "-1", "-movflags", "+faststart", str(dst)]
    tmp = dst.with_suffix(".tmp.mp4")
    args[-1] = str(tmp)
    run_ffmpeg(args, duration=meta.get("duration"), progress=progress)
    tmp.replace(dst)


def extract_audio(src: Path, dst: Path) -> None:
    tmp = dst.with_suffix(".tmp.wav")
    run_ffmpeg(["-i", str(src), "-map", "0:a:0", "-vn", "-af", AUDIO_SYNC_FILTER,
                "-ac", "1", "-ar", str(ANALYSIS_SAMPLE_RATE), "-c:a", "pcm_s16le", str(tmp)])
    tmp.replace(dst)


def ingest_file(path: Path, *, force: bool = False,
                progress: Callable[[str, float], None] | None = None) -> IngestResult:
    require_ffmpeg()
    path = Path(path).resolve()
    if progress:
        progress("hashing", 0.0)
    h = hash_cached(path)

    existing = db.get_media(h)
    if existing and not force:
        pp = Path(existing["proxy_path"]) if existing["proxy_path"] else None
        ap = Path(existing["audio_path"]) if existing["audio_path"] else None
        if (pp is None or pp.exists()) and (ap is None or ap.exists()):
            if existing["path"] != str(path):
                db.put_media(h, path, existing["size"], existing["meta"], pp, ap)
            return IngestResult(path, h, True, existing["meta"], pp, ap)

    meta = probe(path)
    if not meta.get("has_video"):
        raise ValueError(f"{path.name}: no video stream")
    meta["ingest_version"] = VERSION
    meta["source_name"] = path.name

    out_dir = get_settings().proxy_dir_for(h)
    out_dir.mkdir(parents=True, exist_ok=True)
    proxy = out_dir / "proxy.mp4"
    make_proxy(path, meta, proxy,
               progress=(lambda f: progress("proxy", f)) if progress else None)
    audio = None
    if meta.get("has_audio"):
        if progress:
            progress("audio", 0.0)
        audio = out_dir / "audio.wav"
        extract_audio(path, audio)

    db.put_media(h, path, path.stat().st_size, meta, proxy, audio)
    if progress:
        progress("done", 1.0)
    return IngestResult(path, h, False, meta, proxy, audio)


def ingest_paths(paths: Iterable[Path], *, force: bool = False,
                 on_file: Callable[[Path, int, int], None] | None = None,
                 progress: Callable[[str, float], None] | None = None) -> list[IngestResult]:
    files = iter_media(paths)
    results = []
    for i, f in enumerate(files):
        if on_file:
            on_file(f, i, len(files))
        results.append(ingest_file(f, force=force, progress=progress))
    return results
