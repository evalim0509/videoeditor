"""RenderOptions: every styling decision as an editable setting.

Defaults follow the user's stated preferences (vlog style): keep the original aspect ratio, plain
caption lines (no word-by-word highlight), don't change the colour. Everything is stored with the
edit (selection/project JSON) so the app can change any of it later.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .editspec import EditSpec


class RenderOptions(BaseModel):
    aspect: Optional[str] = None                 # None = keep the footage's own shape
    captions: Optional[bool] = None              # None = follow the style preset
    caption_style: Literal["static", "karaoke"] = "static"
    caption_case: Literal["original", "lower", "upper"] = "original"
    caption_scale: float = 1.0                   # 1.0 = sized like the reference
    caption_position: Literal["auto", "bottom", "middle", "top"] = "auto"
    caption_speakers: Literal["me", "all"] = "me"   # only caption your own voice
    punch_ins: bool = True                       # zoom cuts inside long clips (pacing)
    punch_in_spacing: Optional[float] = None     # seconds between zoom cuts; None = auto (≥ 4 s)
    face_follow: bool = True                     # keep faces framed when cropping
    color_match: bool = False                    # move colours toward the reference look
    beat_sync: bool = True                       # cut on the beat when the reference does
    loudness_lufs: float = -14.0

    @classmethod
    def from_spec(cls, spec: EditSpec, base: "RenderOptions | None" = None) -> "RenderOptions":
        o = (base or cls()).model_copy()
        if spec.aspect_ratio:
            o.aspect = spec.aspect_ratio
        if spec.captions is not None:
            o.captions = spec.captions
        for k, v in (spec.options or {}).items():   # style wishes from the instruction text
            if k in cls.model_fields:
                setattr(o, k, v)
        return o
