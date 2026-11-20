"""Stage: clip selection.

EditSpec + analysed footage → an ordered list of clips (3–15 s), each with a plain-language reason,
plus runner-ups for the review screen.

Never cut mid-word: every clip boundary is a "safe" time that lies outside all word intervals
(sentence boundaries, pauses, shot cuts in silence, or file edges).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

from . import db
from .config import DEFAULT_CONFIG_DIR, get_settings
from .editspec import EditSpec

WORD_GUARD = 0.04  # a cut must be at least this far from any word


# ------------------------------------------------------------------ config

def selector_config_path() -> Path:
    user = get_settings().config_dir / "selector.yaml"
    if not user.exists():
        shutil.copy(DEFAULT_CONFIG_DIR / "selector.yaml", user)
    return user


@lru_cache(maxsize=4)
def _load_cfg(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_config() -> dict:
    p = selector_config_path()
    return _load_cfg(str(p), p.stat().st_mtime)


# ------------------------------------------------------------------ data

@dataclass
class Clip:
    hash: str
    file: str
    start: float
    end: float
    score: float = 0.0
    features: dict = field(default_factory=dict)
    reason: str = ""
    text: str = ""
    id: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(self.duration, 3)
        return d


class FileAnalysis:
    """All cached analyses for one file, as per-second arrays."""

    def __init__(self, file_hash: str, visual_query: str | None = None,
                 keywords: list[str] | None = None, exclude: list[str] | None = None):
        from .audio import analyze as audio_analyze
        from .shots import shots as shot_detect
        from .transcribe import transcribe
        from .visual import clip_embeddings, detect_faces

        self.hash = file_hash
        self.media = db.get_media(file_hash)
        self.file = Path(self.media["path"]).name
        self.duration = float(self.media["meta"]["duration"])
        self.transcript = transcribe(file_hash)
        self.words = self.transcript["words"]
        self.sentences = self.transcript["sentences"]
        au = audio_analyze(file_hash)
        s = au["scores"]
        self.n = len(s["energy"])
        self.scores = {k: np.asarray(v[: self.n], float) for k, v in s.items()}
        self.beats = au["beats"]
        self.cuts = shot_detect(file_hash)["cuts"]
        faces = detect_faces(file_hash)["per_second"]
        self.face_count = np.asarray((faces["count"] + [0] * self.n)[: self.n], float)
        self.face_area = np.asarray((faces["largest_area"] + [0.0] * self.n)[: self.n], float)
        self.emb = clip_embeddings(file_hash)
        self.visual = None
        self.visual_query = visual_query
        if visual_query:
            self.visual = self._clip_relative(visual_query)
        self.keyword_visual = {k: self._clip_relative(k) for k in (keywords or [])}
        self.exclude_visual = {k: self._clip_sim(k) for k in (exclude or [])}

    def _clip_sim(self, text: str) -> np.ndarray:
        from .visual import clip_embed_text

        if not len(self.emb):
            return np.zeros(self.n)
        q = clip_embed_text([text, f"a photo of {text}"]).mean(0)
        q /= np.linalg.norm(q)
        sims = self.emb @ q
        return np.pad(sims, (0, max(0, self.n - len(sims))), mode="edge")[: self.n]

    def _clip_relative(self, text: str) -> np.ndarray:
        sims = self._clip_sim(text)
        lo, hi = np.median(sims), sims.max()
        return np.clip((sims - lo) / max(1e-6, hi - lo), 0, 1)

    # -------------------------------------------------------------- safe cut points
    def safe_zones(self) -> list[tuple[float, float]]:
        """Time ranges where a cut touches no word: the gaps between consecutive words, shrunk by
        a guard margin (smaller when the gap is tight; words that touch leave a single boundary
        point), plus the file's leading and trailing silence."""
        if getattr(self, "_zones", None) is not None:
            return self._zones
        ws = sorted((w["start"], w["end"]) for w in self.words)
        zones: list[tuple[float, float]] = []
        if not ws:
            zones = [(0.0, self.duration)]
        else:
            zones.append((0.0, max(0.0, ws[0][0] - min(WORD_GUARD, ws[0][0] / 2))))
            for (a0, a1), (b0, b1) in zip(ws, ws[1:]):
                gap = b0 - a1
                if gap <= 0:
                    t = (a1 + b0) / 2
                    zones.append((t, t))
                else:
                    g = min(WORD_GUARD, gap / 2)
                    zones.append((a1 + g, b0 - g))
            last = ws[-1][1]
            zones.append((min(self.duration, last + min(WORD_GUARD, max(0.0, self.duration - last) / 2)),
                          self.duration))
        self._zones = zones
        return zones

    def zone_index_before_word(self, wi: int) -> int:
        return wi  # zone i lies between word i-1 and word i

    def is_safe(self, t: float) -> bool:
        return any(lo - 1e-6 <= t <= hi + 1e-6 for lo, hi in self.safe_zones())

    def safe_before(self, t: float, floor: float) -> float:
        best = max((min(hi, t) for lo, hi in self.safe_zones() if lo <= t + 1e-9), default=0.0)
        return max(floor, best)

    def safe_after(self, t: float, ceil: float) -> float:
        best = min((max(lo, t) for lo, hi in self.safe_zones() if hi >= t - 1e-9), default=self.duration)
        return min(ceil, best)


