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


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
