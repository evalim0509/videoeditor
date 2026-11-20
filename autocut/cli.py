"""autocut command-line interface (Typer).

Every pipeline stage has its own command so it can be run and tested alone. Heavy libraries are
imported inside the commands so `autocut --help` stays instant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__

app = typer.Typer(
    name="autocut",
    help="Fully offline, local AI video editor. Drop in footage + reference videos, give an "
         "instruction, get an edit.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
console = Console()


@app.callback()
def _main() -> None:
    """autocut — offline AI video editor."""


@app.command()
def version() -> None:
    """Print the autocut version."""
    console.print(f"autocut {__version__}")


@app.command()
def hardware() -> None:
    """Show detected OS / GPU and the model sizes autocut will use."""
    from .hardware import detect

    hw = detect()
    t = Table(show_header=False, box=None)
    for k, v in hw.as_dict().items():
        t.add_row(k, str(v))
    console.print(t)


@app.command()
def status() -> None:
    """List ingested media and which analyses are cached."""
    from . import db

    rows = db.all_media()
    if not rows:
        console.print("No media ingested yet. Run [bold]autocut ingest <folder>[/bold].")
        return
    t = Table("hash", "file", "duration", "size", "cached analyses")
    for r in rows:
        m = r["meta"]
        stages = ", ".join(sorted({s for s, _ in db.cached_stages(r["hash"])})) or "-"
        t.add_row(r["hash"][:12], Path(r["path"]).name, f"{m.get('duration', 0):.1f}s",
                  f"{m.get('display_width')}x{m.get('display_height')}", stages)
    console.print(t)


@app.command()
def probe(file: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Print normalised metadata for a media file (codec, rotation, HDR, VFR...)."""
    from .media import probe as _probe

    console.print_json(json.dumps(_probe(file)))


@app.command()
def ingest(
    paths: List[Path] = typer.Argument(..., exists=True, help="Files or folders of footage."),
    force: bool = typer.Option(False, help="Re-ingest even if already cached."),
) -> None:
    """Hash, probe and build 480p proxies for footage (skips files already ingested)."""
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    from .ingest import ingest_file, iter_media

    files = iter_media(paths)
    if not files:
        console.print("[yellow]No video files found.[/yellow]")
        raise typer.Exit(1)
    with Progress(TextColumn("{task.description}"), BarColumn(), TextColumn("{task.fields[stage]}"),
                  TimeElapsedColumn(), console=console) as prog:
        for f in files:
            task = prog.add_task(f.name, total=1.0, stage="")
            res = ingest_file(f, force=force,
                              progress=lambda s, x, t=task: prog.update(t, completed=x, stage=s))
            prog.update(task, completed=1.0, stage="cached (skipped)" if res.skipped else "ingested")
            m = res.meta
            notes = []
            if m.get("rotation"):
                notes.append(f"rotated {m['rotation']}°")
            if m.get("is_hdr"):
                notes.append(f"HDR {m.get('hdr_format')} → SDR")
            if m.get("is_vfr"):
                notes.append(f"VFR {m.get('fps_avg')} → CFR {m.get('fps')}")
            prog.console.print(f"  {res.hash[:12]}  {m['display_width']}x{m['display_height']} "
                               f"{m['video_codec']} {m['duration']:.1f}s  {'; '.join(notes)}")


def _resolve(ref: str) -> dict:
    """Find an ingested file by path or hash prefix; ingest it on the fly if it's a new path."""
    from . import db

    m = db.find_media(ref)
    if m is None and Path(ref).is_file():
        from .ingest import ingest_file

        console.print(f"[dim]Ingesting {ref} first…[/dim]")
        ingest_file(Path(ref))
        m = db.find_media(ref)
    if m is None:
        console.print(f"[red]Unknown media: {ref}[/red] (give a file path or a hash prefix)")
        raise typer.Exit(1)
    return m


@app.command()
def models(
    whisper: List[str] = typer.Option(None, help="Whisper sizes to fetch (default: hardware pick)."),
) -> None:
    """Download all model weights once (Whisper, PANNs, CLIP, face detector). Then fully offline."""
    from .hardware import detect
    from .models import download_all

    for line in download_all(whisper or [detect().whisper_model]):
        console.print("✓ " + line)


