"""Thin wrappers around the ffmpeg / ffprobe executables (all media work goes through FFmpeg)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Callable, Sequence


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise FFmpegError(f"{tool} not found on PATH. Install FFmpeg first.")


def ffprobe_json(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}: {p.stderr.strip()[-500:]}")
    return json.loads(p.stdout)


_TIME_RE = re.compile(r"out_time_us=(\d+)")


def run_ffmpeg(args: Sequence[str], *, duration: float | None = None,
               progress: Callable[[float], None] | None = None) -> None:
    """Run ffmpeg; raise FFmpegError with the tail of stderr on failure.

    If ``progress`` and ``duration`` are given, progress(fraction 0..1) is called as encoding runs.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if progress and duration:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)
    if progress and duration:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            m = _TIME_RE.match(line.strip())
            if m:
                progress(min(1.0, int(m.group(1)) / 1e6 / duration))
        stderr = proc.stderr.read() if proc.stderr else ""
        rc = proc.wait()
    else:
        p = subprocess.run(cmd, capture_output=True, text=True)
        rc, stderr = p.returncode, p.stderr
    if rc != 0:
        raise FFmpegError("ffmpeg failed:\n  " + " ".join(cmd) + "\n" + stderr.strip()[-2000:])


@lru_cache(maxsize=1)
def available_filters() -> set[str]:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True,
                         text=True).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) == 3:
            names.add(parts[1])
    return names


def load_audio(path: Path, sample_rate: int = 16000, af: str | None = None):
    """Decode any audio/video file to mono float32 numpy at ``sample_rate`` using FFmpeg."""
    import numpy as np

    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-vn", *(["-af", af] if af else []),
           "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise FFmpegError(f"could not decode audio from {path}: {p.stderr.decode()[-500:]}")
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0