# ------------------------------------------------------------------ candidates

def _snap_to_cut(fa: FileAnalysis, t: float, lo: float, hi: float) -> float:
    """Prefer a shot cut inside [lo, hi] (a silent zone) if there is one."""
    inside = [c for c in fa.cuts if lo <= c <= hi]
    if inside:
        return min(inside, key=lambda c: abs(c - t))
    return t


def generate_candidates(fa: FileAnalysis, cfg: dict) -> list[Clip]:
    cc = cfg["candidates"]
    mn, mx = cc["min_length"], cc["max_length"]
    out: list[Clip] = []
    sents = fa.sentences
    zones = fa.safe_zones()  # zone k sits just before word k (zone len(words) = trailing silence)
    laugh = fa.scores.get("laughter", np.zeros(fa.n))
    laugh_thr = max(0.1, 0.5 * float(laugh.max())) if len(laugh) else 1.0

    # 1. speech windows: runs of consecutive sentences, cut only inside the silent zones around them
    for i in range(len(sents)):
        zlo, zhi = zones[sents[i]["first_word"]]
        start = min(zhi, max(zlo, sents[i]["start"] - cc["pad_before"]))
        start = _snap_to_cut(fa, start, zlo, zhi)
        for j in range(i, len(sents)):
            elo, ehi = zones[sents[j]["last_word"] + 1]
            end = max(elo, min(ehi, sents[j]["end"] + cc["pad_after"]))
            # include laughter right after the last sentence (punchline → laugh)
            k = int(sents[j]["end"])
            while (k < fa.n and laugh[k] >= laugh_thr and k + 1.0 <= ehi
                   and k + 1 - sents[j]["end"] <= cc["laugh_extend"]):
                end = max(end, k + 1.0)
                k += 1
            end = _snap_to_cut(fa, end, elo, ehi)
            dur = end - start
            if dur > mx:
                break
            if dur >= mn:
                text = "".join(w["word"] for w in fa.words[sents[i]["first_word"]: sents[j]["last_word"] + 1]).strip()
                out.append(Clip(fa.hash, fa.file, round(start, 3), round(end, 3), text=text,
                                features={"first_sentence": i, "last_sentence": j}))

    # 2. stretches without speech (food close-ups, scenery, laughter with no words...)
    step = cc["silent_step"]
    for a, b in zones:
        if b - a < mn:
            continue
        t = a
        while t + mn <= b + 1e-6:
            for L in (mn, (mn + mx) / 3, (mn + mx) / 2):
                if t + L <= b + 1e-6:
                    s0 = _snap_to_cut(fa, t, a, min(b, t + 0.5))
                    out.append(Clip(fa.hash, fa.file, round(s0, 3), round(min(s0 + L, b), 3), text=""))
            t += step
    # rounding to ms must not push a boundary into a word
    return [c for c in out if c.duration >= mn - 1e-6 and fa.is_safe(c.start) and fa.is_safe(c.end)]


