"""Projects: one folder per project under data/projects/<id>/ with a project.json.

project.json
{
  "id", "name", "created",
  "footage": [{"id", "name", "path", "size", "hash", "status", "error"}],
  "refs":    [... same ...],
  "style": null | preset name,             # chosen preset (or the one built from refs)
  "instruction": "", "options": {RenderOptions}, "context": "",
  "edits": [{"id", "created", "instruction", "summary", "status", "selection", "video",
             "preview", "duration", "error"}]
}
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from ..config import get_settings
from ..options import RenderOptions

_lock = threading.RLock()


def projects_dir() -> Path:
    d = get_settings().projects_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_dir(pid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]{6,32}", pid):
        raise KeyError(pid)
    return projects_dir() / pid


def _file(pid: str) -> Path:
    return project_dir(pid) / "project.json"


def load(pid: str) -> dict:
    with _lock:
        f = _file(pid)
        if not f.exists():
            raise KeyError(pid)
        return json.loads(f.read_text())


def save(p: dict) -> dict:
    with _lock:
        f = _file(p["id"])
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, indent=2))
        tmp.replace(f)
    return p


def update(pid: str, fn) -> dict:
    """Atomically load → modify → save."""
    with _lock:
        p = load(pid)
        fn(p)
        return save(p)


def create(name: str) -> dict:
    pid = uuid.uuid4().hex[:12]
    d = project_dir(pid)
    for sub in ("footage", "refs", "edits", "uploads"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    p = {"id": pid, "name": (name or "Untitled project").strip()[:80], "created": time.time(),
         "footage": [], "refs": [], "style": None, "instruction": "",
         "options": RenderOptions().model_dump(), "context": "", "edits": []}
    return save(p)


def list_all() -> list[dict]:
    out = []
    for f in sorted(projects_dir().glob("*/project.json")):
        try:
            p = json.loads(f.read_text())
            out.append({"id": p["id"], "name": p["name"], "created": p["created"],
                        "footage": len(p["footage"]), "edits": len(p["edits"]),
                        "thumb": next((e["id"] for e in reversed(p["edits"]) if e.get("status") == "done"), None)})
        except Exception:
            continue
    return sorted(out, key=lambda x: -x["created"])


def delete(pid: str) -> None:
    with _lock:
        shutil.rmtree(project_dir(pid), ignore_errors=True)


def find_item(p: dict, item_id: str) -> tuple[str, dict] | None:
    for kind in ("footage", "refs"):
        for it in p[kind]:
            if it["id"] == item_id:
                return kind, it
    return None


def find_edit(p: dict, eid: str) -> dict | None:
    return next((e for e in p["edits"] if e["id"] == eid), None)
