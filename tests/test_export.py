"""Exports read back by OpenTimelineIO (an independent implementation) must match our cut points."""
import subprocess

import pytest


@pytest.fixture()
def sel(tmp_home, tmp_path):
    from autocut.ingest import ingest_file

    src = tmp_path / "clip.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30", "-f", "lavfi",
                    "-i", "sine=f=440:sample_rate=48000", "-t", "12", "-c:v", "libx264", "-c:a", "aac", str(src)],
                   check=True)
    h = ingest_file(src).hash
    return {"clips": [{"hash": h, "start": 1.0, "end": 3.5}, {"hash": h, "start": 6.2, "end": 9.0},
                      {"hash": h, "start": 4.0, "end": 5.0}], "options": {}}


EXPECTED = [(30, 105), (186, 270), (120, 150)]


@pytest.mark.parametrize("fmt,adapter", [("fcpxml", "fcpx_xml"), ("premiere", "fcp_xml"), ("edl", "cmx_3600")])
def test_roundtrip_with_opentimelineio(sel, tmp_path, fmt, adapter):
    otio = pytest.importorskip("opentimelineio")
    from autocut.export import FORMATS, render_format

    f = tmp_path / f"t{FORMATS[fmt][0]}"
    f.write_text(render_format(sel, fmt, "test"))
    tl = otio.adapters.read_from_file(str(f), adapter_name=adapter, **({"rate": 30} if fmt == "edl" else {}))
    if isinstance(tl, otio.schema.SerializableCollection):
        tl = tl.find_children(descended_from_type=otio.schema.Timeline)[0]
    vt = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video][0]
    got = [(round(c.source_range.start_time.to_frames(30)),
            round(c.source_range.start_time.to_frames(30) + c.source_range.duration.to_frames(30)))
           for c in vt.find_clips()]
    assert got == EXPECTED
    assert abs(tl.duration().to_seconds() - (75 + 84 + 30) / 30) < 1e-6


def test_edl_text_and_zip(sel):
    import io
    import zipfile

    from autocut.export import export_zip, to_edl

    edl = to_edl(sel, "x")
    assert "001  AX       AA/V  C        00:00:01:00 00:00:03:15 01:00:00:00 01:00:02:15" in edl
    names = zipfile.ZipFile(io.BytesIO(export_zip(sel, "x", "e"))).namelist()
    assert sorted(names) == ["README.txt", "e.edl", "e.fcpxml", "e.srt", "e.xml"]
