from typer.testing import CliRunner

from autocut.cli import app

runner = CliRunner()


def test_help_lists_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "hardware" in r.output
    assert "status" in r.output


def test_hardware_command():
    r = runner.invoke(app, ["hardware"])
    assert r.exit_code == 0
    assert "whisper_model" in r.output


def test_cache_roundtrip(tmp_home):
    from autocut import db

    assert db.cache_get("abc", "stage", "1") is None
    db.cache_put("abc", "stage", "1", {"x": [1, 2, 3]})
    assert db.cache_get("abc", "stage", "1") == {"x": [1, 2, 3]}
    assert db.cache_get("abc", "stage", "2") is None
    db.blob_put("abc", "emb", "1", b"\x00\x01")
    assert db.blob_get("abc", "emb", "1") == b"\x00\x01"
