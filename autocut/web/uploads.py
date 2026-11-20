"""Chunked, resumable uploads (large 4K phone videos over shaky Wi-Fi / mobile data).

Protocol
  POST /api/projects/{pid}/uploads {filename, size, kind, fingerprint}
       → {upload_id, offset}     (same fingerprint = resume the existing upload at its offset)
  PUT  /api/uploads/{upload_id}?offset=N   body: raw bytes of the next chunk
       → {offset, done}          (409 with the server's offset if they disagree → client re-syncs)
  GET  /api/uploads/{upload_id} → {offset, size, done}
When the last byte arrives the file is moved into the project and analysis is queued.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from . import projects as P

MAX_CHUNK = 64 * 1024 * 1024
ALLOWED = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".3gp", ".hevc"}


class UploadError(Exception):
    def __init__(self, status: int, msg: str, offset: int | None = None):
        super().__init__(msg)
        self.status, self.offset = status, offset


def _meta_path(uid: str) -> Path:
    pid, _ = uid.split("_", 1)
    return P.project_dir(pid) / "uploads" / f"{uid}.json"


def _load(uid: str) -> dict:
    if not re.fullmatch(r"[a-z0-9]{6,32}_[a-z0-9]{6,32}", uid):
        raise UploadError(404, "Unknown upload.")
    f = _meta_path(uid)
    if not f.exists():
        raise UploadError(404, "Unknown upload.")
    return json.loads(f.read_text())


def _save(u: dict) -> None:
    _meta_path(u["id"]).write_text(json.dumps(u))


def safe_name(name: str) -> str:
    base = Path(name).name
    stem = re.sub(r"[^\w\-. ]+", "_", Path(base).stem).strip(" .")[:80] or "video"
    return stem + Path(base).suffix.lower()


def start(pid: str, filename: str, size: int, kind: str, fingerprint: str) -> dict:
    P.load(pid)  # 404 if the project doesn't exist
    if kind not in ("footage", "refs"):
        raise UploadError(400, "kind must be footage or refs")
    if Path(filename).suffix.lower() not in ALLOWED:
        raise UploadError(415, f"{Path(filename).suffix or 'This file type'} isn't a supported video.")
    up = P.project_dir(pid) / "uploads"
    for f in up.glob("*.json"):
        u = json.loads(f.read_text())
        if u["fingerprint"] == fingerprint and u["kind"] == kind and not u.get("done"):
            return {"upload_id": u["id"], "offset": (up / f"{u['id']}.part").stat().st_size
                    if (up / f"{u['id']}.part").exists() else 0}
    uid = f"{pid}_{uuid.uuid4().hex[:12]}"
    u = {"id": uid, "pid": pid, "filename": safe_name(filename), "size": int(size), "kind": kind,
         "fingerprint": fingerprint, "created": time.time(), "done": False}
    _save(u)
    (up / f"{uid}.part").touch()
    return {"upload_id": uid, "offset": 0}


def status(uid: str) -> dict:
    u = _load(uid)
    part = _meta_path(uid).with_suffix(".part")
    off = part.stat().st_size if part.exists() else u["size"]
    return {"offset": off, "size": u["size"], "done": u.get("done", False), "item": u.get("item")}


async def append(uid: str, offset: int, stream) -> dict:
    u = _load(uid)
    if u.get("done"):
        return {"offset": u["size"], "done": True, "item": u.get("item")}
    part = _meta_path(uid).with_suffix(".part")
    cur = part.stat().st_size
    if offset != cur:
        raise UploadError(409, "Offset mismatch", cur)
    written = 0
    with open(part, "ab") as f:
        async for chunk in stream:
            written += len(chunk)
            if written > MAX_CHUNK or cur + written > u["size"]:
                f.truncate(cur)
                raise UploadError(413, "Chunk too large.")
            f.write(chunk)
    new = cur + written
    if new == u["size"]:
        return finish(u, part)
    return {"offset": new, "done": False}


def finish(u: dict, part: Path) -> dict:
    from . import jobs

    pid, kind = u["pid"], u["kind"]
    dest_dir = P.project_dir(pid) / kind
    dest = dest_dir / u["filename"]
    i = 1
    while dest.exists():
        dest = dest_dir / f"{Path(u['filename']).stem}_{i}{Path(u['filename']).suffix}"
        i += 1
    part.replace(dest)
    item = {"id": uuid.uuid4().hex[:10], "name": u["filename"], "path": str(dest),
            "size": u["size"], "status": "queued", "added": time.time()}
    P.update(pid, lambda p: p[kind].append(item))
    u.update(done=True, item=item["id"])
    _save(u)
    jobs.queue_analyze(pid, item["id"])
    return {"offset": u["size"], "done": True, "item": item["id"]}