@app.command()
def transcribe(
    file: str = typer.Argument(..., help="File path or hash prefix."),
    model: Optional[str] = typer.Option(None, help="Whisper size (tiny/base/small/medium/large-v3)."),
    language: Optional[str] = typer.Option(None, help="Force a language code, e.g. 'en'."),
    force: bool = typer.Option(False, help="Ignore the cache."),
    words: bool = typer.Option(False, help="Print word-level timestamps."),
) -> None:
    """Transcribe a file with faster-whisper (word timestamps, cached)."""
    import time

    from .transcribe import fmt_ts, transcribe as _transcribe

    m = _resolve(file)
    t0 = time.time()
    tr = _transcribe(m["hash"], model_size=model, language=language, force=force)
    console.print(f"[bold]{Path(m['path']).name}[/bold]  language={tr['language']} "
                  f"({tr['language_prob']:.0%})  model={tr['model']}  "
                  f"{len(tr['words'])} words, {len(tr['sentences'])} sentences  "
                  f"[dim]{time.time() - t0:.1f}s[/dim]")
    for s in tr["sentences"]:
        console.print(f"[cyan]{fmt_ts(s['start'])}–{fmt_ts(s['end'])}[/cyan]  {s['text']}")
        if words:
            ws = tr["words"][s["first_word"]: s["last_word"] + 1]
            console.print("    [dim]" + " ".join(f"{w['word'].strip()}@{w['start']:.2f}"
                                                for w in ws) + "[/dim]")


@app.command()
def shots(
    file: str = typer.Argument(..., help="File path or hash prefix."),
    force: bool = typer.Option(False, help="Ignore the cache."),
) -> None:
    """Detect shot changes with PySceneDetect (on the proxy, cached)."""
    from .shots import shots as _shots
    from .transcribe import fmt_ts

    m = _resolve(file)
    r = _shots(m["hash"], force=force)
    console.print(f"[bold]{Path(m['path']).name}[/bold]: {r['count']} shot(s)")
    for i, s in enumerate(r["shots"], 1):
        console.print(f"  {i:3d}  {fmt_ts(s['start'])} – {fmt_ts(s['end'])}  ({s['end'] - s['start']:.1f}s)")


@app.command("audio")
def audio_cmd(
    file: str = typer.Argument(..., help="File path or hash prefix."),
    force: bool = typer.Option(False, help="Ignore the cache."),
) -> None:
    """Per-second PANNs event scores (laughter, cheering, ...) + loudness/energy + beats."""
    from .audio import EVENT_NAMES, analyze

    m = _resolve(file)
    r = analyze(m["hash"], force=force)
    s = r["scores"]
    console.print(f"[bold]{Path(m['path']).name}[/bold]: {r['seconds']} s, integrated "
                  f"{r['integrated_lufs']} LUFS, peak {r['peak_dbfs']} dBFS, tempo {r['tempo']} BPM, "
                  f"{len(r['beats'])} beats")
    t = Table("event", "mean", "max", "seconds > 0.3")
    for c in EVENT_NAMES + ["energy"]:
        v = s[c]
        t.add_row(c, f"{sum(v) / max(1, len(v)):.3f}", f"{max(v, default=0):.3f}",
                  str(sum(1 for x in v if x > 0.3)))
    console.print(t)


@app.command()
def inspect(
    file: str = typer.Argument(..., help="File path or hash prefix."),
    top: int = typer.Option(10, help="How many moments per category."),
    categories: str = typer.Option("laughter,energy,cheering,applause,shouting,music,speech",
                                   help="Comma-separated categories to list."),
    preview: Optional[Path] = typer.Option(None, help="Also write a video of the top moments "
                                           "(of the first two categories) to this .mp4 path."),
) -> None:
    """Run (or load cached) analyses and list the top moments of a file."""
    from .audio import analyze
    from .moments import confidence_label, make_preview, top_moments, words_near
    from .shots import shots as _shots
    from .transcribe import fmt_ts, transcribe as _transcribe

    m = _resolve(file)
    meta = m["meta"]
    with console.status("analysing (cached results are reused)…"):
        tr = _transcribe(m["hash"])
        au = analyze(m["hash"])
        sh = _shots(m["hash"])
    console.print(f"[bold]{Path(m['path']).name}[/bold]  {meta['display_width']}x{meta['display_height']} "
                  f"{meta['duration']:.1f}s  {meta.get('device') or ''}")
    console.print(f"  speech: {len(tr['words'])} words ({tr['language']})   shots: {sh['count']}   "
                  f"loudness: {au['integrated_lufs']} LUFS   tempo: {au['tempo']} BPM")
    cats = [c.strip() for c in categories.split(",") if c.strip()]
    preview_items: list[tuple[str, float]] = []
    for cat in cats:
        series = au["scores"].get(cat)
        if series is None:
            continue
        moments = top_moments(series, k=top, min_gap=4)
        t = Table(title=f"Top {cat} moments", title_justify="left")
        t.add_column("#", justify="right")
        t.add_column("time")
        t.add_column("score", justify="right")
        t.add_column("strength")
        t.add_column("what's said around it")
        for i, mo in enumerate(moments, 1):
            t.add_row(str(i), fmt_ts(mo["t"]), f"{mo['peak']:.3f}", confidence_label(cat, mo["peak"]),
                      words_near(tr, mo["t"] - 1.5, mo["t"] + 2.5)[:70])
            if cat in cats[:2]:
                preview_items.append((f"{cat} #{i}  {fmt_ts(mo['t'])}  {mo['peak']:.2f}", mo["t"]))
        console.print(t)
    if preview and preview_items:
        with console.status("rendering preview…"):
            out = make_preview(Path(m["proxy_path"]), preview_items, preview)
        console.print(f"Preview written to [bold]{out}[/bold]")


