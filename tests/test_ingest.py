"""Ingest tests on a synthetic 'worst-case phone clip': HEVC 10-bit HLG HDR, rotation metadata,
variable frame rate, AAC audio in a .MOV container."""

import subprocess
from pathlib import Path

import pytest

from autocut.ffmpeg import ffprobe_json


def _make_phone_clip(path: Path) -> Path:
    raw = path.with_name("raw.mov")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30",
        "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000",
        "-t", "3",
        # drop every 7th frame but keep timestamps -> variable frame rate (like a phone)
        "-vf", "select='not(eq(mod(n\\,7)\\,3))'", "-fps_mode", "vfr",
        "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
        "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
        "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
        "-tag:v", "hvc1", "-c:a", "aac", str(raw)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(raw),
                    "-c", "copy", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def phone_clip(tmp_path_factory):
    return _make_phone_clip(tmp_path_factory.mktemp("clip") / "IMG_TEST.MOV")


def test_probe_detects_phone_quirks(phone_clip):
    from autocut.media import probe

    m = probe(phone_clip)
    assert m["video_codec"] == "hevc"
    assert m["is_hdr"] and m["hdr_format"] == "HLG"
    assert m["rotation"] in (90, 270)
    assert (m["display_width"], m["display_height"]) == (360, 640)
    assert m["orientation"] == "portrait"
    assert m["is_vfr"]
    assert m["fps"] == 30.0
    assert m["bit_depth"] == 10


def test_ingest_makes_upright_sdr_cfr_proxy_and_skips_second_time(tmp_home, phone_clip):
    from autocut.ingest import ingest_file

    r1 = ingest_file(phone_clip)
    assert not r1.skipped
    info = ffprobe_json(r1.proxy_path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    # upright portrait 480 wide, no rotation left
    assert (v["width"], v["height"]) == (480, 854)
    assert not any("rotation" in sd for sd in v.get("side_data_list", []))
    # constant frame rate, SDR BT.709 8-bit
    assert v["r_frame_rate"] == v["avg_frame_rate"] == "30/1"
    assert v["pix_fmt"] == "yuv420p"
    assert v.get("color_transfer") == "bt709"
    assert v["codec_name"] == "h264"
    # A/V durations line up with the source
    assert abs(float(v["duration"]) - r1.meta["duration"]) < 0.1
    assert abs(float(a["duration"]) - float(v["duration"])) < 0.1
    assert r1.audio_path and r1.audio_path.exists()

    r2 = ingest_file(phone_clip)
    assert r2.skipped and r2.hash == r1.hash
