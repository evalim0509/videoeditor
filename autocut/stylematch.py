"""Stage: style matching — turn a selection into a styled timeline and render it.

- Pacing toward the preset: long clips get "punch-in" cuts (alternating 1.0x / 1.15x framing) at
  word gaps, so the cut rate approaches the reference's without dropping any speech.
- Beat-synced cuts: when the preset cuts on the beat, clip boundaries move onto nearby beats if a
  beat falls in the word-free zone around the boundary.
- Smooth face-following crop to the target aspect ratio (dead zone + easing, no jitter).
- Word-timed burned-in captions (ASS karaoke: each word lights up as it's spoken), sized and placed
  like the reference.
- Basic colour matching: a 3D LUT that moves the footage's Lab mean/std part-way toward the
  reference's.
"""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import db
from .config import ASSETS_DIR, get_settings
from .editspec import EditSpec
from .render import TimelineClip, render_timeline
from .style import ASPECTS, StyleProfile

PUNCH_ZOOM = 1.15
FACE_DEADZONE = 0.22      # fraction of the crop width the face may drift before the camera follows
FACE_TAU = 0.6            # seconds: easing time constant of the virtual camera
COLOR_STRENGTH = 0.6      # how far toward the reference look (0..1)
MIN_SHOW = 0.5            # seconds a caption line stays up at least (when there's room)
OUTPUT_SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}


@dataclass
class Segment:
    """A contiguous piece of a selected clip (punch-ins split clips into several)."""
    hash: str
    start: float
    end: float
    clip_index: int
    zoom: float = 1.0
    continuous_with_next: bool = False
    filters: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ geometry

def output_size(aspect: str, src_w: int, src_h: int) -> tuple[int, int]:
    src_aspect = src_w / src_h
    if abs(np.log(ASPECTS[aspect] / src_aspect)) < 0.03:
        return src_w - src_w % 2, src_h - src_h % 2
    if aspect in OUTPUT_SIZES:
        return OUTPUT_SIZES[aspect]
    return (1920, 1080) if aspect == "16:9" else (2560, 1080)


def crop_box(src_w: int, src_h: int, aspect: float, zoom: float) -> tuple[int, int]:
    if aspect < src_w / src_h:
        ch, cw = src_h, src_h * aspect
    else:
        cw, ch = src_w, src_w / aspect
    cw, ch = cw / zoom, ch / zoom
    return int(cw) - int(cw) % 2, int(ch) - int(ch) % 2


