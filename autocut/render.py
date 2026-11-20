"""Stage: render a timeline of clips from the ORIGINAL files.

1. Each clip is cut frame-accurately from its original (seek + exact frame count), normalised the
   same way as proxies (upright, HDR→SDR, constant frame rate) plus optional per-clip filters
   (crop / colour from style matching), into a near-lossless intermediate.
2. Audio of each clip is extracted with a short tail taken from the silence after the cut, and
   consecutive clips are joined with equal-power crossfades. The tail is limited so it never reaches
   the next word (never cut mid-word), and the total audio length equals the video length exactly.
3. The joined audio is loudness-normalised to -14 LUFS (two-pass EBU R128 loudnorm), muxed with the
   concatenated video, optional burned-in subtitles (ASS) are applied, and H.264/AAC MP4 is written.
   A lightweight 540p preview for phone streaming is written next to it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import db
from .config import get_settings
from .ffmpeg import run_ffmpeg
from .media import AUDIO_SYNC_FILTER, normalize_video_filters

TARGET_LUFS = -14.0
TRUE_PEAK = -1.0
MAX_XFADE = 0.08      # seconds of audio crossfade between clips
MIN_XFADE = 0.01
AUDIO_RATE = 48000


@dataclass
class TimelineClip:
    hash: str
    start: float
    end: float
    video_filters: list[str] | None = None   # extra filters after normalisation (crop, colour...)
    xfade_room: float | None = None          # silence available after `end` (computed if None)


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


def frame_snap(t: float, fps: float) -> float:
    return round(t * fps) / fps


def silence_after(file_hash: str, t: float) -> float:
    """Seconds of word-free audio after time t in this file (from the cached transcript)."""
    from .transcribe import get_transcript

    tr = get_transcript(file_hash)
    media = db.get_media(file_hash)
    dur = media["meta"]["duration"]
    if not tr or not tr["words"]:
        return dur - t
    nxt = [w["start"] for w in tr["words"] if w["start"] >= t - 1e-3]
    return max(0.0, (min(nxt) if nxt else dur) - t - 0.01)


def _loudnorm_measure(wav: Path, target: float = TARGET_LUFS) -> dict:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(wav), "-af",
                        f"loudnorm=I={target}:TP={TRUE_PEAK}:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    if not m:
        raise RuntimeError("loudnorm measurement failed:\n" + p.stderr[-1500:])
    return json.loads(m.group(0))


def measure_lufs(path: Path) -> float:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:a:0",
                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    return float(m[-1]) if m else float("nan")


def render_timeline(clips: list[TimelineClip], out: Path, *, fps: float | None = None,
                    size: tuple[int, int] | None = None, subtitles: Path | None = None,
                    preview: bool = True, crf: int = 18, fontsdir: Path | None = None,
                    loudness: float = TARGET_LUFS,
                    progress: Callable[[str, float], None] | None = None) -> dict:
    if not clips:
        raise ValueError("Nothing to render: the timeline is empty.")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    medias = [db.get_media(c.hash) for c in clips]
    first = medias[0]["meta"]
    fps = fps or first.get("fps") or 30.0
    if size is None:
        size = (first["display_width"], first["display_height"])
    W, H = _even(size[0]), _even(size[1])
    work = get_settings().home / "renders" / uuid.uuid4().hex[:12]
    work.mkdir(parents=True, exist_ok=True)

    def prog(stage: str, x: float) -> None:
        if progress:
            progress(stage, x)

    try:
        seg_videos, seg_audios, frames, xfades = [], [], [], []
        n = len(clips)
        for i, (c, m) in enumerate(zip(clips, medias)):
            prog(f"cutting clip {i + 1}/{n}", i / n * 0.7)
            src = Path(m["path"])
            meta = m["meta"]
            s = frame_snap(c.start, fps)
            nf = max(1, int(round((c.end - c.start) * fps)))
            frames.append(nf)
            dur = nf / fps
            vf = normalize_video_filters(meta, fps=fps)
            vf += list(c.video_filters or [])
            # fit into the output frame (scale + pad only if the clip's shape differs)
            vf += [f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos",
                   f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1", "format=yuv420p"]
            v_out = work / f"v{i:03d}.mkv"
            run_ffmpeg(["-ss", f"{s:.6f}", "-i", str(src), "-map", "0:v:0", "-vf", ",".join(vf),
                        "-frames:v", str(nf), "-fps_mode", "cfr", "-an",
                        "-c:v", "libx264", "-preset", "superfast", "-crf", "10",
                        "-pix_fmt", "yuv420p", str(v_out)])
            seg_videos.append(v_out)

            # audio: clip + tail (for the crossfade into the next clip)
            room = c.xfade_room if c.xfade_room is not None else silence_after(c.hash, c.end)
            xf = 0.0 if i == n - 1 else max(0.0, min(MAX_XFADE, room))
            xfades.append(xf)
            a_out = work / f"a{i:03d}.wav"
            if meta.get("has_audio"):
                run_ffmpeg(["-ss", f"{s:.6f}", "-i", str(src), "-map", "0:a:0", "-vn",
                            "-af", AUDIO_SYNC_FILTER + f",aformat=channel_layouts=stereo",
                            "-ar", str(AUDIO_RATE), "-ac", "2", "-t", f"{dur + xf:.6f}",
                            "-c:a", "pcm_s16le", str(a_out)])
            else:
                run_ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo",
                            "-t", f"{dur + xf:.6f}", "-c:a", "pcm_s16le", str(a_out)])
            seg_audios.append(a_out)

        # ---- join audio with crossfades (equal power); total = sum of clip durations
        prog("mixing audio", 0.72)
        joined = work / "audio_joined.wav"
        if n == 1:
            shutil.copy(seg_audios[0], joined)
        else:
            inputs, graph = [], []
            for a in seg_audios:
                inputs += ["-i", str(a)]
            # pad each segment to its exact length so rounding never accumulates drift
            for i in range(n):
                L = frames[i] / fps + xfades[i]
                graph.append(f"[{i}:a]apad,atrim=0:{L:.6f},asetpts=PTS-STARTPTS[s{i}]")
            prev = "s0"
            for i in range(1, n):
                d = xfades[i - 1]
                lab = f"x{i}"
                if d >= MIN_XFADE:
                    graph.append(f"[{prev}][s{i}]acrossfade=d={d:.4f}:c1=qsin:c2=qsin[{lab}]")
                else:
                    graph.append(f"[{prev}][s{i}]concat=n=2:v=0:a=1[{lab}]")
                prev = lab
            run_ffmpeg(inputs + ["-filter_complex", ";".join(graph), "-map", f"[{prev}]",
                                 "-c:a", "pcm_s16le", str(joined)])

        # ---- loudness: two-pass loudnorm to -14 LUFS
        prog("normalising loudness", 0.78)
        meas = _loudnorm_measure(joined, loudness)
        loud = work / "audio_loud.wav"
        ln = (f"loudnorm=I={loudness}:TP={TRUE_PEAK}:LRA=11:measured_I={meas['input_i']}:"
              f"measured_TP={meas['input_tp']}:measured_LRA={meas['input_lra']}:"
              f"measured_thresh={meas['input_thresh']}:offset={meas['target_offset']}:linear=true")
        run_ffmpeg(["-i", str(joined), "-af", ln + f",aresample={AUDIO_RATE}", "-ar", str(AUDIO_RATE),
                    "-c:a", "pcm_s16le", str(loud)])
        # loudnorm can fall short when very quiet audio would exceed the peak limit; finish with
        # a gain correction into a look-ahead true-peak limiter (latency-compensated: no A/V shift)
        got = measure_lufs(loud)
        if abs(got - loudness) > 0.3:
            fixed = work / "audio_fixed.wav"
            lim = 10 ** ((TRUE_PEAK - 0.5) / 20)
            run_ffmpeg(["-i", str(loud), "-af",
                        f"volume={loudness - got + 0.4:.2f}dB,"
                        f"alimiter=limit={lim:.4f}:attack=5:release=60:latency=1:level=disabled",
                        "-ar", str(AUDIO_RATE), "-c:a", "pcm_s16le", str(fixed)])
            loud = fixed

        # ---- concat video, burn subtitles, encode final
        prog("encoding final video", 0.82)
        lst = work / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_videos))
        total = sum(frames) / fps
        vf_final = ["setpts=PTS-STARTPTS"]
        if subtitles:
            def esc(p: Path) -> str:
                return str(p).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
            vf_final.append(f"subtitles='{esc(subtitles)}'" +
                            (f":fontsdir='{esc(fontsdir)}'" if fontsdir and fontsdir.exists() else ""))
        tmp_out = out.with_suffix(".part.mp4")
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(loud),
                    "-map", "0:v:0", "-map", "1:a:0", "-vf", ",".join(vf_final),
                    "-c:v", "libx264", "-preset", "fast", "-crf", str(crf), "-pix_fmt", "yuv420p",
                    "-profile:v", "high", "-r", f"{fps}", "-fps_mode", "cfr",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                    "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE),
                    "-t", f"{total:.6f}", "-movflags", "+faststart", str(tmp_out)],
                   duration=total, progress=lambda f: prog("encoding final video", 0.82 + 0.13 * f))
        tmp_out.replace(out)

        prev_path = None
        if preview:
            prog("making phone preview", 0.96)
            prev_path = out.with_name(out.stem + ".preview.mp4")
            ph = 540
            run_ffmpeg(["-i", str(out), "-vf", f"scale='if(gt(iw,ih),-2,{ph})':'if(gt(iw,ih),{ph},-2)'",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-maxrate", "1500k",
                        "-bufsize", "3000k", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                        str(prev_path)])
        prog("done", 1.0)
        return {"output": str(out), "preview": str(prev_path) if prev_path else None,
                "duration": round(total, 3), "frames": sum(frames), "fps": fps, "size": [W, H],
                "clip_frames": frames, "crossfades": [round(x, 3) for x in xfades[:-1]],
                "loudness_in": float(meas["input_i"])}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def clips_from_selection(sel: dict) -> list[TimelineClip]:
    return [TimelineClip(c["hash"], float(c["start"]), float(c["end"])) for c in sel["clips"]]