@app.command()
def visual(
    file: str = typer.Argument(..., help="File path or hash prefix."),
    force: bool = typer.Option(False, help="Ignore the cache."),
) -> None:
    """CLIP embeddings at 1 fps + MediaPipe face positions (on the proxy, cached)."""
    import time

    from .visual import analyze as _analyze

    m = _resolve(file)
    t0 = time.time()
    r = _analyze(m["hash"], force=force)
    console.print(f"[bold]{Path(m['path']).name}[/bold]: {r['clip_frames']} CLIP frames, "
                  f"{r['face_samples']} face samples, face visible in {r['seconds_with_face']}/"
                  f"{r['clip_frames']} s  [dim]{time.time() - t0:.1f}s[/dim]")


@app.command()
def search(
    query: str = typer.Argument(..., help='What to look for, e.g. "eating dumplings".'),
    file: Optional[List[str]] = typer.Option(None, help="Limit to these files (default: all)."),
    top: int = typer.Option(10, help="Number of results."),
    preview: Optional[Path] = typer.Option(None, help="Write a video of the results to this path."),
) -> None:
    """Find moments in your footage that match a description (CLIP, fully offline)."""
    from . import db
    from .moments import make_preview, words_near
    from .transcribe import fmt_ts, get_transcript
    from .visual import search as _search

    hashes = [_resolve(f)["hash"] for f in file] if file else None
    with console.status("searching…"):
        res = _search(query, hashes, top=top)
    t = Table("#", "file", "time", "similarity", "said", title=f'"{query}"', title_justify="left")
    items = []
    for i, r in enumerate(res, 1):
        m = db.get_media(r["hash"])
        t.add_row(str(i), Path(m["path"]).name, fmt_ts(r["t"]), f"{r['score']:.3f}",
                  words_near(get_transcript(r["hash"]), r["t"] - 1, r["t"] + 2)[:50])
        items.append((f"#{i} {fmt_ts(r['t'])} {r['score']:.2f}", r["t"] + 0.5, m["proxy_path"]))
    console.print(t)
    if preview and items:
        out = make_preview(None, items, preview, before=1.0, after=2.0)
        console.print(f"Preview written to [bold]{out}[/bold]")


@app.command()
def style(
    refs: List[Path] = typer.Argument(None, help="Reference videos or folders."),
    name: str = typer.Option("default", help="Preset name to save as."),
    show: Optional[str] = typer.Option(None, help="Explain an existing preset."),
    list_: bool = typer.Option(False, "--list", help="List saved presets."),
) -> None:
    """Analyse reference videos into a StyleProfile preset and explain it in plain language."""
    from .style import analyze_references, explain, list_profiles, load_profile, preset_path

    if list_:
        for n in list_profiles():
            console.print(n)
        return
    if show:
        p = load_profile(show)
    else:
        if not refs:
            console.print("[red]Give reference videos/folders, or --show NAME / --list.[/red]")
            raise typer.Exit(1)
        with console.status("analysing references (cached results are reused)…"):
            p = analyze_references(refs, name)
        console.print(f"Saved preset [bold]{p.name}[/bold] → {preset_path(p.name)}")
    console.print(f"\n[bold]Style “{p.name}”[/bold] from {len(p.references)} reference(s), "
                  f"{p.total_seconds:.0f}s total:")
    for line in explain(p):
        console.print(f"  • {line}")


