"""Rule-based intent parser: regex patterns + an editable YAML vocabulary (no model, no network).

Every matched phrase is "consumed" (blanked out). Whatever meaningful words are left over are
reported as warnings, so the user can see what wasn't understood.
"""

from __future__ import annotations

import re
import shutil
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import DEFAULT_CONFIG_DIR, get_settings
from ..editspec import MOODS, EditSpec
from . import register

PHRASE_STOP = (r"(?=,|\.|;|!|\?| and | but | with | in the style| in | for | that | so |"
               r" no | without | then | plus |$)")


def vocab_path() -> Path:
    """User-editable copy of the vocabulary (created from the bundled default on first use)."""
    user = get_settings().config_dir / "intent.yaml"
    if not user.exists():
        shutil.copy(DEFAULT_CONFIG_DIR / "intent.yaml", user)
    return user


@lru_cache(maxsize=4)
def _load_vocab(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text())


def load_vocab() -> dict:
    p = vocab_path()
    return _load_vocab(str(p), p.stat().st_mtime)


def _alt(words) -> str:
    """Regex alternation of phrases, longest first, whole words."""
    ws = sorted({str(w).lower() for w in words}, key=len, reverse=True)
    return "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in ws)


class _Text:
    """Lower-cased instruction with a consumption mask."""

    def __init__(self, text: str):
        t = text.lower()
        t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
        t = re.sub(r"[–—]", "-", t)
        self.s = " " + re.sub(r"\s+", " ", t).strip() + " "

    def find(self, pattern: str):
        return list(re.finditer(pattern, self.s))

    def consume(self, m: re.Match, group: int = 0) -> None:
        a, b = m.span(group)
        self.s = self.s[:a] + " " * (b - a) + self.s[b:]

    def leftover_words(self) -> list[str]:
        return re.findall(r"[a-z0-9'][a-z0-9'\-]*", self.s)


def _number(tok: str, numbers: dict) -> float | None:
    tok = tok.strip().lower()
    try:
        return float(tok)
    except ValueError:
        pass
    return float(numbers[tok]) if tok in numbers else None


