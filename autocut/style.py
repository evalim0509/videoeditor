"""Stage: style analysis of reference videos → StyleProfile preset (JSON in data/presets/).

Per reference (cached by file hash under stage "style_ref"):
- content crop (black bars from screen recordings / letterboxing removed via FFmpeg cropdetect)
- shot lengths (PySceneDetect), cuts per minute
- aspect ratio of the content
- beat sync: how often cuts land on a musical beat compared with chance
- captions: text-like regions per frame (OpenCV morphology), when/where they appear
- audio: integrated loudness, share of music / speech
- look: brightness, contrast, saturation, warmth + Lab mean/std (for colour matching)
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from . import db
from .config import get_settings

VERSION = "2"
STAGE = "style_ref"
ASPECTS = {"9:16": 9 / 16, "4:5": 4 / 5, "1:1": 1.0, "4:3": 4 / 3, "16:9": 16 / 9, "21:9": 21 / 9}
BEAT_TOLERANCE = 0.08  # a cut within 80 ms of a beat counts as "on the beat"


class StyleProfile(BaseModel):
    name: str
    references: list[str] = Field(default_factory=list)
    total_seconds: float = 0.0
    # pacing
    shot_count: int = 0
    cuts_per_minute: float = 0.0
    shot_median: float = 0.0
    shot_mean: float = 0.0
    shot_p10: float = 0.0
    shot_p90: float = 0.0
    shot_lengths: list[float] = Field(default_factory=list)
    shot_histogram: dict[str, int] = Field(default_factory=dict)
    # framing
    aspect_ratio: str = "16:9"
    orientation: str = "landscape"
    # music / beats
    music_share: float = 0.0
    speech_share: float = 0.0
    tempo: float = 0.0
    beat_synced: bool = False
    beat_sync_ratio: float = 0.0     # fraction of cuts on a beat
    beat_sync_chance: float = 0.0    # fraction expected by chance
    # captions
    captions_present: bool = False
    caption_share: float = 0.0       # fraction of sampled frames with text
    caption_position: str = "none"   # bottom | middle | top | none
    caption_height: float = 0.0      # typical text line height as fraction of frame height
    # audio level
    loudness_lufs: float | None = None
    # look
    brightness: float = 0.0          # mean luma 0..1
    contrast: float = 0.0            # luma std 0..1
    saturation: float = 0.0          # mean HSV saturation 0..1
    warmth: float = 0.0              # Lab b* mean (positive = warm/yellow, negative = cool/blue)
    lab_mean: list[float] = Field(default_factory=list)
    lab_std: list[float] = Field(default_factory=list)


# ---------------------------------------------------------------- per-reference measurements

def detect_crop(video: Path, duration: float, W: int, H: int) -> tuple[int, int, int, int] | None:
    """Find the real picture inside a screen recording / letterboxed video → (w, h, x, y).

    Works for black bars *and* coloured/blurred bars (e.g. YouTube "ambient mode" glow) by looking
    for detail instead of darkness: the picture has sharp edges, bars are flat or blurry. The
    per-column/row detail profile is a median over ~30 frames so one dark scene can't fool it."""
    import cv2

    from .visual import iter_frames

    fps = min(2.0, 30.0 / max(1.0, duration))
    cols, rows = [], []
    for _, fr in iter_frames(video, fps, W, H, offset=0.5):
        g = cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY).astype(np.float32)
        mag = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
        cols.append(mag.mean(axis=0))
        rows.append(mag.mean(axis=1))
    if not cols:
        return None

    def span(profile: np.ndarray) -> tuple[int, int]:
        n = len(profile)
        core = profile[int(n * 0.3): int(n * 0.7)]
        thr = 0.35 * float(np.median(core))
        above = np.where(profile > thr)[0]
        if not len(above):
            return 0, n
        a, b = int(above[0]), int(above[-1]) + 1
        # ignore the 1-2 px boundary edge between bar and picture
        return (a if a > 2 else 0), (b if b < n - 2 else n)

    def symmetric(a: int, b: int, n: int) -> tuple[int, int]:
        # bars come in pairs (pillarbox / letterbox); a one-sided low-detail strip is just
        # scenery (a table, the sky), not a bar
        lo, hi = a, n - b
        if lo >= 0.02 * n and hi >= 0.02 * n and min(lo, hi) / max(lo, hi) >= 0.5:
            return a, b
        return 0, n

    x0, x1 = symmetric(*span(np.median(np.array(cols), axis=0)), W)
    y0, y1 = symmetric(*span(np.median(np.array(rows), axis=0)), H)
    w, h = x1 - x0, y1 - y0
    if w * h > 0.97 * W * H or w < 0.3 * W or h < 0.3 * H:
        return None
    # snap to a standard aspect ratio if within 3 %
    r = w / h
    std = min(ASPECTS.values(), key=lambda a: abs(np.log(a / r)))
    if abs(np.log(std / r)) < 0.03:
        cx, cy = x0 + w / 2, y0 + h / 2
        if std > r:
            w = min(W, int(round(h * std)))
        else:
            h = min(H, int(round(w / std)))
        x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
        x0, y0 = max(0, min(W - w, x0)), max(0, min(H - h, y0))
    return (w - w % 2, h - h % 2, x0, y0)