@app.command()
def parse(instruction: str = typer.Argument(..., help='e.g. "funny 60 second tiktok with captions"')) -> None:
    """Convert an instruction into a structured EditSpec (and show what wasn't understood)."""
    from .intent import parse as _parse

    spec = _parse(instruction)
    console.print(f"[bold]Understood:[/bold] {spec.summary()}")
    for w in spec.warnings:
        console.print(f"[yellow]⚠ {w}[/yellow]")
    console.print_json(spec.model_dump_json())


@app.command()
def voice(audio: Path = typer.Argument(..., exists=True, dir_okay=False,
                                        help="A voice recording (wav/webm/m4a/mp3/...).")) -> None:
    """Transcribe a spoken instruction locally and convert it into an EditSpec."""
    from .editspec import EditSpec
    from .voice import voice_to_spec

    with console.status("listening…"):
        r = voice_to_spec(audio)
    tr = r["transcript"]
    console.print(f"[bold]Heard[/bold] ({tr['duration']}s audio, {tr['seconds']}s to transcribe): "
                  f"“{tr['text']}”")
    spec = EditSpec(**r["spec"])
    console.print(f"[bold]Understood:[/bold] {spec.summary()}")
    for w in spec.warnings:
        console.print(f"[yellow]⚠ {w}[/yellow]")


@app.command()
def password(
    new: Optional[str] = typer.Option(None, help="New password (prompted if omitted)."),
) -> None:
    """Set the web app login password."""
    from .web.auth import set_password

    pw = new or typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    set_password(pw)
    console.print("Password updated. All devices need to log in again.")


@app.command("select")
def select_cmd(
    instruction: str = typer.Argument(..., help='e.g. "funny 60 seconds"'),
    footage: List[Path] = typer.Option(..., "--footage", "-f", exists=True, help="Files or folders."),
    out: Path = typer.Option(Path("output/selection.json"), help="Where to save the selection."),
    preview: Optional[Path] = typer.Option(None, help="Render a quick proxy preview of the picks."),
    style_name: Optional[str] = typer.Option(None, "--style", help="Style preset to pace toward."),
) -> None:
    """Pick the best clips for an instruction (snapped to sentences, never mid-word)."""
    from .analysis import analyze_all
    from .editspec import EditSpec
    from .ingest import ingest_paths
    from .intent import parse as _parse
    from .moments import make_preview
    from .select import select_clips
    from .transcribe import fmt_ts

    spec = _parse(instruction)
    console.print(f"[bold]Understood:[/bold] {spec.summary()}")
    for w in spec.warnings:
        console.print(f"[yellow]⚠ {w}[/yellow]")
    with console.status("ingesting & analysing (cached results are reused)…") as st:
        results = ingest_paths(footage)
        for r in results:
            analyze_all(r.hash, progress=lambda s, x, n=r.path.name: st.update(f"{n}: {s}…"))
        from .style import load_profile

        prof = load_profile(style_name or spec.style_preset) if (style_name or spec.style_preset) else None
        sel = select_clips([r.hash for r in results], spec, style=prof)
    t = Table("#", "file", "start", "end", "len", "score", "why", title=(
        f"{len(sel['clips'])} clips, {sel['total_duration']:.1f}s (target {sel['target']:.0f}s, "
        f"allowed {sel['range'][0]}–{sel['range'][1]}s, mood: {sel['mood_used']})"),
        title_justify="left", show_lines=True)
    for i, c in enumerate(sel["clips"], 1):
        t.add_row(str(i), c["file"], fmt_ts(c["start"]), fmt_ts(c["end"]), f"{c['duration']:.1f}s",
                  f"{c['score']:.2f}", c["reason"])
    console.print(t)
    for w in sel["warnings"]:
        console.print(f"[yellow]⚠ {w}[/yellow]")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sel, indent=2))
    console.print(f"Saved selection → {out}  ({len(sel['runner_ups'])} runner-ups included)")
    if preview and sel["clips"]:
        from . import db

        items = [(f"#{i} {fmt_ts(c['start'])}-{fmt_ts(c['end'])}", c["start"],
                  db.get_media(c["hash"])["proxy_path"], c["duration"])
                 for i, c in enumerate(sel["clips"], 1)]
        with console.status("rendering preview…"):
            make_preview(None, items, preview)
        console.print(f"Preview → {preview}")