def face_track(file_hash: str, start: float, end: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Face centre (normalised x, y) sampled at the face-detection rate, gaps filled."""
    from .visual import detect_faces

    f = detect_faces(file_hash)
    ts, xs, ys = [], [], []
    for s in f["samples"]:
        if start - 1.0 <= s["t"] <= end + 1.0:
            ts.append(s["t"])
            if s["faces"]:
                fc = s["faces"][0]
                xs.append(fc["x"] + fc["w"] / 2)
                ys.append(fc["y"] + fc["h"] / 2)
            else:
                xs.append(np.nan)
                ys.append(np.nan)
    if not ts:
        return np.array([start, end]), np.array([0.5, 0.5]), np.array([0.45, 0.45])
    t, x, y = np.array(ts), np.array(xs, float), np.array(ys, float)
    for a in (x, y):
        ok = ~np.isnan(a)
        if not ok.any():
            a[:] = 0.5 if a is x else 0.45
        else:
            a[~ok] = np.interp(t[~ok], t[ok], a[ok])
    return t, x, y


def smooth_camera(t: np.ndarray, target: np.ndarray, deadzone: float) -> np.ndarray:
    """Virtual camera: median-filter the face path, follow only when the face leaves a dead zone,
    and ease toward it (exponential smoothing). Returns camera centre per sample."""
    from scipy.ndimage import median_filter

    tgt = median_filter(target, size=5, mode="nearest") if len(target) >= 5 else target
    cam = np.empty_like(tgt)
    cam[0] = tgt[0]
    goal = tgt[0]
    for i in range(1, len(tgt)):
        dt = max(1e-3, t[i] - t[i - 1])
        if abs(tgt[i] - goal) > deadzone:
            goal = tgt[i]
        alpha = 1 - np.exp(-dt / FACE_TAU)
        cam[i] = cam[i - 1] + alpha * (goal - cam[i - 1])
    return cam


def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    (x0, y0), (x1, y1) = points[0], points[-1]
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs((y1 - y0) * px - (x1 - x0) * py + x1 * y0 - y1 * x0) / max(1e-9, np.hypot(x1 - x0, y1 - y0))
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return _rdp(points[: idx + 1], eps)[:-1] + _rdp(points[idx:], eps)
    return [points[0], points[-1]]


def piecewise_expr(keys: list[tuple[float, float]]) -> str:
    """FFmpeg expression in t for a piecewise-linear path through (time, value) keys."""
    keys = [(round(t, 3), round(v, 1)) for t, v in keys]
    if len(keys) == 1 or all(abs(v - keys[0][1]) < 0.5 for _, v in keys):
        return f"{keys[0][1]}"
    expr = f"{keys[-1][1]}"
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        seg = f"{v0}+({v1 - v0:.1f})*(t-{t0})/{max(1e-3, t1 - t0):.3f}"
        expr = f"if(lt(t,{t1}),{seg},{expr})"
    return f"if(lt(t,{keys[0][0]}),{keys[0][1]},{expr})"


def crop_filter(meta: dict, seg: Segment, aspect: float) -> str | None:
    """Time-varying crop that keeps the face framed (t is local to the segment)."""
    W, H = meta["display_width"], meta["display_height"]
    cw, ch = crop_box(W, H, aspect, seg.zoom)
    if cw >= W - 2 and ch >= H - 2:
        return None
    t, fx, fy = face_track(seg.hash, seg.start, seg.end)
    cam_x = smooth_camera(t, fx, FACE_DEADZONE * cw / W)
    cam_y = smooth_camera(t, fy, FACE_DEADZONE * ch / H)
    # face slightly above centre vertically (rule of thirds-ish)
    xs = np.clip(cam_x * W - cw / 2, 0, W - cw)
    ys = np.clip(cam_y * H - ch * 0.42, 0, H - ch)
    sel = (t >= seg.start - 0.3) & (t <= seg.end + 0.3)
    if not sel.any():
        sel[:] = True
    kx = _rdp([(ti - seg.start, xi) for ti, xi in zip(t[sel], xs[sel])], eps=2.0)
    ky = _rdp([(ti - seg.start, yi) for ti, yi in zip(t[sel], ys[sel])], eps=2.0)
    return f"crop={cw}:{ch}:'{piecewise_expr(kx)}':'{piecewise_expr(ky)}'"


# ------------------------------------------------------------------ pacing & beats

def target_segment_length(profile: StyleProfile | None) -> float | None:
    if profile is None or profile.cuts_per_minute < 6:
        return None  # reference is (nearly) one take: no extra cuts
    return float(np.clip(profile.shot_median * 1.8, 1.6, 4.0))


def plan_punch_ins(fa, start: float, end: float, seg_len: float) -> list[float]:
    """Split points inside [start, end] at word gaps, roughly every seg_len seconds."""
    dur = end - start
    if dur < seg_len * 1.6:
        return []
    zones = [(lo, hi) for lo, hi in fa.safe_zones() if hi >= start + 1.0 and lo <= end - 1.0]
    points: list[float] = []
    want = start + seg_len
    while want < end - 1.0:
        best = None
        for lo, hi in zones:
            p = min(max(want, lo), hi)
            if p - (points[-1] if points else start) < 1.2 or end - p < 1.2:
                continue
            if best is None or abs(p - want) < abs(best - want):
                best = p
        if best is None or abs(best - want) > seg_len * 0.6:
            want += seg_len * 0.5
            continue
        points.append(round(best, 3))
        want = best + seg_len
    return points


def snap_to_beats(fa, t: float, window: float = 0.25) -> float:
    beats = [b for b in fa.beats if abs(b - t) <= window]
    for b in sorted(beats, key=lambda b: abs(b - t)):
        if fa.is_safe(b):
            return round(b, 3)
    return t


# ------------------------------------------------------------------ colour

def footage_lab_stats(file_hash: str, ranges: list[tuple[float, float]]) -> tuple[list, list]:
    import cv2

    from .visual import _proxy_dims, iter_frames

    m = db.get_media(file_hash)
    W, H = _proxy_dims(m)
    labs = []
    for t, fr in iter_frames(Path(m["proxy_path"]), 1.0, W, H, offset=0.5):
        if any(a <= t <= b for a, b in ranges):
            lab = cv2.cvtColor(cv2.cvtColor(fr, cv2.COLOR_RGB2BGR), cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
            labs.append(np.concatenate([lab.mean(0), lab.std(0)]))
    if not labs:
        return [50.0, 0.0, 0.0], [20.0, 8.0, 8.0]
    a = np.array(labs)
    return ([a[:, 0].mean() * 100 / 255, a[:, 1].mean() - 128, a[:, 2].mean() - 128],
            [a[:, 3].mean() * 100 / 255, a[:, 4].mean(), a[:, 5].mean()])


def make_color_lut(src_mean, src_std, ref_mean, ref_std, path: Path, size: int = 33,
                   strength: float = COLOR_STRENGTH) -> Path:
    """Reinhard-style Lab statistics transfer baked into a .cube 3D LUT."""
    import cv2

    g = np.linspace(0, 1, size, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")  # red changes fastest in .cube order
    rgb = np.stack([r, gg, b], axis=-1).reshape(-1, 1, 3)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)  # float: L 0..100, a/b ~ -127..127
    sm, ss = np.array(src_mean, np.float32), np.array(src_std, np.float32)
    rm, rs = np.array(ref_mean, np.float32), np.array(ref_std, np.float32)
    ratio = np.clip(rs / np.maximum(ss, 1e-3), 0.75, 1.33)
    shift = np.clip(rm - sm, [-10, -8, -8], [10, 8, 8])
    target = (lab - sm) * ratio + sm + shift
    out_lab = lab + strength * (target - lab)
    out = np.clip(cv2.cvtColor(out_lab.astype(np.float32), cv2.COLOR_LAB2RGB), 0, 1).reshape(-1, 3)
    with open(path, "w") as f:
        f.write(f"TITLE \"autocut colour match\"\nLUT_3D_SIZE {size}\n")
        for row in out:
            f.write(f"{row[0]:.6f} {row[1]:.6f} {row[2]:.6f}\n")
    return path


def _ffpath(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


# ------------------------------------------------------------------ captions

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_captions(segments: list[Segment], fps: float, size: tuple[int, int],
                   profile: StyleProfile | None, path: Path, max_words: int = 6,
                   max_len: float = 3.5, max_gap: float = 0.9, max_chars: int = 30) -> int:
    """Word-timed karaoke captions on the output timeline. Returns the number of caption events."""
    from .transcribe import get_transcript

    W, H = size
    portrait = H > W
    rel = profile.caption_height if (profile and profile.caption_height) else 0.045
    font = int(np.clip(rel * H * 1.05, H * (0.032 if portrait else 0.028), H * 0.06))
    margin_v = int(H * (0.24 if portrait else 0.07))
    if profile and profile.caption_position == "top":
        align, margin_v = 8, int(H * 0.08)
    elif profile and profile.caption_position == "middle":
        align, margin_v = 5, 0
    else:
        align = 2
    outline = max(2, font // 14)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Inter SemiBold,{font},&H00FFFFFF,&H70FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,{outline},1,{align},{int(W * 0.08)},{int(W * 0.08)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    # map words onto the output timeline, grouped per original clip (punch-ins are contiguous)
    offset = 0.0
    by_clip: dict[int, list[tuple[float, float, str]]] = {}
    clip_end_out: dict[int, float] = {}
    for seg in segments:
        nf = max(1, int(round((seg.end - seg.start) * fps)))
        tr = get_transcript(seg.hash) or {"words": []}
        for w in tr["words"]:
            mid = (w["start"] + w["end"]) / 2
            if seg.start <= mid < seg.end:
                a = offset + max(0.0, w["start"] - seg.start)
                b = offset + min(seg.end, w["end"]) - seg.start
                raw = w["word"]
                lst = by_clip.setdefault(seg.clip_index, [])
                if lst and raw and not raw[0].isspace() and lst[-1][1] >= a - 0.3:
                    # continuation piece (e.g. "2" + ".20"): merge into the previous word
                    pa, _, pw = lst[-1]
                    lst[-1] = (pa, b, pw + raw.strip())
                else:
                    lst.append((a, b, raw.strip()))
        offset += nf / fps
        clip_end_out[seg.clip_index] = offset
    for ci, words in by_clip.items():
        # 1. split into chunks at sentence ends / pauses / length limits
        chunks: list[list[tuple[float, float, str]]] = []
        cur: list[tuple[float, float, str]] = []
        for wd in words:
            if cur and (len(cur) >= max_words or wd[1] - cur[0][0] > max_len
                        or wd[0] - cur[-1][1] > max_gap
                        or sum(len(x[2]) + 1 for x in cur) + len(wd[2]) > max_chars):
                chunks.append(cur)
                cur = []
            cur.append(wd)
            if wd[2][-1:] in ".?!" or (wd[2][-1:] == "," and len(cur) >= 3):
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        # 2a. drop a line that just repeats the previous one (common recognition glitch)
        def norm(ch):
            return re.sub(r"[^a-z0-9 ]", "", " ".join(x[2] for x in ch).lower()).strip()

        deduped: list[list[tuple[float, float, str]]] = []
        for ch in chunks:
            if deduped and norm(ch) == norm(deduped[-1]) and ch[0][0] - deduped[-1][-1][1] < 1.5:
                a, _, w = deduped[-1][-1]
                deduped[-1][-1] = (a, ch[-1][1], w)  # keep the first, extend its time
                continue
            deduped.append(ch)
        # 2b. merge flashes: a chunk shorter than MIN_SHOW joins its neighbour when they touch
        merged: list[list[tuple[float, float, str]]] = []
        for ch in deduped:
            if merged:
                prev = merged[-1]
                short = (prev[-1][1] - prev[0][0] < MIN_SHOW) or (ch[-1][1] - ch[0][0] < MIN_SHOW)
                touching = ch[0][0] - prev[-1][1] < 0.25
                fits = len(prev) + len(ch) <= max_words + 2 and \
                    sum(len(x[2]) + 1 for x in prev + ch) <= max_chars + 12
                if short and touching and fits:
                    prev.extend(ch)
                    continue
            merged.append(list(ch))
        # 3. emit karaoke events (each word lights up when spoken), min on-screen time
        for k, ch in enumerate(merged):
            nxt_start = merged[k + 1][0][0] if k + 1 < len(merged) else None
            start = ch[0][0]
            end = max(ch[-1][1] + 0.3, start + MIN_SHOW)
            end = min(end, nxt_start if nxt_start is not None else 1e9, clip_end_out[ci])
            if end - start < 0.12:
                continue
            parts = []
            for j, (a, b, word) in enumerate(ch):
                nxt = ch[j + 1][0] if j + 1 < len(ch) else b
                cs = max(1, int(round((nxt - a) * 100)))
                parts.append(f"{{\\kf{cs}}}{word.replace('{', '(').replace('}', ')')}")
            events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,"
                          + " ".join(parts))
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return len(events)


# ------------------------------------------------------------------ assemble

def build_segments(selection: dict, profile: StyleProfile | None) -> tuple[list[Segment], dict]:
    from .select import FileAnalysis

    fas: dict[str, FileAnalysis] = {}
    segs: list[Segment] = []
    seg_len = target_segment_length(profile)
    beat = bool(profile and profile.beat_synced)
    punch_count = beat_moves = 0
    for ci, c in enumerate(selection["clips"]):
        fa = fas.setdefault(c["hash"], FileAnalysis(c["hash"]))
        start, end = float(c["start"]), float(c["end"])
        if beat:
            ns, ne = snap_to_beats(fa, start), snap_to_beats(fa, end)
            if ne - ns >= 2.5:
                beat_moves += (ns != start) + (ne != end)
                start, end = ns, ne
        points = plan_punch_ins(fa, start, end, seg_len) if seg_len else []
        fps = float(fa.media["meta"].get("fps") or 30.0)
        # snap splits to the frame grid so contiguous pieces join without a dropped/duplicated frame
        start = round(start * fps) / fps
        points = [round(p * fps) / fps for p in points]
        punch_count += len(points)
        bounds = [start] + points + [end]
        for k in range(len(bounds) - 1):
            segs.append(Segment(c["hash"], bounds[k], bounds[k + 1], ci,
                                zoom=PUNCH_ZOOM if k % 2 == 1 else 1.0,
                                continuous_with_next=k < len(bounds) - 2))
    return segs, {"punch_ins": punch_count, "beat_moves": beat_moves,
                  "segment_target": seg_len}


def render_styled(selection: dict, out: Path, *, spec: EditSpec | None = None,
                  profile: StyleProfile | None = None, preview: bool = True,
                  progress: Callable[[str, float], None] | None = None) -> dict:
    spec = spec or EditSpec(**selection.get("spec", {}))
    first = db.get_media(selection["clips"][0]["hash"])["meta"]
    aspect = spec.aspect_ratio or (profile.aspect_ratio if profile else None)
    if aspect not in ASPECTS:
        aspect = None
    src_w, src_h = first["display_width"], first["display_height"]
    size = output_size(aspect, src_w, src_h) if aspect else (src_w - src_w % 2, src_h - src_h % 2)
    fps = first.get("fps") or 30.0
    captions = spec.captions if spec.captions is not None else bool(profile and profile.captions_present)

    work = get_settings().home / "renders" / f"style_{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        segs, pace = build_segments(selection, profile)
        # colour: one LUT per source file, toward the reference look
        luts: dict[str, Path] = {}
        if profile and profile.lab_mean:
            for h in {s.hash for s in segs}:
                ranges = [(s.start, s.end) for s in segs if s.hash == h]
                sm, ss = footage_lab_stats(h, ranges)
                luts[h] = make_color_lut(sm, ss, profile.lab_mean, profile.lab_std,
                                         work / f"{h[:10]}.cube")
        target_aspect = ASPECTS[aspect] if aspect else src_w / src_h
        tclips = []
        for i, s in enumerate(segs):
            meta = db.get_media(s.hash)["meta"]
            vf = []
            cf = crop_filter(meta, s, target_aspect)
            if cf:
                vf.append(cf)
            if s.hash in luts:
                vf.append(f"lut3d=file='{_ffpath(luts[s.hash])}':interp=tetrahedral")
            tclips.append(TimelineClip(s.hash, s.start, s.end, video_filters=vf,
                                       xfade_room=0.0 if s.continuous_with_next else None))
        subs = None
        n_captions = 0
        if captions:
            subs = work / "captions.ass"
            n_captions = build_captions(segs, fps, size, profile, subs)
        info = render_timeline(tclips, out, fps=fps, size=size, subtitles=subs, preview=preview,
                               fontsdir=ASSETS_DIR / "fonts", progress=progress)
        info.update({"aspect": aspect or "source", "captions": n_captions, "color_matched": bool(luts),
                     "segments": len(segs), **pace,
                     "style": profile.name if profile else None})
        return info
    finally:
        shutil.rmtree(work, ignore_errors=True)
