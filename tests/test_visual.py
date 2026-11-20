import subprocess

import pytest

from conftest import real_footage


def test_iter_frames_count_and_shape(tmp_path):
    from autocut.visual import iter_frames

    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30",
                    "-t", "3", str(clip)], check=True)
    frames = list(iter_frames(clip, 1.0, 160, 90, offset=0.5))
    assert len(frames) == 3
    assert frames[0][1].shape == (90, 160, 3)
    assert abs(frames[1][0] - 1.5) < 1e-6


@pytest.mark.skipif(not real_footage(), reason="no test footage")
def test_search_and_faces_on_real_footage():
    """Uses the real (already cached) analyses of the first test clip."""
    from autocut import db
    from autocut.ingest import ingest_file
    from autocut.visual import detect_faces, search

    r = ingest_file(real_footage()[0])
    faces = detect_faces(r.hash)
    secs = faces["per_second"]["count"]
    assert sum(1 for c in secs if c) > 0.5 * len(secs)  # selfie footage: face most of the time
    res = search("food", [r.hash], top=3)
    assert len(res) == 3 and all(0 <= x["t"] <= r.meta["duration"] for x in res)
