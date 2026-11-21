"""Stage: what effects do the reference videos use? (sound effects, visual effects, stickers)

autocut can't lift the exact sounds/stickers out of someone else's video (they're mixed into its
picture and soundtrack), so it measures *what kind* is used, *how often* and *where*, and the edit
then adds similar ones from your library (your own files first, then the built-in ones).

Per reference (cached by file hash, stage "effects_ref"):
- sfx:        events/minute by kind (whoosh, ding, boom, pop, shutter, generic) from PANNs on
              1 s windows every 0.25 s; share of sound effects that land on a cut
- flash:      white/bright flash frames at cuts (luma spike over the neighbours)
- zoom/shake: dense optical flow (Farneback) on the proxy → sustained zoom-in (positive
              divergence) and shake (jittery global motion)
- stickers:   small, colourful, sharp-edged regions that stay perfectly still for ≥ 1 s while the
              video underneath changes (overlays), excluding caption text
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import db

VERSION = "7"
STAGE = "effects_ref"

SFX_GROUPS = {
    "whoosh": ["Whoosh, swoosh, swish", "Whip"],
    "ding": ["Ding", "Chime", "Ding-dong", "Glockenspiel", "Wind chime", "Cash register"],
    "boom": ["Bang", "Explosion", "Thump, thud", "Slam"],
    "pop": ["Boing", "Clicking", "Squeak", "Toot"],
    "shutter": ["Camera"],
    "generic": ["Sound effect"],
}
SFX_THRESHOLD = 0.3
ON_CUT = 0.35  # seconds


def _sfx_events(wav: Path) -> list[dict]:
    import soundfile as sf
    import torch

    from .audio import _label_index, _panns

    y, sr = sf.read(str(wav), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != 32000:
        import librosa

        y = librosa.resample(y, orig_sr=sr, target_sr=32000)
    model, dev = _panns()
    idx = _label_index()
    groups = {g: [idx[n] for n in names if n in idx] for g, names in SFX_GROUPS.items()}
    hop, win = 8000, 32000  # 0.25 s hop, 1 s windows
    starts = list(range(0, max(1, len(y) - win), hop))
    probs = []
    with torch.no_grad():
        for b in range(0, len(starts), 64):
            batch = np.stack([np.pad(y[s:s + win], (0, max(0, win - len(y[s:s + win])))) for s in starts[b:b + 64]])
            probs.append(model(torch.from_numpy(batch).to(dev))["clipwise_output"].cpu().numpy())
    if not probs:
        return []
    P = np.concatenate(probs)
    events = []
    for g, ids in groups.items():
        sc = P[:, ids].max(axis=1)
        last = -9.0
        for i in np.argsort(-sc):
            if sc[i] < SFX_THRESHOLD:
                break
            t = starts[i] / 32000 + 0.5
            if all(abs(t - e["t"]) > 0.6 for e in events if e["kind"] == g):
                events.append({"t": round(float(t), 2), "kind": g, "score": round(float(sc[i]), 3)})
    return sorted(events, key=lambda e: e["t"])


def _bursts(wav: Path) -> list[tuple[float, float, float]]:
    """Short loud bursts (start, duration, dB above the surrounding 2 s)."""
    import librosa
    from scipy.ndimage import median_filter

    from .ffmpeg import load_audio

    sr, hop = 22050, 220
    y = load_audio(wav, sr)
    if len(y) < sr:
        return []
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop)) + 1e-9
    db_ = 20 * np.log10(np.sqrt((S ** 2).mean(0)))
    jump = db_ - median_filter(db_, size=int(2.0 * sr / hop), mode="nearest")
    fps, out, i = sr / hop, [], 0
    while i < len(jump):
        if jump[i] > 5:
            j = i
            while j < len(jump) and jump[j] > 2:
                j += 1
            dur = (j - i) / fps
            if 0.05 <= dur <= 1.5:
                out.append((i / fps, dur, float(jump[i:j].max())))
            i = j + 1
        else:
            i += 1
    return out


def _cut_sfx(wav: Path, cuts: list[float], duration: float, labelled: list[dict]) -> list[dict]:
    """Sound effects placed on cuts (whoosh/hit on every transition) — the most common use. Model
    labels miss them once they're mixed under music and voice, so: strong bursts (≥ 10 dB) right at
    cuts, far more often than at random moments → they're sound effects."""
    if len(cuts) < 4:
        return []
    strong = [b for b in _bursts(wav) if b[2] >= 10]

    def hit(t: float) -> tuple | None:
        return next((b for b in strong if -0.45 <= b[0] - t <= 0.25), None)

    at_cuts = [hit(c) for c in cuts]
    frac = sum(1 for h in at_cuts if h) / len(cuts)
    mids = [(a + b) / 2 for a, b in zip([0.0] + cuts, cuts + [duration]) if b - a > 1.2]
    base = sum(1 for m in mids if hit(m)) / max(1, len(mids))
    if frac < 0.4 or frac < 2.5 * base:
        return []
    events = []
    for c, h in zip(cuts, at_cuts):
        if not h:
            continue
        lab = next((e["kind"] for e in labelled if abs(e["t"] - h[0]) < 0.6), "whoosh")
        events.append({"t": round(h[0], 2), "kind": lab, "score": round(h[2] / 20, 3), "on_cut": True})
    return events


