"""Timeline document: anchoring follows clips/words, edits are validated (never mid-word), effects
follow references/instruction, text renders at its position."""
from pathlib import Path


def _doc():
    return {"version": 1, "canvas": {"w": 1920, "h": 1080, "fps": 30.0}, "options": {},
            "clips": [{"id": "a", "hash": "h", "start": 10.0, "end": 14.0}, {"id": "b", "hash": "h", "start": 2.0, "end": 5.0}],
            "texts": [{"id": "t1", "clip": "b", "src": 3.0, "dur": 1.0, "text": "hi", "x": .5, "y": .9, "kind": "caption", "style": {}},
                      {"id": "t2", "clip": "a", "offset": 1.0, "dur": 2.0, "text": "title", "x": .3, "y": .2, "kind": "title", "style": {"size": .08}}],
            "stickers": [], "sounds": [], "effects": []}


def test_anchoring_follows_clips_and_words():
    from autocut.timeline import clip_starts, resolve_time, total_duration

    d = _doc()
    assert total_duration(d) == 7.0 and clip_starts(d) == {"a": 0.0, "b": 4.0}
    assert resolve_time(d, d["texts"][0]) == (5.0, 6.0)    # word at source 3.0 s, clip b starts at 4 s
    d["clips"].reverse()                                  # reorder: both move with their clip
    assert resolve_time(d, d["texts"][0]) == (1.0, 2.0)
    assert resolve_time(d, d["texts"][1]) == (4.0, 6.0)
    d["clips"][0]["start"] = 4.2                          # trim away the captioned words → hidden
    assert resolve_time(d, d["texts"][0]) is None


def test_normalize_snaps_edits_into_word_gaps(tmp_home):
    from autocut import db
    from autocut.timeline import normalize

    db.put_media("h", Path("x.mov"), 1, {"duration": 30.0, "display_width": 1920, "display_height": 1080, "fps": 30.0}, None, None)
    db.cache_put("h", "transcript", "2:x:auto", {"language": "en", "segments": [], "sentences": [],
                 "words": [{"word": f" w{i}", "start": i * 1.0, "end": i * 1.0 + 0.6, "prob": .9} for i in range(30)]})
    d = _doc()
    d["clips"][0]["end"] = 13.3                           # inside the word 13.0–13.6
    d["texts"].append({"id": "x", "clip": "gone", "offset": 0, "dur": 1, "text": "orphan"})
    out = normalize(d)
    assert 13.6 <= out["clips"][0]["end"] <= 14.0          # moved to the gap after the word
    assert all(t["clip"] in ("a", "b") for t in out["texts"])


def test_write_ass_positions_and_styles(tmp_path):
    from autocut.timeline import write_ass

    d = _doc()
    n = write_ass(d, tmp_path / "t.ass")
    text = (tmp_path / "t.ass").read_text()
    assert n == 2 and "\\pos(576,216)" in text and "title" in text and "\\fs86" in text


def test_effect_switches_follow_references_and_instruction():
    from autocut.options import RenderOptions
    from autocut.style import StyleProfile
    from autocut.timeline import _effect_switches

    fx = StyleProfile(name="fx", effects={"sfx_per_minute": 20, "stickers_per_minute": 6, "flash_rate": .5,
                                         "zooms_per_minute": 0, "shakes_per_minute": 0})
    clean = StyleProfile(name="clean", effects={"sfx_per_minute": 0})
    assert _effect_switches(fx, RenderOptions()) == {"sfx": True, "stickers": True, "flash": True, "zoom": False, "shake": False}
    assert not any(_effect_switches(clean, RenderOptions()).values())
    assert _effect_switches(clean, RenderOptions(sound_effects=True))["sfx"] is True       # "add sound effects"
    assert _effect_switches(fx, RenderOptions(stickers=False))["stickers"] is False         # "no stickers"
