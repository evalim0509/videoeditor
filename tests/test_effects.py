"""Effect detection: a reference built with whooshes on cuts, flashes and stickers is recognised;
plain footage isn't. Uses the real reference footage as moving background (skipped without it)."""
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT

REF = ROOT / "references" / "reference_01.mp4"


def test_library_has_sounds_and_stickers(tmp_home):
    from autocut.library import list_sfx, list_stickers, pick_sfx, pick_sticker, resolve

    sfx = list_sfx()
    assert {s["kind"] for s in sfx} >= {"whoosh", "pop", "ding", "boom"}
    assert resolve(pick_sfx("whoosh"), "sfx").exists()
    assert len(list_stickers()) >= 40 and resolve(pick_sticker("laughter"), "stickers").exists()


@pytest.mark.skipif(not REF.exists(), reason="needs the reference footage as background")
def test_detects_whoosh_flash_stickers(tmp_home, tmp_path):
    from autocut.effects import enabled, measure_effects, summarize
    from autocut.ingest import ingest_file
    from autocut.library import resolve

    out = tmp_path / "fx.mp4"
    starts = [4, 9, 14, 19, 27, 33, 40, 46, 52, 57]
    n = len(starts)
    inputs, g = [], ""
    for i, s in enumerate(starts):
        inputs += ["-ss", str(s), "-t", "2", "-i", str(REF)]
        g += f"[{i}:v]scale=-2:480,crop=854:480,setsar=1,fps=30[v{i}];[{i}:a]aresample=48000,volume=0.4[a{i}];"
    g += "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca];"
    fl = "+".join(f"between(t,{2*k-0.05:.2f},{2*k+0.05:.2f})" for k in range(1, n))
    g += f"[cv]drawbox=x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill:enable='{fl}'[fv];[{n}:v]scale=110:-1[s1];"
    g += f"[fv][s1]overlay=x=620:y=60:enable='between(t,1,2.5)+between(t,7,8.5)+between(t,13,14.5)'[ov];"
    g += "".join(f"[{n+1}:a]adelay={int((2*k-0.35)*1000)}|{int((2*k-0.35)*1000)},volume=1.6[w{k}];" for k in range(1, n))
    g += "[ca]" + "".join(f"[w{k}]" for k in range(1, n)) + f"amix=inputs={n}:normalize=0:duration=first[oa]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-loop", "1", "-i", str(resolve("builtin:laugh", "stickers")),
                    "-i", str(resolve("builtin:whoosh", "sfx")), "-filter_complex", g, "-map", "[ov]", "-map", "[oa]",
                    "-t", "20", "-c:v", "libx264", "-crf", "20", "-c:a", "aac", str(out)], check=True)
    m = measure_effects(ingest_file(out).hash)
    on = enabled(summarize([m]))
    assert on["sfx"] and on["flash"] and on["stickers"], m
    assert m["sfx_on_cuts"] >= 6 and m["flashes"] >= 7 and len(m["stickers"]) >= 2

    plain = measure_effects(ingest_file(REF).hash)
    assert not any(enabled(summarize([plain])).values())
