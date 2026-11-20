"""Stage: export the edit to professional editors.

- FCPXML 1.8  → Final Cut Pro, DaVinci Resolve (1.8: widest import support, file path on the asset)
- CMX3600 EDL → Premiere Pro, Resolve, Avid, anything
- FCP7 XML (xmeml v5) → Premiere Pro's most reliable timeline import
- SRT captions → all of them (import as a caption/subtitle track)

All formats reference the ORIGINAL files, at frame-accurate in/out points (same snapping as the
renderer). Zoom punch-ins, crops and the colour LUT are render-time effects and are not exported;
each clip is one event so you can finish it your way.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import db


@dataclass
class Event:
    index: int
    hash: str
    path: Path
    name: str
    src_in: int        # frames on the source
    src_out: int
    rec_in: int        # frames on the record (edit) timeline
    rec_out: int
    src_frames: int    # source length in frames
    width: int
    height: int
    has_audio: bool


def _rate(fps: float) -> tuple[int, int, bool]:
    """(timebase, frame_duration numerator/denominator, ntsc?) — e.g. 29.97 → (30, 1001/30000, True)."""
    ntsc = abs(fps - round(fps)) > 0.01
    tb = int(round(fps))
    return tb, (1001, tb * 1000) if ntsc else (1, tb), ntsc


def timeline(sel: dict) -> tuple[list[Event], float]:
    clips = sel["clips"]
    if not clips:
        raise ValueError("The edit has no clips.")
    first = db.get_media(clips[0]["hash"])["meta"]
    fps = float(first.get("fps") or 30.0)
    events, rec = [], 0
    for i, c in enumerate(clips, 1):
        m = db.get_media(c["hash"])
        meta = m["meta"]
        sin = int(round(c["start"] * fps))
        n = max(1, int(round((c["end"] - c["start"]) * fps)))
        events.append(Event(i, c["hash"], Path(m["path"]), Path(m["path"]).name, sin, sin + n, rec, rec + n,
                            int(round(meta["duration"] * fps)), meta["display_width"],
                            meta["display_height"], bool(meta.get("has_audio"))))
        rec += n
    return events, fps


def _tc(frames: int, tb: int) -> str:
    h, rem = divmod(frames, tb * 3600)
    m, rem = divmod(rem, tb * 60)
    s, f = divmod(rem, tb)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


# ------------------------------------------------------------------ EDL

def to_edl(sel: dict, title: str = "autocut") -> str:
    events, fps = timeline(sel)
    tb, _, ntsc = _rate(fps)
    start = tb * 3600  # record timeline starts at 01:00:00:00, the usual convention
    lines = [f"TITLE: {title[:60]}", f"FCM: NON-DROP FRAME", ""]
    for e in events:
        reel = "AX"
        track = "AA/V" if e.has_audio else "V"
        lines.append(f"{e.index:03d}  {reel:<8} {track:<5} C        {_tc(e.src_in, tb)} {_tc(e.src_out, tb)} "
                     f"{_tc(start + e.rec_in, tb)} {_tc(start + e.rec_out, tb)}")
        lines.append(f"* FROM CLIP NAME: {e.name}")
        lines.append(f"* SOURCE FILE: {e.path}")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ FCPXML 1.8

def _rt(frames: int, num: int, den: int) -> str:
    f = Fraction(frames * num, den)
    return f"{f.numerator}/{f.denominator}s" if f.denominator != 1 else f"{f.numerator}s"


def to_fcpxml(sel: dict, title: str = "autocut") -> str:
    events, fps = timeline(sel)
    tb, (num, den), ntsc = _rate(fps)
    W, H = events[0].width, events[0].height
    assets, seen = [], {}
    for e in events:
        if e.hash in seen:
            continue
        aid = f"r{len(seen) + 2}"
        seen[e.hash] = aid
        assets.append(
            f'    <asset id="{aid}" name={quoteattr(e.path.stem)} uid="{e.hash[:32].upper()}" '
            f'src={quoteattr(e.path.resolve().as_uri())} start="0s" '
            f'duration="{_rt(e.src_frames, num, den)}" hasVideo="1" format="r1"'
            + (f' hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"' if e.has_audio else "")
            + "/>")
    total = events[-1].rec_out
    clips = "\n".join(
        f'          <asset-clip ref="{seen[e.hash]}" offset="{_rt(e.rec_in, num, den)}" name={quoteattr(e.path.stem)} '
        f'start="{_rt(e.src_in, num, den)}" duration="{_rt(e.rec_out - e.rec_in, num, den)}" tcFormat="NDF"'
        + (' audioRole="dialogue"' if e.has_audio else "") + "/>"
        for e in events)
    fmt_name = f"FFVideoFormat{H}p{tb if not ntsc else str(round(fps, 2)).replace('.', '')}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
  <resources>
    <format id="r1" name="{fmt_name}" frameDuration="{_rt(1, num, den)}" width="{W}" height="{H}" colorSpace="1-1-1 (Rec. 709)"/>
{chr(10).join(assets)}
  </resources>
  <library>
    <event name="autocut">
      <project name={quoteattr(title)}>
        <sequence format="r1" duration="{_rt(total, num, den)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
{clips}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


# ------------------------------------------------------------------ FCP7 XML (Premiere)

def to_xmeml(sel: dict, title: str = "autocut") -> str:
    events, fps = timeline(sel)
    tb, _, ntsc = _rate(fps)
    ntsc_s = "TRUE" if ntsc else "FALSE"
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc_s}</ntsc></rate>"
    W, H = events[0].width, events[0].height
    defined: set[str] = set()

    def file_el(e: Event) -> str:
        fid = f"file-{e.hash[:12]}"
        if fid in defined:
            return f'<file id="{fid}"/>'
        defined.add(fid)
        audio = (f"<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>"
                 f"</samplecharacteristics><channelcount>2</channelcount></audio>") if e.has_audio else ""
        return (f'<file id="{fid}"><name>{escape(e.name)}</name><pathurl>{escape(e.path.resolve().as_uri())}</pathurl>'
                f"{rate}<duration>{e.src_frames}</duration><timecode>{rate}<string>00:00:00:00</string>"
                f"<frame>0</frame><displayformat>NDF</displayformat></timecode>"
                f"<media><video><samplecharacteristics><width>{e.width}</width><height>{e.height}</height>"
                f"</samplecharacteristics></video>{audio}</media></file>")

    def clipitem(e: Event, kind: str, track: int = 1) -> str:
        cid = f"clipitem-{kind}{track}-{e.index}"
        link = ""
        if e.has_audio:
            link = "".join(
                f"<link><linkclipref>clipitem-{k}{t}-{e.index}</linkclipref><mediatype>{'video' if k == 'v' else 'audio'}</mediatype>"
                f"<trackindex>{t}</trackindex><clipindex>{e.index}</clipindex></link>"
                for k, t in (("v", 1), ("a", 1), ("a", 2)))
        st = "" if kind == "v" else f"<sourcetrack><mediatype>audio</mediatype><trackindex>{track}</trackindex></sourcetrack>"
        return (f'<clipitem id="{cid}"><name>{escape(e.name)}</name><enabled>TRUE</enabled>'
                f"<duration>{e.src_frames}</duration>{rate}<start>{e.rec_in}</start><end>{e.rec_out}</end>"
                f"<in>{e.src_in}</in><out>{e.src_out}</out>{file_el(e)}{st}{link}</clipitem>")

    vtrack = "".join(clipitem(e, "v") for e in events)
    atracks = "".join(f"<track>{''.join(clipitem(e, 'a', t) for e in events if e.has_audio)}</track>" for t in (1, 2))
    total = events[-1].rec_out
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence id="sequence-1">
    <name>{escape(title)}</name>
    <duration>{total}</duration>
    {rate}
    <timecode>{rate}<string>01:00:00:00</string><frame>{tb * 3600}</frame><displayformat>NDF</displayformat></timecode>
    <media>
      <video>
        <format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
        <track>{vtrack}</track>
      </video>
      <audio>{atracks}</audio>
    </media>
  </sequence>
</xmeml>
"""


