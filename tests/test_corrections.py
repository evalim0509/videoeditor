"""Transcript corrections: manual edits, find & replace, effective transcript, undo; options."""
from autocut.editspec import EditSpec
from autocut.options import RenderOptions


def _setup(db):
    words = [(" Yeah,", 1.0, 1.3), (" it", 1.3, 1.4), (" is", 1.4, 1.6), (" Ywansuk.", 1.6, 2.4),
             (" Great.", 3.0, 3.4)]
    db.put_media("h1", __import__("pathlib").Path("x.mov"), 1, {"duration": 5.0}, None, None)
    db.cache_put("h1", "transcript", "2:x:auto", {
        "language": "en", "words": [{"word": w, "start": a, "end": b, "prob": 0.5} for w, a, b in words],
        "sentences": [], "segments": []})


def test_manual_correction_keeps_time_span(tmp_home):
    from autocut import corrections as C, db

    _setup(db)
    e = C.correct_text("h1", 1.55, 2.5, "you want some?")
    ws = C.words_in("h1", 0, 5)
    text = "".join(w["word"] for w in ws).strip()
    assert text == "Yeah, it is you want some? Great."
    new = [w for w in ws if w["prob"] == 1.0]
    assert new[0]["start"] >= 1.6 - 1e-6 and new[-1]["end"] <= 2.4 + 1e-6   # same span → cuts stay safe
    assert C.effective_transcript("h1")["edited"] is True
    assert C.clear_edits("h1", e["id"]) == 1
    assert "Ywansuk" in "".join(w["word"] for w in C.words_in("h1", 0, 5))


def test_find_replace_fuzzy(tmp_home):
    from autocut import corrections as C, db

    _setup(db)
    assert C.find_replace("h1", "it is Ywansuk", "it is. Do you want some?")
    assert "Do you want some?" in "".join(w["word"] for w in C.words_in("h1", 0, 5))
    assert C.find_replace("h1", "completely different words here", "x") is None


def test_render_options_defaults_and_spec():
    o = RenderOptions()
    assert o.aspect is None and o.caption_style == "static" and o.color_match is False
    o2 = RenderOptions.from_spec(EditSpec(aspect_ratio="9:16", captions=True))
    assert o2.aspect == "9:16" and o2.captions is True