@app.command()
def render(
    selection: Path = typer.Argument(Path("output/selection.json"), exists=True,
                                     help="Selection JSON from `autocut select`."),
    out: Path = typer.Option(Path("output/edit.mp4"), "--out", "-o", help="Output MP4."),
    preview: bool = typer.Option(True, help="Also write a 540p phone preview."),
    style_name: Optional[str] = typer.Option(None, "--style", help="Style preset to match."),
    plain: bool = typer.Option(False, help="No style matching: just cut, crossfade, normalise."),
    aspect: Optional[str] = typer.Option(None, help='"source", "16:9", "9:16", "1:1" or "4:5".'),
    captions: Optional[bool] = typer.Option(None, "--captions/--no-captions"),
    caption_style: Optional[str] = typer.Option(None, help='"static" (whole line) or "karaoke".'),
    caption_case: Optional[str] = typer.Option(None, help='"original", "lower" or "upper".'),
    caption_scale: Optional[float] = typer.Option(None, help="Caption size multiplier, e.g. 1.3"),
    caption_position: Optional[str] = typer.Option(None, help='"auto", "bottom", "middle", "top".'),
    punch_ins: Optional[bool] = typer.Option(None, "--punch-ins/--no-punch-ins"),
    punch_in_spacing: Optional[float] = typer.Option(None, help="Seconds between zoom cuts (0 = auto)."),
    color_match: Optional[bool] = typer.Option(None, "--color-match/--no-color-match"),
) -> None:
    """Render a selection from the originals, matched to a style. Every setting can be overridden
    and the choice is saved back into the selection file."""
    import time

    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    from .editspec import EditSpec
    from .render import clips_from_selection, measure_lufs, render_timeline
    from .style import load_profile
    from .stylematch import render_styled

    from .options import RenderOptions

    sel = json.loads(selection.read_text())
    spec = EditSpec(**sel.get("spec", {}))
    opts = RenderOptions(**sel["options"]) if sel.get("options") else RenderOptions.from_spec(spec)
    overrides = {"aspect": (None if aspect == "source" else aspect) if aspect else ...,
                 "captions": captions, "caption_style": caption_style, "caption_case": caption_case,
                 "caption_scale": caption_scale, "caption_position": caption_position,
                 "punch_ins": punch_ins, "color_match": color_match,
                 "punch_in_spacing": (punch_in_spacing or None) if punch_in_spacing is not None else None}
    for k, v in overrides.items():
        if v is not None and v is not ...:
            setattr(opts, k, v)
        elif k == "aspect" and v is None:
            opts.aspect = None
    sel["options"] = opts.model_dump()
    if style_name:
        sel["style"] = style_name
    selection.write_text(json.dumps(sel, indent=2))  # remember manual choices
    name = style_name or spec.style_preset or sel.get("style")
    prof = load_profile(name) if name else None
    t0 = time.time()
    with Progress(TextColumn("{task.fields[stage]}"), BarColumn(), TimeElapsedColumn(),
                  console=console) as prog:
        task = prog.add_task("render", total=1.0, stage="starting")
        cb = lambda s, x: prog.update(task, completed=x, stage=s)  # noqa: E731
        if plain:
            info = render_timeline(clips_from_selection(sel), out, preview=preview, progress=cb)
        else:
            info = render_styled(sel, out, spec=spec, profile=prof, options=opts, preview=preview,
                                 progress=cb)
    console.print(f"Rendered [bold]{info['output']}[/bold]: {info['duration']:.2f}s, "
                  f"{info['size'][0]}x{info['size'][1]} @ {info['fps']} fps, "
                  f"{len(info['clip_frames'])} clips, loudness {measure_lufs(Path(info['output'])):.1f} LUFS "
                  f"(was {info['loudness_in']:.1f})  [dim]{time.time() - t0:.0f}s[/dim]")
    if not plain:
        console.print(f"Style: {info.get('style') or 'none'} · aspect {info['aspect']} · "
                      f"{info['segments']} shots ({info['punch_ins']} punch-in cuts) · "
                      f"{info['captions']} caption lines ({opts.caption_style}) · "
                      f"colour changed: {info['color_matched']}")
    if info["preview"]:
        console.print(f"Phone preview: {info['preview']}")


