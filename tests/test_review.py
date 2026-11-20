"""Review operations: remove/restore/swap/move/add/trim (word-safe), decision log."""
import pytest


@pytest.fixture()
def sel(tmp_home):
    from pathlib import Path

    from autocut import db

    words = [(f" w{i}", i * 1.0, i * 1.0 + 0.6) for i in range(40)]  # word every second, 0.4 s gaps
    db.put_media("hh", Path("x.mov"), 1, {"duration": 40.0}, None, None)
    db.cache_put("hh", "transcript", "2:x:auto", {"language": "en", "segments": [], "sentences": [],
                 "words": [{"word": w, "start": a, "end": b, "prob": 0.9} for w, a, b in words]})
    c = lambda i, a, b: {"id": i, "hash": "hh", "file": "x.mov", "start": a, "end": b, "text": "", "reason": ""}
    return {"spec": {"mood": "funny", "source_text": "funny"}, "clips": [c("c1", 2.7, 6.7), c("c2", 10.7, 14.7), c("c3", 20.7, 24.7)],
            "runner_ups": [c("r1", 30.7, 34.7), c("r2", 12.7, 16.7)], "removed": []}


def test_remove_restore_move_add_swap(sel):
    from autocut import db
    from autocut.review import ReviewError, apply

    apply(sel, {"op": "remove", "clip_id": "c2"})
    assert [c["id"] for c in sel["clips"]] == ["c1", "c3"] and sel["removed"][0]["id"] == "c2"
    apply(sel, {"op": "restore", "clip_id": "c2"})
    assert [c["id"] for c in sel["clips"]] == ["c1", "c3", "c2"]   # restored at the end
    apply(sel, {"op": "move", "clip_id": "c2", "dir": -1})
    assert [c["id"] for c in sel["clips"]] == ["c1", "c2", "c3"]
    with pytest.raises(ReviewError):
        apply(sel, {"op": "add", "runner_id": "r2"})          # overlaps c2
    apply(sel, {"op": "swap", "clip_id": "c1"})               # next best non-overlapping = r1
    assert sel["clips"][0]["id"] == "r1" and sel["runner_ups"][0]["id"] == "c1"
    decisions = [r["decision"] for r in db.feedback_rows()]
    assert {"remove", "restore", "swap_in", "swap_out"} <= set(decisions)


def test_trim_snaps_to_word_gaps_and_respects_neighbours(sel):
    from autocut.review import apply

    apply(sel, {"op": "trim", "clip_id": "c1", "edge": "end", "delta": 0.5})
    end = sel["clips"][0]["end"]
    assert 7.6 <= end <= 8.0          # in the gap after the word 7.0–7.6, never inside a word
    apply(sel, {"op": "trim", "clip_id": "c1", "edge": "start", "delta": -0.5})
    st = sel["clips"][0]["start"]
    assert 1.6 <= st <= 2.0
    for _ in range(10):               # can't grow into the next clip (c2 starts at 10.7)
        try:
            apply(sel, {"op": "trim", "clip_id": "c1", "edge": "end", "delta": 0.5})
        except Exception:
            break
    assert sel["clips"][0]["end"] <= 10.7
