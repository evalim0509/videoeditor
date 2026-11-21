"""Sound-effect and sticker library.

Built in:
- Sound effects synthesised locally with FFmpeg (royalty-free, nothing downloaded): whoosh, swoosh
  up/down, pop, click, ding, chime, boom, thud, riser, boing, shutter, cash.
- Emoji stickers rendered from Noto Color Emoji (OFL) in autocut/assets/stickers/.
Your own packs: drop audio files into data/library/sfx/ and PNG/WebP stickers into
data/library/stickers/ (or upload them in the app). Name files after what they are ("whoosh_2.wav",
"pop_soft.mp3", "laugh_cat.png") — matching names are preferred over the built-in ones.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import ASSETS_DIR, get_settings
from .ffmpeg import run_ffmpeg

SFX_KINDS = ["whoosh", "swoosh_up", "swoosh_down", "pop", "click", "ding", "chime", "boom", "thud",
             "riser", "boing", "shutter", "cash"]
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
IMAGE_EXTS = {".png", ".webp", ".gif"}

# FFmpeg lavfi recipes (mono 48 kHz). Each is short and normalised later in the mix.
_RECIPES = {
    "whoosh": "anoisesrc=d=0.7:c=pink:a=0.9,bandpass=f=1200:w=1800,afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.4",
    "swoosh_up": "anoisesrc=d=0.45:c=pink:a=0.9,bandpass=f=2600:w=2600,afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.15",
    "swoosh_down": "anoisesrc=d=0.6:c=brown:a=0.9,bandpass=f=700:w=900,afade=t=in:d=0.12,afade=t=out:st=0.12:d=0.48:curve=exp",
    "pop": "aevalsrc='0.9*sin(2*PI*(900-5000*t)*t)*exp(-38*t)':d=0.14",
    "click": "aevalsrc='(random(0)*2-1)*exp(-260*t)':d=0.04,highpass=f=1500",
    "ding": "aevalsrc='0.6*sin(2*PI*1320*t)*exp(-4*t)+0.3*sin(2*PI*2640*t)*exp(-6*t)+0.15*sin(2*PI*3960*t)*exp(-9*t)':d=1.3",
    "chime": "aevalsrc='0.4*sin(2*PI*1568*t)*exp(-3*t)+0.35*sin(2*PI*2093*(t-0.12))*exp(-3*(t-0.12))*gte(t,0.12)+0.3*sin(2*PI*2637*(t-0.24))*exp(-3*(t-0.24))*gte(t,0.24)':d=1.4",
    "boom": "aevalsrc='0.9*sin(2*PI*(70-30*t)*t)*exp(-3.2*t)+0.3*(random(0)*2-1)*exp(-20*t)':d=1.2,lowpass=f=900",
    "thud": "aevalsrc='0.9*sin(2*PI*60*t)*exp(-12*t)+0.5*(random(0)*2-1)*exp(-45*t)':d=0.45,lowpass=f=500",
    "riser": "aevalsrc='0.35*sin(2*PI*(200*t+700*t*t))*(t/1.2)+0.25*(random(0)*2-1)*(t/1.2)':d=1.2,highpass=f=150",
    "boing": "aevalsrc='0.8*sin(2*PI*(260+90*sin(2*PI*14*t))*t)*exp(-4*t)':d=0.7",
    "shutter": "aevalsrc='(random(0)*2-1)*(exp(-300*t)+0.8*exp(-300*(t-0.09))*gte(t,0.09))':d=0.18,bandpass=f=3000:w=4000",
    "cash": "aevalsrc='0.5*sin(2*PI*2349*t)*exp(-5*t)+0.5*sin(2*PI*3136*(t-0.1))*exp(-5*(t-0.1))*gte(t,0.1)+0.3*(random(0)*2-1)*exp(-80*t)':d=1.0",
}

# which emoji fit which moment (used when placing stickers automatically)
STICKER_MOODS = {
    "laughter": ["laugh", "rofl", "skull", "sweat"],
    "energy": ["fire", "sparkles", "hundred", "party"],
    "shock": ["shock", "wow", "eyes"],
    "food": ["yum", "noodles", "dumpling", "chili", "hot"],
    "love": ["love_eyes", "heart"],
    "travel": ["plane", "pin", "camera"],
    "generic": ["sparkles", "star", "eyes"],
}


def builtin_sfx_dir() -> Path:
    d = get_settings().home / "library" / "builtin_sfx"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_dir(kind: str) -> Path:
    d = get_settings().home / "library" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_builtin_sfx() -> dict[str, Path]:
    out = {}
    for name, recipe in _RECIPES.items():
        p = builtin_sfx_dir() / f"{name}.wav"
        if not p.exists():
            run_ffmpeg(["-f", "lavfi", "-i", recipe, "-af", "loudnorm=I=-16:TP=-2,aresample=48000",
                        "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(p)])
        out[name] = p
    return out


def _kind_of(name: str, kinds: list[str]) -> str | None:
    n = name.lower()
    for k in sorted(kinds, key=len, reverse=True):
        if k.replace("_", "") in re.sub(r"[^a-z]", "", n):
            return k
    return None


def list_sfx() -> list[dict]:
    """All sound effects: yours first, then built-in. id = 'user:<file>' or 'builtin:<kind>'."""
    items = []
    for f in sorted(user_dir("sfx").iterdir()):
        if f.suffix.lower() in AUDIO_EXTS:
            items.append({"id": f"user:{f.name}", "name": f.stem, "kind": _kind_of(f.stem, SFX_KINDS),
                          "path": str(f), "source": "yours"})
    for k, p in ensure_builtin_sfx().items():
        items.append({"id": f"builtin:{k}", "name": k.replace("_", " "), "kind": k, "path": str(p),
                      "source": "built-in"})
    return items


def list_stickers() -> list[dict]:
    items = []
    names = [n for lst in STICKER_MOODS.values() for n in lst]
    for f in sorted(user_dir("stickers").iterdir()):
        if f.suffix.lower() in IMAGE_EXTS:
            items.append({"id": f"user:{f.name}", "name": f.stem, "kind": _kind_of(f.stem, names) or f.stem,
                          "path": str(f), "source": "yours"})
    for f in sorted((ASSETS_DIR / "stickers").glob("*.png")):
        items.append({"id": f"builtin:{f.stem}", "name": f.stem.replace("_", " "), "kind": f.stem,
                      "path": str(f), "source": "built-in"})
    return items


def resolve(asset_id: str, kind: str) -> Path:
    src, _, name = asset_id.partition(":")
    if kind == "sfx":
        if src == "user":
            p = user_dir("sfx") / Path(name).name
        else:
            p = ensure_builtin_sfx().get(name, builtin_sfx_dir() / f"{name}.wav")
    else:
        p = (user_dir("stickers") / Path(name).name) if src == "user" else (ASSETS_DIR / "stickers" / f"{Path(name).name}.png")
    if not p.exists():
        raise FileNotFoundError(f"{kind} '{asset_id}' not found")
    return p


def pick_sfx(kind: str) -> str:
    """Asset id for a kind of sound effect — your own file if you have a matching one."""
    for it in list_sfx():
        if it["source"] == "yours" and it["kind"] == kind:
            return it["id"]
    return f"builtin:{kind}" if kind in _RECIPES else "builtin:whoosh"


def pick_sticker(mood: str, n: int = 0) -> str:
    names = STICKER_MOODS.get(mood, STICKER_MOODS["generic"])
    yours = [it for it in list_stickers() if it["source"] == "yours" and it["kind"] in names]
    if yours:
        return yours[n % len(yours)]["id"]
    return f"builtin:{names[n % len(names)]}"