# ------------------------------------------------------------------ features & scoring

def _rng(fa: FileAnalysis, c: Clip) -> slice:
    a = int(np.floor(c.start))
    b = max(a + 1, int(np.ceil(c.end)))
    return slice(a, min(b, fa.n))


def _mixmax(x: np.ndarray) -> float:
    return float(0.6 * x.max() + 0.4 * x.mean()) if len(x) else 0.0


def compute_features(fa: FileAnalysis, c: Clip, spec: EditSpec, cfg: dict, mood_cfg: dict) -> dict:
    r = _rng(fa, c)
    s = fa.scores
    laugh = s["laughter"][r]
    file_laugh_max = max(0.02, float(s["laughter"].max()))
    words = [w for w in fa.words if w["start"] >= c.start - 1e-3 and w["end"] <= c.end + 1e-3]
    n_words = len(words)
    speech_secs = sum(w["end"] - w["start"] for w in words)
    f = {
        "laughter": 0.5 * min(1.0, laugh.max() / file_laugh_max if len(laugh) else 0)
                    + 0.5 * min(1.0, (laugh.max() if len(laugh) else 0) / 0.3),
        "energy": _mixmax(s["energy"][r]),
        "music": _mixmax(s["music"][r]),
        "cheering": _mixmax(s["cheering"][r]),
        "applause": _mixmax(s["applause"][r]),
        "shouting": _mixmax(s["shouting"][r]),
        "speech": min(1.0, speech_secs / max(0.5, c.duration) * 1.4),
        "speech_rate": min(1.0, n_words / max(0.5, c.duration) / 3.0),
        "clarity": float(np.mean([w["prob"] for w in words])) if words else 0.5,
        "face": float(np.mean(fa.face_count[r] > 0)) if r.stop > r.start else 0.0,
        "face_size": min(1.0, float(np.mean(fa.face_area[r])) / 0.12) if r.stop > r.start else 0.0,
    }
    f["calmness"] = 1.0 - f["energy"]
    # dead air: no talking and nothing notable to hear (no laughter / cheering / music / shouting)
    events = max(f["laughter"], f["cheering"], f["applause"], f["music"], f["shouting"])
    f["dead_air"] = 1.0 if (f["speech"] < 0.1 and events < 0.3) else 0.0
    # garbled: there are "words" but the recogniser had almost no confidence → likely noise/invented
    f["garbled"] = 1.0 if (n_words and f["clarity"] < 0.25) else 0.0
    if "first_sentence" in c.features:
        f["complete"] = 1.0
    else:
        f["complete"] = 0.5
    lo, hi = mood_cfg.get("preferred_length", [4, 10])
    d = c.duration
    f["length_fit"] = 1.0 if lo <= d <= hi else float(np.exp(-((min(abs(d - lo), abs(d - hi))) / 3.0) ** 2))
    if fa.visual is not None:
        f["visual"] = _mixmax(fa.visual[r])
    kw_hits = []
    text_l = c.text.lower()
    for k in spec.include_keywords:
        stem = k.lower().rstrip("s")
        vis = fa.keyword_visual.get(k)
        if re.search(rf"\b{re.escape(stem)}", text_l) or (vis is not None and _mixmax(vis[r]) > 0.8):
            kw_hits.append(k)
    f["keyword"] = 1.0 if kw_hits else 0.0
    f = {k: round(float(v), 4) for k, v in f.items()}
    f["_kw_hits"] = kw_hits  # type: ignore[assignment]
    return f


def is_excluded(fa: FileAnalysis, c: Clip, spec: EditSpec) -> str | None:
    r = _rng(fa, c)
    text_l = c.text.lower()
    for k in spec.exclude_keywords:
        stem = k.lower().rstrip("s")
        if re.search(rf"\b{re.escape(stem)}", text_l):
            return k
        sims = fa.exclude_visual.get(k)
        if sims is not None and len(sims):
            thr = max(0.26, float(np.percentile(sims, 90)))
            if sims[r].max() >= thr:
                return k
    return None


