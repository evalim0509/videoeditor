"""SQLite cache. Every analysis result is keyed by (file_hash, stage, version).

Tables
------
media     one row per unique file content (hash) with ffprobe metadata and proxy paths
paths     path -> hash, with size+mtime so unchanged files are never re-hashed
analysis  cached JSON results per (hash, stage, version)
blobs     cached binary results (e.g. embeddings) per (hash, stage, version)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    hash        TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    meta        TEXT NOT NULL,
    proxy_path  TEXT,
    audio_path  TEXT,
    ingested_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS paths (
    path   TEXT PRIMARY KEY,
    size   INTEGER NOT NULL,
    mtime  REAL NOT NULL,
    hash   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis (
    hash       TEXT NOT NULL,
    stage      TEXT NOT NULL,
    version    TEXT NOT NULL,
    result     TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (hash, stage, version)
);
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash        TEXT NOT NULL,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    decision    TEXT NOT NULL,
    mood        TEXT,
    instruction TEXT,
    features    TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS blobs (
    hash       TEXT NOT NULL,
    stage      TEXT NOT NULL,
    version    TEXT NOT NULL,
    data       BLOB NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (hash, stage, version)
);
"""

_lock = threading.Lock()
_initialised: set[str] = set()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = Path(db_path or get_settings().db_path)
    conn = _connect(db_path)
    try:
        key = str(db_path)
        if key not in _initialised:
            with _lock:
                conn.executescript(SCHEMA)
                _initialised.add(key)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- media / paths

def lookup_path(path: Path) -> str | None:
    """Return the cached hash for a path if its size and mtime are unchanged."""
    st = path.stat()
    with connect() as c:
        row = c.execute("SELECT size, mtime, hash FROM paths WHERE path=?", (str(path),)).fetchone()
    if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1e-3:
        return row["hash"]
    return None


def remember_path(path: Path, file_hash: str) -> None:
    st = path.stat()
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO paths(path, size, mtime, hash) VALUES (?,?,?,?)",
                  (str(path), st.st_size, st.st_mtime, file_hash))


def get_media(file_hash: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM media WHERE hash=?", (file_hash,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["meta"] = json.loads(d["meta"])
    return d


def find_media(ref: str) -> dict | None:
    """Find media by hash, hash prefix, or path."""
    p = Path(ref)
    if p.exists():
        h = lookup_path(p.resolve())
        if h:
            return get_media(h)
    with connect() as c:
        row = c.execute("SELECT hash FROM media WHERE hash LIKE ? OR path=?",
                        (ref + "%", str(p.resolve()) if p.exists() else ref)).fetchone()
    return get_media(row["hash"]) if row else None


def put_media(file_hash: str, path: Path, size: int, meta: dict,
              proxy_path: Path | None, audio_path: Path | None) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO media(hash, path, size, meta, proxy_path, audio_path, ingested_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (file_hash, str(path), size, json.dumps(meta),
             str(proxy_path) if proxy_path else None,
             str(audio_path) if audio_path else None, time.time()))


def all_media() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM media ORDER BY ingested_at").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"])
        out.append(d)
    return out


# ---------------------------------------------------------------- analysis cache

def cache_get(file_hash: str, stage: str, version: str) -> Any | None:
    with connect() as c:
        row = c.execute("SELECT result FROM analysis WHERE hash=? AND stage=? AND version=?",
                        (file_hash, stage, version)).fetchone()
    return json.loads(row["result"]) if row else None


def cache_put(file_hash: str, stage: str, version: str, result: Any) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO analysis(hash, stage, version, result, created_at)"
                  " VALUES (?,?,?,?,?)",
                  (file_hash, stage, version, json.dumps(result), time.time()))


def blob_get(file_hash: str, stage: str, version: str) -> bytes | None:
    with connect() as c:
        row = c.execute("SELECT data FROM blobs WHERE hash=? AND stage=? AND version=?",
                        (file_hash, stage, version)).fetchone()
    return bytes(row["data"]) if row else None


def blob_put(file_hash: str, stage: str, version: str, data: bytes) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO blobs(hash, stage, version, data, created_at)"
                  " VALUES (?,?,?,?,?)", (file_hash, stage, version, data, time.time()))


def cached_stages(file_hash: str) -> list[tuple[str, str]]:
    with connect() as c:
        rows = c.execute("SELECT stage, version FROM analysis WHERE hash=? UNION "
                         "SELECT stage, version FROM blobs WHERE hash=?",
                         (file_hash, file_hash)).fetchall()
    return [(r["stage"], r["version"]) for r in rows]


# ---------------------------------------------------------------- review decisions

def log_feedback(file_hash: str, start: float, end: float, decision: str, *, mood: str | None = None,
                 instruction: str | None = None, features: dict | None = None) -> None:
    """Record keep/remove/swap/add/trim decisions from the review screen (for tuning weights)."""
    with connect() as c:
        c.execute("INSERT INTO feedback(hash, start, end, decision, mood, instruction, features, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?)",
                  (file_hash, start, end, decision, mood, instruction,
                   json.dumps(features) if features else None, time.time()))


def feedback_rows(limit: int = 1000) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
