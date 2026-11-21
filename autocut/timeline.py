"""The editable timeline (what the CapCut-style editor shows and the renderer renders).

{
  "version": 1,
  "canvas":  {"w": 1920, "h": 1080, "fps": 30.0},
  "options": {RenderOptions},
  "clips":   [{"id", "hash", "start", "end"}],                     # main track, in play order
  "texts":   [{"id", "clip", "src" | "offset", "dur", "text", "x", "y", "scale", "rotation",
               "kind": "caption"|"title", "style": {...}, "auto", "edited"}],
  "stickers":[{"id", "clip", "offset", "dur", "asset", "x", "y", "scale", "rotation", "auto", "edited"}],
  "sounds":  [{"id", "clip", "offset", "asset", "gain_db", "auto", "edited"}],
  "effects": [{"id", "clip", "offset", "dur", "kind": "zoom"|"flash"|"shake", "auto", "edited"}]
}

Anchoring keeps things where they belong when clips are reordered or trimmed:
- captions are anchored to the *source time* of the words (`src`), so they follow the speech;
- everything else is anchored to its clip (`offset` seconds from the clip's start).
An item outside its clip (after trimming) is simply not shown. Items you add or edit are marked
`edited`; regenerating the automatic ones (e.g. after the quick review screen) never touches them.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from . import db
from .editspec import EditSpec
from .options import RenderOptions
from .style import ASPECTS, StyleProfile

VERSION = 1
TRACKS = ("texts", "stickers", "sounds", "effects")


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def clip_frames(c: dict, fps: float) -> int:
    return max(1, int(round((c["end"] - c["start"]) * fps)))


def clip_starts(doc: dict) -> dict[str, float]:
    """Output-timeline start of every clip (same frame rounding as the renderer)."""
    fps, t, out = doc["canvas"]["fps"], 0, {}
    for c in doc["clips"]:
        out[c["id"]] = t / fps
        t += clip_frames(c, fps)
    return out


def total_duration(doc: dict) -> float:
    fps = doc["canvas"]["fps"]
    return sum(clip_frames(c, fps) for c in doc["clips"]) / fps


def resolve_time(doc: dict, item: dict, starts: dict | None = None) -> tuple[float, float] | None:
    """(t0, t1) on the output timeline, or None if the item isn't visible (its clip is gone or it
    falls outside the clip after trimming)."""
    starts = starts or clip_starts(doc)
    clip = next((c for c in doc["clips"] if c["id"] == item.get("clip")), None)
    if clip is None:
        return None
    cdur = clip_frames(clip, doc["canvas"]["fps"]) / doc["canvas"]["fps"]
    off = (item["src"] - clip["start"]) if "src" in item else item.get("offset", 0.0)
    dur = item.get("dur", 0.0)
    if off >= cdur - 0.02 or off + max(dur, 0.01) <= 0:
        return None
    a = max(0.0, off)
    b = min(cdur, off + dur) if dur else a
    return starts[clip["id"]] + a, starts[clip["id"]] + b


# ------------------------------------------------------------------ build from an auto-edit

def _canvas(selection: dict, opts: RenderOptions) -> dict:
    from .stylematch import output_size

    first = db.get_media(selection["clips"][0]["hash"])["meta"]
    sw, sh = first["display_width"], first["display_height"]
    aspect = opts.aspect if opts.aspect in ASPECTS else None
    w, h = output_size(aspect, sw, sh) if aspect else (sw - sw % 2, sh - sh % 2)
    return {"w": w, "h": h, "fps": float(first.get("fps") or 30.0)}


def _effect_switches(profile: StyleProfile | None, opts: RenderOptions) -> dict:
    from .effects import enabled

    on = enabled(profile.effects if profile else None)
    for key, opt in (("sfx", opts.sound_effects), ("stickers", opts.stickers), ("flash", opts.flash),
                     ("shake", opts.shake)):
        if opt is not None:
            on[key] = opt
    return on


def caption_items(doc: dict, selection: dict, profile: StyleProfile | None, opts: RenderOptions) -> list[dict]:
    """Caption lines (your voice only, chunked like before) as positioned text items."""
    import tempfile

    from .stylematch import Segment, build_captions

    fps = doc["canvas"]["fps"]
    W, H = doc["canvas"]["w"], doc["canvas"]["h"]
    segs = [Segment(c["hash"], c["start"], c["end"], i) for i, c in enumerate(doc["clips"])]
    lines: list[tuple[float, float, str]] = []
    with tempfile.TemporaryDirectory() as d:
        build_captions(segs, fps, (W, H), profile, Path(d) / "c.ass", options=opts, collect=lines)
    starts = clip_starts(doc)
    pos = opts.caption_position
    if pos == "auto":
        pos = profile.caption_position if (profile and profile.caption_position not in (None, "none")) else "bottom"
    y = {"top": 0.1, "middle": 0.5}.get(pos, 0.76 if H > W else 0.9)
    rel = profile.caption_height if (profile and profile.caption_height) else 0.045
    size = float(np.clip(rel * 1.05, 0.032 if H > W else 0.028, 0.06))
    items = []
    for a, b, text in lines:
        # which clip is this line in? anchor it to the source time of its first word
        clip = next((c for c in doc["clips"] if starts[c["id"]] - 1e-3 <= a < starts[c["id"]] + clip_frames(c, fps) / fps), None)
        if not clip:
            continue
        src = clip["start"] + (a - starts[clip["id"]])
        items.append({"id": _id("t"), "clip": clip["id"], "src": round(src, 3), "dur": round(b - a, 3),
                      "text": text, "x": 0.5, "y": y, "scale": 1.0, "rotation": 0.0, "kind": "caption",
                      "style": {"size": round(size * opts.caption_scale, 4), "color": "#FFFFFF",
                                "outline": "#101010", "bold": False, "box": False,
                                "karaoke": opts.caption_style == "karaoke"},
                      "auto": True, "edited": False})
    return items


def effect_items(doc: dict, selection: dict, profile: StyleProfile | None, opts: RenderOptions) -> dict:
    """Automatic zoom cuts, sound effects, stickers, flashes and shakes, matched to the references."""
    from .library import pick_sfx, pick_sticker
    from .stylematch import plan_punch_ins, target_segment_length
    from .select import FileAnalysis

    out = {"stickers": [], "sounds": [], "effects": []}
    fps = doc["canvas"]["fps"]
    clips = doc["clips"]
    on = _effect_switches(profile, opts)
    fx = (profile.effects if profile else None) or {}
    dur = total_duration(doc)
    fas: dict[str, FileAnalysis] = {}

    def fa(h: str) -> FileAnalysis:
        if h not in fas:
            fas[h] = FileAnalysis(h)
        return fas[h]

    # zoom cuts (punch-ins) at word gaps
    seg_len = target_segment_length(profile, opts.punch_in_spacing) if opts.punch_ins else None
    if seg_len:
        for c in clips:
            pts = plan_punch_ins(fa(c["hash"]), c["start"], c["end"], seg_len)
            bounds = [c["start"]] + pts + [c["end"]]
            for k in range(1, len(bounds) - 1, 2):
                out["effects"].append({"id": _id("e"), "clip": c["id"], "kind": "zoom",
                                       "offset": round(bounds[k] - c["start"], 3),
                                       "dur": round(bounds[k + 1] - bounds[k], 3), "scale": 1.15,
                                       "auto": True, "edited": False})
    starts = clip_starts(doc)
    cut_after = [c for c in clips[:-1]]  # a sound / flash goes on the cut at the end of these
    # sound effects: on cuts (most common), kinds in the reference's proportions
    if on["sfx"] and len(clips) > 1:
        kinds = fx.get("sfx_kinds") or {"whoosh": 1}
        order = [k for k, _ in sorted(kinds.items(), key=lambda kv: -kv[1])]
        rate = fx.get("sfx_per_minute") or 12.0
        n = max(1, min(len(cut_after), int(round(rate * dur / 60))))
        step = len(cut_after) / n
        for k in range(n):
            c = cut_after[int(k * step)]
            kind = order[k % len(order)] if order else "whoosh"
            kind = {"generic": "whoosh", "shutter": "shutter"}.get(kind, kind)
            cdur = clip_frames(c, fps) / fps
            out["sounds"].append({"id": _id("s"), "clip": c["id"], "offset": round(max(0.0, cdur - 0.3), 3),
                                  "asset": pick_sfx(kind), "gain_db": -6.0, "auto": True, "edited": False})
    # flashes on cuts
    if on["flash"] and len(clips) > 1:
        rate = fx.get("flash_rate") or 0.3
        every = max(1, int(round(1 / max(0.05, rate))))
        for k, c in enumerate(cut_after):
            if k % every == 0:
                cdur = clip_frames(c, fps) / fps
                out["effects"].append({"id": _id("e"), "clip": c["id"], "kind": "flash",
                                       "offset": round(max(0.0, cdur - 0.05), 3), "dur": 0.12,
                                       "auto": True, "edited": False})
    # stickers / shakes at the most expressive moments
    if on["stickers"] or on["shake"]:
        moments = []
        for c in clips:
            f = fa(c["hash"])
            a, b = int(c["start"]), max(int(c["start"]) + 1, int(np.ceil(c["end"])))
            lau = f.scores["laughter"][a:b]
            en = f.scores["energy"][a:b]
            if not len(en):
                continue
            score = 1.5 * lau / max(0.02, float(f.scores["laughter"].max())) + en
            i = int(np.argmax(score))
            t = a + i + 0.5
            words = [w["word"].lower() for w in f.words if c["start"] <= w["start"] < c["end"]]
            text = "".join(words)
            mood = ("laughter" if lau[i] >= 0.5 * float(f.scores["laughter"].max()) and lau[i] > 0.02 else
                    "shock" if any(k in text for k in ("oh my", "wow", "what", "omg")) else
                    "food" if any(k in text for k in ("eat", "food", "dumpling", "noodle", "sauce", "oil", "lunch", "yum")) else
                    "energy")
            face = f.face_area[min(len(f.face_area) - 1, int(t))] if len(f.face_area) else 0
            moments.append({"clip": c, "t": min(t, c["end"] - 0.3), "score": float(score[i]), "mood": mood,
                            "face": float(face)})
        moments.sort(key=lambda m: -m["score"])
        if on["stickers"]:
            rate = fx.get("stickers_per_minute") or 3.0
            n = max(1, min(len(moments), int(round(rate * dur / 60))))
            size = float(np.clip(fx.get("sticker_size", 0.12) or 0.12, 0.06, 0.25))
            for k, m in enumerate(moments[:n]):
                c = m["clip"]
                xs = 0.8 if k % 2 == 0 else 0.2  # beside the face, alternating sides
                out["stickers"].append({"id": _id("k"), "clip": c["id"], "offset": round(max(0.0, m["t"] - c["start"] - 0.2), 3),
                                        "dur": round(float(fx.get("sticker_seconds", 1.5) or 1.5), 2),
                                        "asset": pick_sticker(m["mood"], k), "x": xs, "y": 0.3,
                                        "scale": 1.0, "w": size, "rotation": -8.0 if k % 2 else 8.0,
                                        "auto": True, "edited": False})
        if on["shake"]:
            rate = fx.get("shakes_per_minute") or 2.0
            for m in moments[:max(1, int(round(rate * dur / 60)))]:
                c = m["clip"]
                out["effects"].append({"id": _id("e"), "clip": c["id"], "kind": "shake",
                                       "offset": round(max(0.0, m["t"] - c["start"] - 0.2), 3), "dur": 0.4,
                                       "auto": True, "edited": False})
    return out


def build_document(selection: dict, spec: EditSpec | None = None, profile: StyleProfile | None = None,
                   options: RenderOptions | None = None, keep: dict | None = None) -> dict:
    """Timeline for an auto-edit. `keep`: an existing document whose items you added/edited are kept."""
    spec = spec or EditSpec(**selection.get("spec", {}))
    opts = options or (RenderOptions(**selection["options"]) if selection.get("options") else RenderOptions.from_spec(spec))
    doc = {"version": VERSION, "options": opts.model_dump(), "style": profile.name if profile else None,
           "clips": [{"id": c.get("id") or _id("c"), "hash": c["hash"], "start": round(c["start"], 3),
                      "end": round(c["end"], 3), "text": c.get("text", ""), "reason": c.get("reason", "")}
                     for c in selection["clips"]],
           "texts": [], "stickers": [], "sounds": [], "effects": []}
    doc["canvas"] = _canvas(selection, opts)
    captions = opts.captions if opts.captions is not None else bool(profile and profile.captions_present)
    if captions:
        doc["texts"] = caption_items(doc, selection, profile, opts)
    for k, v in effect_items(doc, selection, profile, opts).items():
        doc[k] = v
    if keep:
        ids = {c["id"] for c in doc["clips"]}
        for tr in TRACKS:
            mine = [it for it in keep.get(tr, []) if (it.get("edited") or not it.get("auto")) and it.get("clip") in ids]
            gone = {it["id"] for it in mine}
            doc[tr] = [it for it in doc[tr] if it["id"] not in gone] + mine
    return doc


# ------------------------------------------------------------------ validate edits from the app

def normalize(doc: dict) -> dict:
    """Make an edited document safe to render: known fields, clip edges in word gaps (never
    mid-word), sane ranges."""
    from .corrections import effective_transcript
    from .select import FileAnalysis

    opts = RenderOptions(**doc.get("options", {}))
    doc["options"] = opts.model_dump()
    clips = []
    for c in doc.get("clips", []):
        m = db.get_media(c["hash"])
        if not m:
            continue
        fa = FileAnalysis.__new__(FileAnalysis)
        fa.words = (effective_transcript(c["hash"]) or {"words": []})["words"]
        fa.duration = float(m["meta"]["duration"])
        s, e = max(0.0, float(c["start"])), min(fa.duration, float(c["end"]))
        if not fa.is_safe(s):
            s = fa.safe_before(s, 0.0)
        if not fa.is_safe(e):
            e = fa.safe_after(e, fa.duration)
        if e - s < 0.3:
            continue
        clips.append({**c, "id": c.get("id") or _id("c"), "start": round(s, 3), "end": round(e, 3)})
    doc["clips"] = clips
    if clips:  # the shape may have changed in the editor's settings
        doc["canvas"] = _canvas({"clips": clips}, opts)
    ids = {c["id"] for c in clips}
    for tr in TRACKS:
        items = []
        for it in doc.get(tr, []):
            if it.get("clip") not in ids:
                continue
            it.setdefault("id", _id(tr[0]))
            for k in ("x", "y"):
                if k in it:
                    it[k] = float(np.clip(float(it[k]), -0.2, 1.2))
            if "dur" in it:
                it["dur"] = float(np.clip(float(it["dur"]), 0.05, 600))
            if "scale" in it:
                it["scale"] = float(np.clip(float(it["scale"]), 0.2, 6))
            items.append(it)
        doc[tr] = items
    return doc


# ------------------------------------------------------------------ render

def _ass_color(hexstr: str, alpha: int = 0) -> str:
    h = (hexstr or "#FFFFFF").lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def write_ass(doc: dict, path: Path) -> int:
    from .stylematch import _ass_time

    W, H = doc["canvas"]["w"], doc["canvas"]["h"]
    starts = clip_starts(doc)
    case = doc.get("options", {}).get("caption_case", "original")
    events = []
    for it in doc["texts"]:
        span = resolve_time(doc, it, starts)
        if not span or span[1] - span[0] < 0.05:
            continue
        st = it.get("style", {})
        size = max(8, int(round(st.get("size", 0.045) * H * it.get("scale", 1.0))))
        text = it["text"]
        if it.get("kind") == "caption":
            text = text.lower() if case == "lower" else text.upper() if case == "upper" else text
        text = text.replace("{", "(").replace("}", ")").replace("\n", "\\N")
        tags = (f"\\an5\\pos({it['x'] * W:.0f},{it['y'] * H:.0f})\\fs{size}"
                f"\\1c{_ass_color(st.get('color', '#FFFFFF'))}\\3c{_ass_color(st.get('outline', '#101010'))}"
                f"\\bord{max(1, size // 14)}\\shad1" + ("\\b1" if st.get("bold") else ""))
        if abs(it.get("rotation", 0)) > 0.1:
            tags += f"\\frz{-it['rotation']:.1f}"
        if st.get("box"):
            tags += f"\\3c{_ass_color(st.get('box_color', '#000000'), 0x40)}\\bord{max(4, size // 4)}"
        events.append(f"Dialogue: 0,{_ass_time(span[0])},{_ass_time(span[1])},Caption,,0,0,0,,{{{tags}}}{text}")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Inter SemiBold,48,&H00FFFFFF,&H70FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,3,1,5,{int(W * 0.06)},{int(W * 0.06)},20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return len(events)


def render_document(doc: dict, out: Path, *, profile: StyleProfile | None = None, preview: bool = True,
                    progress: Callable[[str, float], None] | None = None) -> dict:
    import shutil

    from .config import ASSETS_DIR, get_settings
    from .library import resolve
    from .render import TimelineClip, render_timeline
    from .stylematch import (Segment, _ffpath, crop_filter, crop_filter_centered, footage_lab_stats,
                             make_color_lut)

    doc = normalize(doc)
    if not doc["clips"]:
        raise ValueError("The timeline has no clips.")
    opts = RenderOptions(**doc["options"])
    W, H, fps = doc["canvas"]["w"], doc["canvas"]["h"], doc["canvas"]["fps"]
    starts = clip_starts(doc)
    work = get_settings().home / "renders" / f"doc_{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        luts: dict[str, Path] = {}
        if opts.color_match and profile and profile.lab_mean:
            for h in {c["hash"] for c in doc["clips"]}:
                sm, ss = footage_lab_stats(h, [(c["start"], c["end"]) for c in doc["clips"] if c["hash"] == h])
                luts[h] = make_color_lut(sm, ss, profile.lab_mean, profile.lab_std, work / f"{h[:10]}.cube")
        tclips: list[TimelineClip] = []
        for c in doc["clips"]:
            meta = db.get_media(c["hash"])["meta"]
            src_aspect = meta["display_width"] / meta["display_height"]
            target = W / H
            # split the clip where zoom cuts start/stop and where shakes happen
            zooms = [e for e in doc["effects"] if e["clip"] == c["id"] and e["kind"] == "zoom"]
            shakes = [e for e in doc["effects"] if e["clip"] == c["id"] and e["kind"] == "shake"]
            cuts = {c["start"], c["end"]}
            for e in zooms + shakes:
                for t in (c["start"] + e["offset"], c["start"] + e["offset"] + e["dur"]):
                    if c["start"] + 0.1 < t < c["end"] - 0.1:
                        cuts.add(round(round(t * fps) / fps, 4))
            bounds = sorted(cuts)
            for k in range(len(bounds) - 1):
                a, b = bounds[k], bounds[k + 1]
                mid = (a + b) / 2 - c["start"]
                zoom = next((e.get("scale", 1.15) for e in zooms if e["offset"] <= mid < e["offset"] + e["dur"]), 1.0)
                shake = any(e["offset"] <= mid < e["offset"] + e["dur"] for e in shakes)
                seg = Segment(c["hash"], a, b, 0, zoom=zoom)
                vf = []
                if abs(target - src_aspect) > 0.01 or zoom > 1.0:
                    cf = crop_filter(meta, seg, target) if opts.face_follow else crop_filter_centered(meta, seg, target)
                    if cf:
                        vf.append(cf)
                if shake:
                    vf.append("crop=iw*0.94:ih*0.94:'(iw-ow)/2+(iw-ow)/2*sin(t*47)':'(ih-oh)/2+(ih-oh)/2*cos(t*39)'")
                if c["hash"] in luts:
                    vf.append(f"lut3d=file='{_ffpath(luts[c['hash']])}':interp=tetrahedral")
                tclips.append(TimelineClip(c["hash"], a, b, video_filters=vf,
                                           xfade_room=0.0 if k < len(bounds) - 2 else None))
        subs = work / "texts.ass"
        n_text = write_ass(doc, subs)
        overlays = []
        for it in doc["stickers"]:
            span = resolve_time(doc, it, starts)
            if span:
                overlays.append({"path": resolve(it["asset"], "stickers"), "t0": span[0], "t1": span[1],
                                 "x": it["x"], "y": it["y"], "w": it.get("w", 0.12) * it.get("scale", 1.0),
                                 "rotation": it.get("rotation", 0)})
        sfx = []
        for it in doc["sounds"]:
            span = resolve_time(doc, {**it, "dur": 0.01}, starts)
            if span:
                sfx.append({"path": resolve(it["asset"], "sfx"), "t": span[0], "gain_db": it.get("gain_db", -6.0)})
        flashes = []
        for it in doc["effects"]:
            if it["kind"] == "flash":
                span = resolve_time(doc, it, starts)
                if span:
                    flashes.append(span)
        info = render_timeline(tclips, out, fps=fps, size=(W, H), subtitles=subs if n_text else None,
                               preview=preview, fontsdir=ASSETS_DIR / "fonts", loudness=opts.loudness_lufs,
                               overlays=overlays, sfx=sfx, flashes=flashes, progress=progress)
        info.update({"texts": n_text, "stickers": len(overlays), "sounds": len(sfx), "flashes": len(flashes),
                     "zooms": sum(1 for e in doc["effects"] if e["kind"] == "zoom"),
                     "shakes": sum(1 for e in doc["effects"] if e["kind"] == "shake")})
        return info
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def save(path: Path, doc: dict) -> None:
    Path(path).write_text(json.dumps(doc, indent=1))