@app.command()
def run(
    instruction: str = typer.Argument(..., help='e.g. "funny 60 second vlog with captions"'),
    footage: List[Path] = typer.Option(..., "--footage", "-f", exists=True, help="Footage files/folders."),
    refs: Optional[List[Path]] = typer.Option(None, "--refs", "-r", exists=True,
                                              help="Reference videos/folders for the style."),
    style_name: Optional[str] = typer.Option(None, "--style", help="Saved preset (or name for the new one)."),
    out_dir: Path = typer.Option(Path("output"), "--out-dir", "-o"),
    name: Optional[str] = typer.Option(None, help="Output file name (default: from the instruction)."),
) -> None:
    """Everything in one go: footage + references + instruction → finished edit(s)."""
    import time

    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    from .pipeline import run as _run

    t0 = time.time()
    with Progress(TextColumn("{task.fields[stage]}"), BarColumn(), TimeElapsedColumn(),
                  console=console) as prog:
        task = prog.add_task("run", total=1.0, stage="starting")
        res = _run(footage, instruction, refs=refs, style_name=style_name, out_dir=out_dir,
                   name=name, progress=lambda s, x: prog.update(task, completed=x, stage=s))
    console.print(f"[bold]Understood:[/bold] {res['summary']}   style: {res['style'] or 'none'}")
    for w in res["warnings"]:
        console.print(f"[yellow]⚠ {w}[/yellow]")
    for o in res["outputs"]:
        console.print(f"✓ {o['video']}  ({o['duration']:.1f}s, {o['clips']} clips)  "
                      f"edit list: {o['selection']}")
    console.print(f"[dim]{time.time() - t0:.0f}s total[/dim]")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="0.0.0.0 = reachable from your phone on the same network."),
    port: int = typer.Option(8765),
) -> None:
    """Start the phone/web app (login required)."""
    import socket

    import uvicorn

    from .web import jobs
    from .web.app import create_app
    from .web.auth import ensure_password

    phrase = ensure_password()
    if phrase:
        console.print(f"\n[bold]First login password:[/bold] [green]{phrase}[/green]  "
                      "(also saved in data/FIRST_LOGIN.txt — change it in the app)\n")
    n = jobs.resume_unfinished()
    if n:
        console.print(f"Resumed {n} unfinished job(s).")
    jobs.ensure_worker()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        lan = s.getsockname()[0]
        s.close()
    except OSError:
        lan = "localhost"
    console.print(f"autocut is running:  this computer → http://localhost:{port}   "
                  f"phone on the same Wi-Fi → http://{lan}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning", timeout_keep_alive=30)


@app.command()
def export(
    selection: Path = typer.Argument(..., exists=True, help="Edit list JSON (from select/run)."),
    out_dir: Path = typer.Option(Path("output"), "--out-dir", "-o"),
    name: Optional[str] = typer.Option(None, help="Base file name (default: the JSON's name)."),
) -> None:
    """Export the timeline as FCPXML (Final Cut/Resolve), EDL, Premiere XML and SRT captions."""
    from .export import FORMATS, export_all

    sel = json.loads(selection.read_text())
    base = name or selection.stem
    for p, (fmt, (_, desc)) in zip(export_all(sel, out_dir, base, sel.get("instruction") or base),
                                   FORMATS.items()):
        console.print(f"✓ {p}  [dim]({desc})[/dim]")


@app.command()
def daemon(port: int = typer.Option(8765)) -> None:
    """Run the server and restart it automatically if it ever stops (used by auto-start)."""
    from .system import daemon as _daemon

    _daemon(port)


@app.command("open")
def open_cmd(port: int = typer.Option(8765)) -> None:
    """Start autocut in the background if needed and open it in the browser."""
    from .system import open_app

    console.print(f"Opening {open_app(port)}")


@app.command()
def install(
    port: int = typer.Option(8765),
    autostart: bool = typer.Option(True, help="Start automatically when you log in."),
    shortcut: bool = typer.Option(True, help="Create a desktop shortcut / double-click launcher."),
    firewall: bool = typer.Option(False, help="Windows: allow phones on your Wi-Fi (admin prompt)."),
) -> None:
    """Set autocut up on this computer: auto-start, background running, launcher + desktop shortcut."""
    from . import system as S

    if autostart:
        for line in S.install_autostart(port):
            console.print("✓ " + line)
    if shortcut:
        for p in S.create_launcher(port):
            console.print(f"✓ Launcher: {p}")
    if firewall:
        console.print(S.allow_firewall(port))
    ok = S.start_background(port)
    console.print(("✓ autocut is running" if ok else "✗ autocut didn't start — see " + str(S.log_path()))
                  + f": http://localhost:{port}  (phone on your Wi-Fi: http://{S.lan_ip()}:{port})")


