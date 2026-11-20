from types import SimpleNamespace

import pytest

from conftest import real_footage


def _fa(words, duration=20.0):
    from autocut.select import FileAnalysis

    fa = FileAnalysis.__new__(FileAnalysis)
    fa.words = [{"start": a, "end": b, "word": " w", "prob": 0.9} for a, b in words]
    fa.duration = duration
    return fa


def test_safe_points_never_inside_words():
    fa = _fa([(1.0, 1.5), (1.6, 2.0), (5.0, 6.0)])
    assert not fa.is_safe(1.2) and not fa.is_safe(1.52) and fa.is_safe(1.55) and fa.is_safe(3.0)
    assert 1.5 < fa.safe_before(1.8, 0) < 1.6     # walks left into the short pause between words
    assert fa.safe_after(5.5, 20) > 6.0
    assert fa.is_safe(0.0) and fa.is_safe(20.0)  # file edges are always safe


@pytest.mark.skipif(not real_footage(), reason="no test footage")
@pytest.mark.parametrize("instruction", ["funny 60 seconds", "calm 30 second clip",
                                          "informative 45 seconds about dumplings"])
def test_selection_on_real_footage(instruction):
    from autocut.analysis import analyze_all
    from autocut.ingest import ingest_file
    from autocut.intent import parse
    from autocut.select import FileAnalysis, select_clips

    r = ingest_file(real_footage()[0])
    analyze_all(r.hash)
    spec = parse(instruction, available_presets=[])
    sel = select_clips([r.hash], spec)
    lo, hi = sel["range"]
    assert lo <= sel["total_duration"] <= hi
    clips = sel["clips"]
    fa = FileAnalysis(r.hash)
    for c in clips:
        assert 3.0 - 1e-6 <= c["duration"] <= 15.0 + 1e-6
        assert fa.is_safe(c["start"]) and fa.is_safe(c["end"]), "mid-word cut"
        assert c["reason"]
    spans = sorted((c["start"], c["end"]) for c in clips)
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(spans, spans[1:])), "overlapping clips"
    assert len(sel["runner_ups"]) > 0


@pytest.mark.skipif(not real_footage(), reason="no test footage")
def test_user_feedback_checkpoint2_funny60():
    """User removed 1:05–1:12 (garbled speech) and 2:17–2:23 (dead air) and kept the other nine."""
    from autocut.analysis import analyze_all
    from autocut.ingest import ingest_file
    from autocut.intent import parse
    from autocut.select import select_clips

    r = ingest_file(real_footage()[0])
    if abs(r.meta["duration"] - 149.4) > 0.5:
        pytest.skip("feedback applies to IMG_0875.MOV only")
    analyze_all(r.hash)
    sel = select_clips([r.hash], parse("funny 60 seconds", available_presets=[]))
    spans = [(c["start"], c["end"]) for c in sel["clips"]]

    def has(t):
        return any(a <= t <= b for a, b in spans)

    assert not has(68.0) and not has(140.0)            # removed by the user
    for t in (61, 10, 19, 24, 39, 26 + 60, 103, 110, 146):  # kept by the user
        assert has(t), f"lost a kept clip at {t}s"