@register("rules")
class RuleBasedParser:
    name = "rules"

    def parse(self, text: str, *, available_presets: list[str] | None = None) -> EditSpec:
        v = load_vocab()
        spec = EditSpec(source_text=text, parser=self.name)
        t = _Text(text)
        numbers = {str(k).lower(): val for k, val in (v.get("number_words") or {}).items()}
        num_re = r"(\d+(?:\.\d+)?|" + _alt(numbers) + r")"
        known_vocab = self._known_words(v)

        # 1. number of versions: "3 versions", "two variations"
        for m in t.find(rf"\b{num_re}\s+(?:different\s+)?(?:{_alt(v['versions_words'])})\b"):
            n = _number(m.group(1), numbers)
            if n and n >= 1:
                spec.versions = int(min(10, n))
                t.consume(m)

        # 2. duration
        duration = self._duration(t, num_re, numbers)

        # 2b. style wishes → edit settings ("no zoom cuts", "lowercase captions", "keep my colours")
        pairs = [(str(ph).lower(), e) for e in (v.get("style_options") or []) + (v.get("effect_options") or [])
                 for ph in e["phrases"]]
        for phrase, entry in sorted(pairs, key=lambda x: -len(x[0])):
            for m in t.find(rf"(?<![\w']){_alt([phrase])}(?![\w'])"):
                spec.options.update(entry["set"])
                if entry["label"] not in spec.option_labels:
                    spec.option_labels.append(entry["label"])
                if entry.get("captions") and spec.captions is None:
                    spec.captions = True
                t.consume(m)
        if re.search(r"caption|subtitle|\btext\b", text.lower()):
            pairs = [(str(ph).lower(), e) for e in (v.get("caption_modifiers") or []) for ph in e["phrases"]]
            for phrase, entry in sorted(pairs, key=lambda x: -len(x[0])):
                for m in t.find(rf"(?<![\w']){_alt([phrase])}(?![\w'])"):
                    spec.options.update(entry["set"])
                    if entry["label"] not in spec.option_labels:
                        spec.option_labels.append(entry["label"])
                    t.consume(m)

        # 3. platform (sets aspect + default length)
        platform_dur = None
        for pname, pinfo in (v.get("platforms") or {}).items():
            for m in t.find(rf"\b(?:{_alt(pinfo['aliases'])})\b"):
                if spec.platform is None:
                    spec.platform = pname
                    spec.aspect_ratio = pinfo.get("aspect")
                    platform_dur = pinfo.get("duration")
                t.consume(m)

        # 4. explicit aspect ratio words override the platform's
        for aspect, words in (v.get("aspects") or {}).items():
            for m in t.find(rf"(?<![\w:]){'(?:' + _alt(words) + ')'}(?![\w:])"):
                spec.aspect_ratio = str(aspect)
                t.consume(m)

        # 5. captions on/off
        cap = _alt(v["captions_on"])
        for m in t.find(rf"\b(?:{_alt(v['captions_off_prefix'])})\s+(?:any\s+|the\s+)?(?:{cap})\b"):
            spec.captions = False
            t.consume(m)
        for m in t.find(rf"\b(?:with\s+|add\s+|and\s+|plus\s+)?(?:{cap})\b"):
            if spec.captions is None:
                spec.captions = True
            t.consume(m)

        # 6. music on/off
        mus = _alt(v["music_words"])
        for m in t.find(rf"\b(?:no|without|w/o|skip)\s+(?:the\s+)?(?:{mus})\b"):
            spec.music = False
            t.consume(m)
        for m in t.find(rf"\b(?:with|add|plus|over|to)\s+(?:some\s+|a\s+)?(?:{mus})\b"):
            spec.music = True
            t.consume(m)

        # 7. style preset
        self._style(t, v, spec, available_presets, known_vocab)

        # 8. visual query: "showing X", "shots of X"
        for m in t.find(rf"\b(?:{_alt(v['visual_prefixes'])})\s+(.+?){PHRASE_STOP}"):
            phrase = self._clean_phrase(m.group(1), v)
            if phrase:
                spec.visual_query = phrase if not spec.visual_query else f"{spec.visual_query}, {phrase}"
                t.consume(m)

        # 9. exclude / include keywords
        for m in t.find(rf"\b(?:{_alt(v['exclude_prefixes'])})\s+(.+?){PHRASE_STOP}"):
            phrase = self._clean_phrase(m.group(1), v)
            if phrase:
                spec.exclude_keywords.append(phrase)
                t.consume(m)
        for m in t.find(rf"\b(?:{_alt(v['include_prefixes'])})\s+(.+?){PHRASE_STOP}"):
            phrase = self._clean_phrase(m.group(1), v)
            if phrase and phrase not in known_vocab:
                spec.include_keywords.append(phrase)
                t.consume(m)

        # 10. mood: most mentioned wins (ties → first mentioned)
        hits: dict[str, list[int]] = {}
        for mood in MOODS:
            for m in t.find(rf"\b(?:{_alt(v['moods'].get(mood, []))})\b"):
                hits.setdefault(mood, []).append(m.start())
                t.consume(m)
        if hits:
            spec.mood = max(hits, key=lambda k: (len(hits[k]), -min(hits[k])))  # type: ignore[assignment]

        # 11. vague length words if no number was given
        if duration is None:
            for m in t.find(rf"\b(?:{_alt(v.get('length_words') or {})})\b"):
                duration = float(v["length_words"][m.group(0).strip()])
                t.consume(m)
                break
        spec.target_duration = duration if duration is not None else (
            float(platform_dur) if platform_dur else None)

        # 12. leftovers → warnings
        filler = {str(w).lower() for w in v.get("filler", [])}
        unknown = [w for w in t.leftover_words() if w not in filler and not w.isdigit()]
        if unknown:
            spec.warnings.append("Didn't understand: " + ", ".join(f"'{w}'" for w in dict.fromkeys(unknown)))
        if spec.versions > 1 and spec.target_duration is None:
            pass
        return spec

    # ------------------------------------------------------------------ helpers

    def _duration(self, t: _Text, num_re: str, numbers: dict) -> float | None:
        dur = None
        qual = r"(?:(under|less than|max(?:imum)?|at most|no more than|up to|around|about|roughly|approx(?:imately)?|~)\s+)?"
        # 1:30 / 0:45
        for m in t.find(rf"\b{qual}(\d{{1,2}}):(\d{{2}})(?:\s*(?:min(?:ute)?s?|long))?\b"):
            dur = int(m.group(2)) * 60 + int(m.group(3))
            dur = self._qualify(dur, m.group(1))
            t.consume(m)
        # "1 and a half minutes", "a minute and a half"
        for m in t.find(rf"\b{qual}{num_re}\s+and\s+a\s+half\s*-?\s*(?:minutes?|mins?)\b"):
            n = _number(m.group(2), numbers)
            if n is not None:
                dur = self._qualify((n + 0.5) * 60, m.group(1))
                t.consume(m)
        for m in t.find(rf"\b{qual}(?:a|one)\s+minute\s+and\s+a\s+half\b"):
            dur = self._qualify(90, m.group(1))
            t.consume(m)
        # "half a minute"
        for m in t.find(rf"\b{qual}half\s+a\s+minute\b"):
            dur = self._qualify(30, m.group(1))
            t.consume(m)
        # "60 second", "60s", "60-sec", "2 minutes", "1.5 min", "a minute", "thirty seconds"
        unit = r"(seconds?|secs?|s|minutes?|mins?|m)"
        for m in t.find(rf"\b{qual}{num_re}\s*-?\s*{unit}\b"):
            n = _number(m.group(2), numbers)
            if n is None:
                continue
            u = m.group(3)
            secs = n * 60 if u.startswith("m") else n
            if 1 <= secs <= 3 * 3600:
                dur = self._qualify(secs, m.group(1))
                t.consume(m)
        return dur

    @staticmethod
    def _qualify(secs: float, q: str | None) -> float:
        if q and q.strip() in ("under", "less than", "max", "maximum", "at most", "no more than", "up to"):
            return round(secs * 0.9, 1)  # stay safely under the limit with ±10 % tolerance
        return float(secs)

    @staticmethod
    def _known_words(v: dict) -> set[str]:
        words: set[str] = set()
        for ws in v["moods"].values():
            words |= {str(w).lower() for w in ws}
        for p in (v.get("platforms") or {}).values():
            words |= {str(w).lower() for w in p["aliases"]}
        for ws in (v.get("aspects") or {}).values():
            words |= {str(w).lower() for w in ws}
        words |= {str(w).lower() for w in v["captions_on"]}
        words |= {str(w).lower() for w in v["music_words"]}
        return words

    def _style(self, t: _Text, v: dict, spec: EditSpec, presets: list[str] | None,
               known: set[str]) -> None:
        presets = presets or []
        lower = {p.lower(): p for p in presets}
        for pat in v.get("style_patterns", []):
            rx = re.escape(pat).replace(r"\ ", r"\s+").replace(r"\{name\}", r"([a-z0-9][\w\-]*)")
            for m in t.find(rf"\b{rx}\b"):
                name = m.group(1)
                if name in known or name in ("the", "my", "a", "this", "that"):
                    continue
                t.consume(m)
                if name in lower:
                    spec.style_preset = lower[name]
                else:
                    # fuzzy: "paris" → "paris_vlog"
                    cands = [p for k, p in lower.items() if k.startswith(name) or name in k.split("_")]
                    if len(cands) == 1:
                        spec.style_preset = cands[0]
                    else:
                        avail = ", ".join(presets) or "none saved yet"
                        spec.warnings.append(f"No style preset called '{name}' (available: {avail})")
                return

    @staticmethod
    def _clean_phrase(phrase: str, v: dict) -> str:
        drop = {"the", "my", "a", "an", "our", "his", "her", "their", "any", "some", "all", "more",
                "of"}
        words = [w for w in re.findall(r"[a-z0-9'][a-z0-9'\-]*", phrase.lower()) if w not in drop]
        return " ".join(words[:5]).strip()