@app.command()
def uninstall() -> None:
    """Remove auto-start (your projects and settings are kept)."""
    from .system import uninstall_autostart

    for p in uninstall_autostart() or ["nothing to remove"]:
        console.print("removed " + p)


@app.command()
def phone(port: int = typer.Option(8765)) -> None:
    """Set up / show how to reach autocut from your phone (home Wi-Fi + Tailscale HTTPS)."""
    from . import system as S

    running = S.is_running(port) or S.start_background(port)
    console.print(f"Server: {'running' if running else 'NOT running'} on port {port}")
    console.print(f"[bold]Home Wi-Fi:[/bold] http://{S.lan_ip()}:{port}   (everything except the mic — "
                  "phones only allow the mic on https)")
    r = S.tailscale_serve(port)
    if r.get("ok"):
        console.print(f"[bold]Anywhere (Tailscale, https, mic works):[/bold] [green]{r['https_url']}[/green]")
    else:
        console.print(f"[yellow]Tailscale: {r['message']}[/yellow]")
        if not r.get("installed"):
            console.print("Install it from https://tailscale.com/download, sign in, then run "
                          "`autocut phone` again.")


def _ts(s: str) -> float:
    """'1:43.5' or '103.5' → seconds."""
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(m) * 60 + float(sec)
    return float(s)


@app.command()
def correct(
    file: str = typer.Argument(..., help="File path or hash prefix."),
    text: Optional[str] = typer.Argument(None, help="What was actually said."),
    start: Optional[str] = typer.Option(None, "--from", help="Start time, e.g. 1:41.6"),
    end: Optional[str] = typer.Option(None, "--to", help="End time, e.g. 1:44.6"),
    find: Optional[str] = typer.Option(None, help="Phrase to find instead of giving times."),
    relisten_: bool = typer.Option(False, "--relisten", help="Re-transcribe that moment instead "
                                   "(TEXT, if given, is used as a hint)."),
    list_: bool = typer.Option(False, "--list", help="Show saved corrections."),
    undo: Optional[str] = typer.Option(None, help="Remove a correction by id ('all' for every one)."),
) -> None:
    """Fix the transcript/captions yourself: replace words, find & replace, or re-listen a moment."""
    from . import corrections as C

    m = _resolve(file)
    h = m["hash"]
    if list_:
        for e in C.get_edits(h):
            console.print(f"{e['id']}  {e['start']:.2f}–{e['end']:.2f}  ({e['source']})  “{e['text']}”",
                          markup=False)
        return
    if undo:
        n = C.clear_edits(h, None if undo == "all" else undo)
        console.print(f"Removed {n} correction(s).")
        return
    if find and not relisten_:
        e = C.find_replace(h, find, text or "")
        if not e:
            console.print(f"[red]Couldn't find “{find}” in the transcript.[/red]")
            raise typer.Exit(1)
    elif start is not None and end is not None:
        e = (C.relisten(h, _ts(start), _ts(end), hint=text) if relisten_
             else C.correct_text(h, _ts(start), _ts(end), text or ""))
    elif find and relisten_:
        hit = C.find_phrase(h, find)
        if not hit:
            raise typer.Exit(1)
        e = C.relisten(h, hit[0], hit[1], hint=text)
    else:
        console.print("[red]Give --from/--to times or --find PHRASE.[/red]")
        raise typer.Exit(1)
    console.print(f"Saved {e['id']} ({e['source']}): {e['start']:.2f}–{e['end']:.2f} → “{e['text']}”")


@app.command()
def context(
    file: str = typer.Argument(..., help="File path or hash prefix."),
    text: str = typer.Argument(..., help='e.g. \'At 1:43 I said "you want some?"; my friend is Mia\''),
) -> None:
    """Give context so autocut can fix its own transcript (quoted phrases, names, places)."""
    from .corrections import apply_context

    m = _resolve(file)
    with console.status("re-listening with your context…"):
        changes = apply_context(m["hash"], text)
    if not changes:
        console.print("Nothing needed changing.")
    for e in changes:
        console.print(f"{e['start']:.2f}–{e['end']:.2f} ({e['source']}) → “{e['text']}”", markup=False)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
