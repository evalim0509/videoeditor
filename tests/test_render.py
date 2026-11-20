import subprocess

from autocut.ffmpeg import ffprobe_json


def _clip(path, freq, vol):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30",
                    "-f", "lavfi", "-i", f"sine=f={freq}:sample_rate=48000", "-t", "6",
                    "-af", f"volume={vol}", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)
    return path


def test_render_frame_exact_av_locked_and_loud(tmp_home, tmp_path):
    from autocut.ingest import ingest_file
    from autocut.render import TimelineClip, measure_lufs, render_timeline

    a = ingest_file(_clip(tmp_path / "a.mp4", 440, 0.05))
    b = ingest_file(_clip(tmp_path / "b.mp4", 660, 0.5))
    clips = [TimelineClip(a.hash, 1.0, 3.5), TimelineClip(b.hash, 0.5, 2.0),
             TimelineClip(a.hash, 4.0, 5.2)]
    out = tmp_path / "out.mp4"
    info = render_timeline(clips, out, preview=True)
    assert info["clip_frames"] == [75, 45, 36]
    streams = ffprobe_json(out)["streams"]
    v = next(s for s in streams if s["codec_type"] == "video")
    au = next(s for s in streams if s["codec_type"] == "audio")
    assert int(v["nb_frames"]) == 156
    assert abs(float(v["duration"]) - 5.2) < 0.01
    assert abs(float(au["duration"]) - float(v["duration"])) < 0.03
    assert abs(measure_lufs(out) + 14.0) < 1.0
    assert (tmp_path / "out.preview.mp4").exists()
