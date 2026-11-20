"""Web app API: projects, settings, resumable uploads, edit guards, static shell."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_home, monkeypatch):
    from autocut.web import auth, jobs
    from autocut.web.app import create_app

    queued = []
    monkeypatch.setattr(jobs, "queue_analyze", lambda pid, item: queued.append(item))
    auth.limiter._fails.clear()
    auth.set_password("correct-horse")
    c = TestClient(create_app())
    c.post("/api/login", json={"password": "correct-horse"})
    c.queued = queued
    return c


def test_shell_is_public_but_data_is_not(tmp_home):
    from autocut.web.app import create_app

    c = TestClient(create_app())
    assert c.get("/").status_code == 200 and "autocut" in c.get("/").text
    assert c.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert c.get("/sw.js").status_code == 200
    assert c.get("/api/projects").status_code == 401


def test_project_crud_and_settings(client):
    p = client.post("/api/projects", json={"name": "Trip"}).json()
    assert client.get("/api/projects").json()[0]["name"] == "Trip"
    r = client.patch(f"/api/projects/{p['id']}", json={"options": {"caption_case": "lower", "aspect": "9:16"},
                                                        "instruction": "funny 30 seconds"})
    assert r.json()["options"]["caption_case"] == "lower" and r.json()["options"]["aspect"] == "9:16"
    assert r.json()["options"]["color_match"] is False  # untouched defaults stay
    assert client.patch(f"/api/projects/{p['id']}", json={"options": {"caption_case": "wavy"}}).status_code == 400
    assert client.post(f"/api/projects/{p['id']}/edits", json={"instruction": "x"}).status_code == 400  # no footage
    assert client.delete(f"/api/projects/{p['id']}").json()["ok"]
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_resumable_upload(client):
    pid = client.post("/api/projects", json={"name": "U"}).json()["id"]
    data = bytes(range(256)) * 4000  # ~1 MB
    start = {"filename": "IMG 1.MOV", "size": len(data), "kind": "footage", "fingerprint": "IMG 1.MOV|1|2"}
    assert client.post(f"/api/projects/{pid}/uploads", json={**start, "filename": "a.exe"}).status_code == 415
    up = client.post(f"/api/projects/{pid}/uploads", json=start).json()
    uid = up["upload_id"]
    assert up["offset"] == 0
    r = client.put(f"/api/uploads/{uid}?offset=0", content=data[:300_000])
    assert r.json() == {"offset": 300_000, "done": False}
    # connection drops; the phone asks again with the same fingerprint → resumes at 300000
    up2 = client.post(f"/api/projects/{pid}/uploads", json=start).json()
    assert up2 == {"upload_id": uid, "offset": 300_000}
    # a stale offset is refused with the server's offset so the client can re-sync
    r = client.put(f"/api/uploads/{uid}?offset=0", content=data[:10])
    assert r.status_code == 409 and r.json()["offset"] == 300_000
    r = client.put(f"/api/uploads/{uid}?offset=300000", content=data[300_000:])
    assert r.json()["done"] is True
    p = client.get(f"/api/projects/{pid}").json()
    assert p["footage"][0]["name"] == "IMG 1.mov" and p["footage"][0]["size"] == len(data)
    assert client.queued == [p["footage"][0]["id"]]       # analysis queued automatically
    from pathlib import Path
    assert Path(p["footage"][0]["path"]).read_bytes() == data
