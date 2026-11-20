import re

import numpy as np

from autocut.stylematch import (Segment, build_captions, crop_box, make_color_lut, piecewise_expr,
                                plan_punch_ins, smooth_camera)


def test_crop_box_vertical_from_landscape():
    assert crop_box(1920, 1080, 9 / 16, 1.0) == (606, 1080)
    w, h = crop_box(1920, 1080, 9 / 16, 1.15)
    assert h < 1080 and abs(w / h - 9 / 16) < 0.01
    assert crop_box(1920, 1080, 16 / 9, 1.0) == (1920, 1080)


def test_camera_ignores_jitter_but_follows_big_moves():
    t = np.arange(0, 8, 0.25)
    rng = np.random.default_rng(1)
    path = 0.5 + rng.normal(0, 0.01, len(t))            # face jitters around the centre
    path[t >= 4] += 0.25                                  # then moves right
    cam = smooth_camera(t, path, deadzone=0.06)
    assert np.std(cam[(t > 0.5) & (t < 4)]) < 0.005       # steady while jittering
    assert cam[-1] > 0.7                                  # caught up after the move
    assert np.max(np.abs(np.diff(cam))) < 0.12            # no jumps (eased)


def test_piecewise_expr():
    assert piecewise_expr([(0, 100.0), (2, 100.2)]) == "100.0"
    e = piecewise_expr([(0, 0.0), (1, 10.0), (2, 10.0)])
    assert e == "if(lt(t,0),0.0,if(lt(t,1),0.0+(10.0)*(t-0)/1.000,if(lt(t,2),10.0+(0.0)*(t-1)/1.000,10.0)))"


def test_identity_lut_when_looks_match(tmp_path):
    p = make_color_lut([50, 0, 5], [20, 6, 7], [50, 0, 5], [20, 6, 7], tmp_path / "x.cube", size=9)
    rows = [list(map(float, l.split())) for l in p.read_text().splitlines()[2:]]
    g = np.linspace(0, 1, 9)
    expected = [[r, gg, b] for b in g for gg in g for r in g]  # red fastest
    assert np.abs(np.array(rows) - np.array(expected)).max() < 0.02


class _FA:
    def __init__(self, zones):
        self._z = zones

    def safe_zones(self):
        return self._z


def test_punch_ins_only_at_word_gaps():
    zones = [(0, 0.2), (2.1, 2.3), (4.0, 4.4), (6.5, 6.6), (9.9, 10.0)]
    pts = plan_punch_ins(_FA(zones), 0.0, 10.0, 2.2)
    assert pts and all(any(lo <= p <= hi for lo, hi in zones) for p in pts)
    assert all(b - a >= 1.2 for a, b in zip([0.0] + pts, pts + [10.0]))


def test_captions_word_timed(tmp_home, tmp_path):
    from autocut import db

    words = [(" Is", 1.0, 1.2), (" that", 1.2, 1.4), (" chili", 1.4, 1.8), (" oil?", 1.8, 2.2),
             (" Yeah,", 2.6, 2.9), (" it", 2.9, 3.0), (" is.", 3.0, 3.4)]
    db.cache_put("fakehash", "transcript", "2:x:auto",
                 {"words": [{"word": w, "start": a, "end": b, "prob": 0.9} for w, a, b in words]})
    segs = [Segment("fakehash", 0.5, 2.4, 0, continuous_with_next=True), Segment("fakehash", 2.4, 4.0, 0)]
    out = tmp_path / "c.ass"
    n = build_captions(segs, 30.0, (1080, 1920), None, out)
    text = out.read_text()
    assert n == 2 and "PlayResY: 1920" in text
    lines = [l for l in text.splitlines() if l.startswith("Dialogue")]
    assert re.sub(r"\{[^}]*\}", "", lines[0].split(",", 9)[9]) == "Is that chili oil?"
    assert "\\kf" not in lines[0]                  # default: plain line, no word highlight
    assert lines[0].split(",")[1] == "0:00:00.50"   # 1.0 s source → 0.5 s on the output timeline
    from autocut.options import RenderOptions

    build_captions(segs, 30.0, (1080, 1920), None, out,
                   options=RenderOptions(caption_style="karaoke", caption_case="lower"))
    k = [l for l in out.read_text().splitlines() if l.startswith("Dialogue")][0]
    assert "\\kf" in k and "is that chili oil?" in re.sub(r"\{[^}]*\}", "", k)