def nearest_aspect(w: float, h: float) -> str:
    r = w / h
    return min(ASPECTS, key=lambda k: abs(np.log(ASPECTS[k] / r)))


def text_boxes(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Candidate burned-in caption lines.

    Light (top-hat) and dark (black-hat) thin strokes → character-sized blobs → merged into lines.
    A line must contain ≥5 character blobs side by side, be ≥10 % of the frame wide (rejects teeth,
    tiny background signage) and have uniform near-white / near-black strokes. Combine with
    ``persistent()`` (same box in an adjacent sample) to reject one-frame flukes."""
    import cv2

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, w = gray.shape
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    boxes = []
    for op, light in ((cv2.MORPH_TOPHAT, True), (cv2.MORPH_BLACKHAT, False)):
        th = cv2.morphologyEx(gray, op, k)
        _, bw = cv2.threshold(th, 60, 255, cv2.THRESH_BINARY)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        keep = np.zeros(n, bool)
        for i in range(1, n):
            x, y, ww, hh, area = stats[i]
            keep[i] = 3 <= hh <= 0.08 * h and ww <= 0.06 * w and area >= 4
        mask = (keep[lab] * 255).astype(np.uint8)
        line = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)))
        cnts, _ = cv2.findContours(line, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            x, y, ww, hh = cv2.boundingRect(c)
            if not (0.015 * h <= hh <= 0.09 * h and ww >= 4 * hh and ww >= 0.10 * w):
                continue
            sub = mask[y:y+hh, x:x+ww] > 0
            nc = cv2.connectedComponents(sub.astype(np.uint8), 8)[0] - 1
            if nc < 5 or nc / (ww / hh) < 0.6:
                continue
            strokes_l = gray[y:y+hh, x:x+ww][sub]
            strokes_s = hsv[y:y+hh, x:x+ww, 1][sub]
            if light and not (strokes_l.mean() > 175 and strokes_s.mean() < 70 and strokes_l.std() < 40):
                continue
            if not light and not (strokes_l.mean() < 60 and strokes_l.std() < 30):
                continue
            boxes.append((x, y, ww, hh))
    return boxes

def iou(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    ix = max(0, min(ax+aw, bx+bw) - max(ax, bx)); iy = max(0, min(ay+ah, by+bh) - max(ay, by))
    inter = ix*iy; return inter / (aw*ah + bw*bh - inter + 1e-9)

def persistent(frames_boxes, thr=0.4):
    """Keep boxes that also appear (IoU ≥ thr) in the previous or next sample."""
    out = []
    for i, bs in enumerate(frames_boxes):
        nb = (frames_boxes[i-1] if i else []) + (frames_boxes[i+1] if i+1 < len(frames_boxes) else [])
        out.append([b for b in bs if any(iou(b, c) >= thr for c in nb)])
    return out


def measure_reference(file_hash: str, *, force: bool = False) -> dict:
    if not force:
        cached = db.cache_get(file_hash, STAGE, VERSION)
        if cached is not None:
            return cached
    import cv2

    from .audio import analyze as audio_analyze
    from .shots import shots as shot_detect
    from .visual import _proxy_dims, iter_frames

    media = db.get_media(file_hash)
    if media is None:
        raise ValueError(f"{file_hash[:12]} is not ingested")
    proxy = Path(media["proxy_path"])
    dur = media["meta"]["duration"]
    W, H = _proxy_dims(media)
    crop = detect_crop(proxy, dur, W, H)
    cw, ch, cx, cy = crop if crop else (W, H, 0, 0)

    sh = shot_detect(file_hash)
    lengths = [round(s["end"] - s["start"], 3) for s in sh["shots"]]
    au = audio_analyze(file_hash)
    scores = au["scores"]
    beats = np.array(au["beats"])
    cuts = np.array(sh["cuts"])
    on_beat = 0.0
    chance = 0.0
    if len(cuts) and len(beats) > 4:
        d = np.abs(cuts[:, None] - beats[None, :]).min(axis=1)
        on_beat = float((d <= BEAT_TOLERANCE).mean())
        chance = float(min(1.0, 2 * BEAT_TOLERANCE / np.median(np.diff(beats))))

    # frames: look + captions, sampled 2 fps inside the content crop
    lum, con, sat, labs, text_y, text_h = [], [], [], [], [], []
    n = 0
    frame_boxes, frame_speech = [], []
    for t, fr in iter_frames(proxy, 2.0, W, H, offset=0.25):
        fr = fr[cy:cy + ch, cx:cx + cw]
        if fr.size == 0:
            continue
        n += 1
        bgr = cv2.cvtColor(fr, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
        lum.append(gray.mean() / 255)
        con.append(gray.std() / 255)
        sat.append(hsv[..., 1].mean() / 255)
        labs.append(np.concatenate([lab.mean(0), lab.std(0)]))
        frame_boxes.append(text_boxes(np.ascontiguousarray(fr)))
        sec = min(len(scores["speech"]) - 1, int(t))
        frame_speech.append(scores["speech"][sec] > 0.5)
    kept = persistent(frame_boxes)
    text_frames = sum(1 for b in kept if b)
    speech_frames = sum(frame_speech)
    speech_text = sum(1 for b, sp in zip(kept, frame_speech) if b and sp)
    for boxes in kept:
        for (_, y, _, bh) in boxes:
            text_y.append((y + bh / 2) / ch)
            text_h.append(bh / ch)
    labs_a = np.array(labs) if labs else np.zeros((1, 6))
    # OpenCV 8-bit Lab: L in 0..255 (→ 0..100), a/b offset by 128
    lab_mean = [float(labs_a[:, 0].mean() * 100 / 255), float(labs_a[:, 1].mean() - 128),
                float(labs_a[:, 2].mean() - 128)]
    lab_std = [float(labs_a[:, 3].mean() * 100 / 255), float(labs_a[:, 4].mean()),
               float(labs_a[:, 5].mean())]
    result = {
        "name": Path(media["path"]).name,
        "duration": dur,
        "frame": [W, H], "crop": [cw, ch, cx, cy] if crop else None,
        "aspect_ratio": nearest_aspect(cw, ch),
        "shot_lengths": lengths, "cuts": sh["cuts"],
        "beat_on": round(on_beat, 3), "beat_chance": round(chance, 3),
        "tempo": au["tempo"],
        "music_share": round(float(np.mean(np.array(scores["music"]) > 0.3)), 3),
        "speech_share": round(float(np.mean(np.array(scores["speech"]) > 0.5)), 3),
        "loudness_lufs": au["integrated_lufs"],
        "frames": n, "text_frames": text_frames,
        "text_while_speech": round(speech_text / max(1, speech_frames), 3),
        "text_y": [round(v, 3) for v in text_y], "text_h": [round(v, 4) for v in text_h],
        "brightness": round(float(np.mean(lum)), 3) if lum else 0,
        "contrast": round(float(np.mean(con)), 3) if con else 0,
        "saturation": round(float(np.mean(sat)), 3) if sat else 0,
        "lab_mean": [round(v, 2) for v in lab_mean], "lab_std": [round(v, 2) for v in lab_std],
    }
    db.cache_put(file_hash, STAGE, VERSION, result)
    return result


# ---------------------------------------------------------------- aggregate → profile

def build_profile(name: str, refs: list[dict]) -> StyleProfile:
    lengths = np.array([x for r in refs for x in r["shot_lengths"]]) if refs else np.array([1.0])
    total = float(sum(r["duration"] for r in refs))
    n_frames = sum(r["frames"] for r in refs) or 1
    text_frames = sum(r["text_frames"] for r in refs)
    text_y = [y for r in refs for y in r["text_y"]]
    text_h = [h for r in refs for h in r["text_h"]]
    w = np.array([r["duration"] for r in refs]) / max(1e-9, total)

    def wavg(key):
        vals = [r[key] for r in refs if r.get(key) is not None]
        ws = [wi for wi, r in zip(w, refs) if r.get(key) is not None]
        return float(np.average(vals, weights=ws)) if vals else None

    caption_share = text_frames / n_frames
    caption_pos = "none"
    band_share = 0.0
    if text_y:
        med = float(np.median(text_y))
        caption_pos = "top" if med < 0.33 else ("middle" if med < 0.62 else "bottom")
        band_share = float(np.mean(np.abs(np.array(text_y) - med) <= 0.08))
    beat_on, beat_chance = wavg("beat_on") or 0.0, wavg("beat_chance") or 0.0
    music_share = wavg("music_share") or 0.0
    aspects = Counter(r["aspect_ratio"] for r in refs).most_common(1)
    aspect = aspects[0][0] if aspects else "16:9"
    bins = [0, 1, 2, 3, 5, 8, 1e9]
    labels = ["<1s", "1-2s", "2-3s", "3-5s", "5-8s", "8s+"]
    hist = np.histogram(lengths, bins=bins)[0]
    lab_mean = np.average([r["lab_mean"] for r in refs], axis=0, weights=w).tolist()
    lab_std = np.average([r["lab_std"] for r in refs], axis=0, weights=w).tolist()
    return StyleProfile(
        name=name, references=[r["name"] for r in refs], total_seconds=round(total, 2),
        shot_count=int(len(lengths)),
        cuts_per_minute=round(sum(len(r["cuts"]) for r in refs) / max(1e-9, total) * 60, 1),
        shot_median=round(float(np.median(lengths)), 2), shot_mean=round(float(lengths.mean()), 2),
        shot_p10=round(float(np.percentile(lengths, 10)), 2),
        shot_p90=round(float(np.percentile(lengths, 90)), 2),
        shot_lengths=[round(float(x), 3) for x in lengths],
        shot_histogram={l: int(c) for l, c in zip(labels, hist)},
        aspect_ratio=aspect,
        orientation="portrait" if ASPECTS[aspect] < 1 else ("square" if aspect == "1:1" else "landscape"),
        music_share=round(music_share, 3), speech_share=round(wavg("speech_share") or 0.0, 3),
        tempo=round(wavg("tempo") or 0.0, 1),
        beat_synced=bool(music_share > 0.3 and beat_on >= max(0.35, 1.6 * beat_chance)),
        beat_sync_ratio=round(beat_on, 3), beat_sync_chance=round(beat_chance, 3),
        captions_present=bool(caption_share >= 0.12 and band_share >= 0.6),
        caption_share=round(caption_share, 3), caption_position=caption_pos,
        caption_height=round(float(np.median(text_h)), 4) if text_h else 0.0,
        loudness_lufs=round(wavg("loudness_lufs"), 1) if wavg("loudness_lufs") is not None else None,
        brightness=round(wavg("brightness"), 3), contrast=round(wavg("contrast"), 3),
        saturation=round(wavg("saturation"), 3), warmth=round(lab_mean[2], 2),
        lab_mean=[round(v, 2) for v in lab_mean], lab_std=[round(v, 2) for v in lab_std],
    )


def explain(p: StyleProfile) -> list[str]:
    """Plain-language description of a style profile."""
    out = []
    pace = ("very fast" if p.shot_median < 1.2 else "fast" if p.shot_median < 2.5
            else "medium" if p.shot_median < 5 else "slow")
    if p.cuts_per_minute < 1:
        out.append("It's essentially one continuous take with (almost) no cuts.")
    else:
        out.append(f"Pacing is {pace}: a cut about every {p.shot_median:.1f} seconds "
                   f"({p.cuts_per_minute:.0f} cuts per minute). Most shots last between "
                   f"{p.shot_p10:.1f} and {p.shot_p90:.1f} seconds.")
    out.append(f"The picture is {p.aspect_ratio} ({p.orientation}).")
    if p.music_share > 0.3:
        tempo = f" at about {p.tempo:.0f} BPM" if p.tempo else ""
        if p.beat_synced:
            out.append(f"There's music under most of it{tempo}, and the cuts land on the beat "
                       f"({p.beat_sync_ratio:.0%} of cuts vs {p.beat_sync_chance:.0%} by chance).")
        else:
            out.append(f"There's music under most of it{tempo}, but the cuts don't follow the beat "
                       f"({p.beat_sync_ratio:.0%} on the beat, about what chance gives).")
    else:
        out.append("There's little or no music; it's mostly voice and natural sound.")
    if p.speech_share > 0.5:
        out.append("People are talking for most of the video.")
    if p.captions_present:
        # box height includes ascenders/descenders + padding (~1.4x the font's x-height)
        size = ("small" if p.caption_height < 0.045 else "medium" if p.caption_height < 0.07
                else "large")
        out.append(f"On-screen text appears often ({p.caption_share:.0%} of the time), "
                   f"{size}, usually at the {p.caption_position} of the frame.")
    else:
        out.append("There are few or no captions / on-screen text.")
    if p.loudness_lufs is not None:
        lvl = ("loud (social-media level)" if p.loudness_lufs > -16 else
               "normal" if p.loudness_lufs > -24 else "quiet")
        out.append(f"Audio is {lvl} at {p.loudness_lufs:.0f} LUFS.")
    look = []
    look.append("bright" if p.brightness > 0.55 else "dark/moody" if p.brightness < 0.35 else "evenly exposed")
    look.append("punchy contrast" if p.contrast > 0.26 else "soft, low contrast" if p.contrast < 0.18 else "moderate contrast")
    look.append("vivid colours" if p.saturation > 0.45 else "muted/pastel colours" if p.saturation < 0.25 else "natural colours")
    look.append("a warm tint" if p.warmth > 8 else "a cool tint" if p.warmth < -2 else "neutral white balance")
    out.append("The look is " + ", ".join(look[:-1]) + f", with {look[-1]}.")
    return out


def preset_path(name: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_") or "style"
    return get_settings().presets_dir / f"{safe}.json"


def save_profile(p: StyleProfile) -> Path:
    path = preset_path(p.name)
    path.write_text(p.model_dump_json(indent=2))
    return path


def load_profile(name: str) -> StyleProfile:
    path = preset_path(name)
    if not path.exists():
        raise FileNotFoundError(f"No style preset named '{name}'")
    return StyleProfile.model_validate_json(path.read_text())


def list_profiles() -> list[str]:
    return sorted(p.stem for p in get_settings().presets_dir.glob("*.json"))


def analyze_references(paths: list[Path], name: str) -> StyleProfile:
    from .ingest import ingest_paths

    results = ingest_paths(paths)
    refs = [measure_reference(r.hash) for r in results]
    profile = build_profile(name, refs)
    save_profile(profile)
    return profile
