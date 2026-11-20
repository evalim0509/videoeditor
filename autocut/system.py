"""Running autocut on the user's computer: background server, auto-start, launcher/shortcut,
and phone access (home Wi-Fi + Tailscale HTTPS).

Everything here is per-user and needs no admin rights:
- Windows: a hidden VBScript in the Startup folder runs `autocut daemon` at log-in; a desktop
  shortcut (.bat) starts it if needed and opens the browser.
- macOS:   a LaunchAgent (RunAtLoad + KeepAlive); a double-clickable `autocut.command` on the Desktop.
- Linux:   a systemd --user service (Restart=always), falling back to ~/.config/autostart; a
  `.desktop` launcher on the Desktop.
`autocut daemon` itself restarts the server if it ever crashes.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from .config import REPO_ROOT, get_settings

PORT = int(os.environ.get("AUTOCUT_PORT", "8765"))
LABEL = "com.autocut.server"


def system() -> str:
    return platform.system().lower()  # windows | darwin | linux


def python_exe(windowless: bool = False) -> str:
    exe = Path(sys.executable)
    if windowless and system() == "windows":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def is_running(port: int = PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/manifest.webmanifest", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "localhost"


def log_path() -> Path:
    d = get_settings().home / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "server.log"


# ------------------------------------------------------------------ background server

def start_background(port: int = PORT) -> bool:
    """Start `autocut daemon` detached (if not already running). Returns True once it answers."""
    if is_running(port):
        return True
    cmd = [python_exe(windowless=True), "-m", "autocut", "daemon", "--port", str(port)]
    kw: dict = {"cwd": str(REPO_ROOT), "stdin": subprocess.DEVNULL}
    log = open(log_path(), "ab")
    kw["stdout"] = log
    kw["stderr"] = log
    if system() == "windows":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)
    for _ in range(60):
        if is_running(port):
            return True
        time.sleep(0.5)
    return False


def daemon(port: int = PORT) -> None:
    """Run the server and restart it if it ever stops unexpectedly."""
    backoff = 2
    while True:
        started = time.time()
        rc = subprocess.call([python_exe(), "-m", "autocut", "serve", "--port", str(port)],
                             cwd=str(REPO_ROOT))
        if rc == 0:
            return
        if time.time() - started > 60:
            backoff = 2
        print(f"[autocut daemon] server exited ({rc}); restarting in {backoff}s", flush=True)
        time.sleep(backoff)
        backoff = min(60, backoff * 2)


def open_app(port: int = PORT) -> str:
    start_background(port)
    url = f"http://localhost:{port}"
    webbrowser.open(url)
    return url


# ------------------------------------------------------------------ auto-start

def _desktop() -> Path:
    home = Path.home()
    if system() == "windows":
        try:  # honours OneDrive-redirected desktops
            import ctypes.wintypes

            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
            if buf.value:
                return Path(buf.value)
        except Exception:
            pass
    for cand in (home / "Desktop", home / "desktop"):
        if cand.exists():
            return cand
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
        if out:
            return Path(out)
    except Exception:
        pass
    (home / "Desktop").mkdir(exist_ok=True)
    return home / "Desktop"


def install_autostart(port: int = PORT) -> list[str]:
    done = []
    s = system()
    py = python_exe(windowless=True)
    if s == "windows":
        startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
        vbs = startup / "autocut.vbs"
        vbs.write_text(
            'Set sh = CreateObject("WScript.Shell")\r\n'
            f'sh.CurrentDirectory = "{REPO_ROOT}"\r\n'
            f'sh.Run """{py}"" -m autocut daemon --port {port}", 0, False\r\n', encoding="utf-8")
        done.append(f"Starts automatically when you log in: {vbs}")
    elif s == "darwin":
        agents = Path.home() / "Library/LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / f"{LABEL}.plist"
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{python_exe()}</string><string>-m</string><string>autocut</string>
    <string>serve</string><string>--port</string><string>{port}</string></array>
  <key>WorkingDirectory</key><string>{REPO_ROOT}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log_path()}</string>
  <key>StandardErrorPath</key><string>{log_path()}</string>
</dict></plist>
""")
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True)
        done.append(f"LaunchAgent installed (starts at log-in, restarts if it stops): {plist}")
    else:
        unit_dir = Path.home() / ".config/systemd/user"
        unit = unit_dir / "autocut.service"
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit.write_text(f"""[Unit]
Description=autocut video editor server
After=network-online.target

