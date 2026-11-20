"""Find top moments in per-second score series (peak picking with non-maximum suppression)."""

from __future__ import annotations

import numpy as np


def smooth(x: list[float] | np.ndarray, width: int = 3) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if width <= 1 or len(a) < width:
        return a
    k = np.ones(width) / width
    return np.convolve(np.pad(a, width // 2, mode="edge"), k, mode="valid")[: len(a)]


def top_moments(series: list[float], k: int = 10, min_gap: float = 4.0, min_score: float = 0.0,
                smooth_width: int = 3) -> list[dict]:
    """Return up to k peaks [{"t": second, "score": smoothed, "peak": raw max nearby}]."""
    raw = np.asarray(series, dtype=float)
    s = smooth(raw, smooth_width)
    taken = np.zeros(len(s), bool)
    out = []
    gap = int(np.ceil(min_gap))
    order = np.argsort(-s, kind="stable")
    for i in order:
        if len(out) >= k or s[i] < min_score:
            break
        if taken[i]:
            continue
        lo, hi = max(0, i - gap), min(len(s), i + gap + 1)
        taken[lo:hi] = True
        win = raw[max(0, i - 1): i + 2]
        out.append({"t": int(i), "score": round(float(s[i]), 4), "peak": round(float(win.max()), 4)})
    return out


def words_near(transcript: dict | None, t0: float, t1: float) -> str:
    if not transcript:
        return ""
    ws = [w["word"] for w in transcript.get("words", []) if w["end"] > t0 and w["start"] < t1]
    return "".join(ws).strip()


def confidence_label(cat: str, peak: float) -> str:
    """Plain-language strength for an event score (energy is relative, so it has its own scale)."""
    if cat == "energy":
        return "high" if peak >= 0.9 else ("medium" if peak >= 0.7 else "low")
    if peak >= 0.3:
        return "clear"
    if peak >= 0.1:
        return "possible"
    return "faint"


def make_preview(video: "Path", items: list[tuple[str, float]], out: "Path",
                 before: float = 1.0, after: float = 3.0) -> "Path":
    """Concatenate short clips around each (label, t) with the label burned in (uses the proxy)."""
    from pathlib import Path

    from .ffmpeg import run_ffmpeg

    args: list[str] = []
    parts = []
    for i, (label, t) in enumerate(items):
        start = max(0.0, t - before)
        args += ["-ss", f"{start:.3f}", "-t", f"{before + after:.3f}", "-i", str(video)]
        text = label.replace(":", r"\:").replace("'", "")
        parts.append(
            f"[{i}:v]scale=-2:480,setsar=1,fps=30,format=yuv420p,"
            f"drawtext=text='{text}':x=12:y=12:fontsize=26:fontcolor=white:"
            f"box=1:boxcolor=black@0.65:boxborderw=8[v{i}];"
            f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}];")
    n = len(items)
    graph = "".join(parts) + "".join(f"[v{i}][a{i}]" for i in range(n)) + \
        f"concat=n={n}:v=1:a=1[v][a]"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                       "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)])
    return Path(out)
