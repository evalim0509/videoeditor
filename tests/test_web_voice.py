"""Web API: login is required everywhere; voice + parse endpoints work end to end."""
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_home):
    from autocut.web import auth
    from autocut.web.app import create_app

    auth.limiter._fails.clear()
    auth.set_password("correct-horse")
    return TestClient(create_app())


def login(c, pw="correct-horse"):
    return c.post("/api/login", json={"password": pw})


def test_everything_requires_login(client):
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/parse", json={"text": "funny"}).status_code == 401
    assert client.post("/api/voice", files={"audio": ("v.wav", b"x")}).status_code == 401


def test_wrong_password_and_rate_limit(client):
    for _ in range(8):
        assert login(client, "nope").status_code == 401
    assert login(client, "nope").status_code == 429
    assert login(client).status_code == 429  # still blocked even with the right one


def test_login_parse_logout(client):
    r = login(client)
    assert r.status_code == 200 and client.get("/api/me").status_code == 200
    r = client.post("/api/parse", json={"text": "funny 60 second tiktok with captions"})
    assert r.json()["spec"]["target_duration"] == 60
    assert "tiktok" in r.json()["summary"]
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_bearer_token_and_password_change(client):
    token = login(client).json()["token"]
    c2 = TestClient(client.app)
    assert c2.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    r = client.post("/api/password", json={"current": "correct-horse", "new": "battery-staple"})
    assert r.status_code == 200
    # old token is invalid after the change (secret rotated), new cookie works
    assert c2.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/api/me").status_code == 200


def test_first_run_generates_password(tmp_home):
    from autocut.web import auth

    phrase = auth.ensure_password()
    assert phrase and auth.check_password(phrase)
    assert (tmp_home.home / "FIRST_LOGIN.txt").exists()
    assert auth.ensure_password() is None


@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="needs espeak-ng to synthesise speech")
def test_voice_endpoint_webm(client, tmp_path):
    wav, webm = tmp_path / "v.wav", tmp_path / "v.webm"
    subprocess.run(["espeak-ng", "-s", "150", "-w", str(wav),
                    "Make a funny sixty second tiktok with captions"], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "libopus", str(webm)], check=True)
    login(client)
    with open(webm, "rb") as f:
        r = client.post("/api/voice", files={"audio": ("voice.webm", f, "audio/webm")})
    assert r.status_code == 200, r.text
    spec = r.json()["spec"]
    assert spec["mood"] == "funny" and spec["target_duration"] == 60 and spec["captions"] is True
