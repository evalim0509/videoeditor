"""Background jobs: one worker thread runs heavy work in order (the computer does the processing;
the phone just watches progress). Job state is kept in memory for live progress and mirrored into
project.json (item / edit status) so it survives restarts — unfinished work is re-queued on start.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

from . import projects as P

_jobs: dict[str, dict] = {}
_q: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()
_worker: threading.Thread | None = None


def _new(pid: str, kind: str, target: str, label: str) -> dict:
    j = {"id": uuid.uuid4().hex[:10], "pid": pid, "kind": kind, "target": target, "label": label,
         "status": "queued", "stage": "waiting…", "progress": 0.0, "created": time.time(),
         "finished": None, "error": None}
    with _lock:
        _jobs[j["id"]] = j
    _q.put(j["id"])
    ensure_worker()
    return j


def get(jid: str) -> dict | None:
    return _jobs.get(jid)


def for_project(pid: str) -> list[dict]:
    with _lock:
        js = [dict(j) for j in _jobs.values() if j["pid"] == pid]
    return sorted(js, key=lambda j: j["created"])


def busy() -> bool:
    with _lock:
        return any(j["status"] in ("queued", "running") for j in _jobs.values())


def _set(j: dict, **kw) -> None:
    with _lock:
        j.update(kw)


# ------------------------------------------------------------------ job kinds

def queue_analyze(pid: str, item_id: str) -> dict:
    p = P.load(pid)
    kind, it = P.find_item(p, item_id)
    return _new(pid, "analyze" if kind == "footage" else "ref", item_id, it["name"])


def queue_style(pid: str) -> dict:
    return _new(pid, "style", "refs", "Studying your reference videos")


def queue_edit(pid: str, eid: str) -> dict:
    return _new(pid, "edit", eid, "Making your edit")


def queue_render(pid: str, eid: str) -> dict:
    return _new(pid, "render", eid, "Rendering your changes")


def _set_item(pid: str, item_id: str, **kw) -> None:
    def fn(p):
        found = P.find_item(p, item_id)
        if found:
            found[1].update(kw)
    P.update(pid, fn)


def _set_edit(pid: str, eid: str, **kw) -> None:
    def fn(p):
        e = P.find_edit(p, eid)
        if e:
            e.update(kw)
    P.update(pid, fn)


def _run_analyze(j: dict, prog: Callable[[str, float], None]) -> None:
    from ..analysis import analyze_all
    from ..ingest import ingest_file

    p = P.load(j["pid"])
    kind, it = P.find_item(p, j["target"])
    _set_item(j["pid"], it["id"], status="processing", error=None)
    res = ingest_file(Path(it["path"]), progress=lambda s, x: prog(
        {"hashing": "checking file", "proxy": "making a light copy for analysis",
         "audio": "extracting sound", "done": "prepared"}.get(s, s), 0.4 * x))
    m = res.meta
    _set_item(j["pid"], it["id"], hash=res.hash, duration=m["duration"],
              width=m["display_width"], height=m["display_height"])
    if kind == "footage":
        analyze_all(res.hash, progress=lambda s, x: prog(s, 0.4 + 0.6 * x))
    _set_item(j["pid"], it["id"], status="ready")
    if kind == "refs":
        # rebuild the project's style from all its ready references
        p = P.load(j["pid"])
        if all(r.get("status") == "ready" for r in p["refs"]):
            queue_style(j["pid"])


def _run_style(j: dict, prog) -> None:
    from ..style import analyze_references, explain

    p = P.load(j["pid"])
    paths = [Path(r["path"]) for r in p["refs"] if r.get("status") == "ready"]
    if not paths:
        return
    prog("measuring pacing, captions and look", 0.2)
    name = f"project_{p['id']}"
    prof = analyze_references(paths, name)
    P.update(j["pid"], lambda q: q.update(style=name, style_explained=explain(prof)))


def _run_edit(j: dict, prog) -> None:
    from ..pipeline import run

    pid, eid = j["pid"], j["target"]
    p = P.load(pid)
    e = P.find_edit(p, eid)
    footage = [Path(f["path"]) for f in p["footage"] if f.get("status") == "ready"]
    if not footage:
        raise ValueError("Add some footage first (and wait until it's prepared).")
    _set_edit(pid, eid, status="running", error=None)
    out_dir = P.project_dir(pid) / "edits" / eid
    res = run(footage, e["instruction"], style_name=p.get("style"), out_dir=out_dir, name="edit",
              base_options=p.get("options"), progress=prog)
    first = res["outputs"][0]
    _set_edit(pid, eid, status="done", summary=res["summary"], warnings=res["warnings"],
              spec=res["spec"], selection=first["selection"], video=first["video"],
              preview=first["preview"], duration=first["duration"], finished=time.time(),
              timeline=first["info"].get("timeline"))
    # extra versions become their own edits
    for k, o in enumerate(res["outputs"][1:], 2):
        def add(q, o=o, k=k):
            q["edits"].append({"id": uuid.uuid4().hex[:10], "created": time.time(),
                               "instruction": f"{e['instruction']} (version {k})",
                               "summary": res["summary"], "status": "done", "spec": res["spec"],
                               "selection": o["selection"], "video": o["video"],
                               "preview": o["preview"], "duration": o["duration"],
                               "timeline": o["info"].get("timeline")})
        P.update(pid, add)


def _run_render(j: dict, prog) -> None:
    import json

    from ..editspec import EditSpec
    from ..options import RenderOptions
    from ..style import list_profiles, load_profile
    from ..stylematch import render_styled

    pid, eid = j["pid"], j["target"]
    p = P.load(pid)
    e = P.find_edit(p, eid)
    sel = json.loads(Path(e["selection"]).read_text())
    if not sel["clips"]:
        raise ValueError("The edit has no clips left.")
    _set_edit(pid, eid, status="running", error=None)
    name = sel.get("style") or p.get("style")
    prof = load_profile(name) if name and name in list_profiles() else None
    from ..timeline import load as load_doc, render_document

    out = Path(e["video"])
    doc_path = Path(e["timeline"]) if e.get("timeline") else None
    if e.get("render_from") == "timeline" and doc_path and doc_path.exists():
        # edited in the timeline editor: render exactly what's on the timeline
        info = render_document(load_doc(doc_path), out, profile=prof, progress=prog)
    else:
        # changed in quick review / settings: rebuild automatic items, keep what you placed yourself
        old = load_doc(doc_path) if doc_path and doc_path.exists() else None
        info = render_styled(sel, out, spec=EditSpec(**sel.get("spec", {})), profile=prof,
                             options=RenderOptions(**sel.get("options", {})), keep_doc=old, progress=prog)
        doc_path = Path(info["timeline"])
    _set_edit(pid, eid, status="done", duration=info["duration"], preview=info["preview"],
              finished=time.time(), rendered=time.time(), timeline=str(doc_path), render_from=None)


RUNNERS = {"analyze": _run_analyze, "ref": _run_analyze, "style": _run_style,
           "edit": _run_edit, "render": _run_render}


def _loop() -> None:
    while True:
        jid = _q.get()
        j = _jobs.get(jid)
        if not j or j["status"] != "queued":
            continue
        _set(j, status="running", stage="starting", started=time.time())

        def prog(stage: str, x: float, j=j) -> None:
            _set(j, stage=stage, progress=round(max(j["progress"], min(1.0, x)), 3))

        try:
            RUNNERS[j["kind"]](j, prog)
            _set(j, status="done", progress=1.0, stage="done", finished=time.time())
        except Exception as ex:  # report to the phone instead of crashing the worker
            traceback.print_exc()
            msg = str(ex) or ex.__class__.__name__
            _set(j, status="error", error=msg, finished=time.time())
            try:
                if j["kind"] in ("analyze", "ref"):
                    _set_item(j["pid"], j["target"], status="error", error=msg)
                elif j["kind"] in ("edit", "render"):
                    _set_edit(j["pid"], j["target"], status="error", error=msg)
            except Exception:
                pass


def ensure_worker() -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, name="autocut-worker", daemon=True)
        _worker.start()


def resume_unfinished() -> int:
    """After a restart, re-queue anything that was waiting or half-done."""
    n = 0
    for meta in P.list_all():
        try:
            p = P.load(meta["id"])
        except KeyError:
            continue
        for kind in ("footage", "refs"):
            for it in p[kind]:
                if it.get("status") in ("queued", "processing"):
                    queue_analyze(p["id"], it["id"])
                    n += 1
        for e in p["edits"]:
            if e.get("status") in ("queued", "running"):
                (queue_render if e.get("selection") else queue_edit)(p["id"], e["id"])
                n += 1
    return n
