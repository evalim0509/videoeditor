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


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
