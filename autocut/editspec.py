"""EditSpec: the structured form of an editing instruction.

Every instruction (typed or spoken) is converted into an EditSpec before any editing logic runs.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Mood = Literal["funny", "hype", "emotional", "calm", "informative", "auto"]
MOODS = ["funny", "hype", "emotional", "calm", "informative"]


class EditSpec(BaseModel):
    source_text: str = ""
    mood: Mood = "auto"
    target_duration: Optional[float] = None       # seconds; None = let the selector decide
    duration_tolerance: float = 0.10              # ±10 %
    aspect_ratio: Optional[str] = None            # "9:16" | "16:9" | "1:1" | "4:5" | None (= source/preset)
    platform: Optional[str] = None                # tiktok | reels | shorts | youtube | instagram ...
    captions: Optional[bool] = None               # None = follow the style preset
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    visual_query: Optional[str] = None            # CLIP search text, e.g. "eating dumplings"
    style_preset: Optional[str] = None
    versions: int = 1
    music: Optional[bool] = None                  # explicit "no music" / "with music"
    options: dict = Field(default_factory=dict)   # style wishes → RenderOptions (e.g. no zoom cuts)
    option_labels: list[str] = Field(default_factory=list)  # what those wishes were, in words
    warnings: list[str] = Field(default_factory=list)
    parser: str = "rules"

    def summary(self) -> str:
        """One plain-language line describing what was understood."""
        bits = []
        if self.mood != "auto":
            bits.append(self.mood)
        if self.target_duration:
            d = self.target_duration
            bits.append(f"{int(d)} s" if d < 90 else f"{d / 60:.1f} min")
        if self.platform:
            bits.append(self.platform)
        if self.aspect_ratio:
            bits.append(self.aspect_ratio)
        if self.captions is True:
            bits.append("with captions")
        elif self.captions is False:
            bits.append("no captions")
        if self.include_keywords:
            bits.append("about " + ", ".join(self.include_keywords))
        if self.exclude_keywords:
            bits.append("without " + ", ".join(self.exclude_keywords))
        if self.visual_query:
            bits.append(f'showing "{self.visual_query}"')
        if self.style_preset:
            bits.append(f"style: {self.style_preset}")
        if self.music is False:
            bits.append("no music")
        elif self.music is True:
            bits.append("with music")
        bits += self.option_labels
        if self.versions > 1:
            bits.append(f"{self.versions} versions")
        return " · ".join(bits) or "best moments (no specific request)"
