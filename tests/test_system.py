"""Computer setup helpers (Linux paths tested here; Windows/macOS share the same logic)."""
import platform

import pytest


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux layout")
def test_launcher_and_autostart_files(tmp_path, monkeypatch):
    from autocut import system as S

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(S.shutil, "which", lambda name: None)   # no systemctl → autostart fallback
    made = S.create_launcher(9999)
    desk = tmp_path / "Desktop" / "autocut.desktop"
    assert str(desk) in made and "autocut open --port 9999" in desk.read_text()
    done = S.install_autostart(9999)
    unit = (tmp_path / ".config/systemd/user/autocut.service").read_text()
    assert "Restart=always" in unit and "serve --port 9999" in unit
    assert (tmp_path / ".config/autostart/autocut.desktop").exists() and done
    removed = S.uninstall_autostart()
    assert len(removed) == 2


def test_tailscale_missing_is_explained(monkeypatch):
    from autocut import system as S

    monkeypatch.setattr(S, "tailscale_bin", lambda: None)
    r = S.tailscale_serve(8765)
    assert r["ok"] is False and "isn't installed" in r["message"]
