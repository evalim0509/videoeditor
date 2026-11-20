"""Google Drive drop folder + Drive link import.

Google Drive for desktop keeps a synced copy of your Drive on the computer (Windows: "G:\\My Drive",
Mac: ~/Library/CloudStorage/GoogleDrive-…/My Drive). A project can watch a folder there: drop
videos into it from your phone's Google Drive app and, once they've finished syncing to the
computer, autocut imports them (in place — no copies) and, if you switched it on, makes an edit with
the project's instruction automatically. All processing stays on your computer.
"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from ..config import VIDEO_EXTS
from . import jobs as J
from . import projects as P

SCAN_EVERY = float(os.environ.get("AUTOCUT_WATCH_SECONDS", "10"))        # seconds between checks
STABLE_FOR = 2          # unchanged size on this many scans in a row = finished syncing
QUIET_BEFORE_EDIT = float(os.environ.get("AUTOCUT_QUIET_SECONDS", "30"))  # no new files for this long
SKIP = re.compile(r"(^\.|^~\$|\.(tmp|part|crdownload|download|gdoc|ini)$)", re.I)

_seen: dict[str, dict[str, tuple[int, float, int]]] = {}
_last_new: dict[str, float] = {}
_thread: threading.Thread | None = None
_lock = threading.Lock()


# ------------------------------------------------------------------ Drive folder detection

def drive_roots() -> list[str]:
    roots: list[Path] = []
    home = Path.home()
    if os.name == "nt":
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            for name in ("My Drive", "Mi unidad", "Mon Drive", "Meine Ablage"):
                p = Path(f"{letter}:\\") / name
                if p.exists():
                    roots.append(p)
        roots += [home / "Google Drive", home / "My Drive"]
    else:
        cs = home / "Library" / "CloudStorage"
        if cs.exists():
            for d in sorted(cs.glob("GoogleDrive-*")):
                roots += [d / "My Drive"] if (d / "My Drive").exists() else [d]
        roots += [home / "Google Drive", home / "GoogleDrive", home / "google-drive", home / "My Drive"]
    out = []
    for r in roots:
        if r.exists() and str(r) not in out:
            out.append(str(r))
    return out


def suggested_folder(project_name: str) -> str | None:
    roots = drive_roots()
    if not roots:
        return None
    safe = re.sub(r"[^\w\- ]+", "_", project_name).strip() or "project"
    return str(Path(roots[0]) / "autocut" / safe)


# ------------------------------------------------------------------ watching

def _videos(folder: Path) -> list[Path]:
    out = []
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not SKIP.search(p.name):
            out.append(p)
    return sorted(out)


def scan_project(pid: str) -> int:
    """Import finished files from the project's watched folder. Returns how many were added."""
    try:
        p = P.load(pid)
    except KeyError:
        return 0
    folder = p.get("watch_folder")
    if not folder or not Path(folder).is_dir():
        return 0
    known = {it.get("source_path") or it["path"] for it in p["footage"]}
    seen = _seen.setdefault(pid, {})
    added = 0
    now = time.time()
    for f in _videos(Path(folder)):
        key = str(f)
        if key in known:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        prev = seen.get(key)
        stable = (prev[2] + 1) if (prev and prev[0] == st.st_size and st.st_size > 0) else 0
        seen[key] = (st.st_size, st.st_mtime, stable)
        if stable >= STABLE_FOR - 1 and now - st.st_mtime > 5:
            item = {"id": uuid.uuid4().hex[:10], "name": f.name, "path": key, "source_path": key,
                    "source": "google-drive", "size": st.st_size, "status": "queued", "added": now}
            P.update(pid, lambda q: (q["footage"].append(item), q.update(auto_edit_pending=True)))
            J.queue_analyze(pid, item["id"])
            seen.pop(key, None)
            added += 1
    if added:
        _last_new[pid] = now
    return added


def maybe_auto_edit(pid: str) -> bool:
    """Start an automatic edit once new footage is all prepared and nothing new arrived lately."""
    p = P.load(pid)
    if not (p.get("auto_edit") and p.get("auto_edit_pending") and p.get("instruction", "").strip()):
        return False
    if time.time() - _last_new.get(pid, 0) < QUIET_BEFORE_EDIT:
        return False
    if any(f.get("status") in ("queued", "processing") for f in p["footage"]):
        return False
    if any(j["status"] in ("queued", "running") for j in J.for_project(pid)):
        return False
    if not any(f.get("status") == "ready" for f in p["footage"]):
        return False
    e = {"id": uuid.uuid4().hex[:10], "created": time.time(), "instruction": p["instruction"],
         "status": "queued", "auto": True}
    P.update(pid, lambda q: (q["edits"].append(e), q.update(auto_edit_pending=False)))
    J.queue_edit(pid, e["id"])
    return True


def _loop() -> None:
    while True:
        try:
            for meta in P.list_all():
                pid = meta["id"]
                scan_project(pid)
                maybe_auto_edit(pid)
        except Exception as ex:  # keep watching no matter what
            print(f"[autocut watch] {ex}")
        time.sleep(SCAN_EVERY)


def ensure_watcher() -> None:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="autocut-watch", daemon=True)
            _thread.start()


# ------------------------------------------------------------------ Drive link import

def drive_file_id(url: str) -> str | None:
    for pat in (r"/file/d/([\w-]{10,})", r"[?&]id=([\w-]{10,})", r"/d/([\w-]{10,})"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def download_drive_file(url: str, dest_dir: Path) -> Path:
    fid = drive_file_id(url)
    if not fid:
        raise ValueError("That doesn't look like a Google Drive file link.")
    dl = f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
    with urllib.request.urlopen(dl, timeout=60) as r:
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
        name = urllib.parse.unquote(m.group(1)) if m else f"drive_{fid}.mp4"
        if "text/html" in r.headers.get("Content-Type", ""):
            raise ValueError("Google Drive didn't send the video — make sure the link is shared with "
                             "'Anyone with the link'.")
        from .uploads import safe_name

        dest = dest_dir / safe_name(name)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    tmp.replace(dest)
    return dest


def import_link(pid: str, url: str, kind: str = "footage") -> list[str]:
    """Download a Drive file (or folder) link into the project and queue analysis."""
    dest_dir = P.project_dir(pid) / kind
    files: list[Path] = []
    if "/folders/" in url:
        try:
            import gdown
        except ImportError as e:
            raise ValueError("Folder links need the 'gdown' package (pip install gdown).") from e
        out = gdown.download_folder(url, output=str(dest_dir / f"drive_{uuid.uuid4().hex[:6]}"),
                                    quiet=True, remaining_ok=True) or []
        files = [Path(f) for f in out if Path(f).suffix.lower() in VIDEO_EXTS]
    else:
        files = [download_drive_file(url, dest_dir)]
    ids = []
    for f in files:
        item = {"id": uuid.uuid4().hex[:10], "name": f.name, "path": str(f), "source": "drive-link",
                "size": f.stat().st_size, "status": "queued", "added": time.time()}
        P.update(pid, lambda q, item=item: q[kind].append(item))
        J.queue_analyze(pid, item["id"])
        ids.append(item["id"])
    return ids