[Service]
WorkingDirectory={REPO_ROOT}
ExecStart={python_exe()} -m autocut serve --port {port}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
""")
        ok = shutil.which("systemctl") and subprocess.run(
            ["systemctl", "--user", "enable", "--now", "autocut.service"], capture_output=True).returncode == 0
        if ok:
            done.append(f"systemd user service enabled (restarts automatically): {unit}")
        else:
            ad = Path.home() / ".config/autostart"
            ad.mkdir(parents=True, exist_ok=True)
            (ad / "autocut.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=autocut server\n"
                f"Exec={python_exe()} -m autocut daemon --port {port}\nPath={REPO_ROOT}\n"
                "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n")
            done.append(f"Autostart entry created (systemd user session not available): {ad / 'autocut.desktop'}")
    return done


def uninstall_autostart() -> list[str]:
    s = system()
    removed = []
    paths = []
    if s == "windows":
        paths = [Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/autocut.vbs"]
    elif s == "darwin":
        p = Path.home() / f"Library/LaunchAgents/{LABEL}.plist"
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        paths = [p]
    else:
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "disable", "--now", "autocut.service"], capture_output=True)
        paths = [Path.home() / ".config/systemd/user/autocut.service", Path.home() / ".config/autostart/autocut.desktop"]
    for p in paths:
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return removed


def create_launcher(port: int = PORT) -> list[str]:
    """Double-clickable launcher + desktop shortcut: starts autocut if needed and opens the app."""
    s = system()
    desk = _desktop()
    made = []
    icon = REPO_ROOT / "autocut/web/static/icon-512.png"
    if s == "windows":
        bat = REPO_ROOT / "autocut-launch.bat"
        bat.write_text(f'@echo off\r\ncd /d "{REPO_ROOT}"\r\n"{python_exe(windowless=True)}" -m autocut open --port {port}\r\n',
                       encoding="utf-8")
        made.append(str(bat))
        ico = REPO_ROOT / "autocut/web/static/autocut.ico"
        try:
            from PIL import Image

            Image.open(icon).save(ico, sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
        except Exception:
            ico = None
        lnk = desk / "autocut.lnk"
        ps = (f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut("{lnk}");'
              f'$s.TargetPath="{bat}";$s.WorkingDirectory="{REPO_ROOT}";$s.WindowStyle=7;'
              + (f'$s.IconLocation="{ico}";' if ico else "") + "$s.Save()")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
        if r.returncode == 0:
            made.append(str(lnk))
        else:  # fallback: a copy of the .bat on the desktop
            shutil.copy(bat, desk / "autocut.bat")
            made.append(str(desk / "autocut.bat"))
    elif s == "darwin":
        cmd = desk / "autocut.command"
        cmd.write_text(f'#!/bin/bash\ncd "{REPO_ROOT}"\n"{python_exe()}" -m autocut open --port {port}\n'
                       "osascript -e 'tell application \"Terminal\" to close front window' >/dev/null 2>&1 &\n")
        cmd.chmod(0o755)
        made.append(str(cmd))
    else:
        entry = ("[Desktop Entry]\nType=Application\nName=autocut\nComment=Offline AI video editor\n"
                 f"Exec={python_exe()} -m autocut open --port {port}\nPath={REPO_ROOT}\nIcon={icon}\n"
                 "Terminal=false\nCategories=AudioVideo;Video;\n")
        for target in (desk / "autocut.desktop", Path.home() / ".local/share/applications/autocut.desktop"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry)
            target.chmod(0o755)
            made.append(str(target))
        subprocess.run(["gio", "set", str(desk / "autocut.desktop"), "metadata::trusted", "true"], capture_output=True)
    return made


# ------------------------------------------------------------------ phone access

def tailscale_bin() -> str | None:
    cands = [shutil.which("tailscale"), "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
             r"C:\Program Files\Tailscale\tailscale.exe"]
    return next((c for c in cands if c and Path(c).exists()), None)


def tailscale_status() -> dict:
    ts = tailscale_bin()
    if not ts:
        return {"installed": False}
    r = subprocess.run([ts, "status", "--json"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"installed": True, "running": False, "error": r.stderr.strip()[-300:]}
    st = json.loads(r.stdout)
    me = st.get("Self", {})
    dns = (me.get("DNSName") or "").rstrip(".")
    return {"installed": True, "running": st.get("BackendState") == "Running",
            "state": st.get("BackendState"), "dns": dns, "ips": me.get("TailscaleIPs", []),
            "https_url": f"https://{dns}" if dns else None}


def tailscale_serve(port: int = PORT) -> dict:
    """Publish autocut inside your private tailnet over HTTPS (not on the public internet)."""
    st = tailscale_status()
    if not st.get("installed"):
        return {**st, "ok": False, "message": "Tailscale isn't installed on this computer yet."}
    if not st.get("running"):
        return {**st, "ok": False, "message": "Tailscale is installed but not logged in — open it and sign in."}
    ts = tailscale_bin()
    r = subprocess.run([ts, "serve", "--bg", "--https=443", f"http://127.0.0.1:{port}"],
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        return {**st, "ok": False, "message": out[-600:]}
    return {**st, "ok": True, "message": out[-600:]}


def allow_firewall(port: int = PORT) -> str:
    """Windows only: allow phones on the home network to reach autocut (needs one admin approval)."""
    if system() != "windows":
        return "Not needed on this system."
    rule = (f'netsh advfirewall firewall add rule name="autocut" dir=in action=allow protocol=TCP '
            f'localport={port} profile=private')
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Start-Process cmd -Verb RunAs -ArgumentList '/c {rule}'"], capture_output=True)
    return "Asked Windows for permission (approve the prompt)." if r.returncode == 0 else r.stderr.decode()[-300:]
