"""Media probing and normalisation rules shared by ingest (proxies) and render (originals).

Phone footage quirks handled here:
- HEVC/H.265 and iPhone .MOV: decoded by FFmpeg; we always re-encode to H.264.
- HDR (HLG / PQ, BT.2020): tone-mapped to SDR BT.709 with zscale+tonemap.
- Rotation metadata (display matrix): FFmpeg auto-rotates while decoding; we record the display
  size so later stages reason about the picture as the viewer sees it.
- Variable frame rate: always converted to a constant frame rate (nearest standard rate) and audio
  is resampled with async compensation so it can't drift.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from .ffmpeg import available_filters, ffprobe_json

STANDARD_RATES = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0]
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def _fraction(s: str | None) -> float:
    if not s or s in ("0/0", "N/A"):
        return 0.0
    try:
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict) -> int:
    rot = 0
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    if not rot and "rotate" in (stream.get("tags") or {}):
        rot = -int(stream["tags"]["rotate"])  # legacy tag is clockwise
    return ((rot % 360) + 360) % 360


def nearest_standard_rate(fps: float) -> float:
    if fps <= 0:
        return 30.0
    return min(STANDARD_RATES, key=lambda r: abs(r - fps))


def probe(path: Path) -> dict:
    """Return a compact, JSON-serialisable metadata dict for a media file."""
    info = ffprobe_json(path)
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    meta: dict = {
        "container": fmt.get("format_name"),
        "duration": float(fmt.get("duration") or 0.0),
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "has_video": video is not None,
        "has_audio": audio is not None,
    }
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    meta["creation_time"] = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    make, model = tags.get("com.apple.quicktime.make"), tags.get("com.apple.quicktime.model")
    meta["device"] = " ".join(x for x in (make, model) if x) or None

    if video:
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        rot = _rotation(video)
        dw, dh = (h, w) if rot in (90, 270) else (w, h)
        r_fps = _fraction(video.get("r_frame_rate"))
        avg_fps = _fraction(video.get("avg_frame_rate"))
        transfer = video.get("color_transfer") or ""
        primaries = video.get("color_primaries") or ""
        pix_fmt = video.get("pix_fmt") or ""
        is_hdr = transfer in HDR_TRANSFERS
        nominal = r_fps or avg_fps
        meta.update({
            "video_codec": video.get("codec_name"),
            "profile": video.get("profile"),
            "pix_fmt": pix_fmt,
            "bit_depth": 10 if ("10" in pix_fmt or "p010" in pix_fmt) else 8,
            "width": w, "height": h,
            "rotation": rot,
            "display_width": dw, "display_height": dh,
            "orientation": "portrait" if dh > dw else ("square" if dh == dw else "landscape"),
            "fps_nominal": round(nominal, 3),
            "fps_avg": round(avg_fps, 3),
            # Phone cameras/screen recorders produce VFR; flag anything whose average rate differs
            # from the nominal rate by more than 0.01 %.
            "is_vfr": bool(nominal and avg_fps and abs(nominal - avg_fps) / nominal > 1e-4),
            "fps": nearest_standard_rate(nominal),  # constant output rate
            "color_transfer": transfer or None,
            "color_primaries": primaries or None,
            "color_space": video.get("color_space"),
            "color_range": video.get("color_range"),
            "is_hdr": is_hdr,
            "hdr_format": {"arib-std-b67": "HLG", "smpte2084": "PQ"}.get(transfer),
            "dolby_vision": any("DOVI" in (sd.get("side_data_type") or "")
                                or "Dolby Vision" in (sd.get("side_data_type") or "")
                                for sd in video.get("side_data_list", []) or []),
        })
    if audio:
        meta.update({
            "audio_codec": audio.get("codec_name"),
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
        })
    return meta


def sdr_filters(meta: dict) -> list[str]:
    """Filters converting HDR input to SDR BT.709 (empty list for SDR input)."""
    if not meta.get("is_hdr"):
        return []
    filters = available_filters()
    tin = meta.get("color_transfer") or "arib-std-b67"
    if "zscale" in filters and "tonemap" in filters:
        pin = "bt2020" if (meta.get("color_primaries") or "bt2020").startswith("bt2020") else "709"
        return [
            f"zscale=tin={tin}:pin={pin}:min=bt2020nc:t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=tonemap=hable:desat=0",
            "zscale=t=bt709:m=bt709:r=tv",
            "format=yuv420p",
        ]
    # Fallback without zimg: colorspace conversion (no proper tone curve, but correct gamut).
    return ["colorspace=all=bt709:iall=bt2020:fast=1", "format=yuv420p"]


def normalize_video_filters(meta: dict, *, fps: float | None = None,
                            short_side: int | None = None) -> list[str]:
    """Upright (auto-rotate is FFmpeg's default), SDR, constant frame rate, optional downscale."""
    chain = sdr_filters(meta)
    if short_side:
        # After auto-rotation iw/ih are the display dimensions.
        chain.append(f"scale='if(gt(iw,ih),-2,{short_side})':'if(gt(iw,ih),{short_side},-2)'"
                     ":flags=bicubic")
    chain.append("setsar=1")
    chain.append(f"fps={fps or meta.get('fps') or 30}")
    if not meta.get("is_hdr"):
        chain.append("format=yuv420p")
    return chain


# Pad/trim audio against its timestamps so A/V stays locked after VFR→CFR conversion.
AUDIO_SYNC_FILTER = "aresample=async=1000:first_pts=0"
