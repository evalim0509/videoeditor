# autocut

A fully offline video editor that runs on **your computer** and is used from **your phone**.
Drop in raw footage and a few videos whose style you like, say or type what you want —
*"funny 60 second vlog with captions"* — and autocut picks the best moments and edits them in that
style. No cloud services, no AI APIs: everything (speech recognition, laughter detection, picture
search, editing) runs locally. You can change every decision afterwards.

## Install (once)

| Computer | Run this in the autocut folder |
|---|---|
| Windows 10/11 | `Set-ExecutionPolicy -Scope Process Bypass -Force; .\setup\windows.ps1` (PowerShell) |
| Mac | `bash setup/mac.sh` |
| Ubuntu/Debian | `bash setup/linux.sh` |

The script installs Python, FFmpeg, Git and Tailscale if missing, picks the right PyTorch for your
GPU (NVIDIA / Apple Silicon / CPU), downloads the AI models once, makes autocut **start
automatically when you log in**, and puts an **autocut** shortcut on your desktop.

## Use it from your phone

1. Install **Tailscale** on your phone (App Store / Google Play) and sign in with the same account
   as on the computer.
2. Open the address `autocut phone` printed — `https://<your-computer>.<tailnet>.ts.net` — in Safari
   or Chrome. Log in (first password: `data/FIRST_LOGIN.txt` on the computer).
3. Share → **Add to Home Screen** to install it like an app.

On the same Wi-Fi you can also use `http://<computer-ip>:8765` (everything works except the
microphone — phones only allow the mic on https, which Tailscale provides).

## Commands (for the curious)

`autocut run --footage F --refs R "instruction"` does everything in one go. Each stage also has
its own command: `ingest`, `transcribe`, `audio`, `shots`, `inspect`, `visual`, `search`, `style`,
`parse`, `voice`, `select`, `render`, `export`, `correct`, `context`, `serve`, `install`, `phone`.
`autocut --help` lists them all.

See `BUILD_PLAN.md` for the design and `PROGRESS.md` for every technical decision.