# ------------------------------------------------------------------ SRT

def to_srt(sel: dict) -> str:
    import tempfile

    from .options import RenderOptions
    from .stylematch import build_captions, build_segments

    events, fps = timeline(sel)
    opts = RenderOptions(**{**sel.get("options", {}), "punch_ins": False})
    segs, _ = build_segments(sel, None, opts)
    lines: list[tuple[float, float, str]] = []
    with tempfile.TemporaryDirectory() as d:
        build_captions(segs, fps, (events[0].width, events[0].height), None, Path(d) / "c.ass",
                       options=opts, collect=lines)

    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    return "\n".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(lines, 1))


FORMATS = {"fcpxml": (".fcpxml", "Final Cut Pro / DaVinci Resolve"),
           "edl": (".edl", "Premiere / Resolve / any editor"),
           "premiere": (".xml", "Premiere Pro (Final Cut 7 XML)"),
           "srt": (".srt", "captions")}


def render_format(sel: dict, fmt: str, title: str) -> str:
    return {"fcpxml": lambda: to_fcpxml(sel, title), "edl": lambda: to_edl(sel, title),
            "premiere": lambda: to_xmeml(sel, title), "srt": lambda: to_srt(sel)}[fmt]()


README = """autocut export
==============
{title}

  {name}.fcpxml  → Final Cut Pro (File ▸ Import ▸ XML) or DaVinci Resolve (File ▸ Import ▸ Timeline)
  {name}.xml     → Premiere Pro (File ▸ Import) — Final Cut 7 XML, the most reliable for Premiere
  {name}.edl     → any editor (Premiere: File ▸ Import; Resolve: File ▸ Import ▸ Timeline)
  {name}.srt     → captions: import as a subtitle/caption track

The timelines point at your original video files:
{files}
If you open them on another computer, relink to the copies there.
Zoom cuts, crops and colour from autocut's render are not included — each clip is one cut.
"""


def export_zip(sel: dict, title: str, name: str = "autocut_edit") -> bytes:
    events, _ = timeline(sel)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fmt, (ext, _) in FORMATS.items():
            z.writestr(f"{name}{ext}", render_format(sel, fmt, title))
        files = "\n".join(f"  {p}" for p in dict.fromkeys(str(e.path) for e in events))
        z.writestr("README.txt", README.format(title=title, name=name, files=files))
    return buf.getvalue()


def export_all(sel: dict, out_dir: Path, name: str, title: str | None = None) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    title = title or name
    paths = []
    for fmt, (ext, _) in FORMATS.items():
        p = out_dir / f"{name}{ext}"
        p.write_text(render_format(sel, fmt, title), encoding="utf-8")
        paths.append(p)
    return paths
