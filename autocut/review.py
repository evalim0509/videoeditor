"""Review operations on an edit list (selection JSON): remove / restore / swap / move / add / trim.

Every change keeps the "never cut mid-word" rule (trims snap to word gaps) and is logged to the
SQLite feedback table so the selector can be tuned from real decisions.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import db

MIN_CLIP = 1.0
MAX_CLIP = 30.0


class ReviewError(ValueError):
    pass


def load(path: Path) -> dict:
    sel = json.loads(Path(path).read_text())
    sel.setdefault("removed", [])
    sel.setdefault("runner_ups", [])
    return sel


def save(path: Path, sel: dict) -> dict:
    sel["total_duration"] = round(sum(c["end"] - c["start"] for c in sel["clips"]), 3)
    sel["changed"] = True
    Path(path).write_text(json.dumps(sel, indent=2))
    return sel


def _find(lst: list[dict], cid: str) -> int:
    for i, c in enumerate(lst):
        if c["id"] == cid:
            return i
    raise ReviewError("That clip isn't in this edit any more.")


def _overlaps(c: dict, others: list[dict]) -> bool:
    return any(o["hash"] == c["hash"] and o["start"] < c["end"] and c["start"] < o["end"] for o in others)


def _new_id(sel: dict, prefix: str) -> str:
    used = {c["id"] for c in sel["clips"] + sel["runner_ups"] + sel["removed"]}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


def _log(sel: dict, c: dict, decision: str) -> None:
    spec = sel.get("spec", {})
    try:
        db.log_feedback(c["hash"], c["start"], c["end"], decision, mood=sel.get("mood_used") or spec.get("mood"),
                        instruction=spec.get("source_text"),
                        features={k: v for k, v in (c.get("features") or {}).items() if k != "contrib"})
    except Exception:
        pass


def _refresh_text(c: dict) -> None:
    from .corrections import words_in

    c["text"] = "".join(w["word"] for w in words_in(c["hash"], c["start"], c["end"])).strip()
    c["duration"] = round(c["end"] - c["start"], 3)


def apply(sel: dict, op: dict) -> dict:
    kind = op.get("op")
    clips, ru, removed = sel["clips"], sel["runner_ups"], sel["removed"]
    if kind == "remove":
        c = clips.pop(_find(clips, op["clip_id"]))
        removed.append(c)
        _log(sel, c, "remove")
    elif kind == "restore":
        c = removed.pop(_find(removed, op["clip_id"]))
        if _overlaps(c, clips):
            removed.append(c)
            raise ReviewError("Can't restore: it overlaps a clip that's in the edit now.")
        clips.append(c)
        _log(sel, c, "restore")
    elif kind == "swap":
        i = _find(clips, op["clip_id"])
        old = clips[i]
        others = clips[:i] + clips[i + 1:]
        cand = next((r for r in ru if not _overlaps(r, others)), None)
        if cand is None:
            raise ReviewError("No other good moments left to swap in.")
        ru.remove(cand)
        clips[i] = cand
        ru.insert(0, old)
        _log(sel, old, "swap_out")
        _log(sel, cand, "swap_in")
    elif kind == "move":
        i = _find(clips, op["clip_id"])
        j = i + (1 if int(op.get("dir", 1)) > 0 else -1)
        if 0 <= j < len(clips):
            clips[i], clips[j] = clips[j], clips[i]
    elif kind == "add":
        k = _find(ru, op["runner_id"])
        cand = ru[k]
        if _overlaps(cand, clips):
            raise ReviewError("That moment overlaps a clip that's already in the edit.")
        ru.pop(k)
        pos = op.get("position")
        clips.insert(len(clips) if pos is None else max(0, min(len(clips), int(pos))), cand)
        _log(sel, cand, "add")
    elif kind == "trim":
        trim(sel, op["clip_id"], op.get("edge", "end"), float(op.get("delta", 0.5)))
    else:
        raise ReviewError(f"Unknown change: {kind}")
    return sel


def trim(sel: dict, cid: str, edge: str, delta: float) -> dict:
    """Move a clip's start or end by ~delta seconds, snapped to the nearest word gap in that
    direction (never mid-word) and never overlapping another clip from the same file."""
    from .select import FileAnalysis

    c = sel["clips"][_find(sel["clips"], cid)]
    fa = FileAnalysis.__new__(FileAnalysis)  # lightweight: only what safe zones need
    from .corrections import effective_transcript

    tr = effective_transcript(c["hash"]) or {"words": []}
    fa.words = tr["words"]
    fa.duration = float(db.get_media(c["hash"])["meta"]["duration"])
    zones = fa.safe_zones()
    others = [o for o in sel["clips"] if o is not c and o["hash"] == c["hash"]]

    def next_safe(t: float, direction: int) -> float:
        if direction > 0:
            cands = [max(lo, t) for lo, hi in zones if hi >= t - 1e-6]
            return min(cands) if cands else fa.duration
        cands = [min(hi, t) for lo, hi in zones if lo <= t + 1e-6]
        return max(cands) if cands else 0.0

    if edge == "start":
        t = next_safe(c["start"] + delta, 1 if delta > 0 else -1)
        t = max(0.0, t)
        lim = max([o["end"] for o in others if o["end"] <= c["start"] + 1e-6], default=0.0)
        t = max(t, lim)
        if c["end"] - t < MIN_CLIP or c["end"] - t > MAX_CLIP:
            raise ReviewError("The clip can't get any shorter/longer that way.")
        c["start"] = round(t, 3)
    else:
        t = next_safe(c["end"] + delta, 1 if delta > 0 else -1)
        t = min(fa.duration, t)
        lim = min([o["start"] for o in others if o["start"] >= c["end"] - 1e-6], default=fa.duration)
        t = min(t, lim)
        if t - c["start"] < MIN_CLIP or t - c["start"] > MAX_CLIP:
            raise ReviewError("The clip can't get any shorter/longer that way.")
        c["end"] = round(t, 3)
    _refresh_text(c)
    _log(sel, c, f"trim_{edge}")
    return sel


def log_kept(sel: dict) -> None:
    for c in sel["clips"]:
        _log(sel, c, "keep")


def caption_lines(file_hash: str, start: float, end: float) -> list[dict]:
    """The clip's spoken sentences (corrected transcript) as editable caption lines."""
    from .corrections import effective_transcript

    tr = effective_transcript(file_hash) or {"words": [], "sentences": []}
    try:
        from .speakers import is_me_fn

        me = is_me_fn(file_hash)
    except Exception:
        me = lambda t: True  # noqa: E731
    lines = []
    for s in tr["sentences"]:
        mid = (s["start"] + s["end"]) / 2
        if start - 0.05 <= mid <= end + 0.05:
            lines.append({"start": s["start"], "end": s["end"], "text": s["text"], "me": me(mid)})
    return lines