def score_clip(f: dict, weights: dict, spec: EditSpec, cfg: dict) -> tuple[float, dict]:
    contrib = {k: weights[k] * f.get(k, 0.0) for k in weights if k in f}
    if "visual" in f:
        contrib["visual"] = cfg["visual_query_weight"] * f["visual"]
    if spec.include_keywords:
        contrib["keyword"] = cfg["keyword_weight"] * f["keyword"]
    return round(sum(contrib.values()), 4), contrib


# ------------------------------------------------------------------ reasons

def _fmt(t: float) -> str:
    m, s = divmod(int(round(t)), 60)
    return f"{m}:{s:02d}"


def make_reason(fa: FileAnalysis, c: Clip, f: dict, contrib: dict, spec: EditSpec, cfg: dict) -> str:
    r = _rng(fa, c)
    parts = []
    ranked = sorted(((v, k) for k, v in contrib.items() if v > 0), reverse=True)
    for v, k in ranked:
        if len(parts) >= 3:
            break
        if k == "laughter" and f["laughter"] > 0.3:
            peak = float(fa.scores["laughter"][r].max())
            at = r.start + int(np.argmax(fa.scores["laughter"][r]))
            strength = "clear" if peak >= 0.3 else "soft" if peak >= 0.05 else "faint"
            best = peak >= 0.95 * float(fa.scores["laughter"].max())
            parts.append(f"{strength} laughter at {_fmt(at)}" + (" (the strongest in this footage)" if best else ""))
        elif k == "energy" and f["energy"] > 0.75:
            at = r.start + int(np.argmax(fa.scores["energy"][r]))
            parts.append(f"lively, punchy moment around {_fmt(at)}")
        elif k == "visual" and f.get("visual", 0) > 0.4:
            parts.append(f"matches “{spec.visual_query}”")
        elif k == "keyword" and f["keyword"]:
            parts.append(f"about “{', '.join(f['_kw_hits'])}”")
        elif k == "music" and f["music"] > 0.3:
            parts.append("music playing")
        elif k == "cheering" and f["cheering"] > 0.3:
            parts.append("cheering")
        elif k == "applause" and f["applause"] > 0.3:
            parts.append("applause")
        elif k == "shouting" and f["shouting"] > 0.3:
            parts.append("shouting / excitement")
        elif k == "calmness" and f["calmness"] > 0.6:
            parts.append("calm, quiet moment")
        elif k == "clarity" and f["clarity"] > 0.7 and c.text:
            parts.append("clearly spoken")
        elif k in ("speech", "speech_rate") and f["speech"] > 0.6 and "lots of talking" not in parts:
            parts.append("lots of talking")
        elif k == "face_size" and f["face_size"] > 0.6:
            parts.append("close-up of your face")
        elif k == "face" and f["face"] > 0.8 and not any("face" in p for p in parts):
            parts.append("you're on camera")
    if not parts:
        parts.append("solid filler that keeps the story moving")
    reason = parts[0][0].upper() + parts[0][1:] + ("; " + "; ".join(parts[1:]) if len(parts) > 1 else "") + "."
    if c.text:
        quote = c.text if len(c.text) <= 70 else c.text[:67].rsplit(" ", 1)[0] + "…"
        reason += f" You say: “{quote}”"
        if f["clarity"] < cfg["low_clarity"]:
            reason += " (speech is hard to make out here)"
    else:
        reason += " No talking in this bit."
    return reason


# ------------------------------------------------------------------ selection

def _overlaps(a: Clip, b: Clip) -> bool:
    return a.hash == b.hash and a.start < b.end and b.start < a.end


STOPWORDS = set("""a an the and or but so to of in on at for with is are was were be been it its it's
s that this these those i i'm im you your we we're our he she they them my me oh yeah yes no not
do does did just like um uh okay ok one very really what how there here then now""".split())


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOPWORDS and len(w) > 1}


