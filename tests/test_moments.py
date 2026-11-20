from autocut.moments import confidence_label, top_moments


def test_top_moments_non_max_suppression():
    s = [0.0] * 30
    s[5], s[6], s[20] = 0.9, 0.8, 0.7
    m = top_moments(s, k=5, min_gap=4, smooth_width=1)
    ts = [x["t"] for x in m]
    assert ts[0] == 5 and 20 in ts and 6 not in ts


def test_confidence_labels():
    assert confidence_label("laughter", 0.5) == "clear"
    assert confidence_label("laughter", 0.15) == "possible"
    assert confidence_label("laughter", 0.02) == "faint"
    assert confidence_label("energy", 0.95) == "high"
    assert confidence_label("energy", 0.6) == "weak"
