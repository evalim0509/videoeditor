from types import SimpleNamespace

from autocut.transcribe import _drop_hallucinations, _sentences


def W(word, start, end):
    return {"word": word, "start": start, "end": end, "prob": 0.9}


def test_sentences_split_on_punctuation_and_pauses():
    words = [W(" Hello", 0.0, 0.4), W(" there.", 0.4, 0.8), W(" How", 1.0, 1.2),
             W(" are", 1.2, 1.4), W(" you", 1.4, 1.6), W(" doing", 3.0, 3.4), W(" today?", 3.4, 3.9)]
    s = _sentences(words)
    assert [x["text"] for x in s] == ["Hello there.", "How are you", "doing today?"]
    assert s[1]["first_word"] == 2 and s[1]["last_word"] == 4
    assert s[0]["start"] == 0.0 and s[0]["end"] == 0.8


def test_long_runon_split_at_biggest_pause():
    words = [W(f" w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(20)]
    words += [W(f" x{i}", 10.6 + i * 0.5, 10.6 + i * 0.5 + 0.4) for i in range(20)]
    for w in words:  # small gaps < PAUSE_SPLIT everywhere, bigger one at index 19/20
        pass
    s = _sentences(words)
    assert all(x["end"] - x["start"] <= 15.0 for x in s)
    assert sum(x["last_word"] - x["first_word"] + 1 for x in s) == len(words)


def _seg(text, lp=-0.5, cr=1.2, nsp=0.1):
    return SimpleNamespace(text=text, avg_logprob=lp, compression_ratio=cr, no_speech_prob=nsp)


def test_hallucination_filter():
    segs = [_seg("real line"), _seg("go go go go go", cr=3.1), _seg("junk", lp=-2.0),
            _seg("Thanks for watching", lp=-1.2, nsp=0.8),
            _seg("again"), _seg("again"), _seg("again"), _seg("end")]
    kept = [s.text for s in _drop_hallucinations(segs)]
    assert kept == ["real line", "again", "again", "end"]