def _duplicate(a: Clip, b: Clip, cfg: dict) -> bool:
    """Same thing said twice (e.g. a retake). Short common phrases like "it's a good one" don't
    count: at least 3 shared content words are required."""
    if a.text and b.text:
        wa, wb = _content_words(a.text), _content_words(b.text)
        shared = wa & wb
        if len(shared) >= 3 and len(shared) / min(len(wa), len(wb)) > cfg["duplicate_text_overlap"]:
            return True
    return False


def select_clips(hashes: list[str], spec: EditSpec, n_runner_ups: int = 10) -> dict:
    cfg = load_config()
    mood = spec.mood if spec.mood in cfg["moods"] else "auto"
    mood_cfg = cfg["moods"][mood]
    weights = mood_cfg["weights"]
    target = float(spec.target_duration or cfg["default_target_seconds"])
    lo_t, hi_t = target * (1 - spec.duration_tolerance), target * (1 + spec.duration_tolerance)

    analyses = [FileAnalysis(h, spec.visual_query, spec.include_keywords, spec.exclude_keywords)
                for h in hashes]
    cands: list[Clip] = []
    excluded = 0
    for fa in analyses:
        for c in generate_candidates(fa, cfg):
            if is_excluded(fa, c, spec):
                excluded += 1
                continue
            f = compute_features(fa, c, spec, cfg, mood_cfg)
            c.score, contrib = score_clip(f, weights, spec, cfg)
            c.reason = make_reason(fa, c, f, contrib, spec, cfg)
            f.pop("_kw_hits", None)
            c.features = {**f, "contrib": {k: round(v, 3) for k, v in contrib.items()}}
            cands.append(c)
    cands.sort(key=lambda c: -c.score)

    chosen: list[Clip] = []
    total = 0.0

    def can_add(c: Clip, limit: float) -> bool:
        return (total + c.duration <= limit + 1e-6
                and not any(_overlaps(c, x) or _duplicate(c, x, cfg) for x in chosen))

    for c in cands:  # best first, aim for the target
        if total >= target - 1.0:
            break
        if can_add(c, hi_t):
            chosen.append(c)
            total += c.duration
    if total < lo_t:  # fill with anything that fits
        for c in cands:
            if total >= lo_t:
                break
            if can_add(c, hi_t):
                chosen.append(c)
                total += c.duration

    # order: chronological (file order, then time); optionally open with the strongest clip
    order = {h: i for i, h in enumerate(hashes)}
    chosen.sort(key=lambda c: (order[c.hash], c.start))
    if mood_cfg.get("hook_first") and len(chosen) > 2:
        best = max(chosen, key=lambda c: c.score)
        chosen.remove(best)
        chosen.insert(0, best)
        best.reason = "Opening hook (strongest clip). " + best.reason
    for i, c in enumerate(chosen, 1):
        c.id = f"c{i}"

    runner_ups: list[Clip] = []
    for c in cands:
        if len(runner_ups) >= n_runner_ups:
            break
        if c in chosen or any(_overlaps(c, x) for x in chosen + runner_ups):
            continue
        if any(_duplicate(c, x, cfg) for x in chosen + runner_ups):
            continue
        runner_ups.append(c)
    for i, c in enumerate(runner_ups, 1):
        c.id = f"r{i}"

    warnings = []
    if total < lo_t:
        warnings.append(f"Only found {total:.0f}s of usable footage for a {target:.0f}s target.")
    if excluded:
        warnings.append(f"Left out {excluded} candidate clips because of your exclusions.")
    for fa in analyses:
        for c in chosen:
            if c.hash == fa.hash:
                assert fa.is_safe(c.start) and fa.is_safe(c.end), f"mid-word cut in {c}"
    return {
        "spec": spec.model_dump(),
        "mood_used": mood,
        "target": target, "range": [round(lo_t, 1), round(hi_t, 1)],
        "total_duration": round(total, 2),
        "clips": [c.to_dict() for c in chosen],
        "runner_ups": [c.to_dict() for c in runner_ups],
        "candidates_considered": len(cands),
        "warnings": warnings,
    }
