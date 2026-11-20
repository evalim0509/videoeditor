import subprocess

import cv2
import numpy as np

from autocut.style import (StyleProfile, build_profile, detect_crop, explain, nearest_aspect,
                           persistent, text_boxes)


def test_nearest_aspect():
    assert nearest_aspect(1920, 1080) == "16:9"
    assert nearest_aspect(1080, 1920) == "9:16"
    assert nearest_aspect(1080, 1350) == "4:5"
    assert nearest_aspect(1000, 1000) == "1:1"


def test_text_boxes_finds_caption_not_texture():
    rng = np.random.default_rng(0)
    frame = (rng.normal(90, 25, (480, 854, 3))).clip(0, 255).astype(np.uint8)
    frame = cv2.GaussianBlur(frame, (9, 9), 0)
    with_text = frame.copy()
    cv2.putText(with_text, "i felt like i was in that one scene", (200, 440),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    boxes = text_boxes(with_text)
    assert boxes and all(400 < y + h / 2 < 460 for (_, y, _, h) in boxes)
    assert text_boxes(frame) == []


def test_persistence_filter():
    a = (10, 10, 100, 12)
    assert persistent([[a], [a], []]) == [[a], [a], []]
    assert persistent([[a], [], [(300, 300, 100, 12)]]) == [[], [], []]


def test_detect_crop_with_blurred_coloured_bars(tmp_path):
    """Screen recording of a 16:9 video with YouTube-style blurred 'ambient' side bars."""
    src = tmp_path / "rec.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=854x480:r=10", "-t", "3",
        "-filter_complex",
        "[0:v]noise=alls=30:allf=t,split[a][b];[b]scale=1044:480,boxblur=40:5,eq=brightness=-0.3[bg];"
        "[bg][a]overlay=95:0", str(src)], check=True)
    crop = detect_crop(src, 3.0, 1044, 480)
    assert crop is not None
    w, h, x, y = crop
    assert abs(w / h - 16 / 9) < 0.02 and abs(x - 95) <= 4


def test_profile_and_explanation():
    ref = {"name": "r.mp4", "duration": 60.0, "shot_lengths": [1.0] * 40 + [3.0] * 10,
           "cuts": list(range(49)), "aspect_ratio": "9:16", "beat_on": 0.8, "beat_chance": 0.3,
           "tempo": 120.0, "music_share": 0.9, "speech_share": 0.2, "loudness_lufs": -14.0,
           "frames": 100, "text_frames": 60, "text_y": [0.8] * 60, "text_h": [0.05] * 60,
           "brightness": 0.6, "contrast": 0.3, "saturation": 0.5,
           "lab_mean": [60, 2, 10], "lab_std": [20, 5, 8]}
    p = build_profile("t", [ref])
    assert isinstance(p, StyleProfile)
    assert p.aspect_ratio == "9:16" and p.orientation == "portrait"
    assert p.beat_synced and p.captions_present and p.caption_position == "bottom"
    text = " ".join(explain(p))
    assert "beat" in text and "9:16" in text and "bottom" in text
