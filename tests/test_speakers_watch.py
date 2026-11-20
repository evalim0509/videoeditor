"""Only-my-voice assignment and the Google Drive drop folder."""
import time

import numpy as np


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def test_two_voice_assignment(tmp_home, monkeypatch):
    from autocut import db, speakers as S

    rng = np.random.default_rng(0)
    me, other = _unit(rng.normal(size=192)), _unit(rng.normal(size=192))
    embs = [_unit(me + rng.normal(scale=.05, size=192)) for _ in range(5)] + \
           [_unit(other + rng.normal(scale=.05, size=192)) for _ in range(4)]
    ranges = [[i * 2.0, i * 2.0 + 1.5] for i in range(9)]
    monkeypatch.setattr(S, "sentence_embeddings", lambda h, force=False: {"ranges": ranges, "emb": np.array(embs, np.float32)})
    S._save_profile(me, "auto: test", 1.0)
    rs = S.speaker_ranges("h")
    assert [r["me"] for r in rs] == [True] * 5 + [False] * 4
    db.put_media("h", __import__("pathlib").Path("x"), 1, {"duration": 20}, None, None)
    S.mark("h", 10.0, 11.5, True)                      # user taps "this is me" on an other-voice line
    assert S.speaker_ranges("h")[5]["me"] is True
    f = S.is_me_fn("h")
    assert f(0.5) is True and f(14.5) is False and f(100) is True


def test_drop_folder_imports_finished_files_only(tmp_home, tmp_path, monkeypatch):
    from autocut.web import jobs, projects as P, watch as W

    queued = []
    monkeypatch.setattr(jobs, "queue_analyze", lambda pid, item: queued.append(item))
    monkeypatch.setattr(jobs, "queue_edit", lambda pid, eid: queued.append(("edit", eid)))
    p = P.create("Trip")
    folder = tmp_path / "My Drive" / "autocut" / "Trip"
    folder.mkdir(parents=True)
    P.update(p["id"], lambda q: q.update(watch_folder=str(folder), auto_edit=True, instruction="funny 30 seconds"))
    f = folder / "IMG_1.MOV"
    f.write_bytes(b"x" * 1000)
    (folder / ".IMG_2.MOV.tmp").write_bytes(b"y")      # Drive's temporary file: ignored
    old = time.time() - 60
    import os
    os.utime(f, (old, old))
    assert W.scan_project(p["id"]) == 0                 # first sighting: maybe still syncing
    assert W.scan_project(p["id"]) == 1                 # unchanged since last scan → import
    assert W.scan_project(p["id"]) == 0                 # never twice
    q = P.load(p["id"])
    assert q["footage"][0]["source"] == "google-drive" and q["footage"][0]["path"] == str(f)
    # automatic edit once everything is ready and things are quiet
    P.update(p["id"], lambda q: q["footage"][0].update(status="ready"))
    W._last_new[p["id"]] = time.time() - 999
    assert W.maybe_auto_edit(p["id"]) is True and P.load(p["id"])["edits"][0]["auto"] is True
    assert W.maybe_auto_edit(p["id"]) is False          # only once per batch


def test_removing_drive_item_keeps_the_file(tmp_home, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from autocut.web import auth, jobs, projects as P
    from autocut.web.app import create_app

    auth.set_password("correct-horse")
    c = TestClient(create_app()); c.post("/api/login", json={"password": "correct-horse"})
    p = P.create("X")
    f = tmp_path / "drive.mov"; f.write_bytes(b"v")
    P.update(p["id"], lambda q: q["footage"].append({"id": "i1", "name": "drive.mov", "path": str(f), "size": 1, "status": "ready"}))
    assert c.delete(f"/api/projects/{p['id']}/items/i1").status_code == 200
    assert f.exists()                                  # your Google Drive file is never deleted