def _visual(proxy: Path, W: int, H: int, crop, cuts: list[float], text_boxes_fn) -> dict:
    import cv2

    from .visual import iter_frames

    cw, ch, cx, cy = crop if crop else (W, H, 0, 0)
    fps = 10.0
    small = 160
    prev = None
    luma, div, motion, ts = [], [], [], []
    still_acc: list[tuple[float, np.ndarray, np.ndarray]] = []
    stickers: list[dict] = []
    window: list[np.ndarray] = []
    for t, fr in iter_frames(proxy, fps, W, H, offset=0.05):
        fr = fr[cy:cy + ch, cx:cx + cw]
        g = cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY)
        gs = cv2.resize(g, (small, int(small * ch / cw)))
        ts.append(t)
        luma.append(float(g.mean()) / 255)
        if prev is not None:
            flow = cv2.calcOpticalFlowFarneback(prev, gs, None, 0.5, 2, 9, 2, 5, 1.1, 0)
            u, v = flow[..., 0], flow[..., 1]
            d = float(np.mean(np.gradient(u, axis=1) + np.gradient(v, axis=0)))
            div.append(d)
            motion.append((float(u.mean()), float(v.mean())))
        else:
            div.append(0.0)
            motion.append((0.0, 0.0))
        prev = gs
        # stickers: sample every 0.5 s, look at 1 s of history
        if len(ts) % 5 == 0:
            window.append(cv2.resize(fr, (small * 2, int(small * 2 * ch / cw))))
            window = window[-5:]          # 2 s of history, sampled every 0.5 s
            if len(window) >= 3:
                stickers += _sticker_blobs(window, t, text_boxes_fn)
    luma_a, div_a = np.array(luma), np.array(div)
    # flashes at cuts: a bright spike within ±0.3 s of a cut
    flashes = 0
    for c in cuts:
        i = int(round(c * fps))
        lo, hi = max(0, i - 3), min(len(luma_a), i + 4)
        if hi - lo < 3:
            continue
        before, after = luma_a[max(0, lo - 5):lo], luma_a[hi:hi + 5]
        # a flash is brighter than BOTH sides (a cut from a dark to a bright scene is not a flash)
        if len(before) and len(after) and luma_a[lo:hi].max() - max(np.median(before), np.median(after)) > 0.15:
            flashes += 1
    # sustained zoom-in: divergence above threshold for ≥ 0.4 s (outside cut frames)
    cut_idx = {int(round(c * fps)) + k for c in cuts for k in (-1, 0, 1)}
    zoom_runs, run = 0, 0
    for i, d in enumerate(div_a):
        if i in cut_idx:
            run = 0
            continue
        run = run + 1 if d > 0.035 else 0
        if run == 4:
            zoom_runs += 1
    # shake: global motion that flips direction quickly with a large amplitude
    shakes, m = 0, np.array(motion)
    for i in range(3, len(m)):
        seg = m[i - 3:i + 1]
        mag = np.abs(seg).max()
        flips = np.sum(np.sign(seg[1:, 0]) != np.sign(seg[:-1, 0])) + np.sum(np.sign(seg[1:, 1]) != np.sign(seg[:-1, 1]))
        near_cut = any(abs(i - int(round(c * fps))) <= 5 for c in cuts)
        if mag > 2.5 and flips >= 4 and not near_cut:
            shakes += 1
    return {"flashes": flashes, "zooms": zoom_runs, "shakes": shakes // 3, "stickers": _merge_stickers(stickers)}


def _sticker_blobs(window: list[np.ndarray], t: float, text_boxes_fn) -> list[dict]:
    import cv2

    hist = np.stack([w.astype(np.int16) for w in window])
    a = hist[-3:]                  # the last 1 s
    h, w = a.shape[1:3]
    still = (np.abs(a[2] - a[0]).max(axis=2) < 10) & (np.abs(a[1] - a[0]).max(axis=2) < 10)
    changed = np.abs(a[2] - a[0]).max(axis=2) > 25
    if changed.mean() < 0.25:      # the whole picture is still → can't tell overlays apart
        return []
    last = window[-1]
    hsv = cv2.cvtColor(last, cv2.COLOR_RGB2HSV)
    colourful = hsv[..., 1] > 90
    edges = cv2.Canny(cv2.cvtColor(last, cv2.COLOR_RGB2GRAY), 80, 160) > 0
    mask = (still & colourful).astype(np.uint8) * 255
    # join the coloured parts of one emoji (face, tears, eyes) into one blob
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask &= (still.astype(np.uint8) * 255)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    texts = text_boxes_fn(last)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        frac = area / (w * h)
        if not (0.002 <= frac <= 0.06) or bw > 0.28 * w or bh > 0.28 * h:
            continue
        if x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1:
            continue  # touching the border: bars / UI
        if edges[y:y + bh, x:x + bw].mean() < 0.08:
            continue
        if any(not (x + bw < tx or tx + tw < x or y + bh < ty or ty + th < y) for tx, ty, tw, th in texts):
            continue
        # an overlay sits on top of MOVING video: the ring around it must be changing
        pad = max(4, int(0.6 * max(bw, bh)))
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(w, x + bw + pad), min(h, y + bh + pad)
        ring = changed[y0:y1, x0:x1].copy()
        ring[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = False
        ring_area = (y1 - y0) * (x1 - x0) - bw * bh
        if ring_area <= 0 or ring.sum() / ring_area < 0.75:
            continue
        # stickers POP IN: the spot looked different shortly before (static scenery never does)
        if len(hist) >= 5:
            before = np.abs(hist[0, y:y + bh, x:x + bw] - hist[-1, y:y + bh, x:x + bw]).mean()
            if before < 18:
                continue
        out.append({"t": round(t, 2), "x": round((x + bw / 2) / w, 3), "y": round((y + bh / 2) / h, 3),
                    "size": round(max(bw / w, bh / h), 3)})
    return out


def _merge_stickers(dets: list[dict]) -> list[dict]:
    """Group detections of the same overlay across samples into appearances."""
    apps: list[dict] = []
    for d in sorted(dets, key=lambda d: d["t"]):
        for a in apps:
            if d["t"] - a["t1"] <= 0.6 and abs(d["x"] - a["x"]) < 0.05 and abs(d["y"] - a["y"]) < 0.05:
                a["t1"] = d["t"]
                break
        else:
            apps.append({"t0": d["t"], "t1": d["t"], "x": d["x"], "y": d["y"], "size": d["size"]})
    # each detection already means "still for 1 s"; two in a row = on screen ≥ 1.5 s
    return [a for a in apps if a["t1"] - a["t0"] >= 0.45]


def measure_effects(file_hash: str, *, force: bool = False) -> dict:
    if not force:
        cached = db.cache_get(file_hash, STAGE, VERSION)
        if cached is not None:
            return cached
    from .shots import shots as shot_detect
    from .style import detect_crop, text_boxes
    from .visual import _proxy_dims

    media = db.get_media(file_hash)
    dur = float(media["meta"]["duration"])
    cuts = shot_detect(file_hash)["cuts"]
    sfx = _sfx_events(Path(media["audio_path"])) if media.get("audio_path") else []
    if media.get("audio_path"):
        cut_fx = _cut_sfx(Path(media["audio_path"]), cuts, dur, sfx)
        sfx = sorted([e for e in sfx if all(abs(e["t"] - c["t"]) > 0.6 for c in cut_fx)] + cut_fx,
                     key=lambda e: e["t"])
    W, H = _proxy_dims(media)
    crop = detect_crop(Path(media["proxy_path"]), dur, W, H)
    vis = _visual(Path(media["proxy_path"]), W, H, crop, cuts, text_boxes)
    on_cut = sum(1 for e in sfx if any(abs(e["t"] - c) <= ON_CUT for c in cuts))
    kinds: dict[str, int] = {}
    for e in sfx:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    result = {"duration": dur, "cuts": len(cuts), "sfx": sfx, "sfx_kinds": kinds,
              "sfx_on_cuts": on_cut, **vis}
    db.cache_put(file_hash, STAGE, VERSION, result)
    return result


def summarize(refs: list[dict]) -> dict:
    """Aggregate per-reference measurements into rates for the StyleProfile."""
    minutes = max(1e-6, sum(r["duration"] for r in refs) / 60)
    n_sfx = sum(len(r["sfx"]) for r in refs)
    kinds: dict[str, int] = {}
    for r in refs:
        for k, v in r["sfx_kinds"].items():
            kinds[k] = kinds.get(k, 0) + v
    cuts = sum(r["cuts"] for r in refs)
    st = [s for r in refs for s in r["stickers"]]
    return {
        "sfx_per_minute": round(n_sfx / minutes, 2),
        "sfx_on_cuts": round(sum(r["sfx_on_cuts"] for r in refs) / max(1, n_sfx), 2),
        "sfx_kinds": kinds,
        "flash_rate": round(sum(r["flashes"] for r in refs) / max(1, cuts), 3),
        "zooms_per_minute": round(sum(r["zooms"] for r in refs) / minutes, 2),
        "shakes_per_minute": round(sum(r["shakes"] for r in refs) / minutes, 2),
        "stickers_per_minute": round(len(st) / minutes, 2),
        "sticker_size": round(float(np.median([s["size"] for s in st])), 3) if st else 0.12,
        "sticker_seconds": round(float(np.median([s["t1"] - s["t0"] for s in st])) + 0.5, 2) if st else 1.5,
    }


# minimum rates in the references before autocut adds that kind of effect automatically
ENABLE_AT = {"sfx_per_minute": 2.0, "stickers_per_minute": 2.0, "flash_rate": 0.15,
             "zooms_per_minute": 1.0, "shakes_per_minute": 1.0}


def enabled(summary: dict | None) -> dict:
    """Which effect types the references actually use (a few stray detections don't count)."""
    s = summary or {}
    return {"sfx": s.get("sfx_per_minute", 0) >= ENABLE_AT["sfx_per_minute"],
            "stickers": s.get("stickers_per_minute", 0) >= ENABLE_AT["stickers_per_minute"],
            "flash": s.get("flash_rate", 0) >= ENABLE_AT["flash_rate"],
            "zoom": s.get("zooms_per_minute", 0) >= ENABLE_AT["zooms_per_minute"],
            "shake": s.get("shakes_per_minute", 0) >= ENABLE_AT["shakes_per_minute"]}
