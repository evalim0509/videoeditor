"""One command: footage + references + instruction → finished edit(s).

ingest → style preset from the references → instruction → EditSpec → analyse → select → render.
Everything is cached, so re-running with a different instruction only redoes selection + render.
Used by `autocut run` and by the web app's background jobs.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from .analysis import analyze_all
from .editspec import EditSpec
from .ingest import ingest_paths, iter_media

Progress = Callable[[str, float], None]


def _slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:n] or "edit"


def preset_for_refs(ref_hashes: list[str], name: str | None = None) -> str:
    """A stable preset name for a set of reference videos (re-used if already analysed)."""
    if name:
        return name
    digest = hashlib.sha1("".join(sorted(ref_hashes)).encode()).hexdigest()[:8]
    return f"refs_{digest}"


def run(footage: list[Path], instruction: str, *, refs: list[Path] | None = None,
        style_name: str | None = None, out_dir: Path = Path("output"), name: str | None = None,
        option_overrides: dict | None = None, progress: Progress | None = None) -> dict:
    from .intent import parse
    from .options import RenderOptions
    from .select import select_clips
    from .style import analyze_references, list_profiles, load_profile
    from .stylematch import render_styled

    def prog(stage: str, x: float) -> None:
        if progress:
            progress(stage, max(0.0, min(1.0, x)))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. ingest footage
    files = iter_media(footage)
    if not files:
        raise ValueError("No video files found in the footage you gave.")
    prog("preparing footage", 0.0)
    results = ingest_paths(files, on_file=lambda f, i, n: prog(f"preparing {f.name}", 0.15 * i / n))
    hashes = [r.hash for r in results]

    # 2. style preset from references (or a saved preset)
    profile = None
    if refs and iter_media(refs):
        prog("studying your reference videos", 0.16)
        ref_results = ingest_paths(refs)
        preset = preset_for_refs([r.hash for r in ref_results], style_name)
        if preset in list_profiles() and not style_name:
            profile = load_profile(preset)
        else:
            profile = analyze_references([r.path for r in ref_results], preset)
    elif style_name:
        profile = load_profile(style_name)

    # 3. instruction → EditSpec (always, before any editing logic)
    spec: EditSpec = parse(instruction)
    if profile is None and spec.style_preset:
        profile = load_profile(spec.style_preset)

    # 4. analyse footage
    n = len(hashes)
    for i, h in enumerate(hashes):
        analyze_all(h, progress=lambda s, x, i=i: prog(s, 0.2 + 0.45 * (i + x) / n))

    # 5–6. select + render each version
    base = name or _slug(instruction)
    outputs = []
    used: list[dict] = []
    for v in range(spec.versions):
        lo = 0.65 + 0.35 * v / spec.versions
        span = 0.35 / spec.versions
        prog(f"choosing the best moments{f' (version {v + 1})' if spec.versions > 1 else ''}", lo)
        sel = select_clips(hashes, spec, style=profile, avoid=used or None)
        opts = RenderOptions.from_spec(spec)
        for k, val in (option_overrides or {}).items():
            setattr(opts, k, val)
        sel["options"] = opts.model_dump()
        sel["style"] = profile.name if profile else None
        sel["instruction"] = instruction
        if not sel["clips"]:
            raise ValueError("Couldn't find any usable clips in this footage.")
        used += sel["clips"]
        stem = base if spec.versions == 1 else f"{base}_v{v + 1}"
        sel_path = out_dir / f"{stem}.json"
        video = out_dir / f"{stem}.mp4"
        sel_path.write_text(json.dumps(sel, indent=2))
        info = render_styled(sel, video, spec=spec, profile=profile, options=opts,
                             progress=lambda s, x: prog(s, lo + span * 0.1 + span * 0.9 * x))
        outputs.append({"video": str(video), "preview": info.get("preview"),
                        "selection": str(sel_path), "duration": info["duration"],
                        "clips": len(sel["clips"]), "info": info})
    prog("done", 1.0)
    return {"spec": spec.model_dump(), "summary": spec.summary(), "warnings": spec.warnings,
            "style": profile.name if profile else None, "outputs": outputs}
