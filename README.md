# autocut

A fully offline video editor that runs on **your computer** and is used from **your phone**.

1. Drop your videos into a **Google Drive folder** (from the Drive app on your phone) — or upload them in the app.
2. Describe the edit in your own words — *"funny 60 second vlog with captions, lowercase captions, no zoom cuts, keep my colours"* — typed or spoken.
3. autocut picks the best moments and edits them in the style of your reference videos, automatically.
4. Change anything afterwards: clips, order, trims, captions (only **your** voice is captioned), shape, colours, zoom cuts… then download or open the edit in Final Cut / Resolve / Premiere.

No cloud services and no AI APIs: speech recognition, voice recognition, laughter detection, picture search and editing all run locally. The internet is only used once, to download the AI models during setup.

## Install (once, no typing)

1. On GitHub open the repository, switch to the branch `claude/pensive-goldberg-hmc49c`, then **Code ▸ Download ZIP**, and unzip it somewhere permanent (e.g. Documents).
2. Double-click the installer in that folder:
   - **Windows:** `INSTALL-Windows.bat` (approve the installer prompts if Windows asks).
   - **Mac:** `INSTALL-Mac.command` (if macOS says it's from an unidentified developer: right-click ▸ Open).
3. Wait for it to finish (first time ~20–40 min: Python, FFmpeg, Tailscale, AI models). It then:
   - starts autocut **automatically whenever you log in** and keeps it running in the background,
   - puts an **autocut** icon on your desktop (double-click to open the app),
   - prints your **first password** (also in `data/FIRST_LOGIN.txt`) and your **phone address**.

Also install **Google Drive for desktop** on the computer (if you want the drop-folder workflow) and sign in.

## Use it from your phone

1. Install **Tailscale** on your phone (App Store / Google Play) and sign in with the same account as on the computer.
2. Open the address the installer printed — `https://<your-computer>.<tailnet>.ts.net` — in Safari/Chrome and log in.
3. Share ▸ **Add to Home Screen** to install it like an app.
4. In a project, tap **Use folder "autocut/<project>"** under *Google Drive*, switch on **Edit automatically when new videos arrive**, and write how you want it edited. From now on, put videos into that Drive folder from your phone and the edit appears in the app.

On the same Wi-Fi you can also use `http://<computer-ip>:8765` (everything except the microphone — phones only allow the mic on https, which Tailscale provides).

## Commands (optional)

`autocut run --footage F --refs R "instruction"` does everything in one go. Every stage also has its own command: `ingest`, `transcribe`, `audio`, `shots`, `inspect`, `visual`, `search`, `style`, `parse`, `voice`, `select`, `render`, `export`, `correct`, `context`, `serve`, `install`, `phone`, `open`. `autocut --help` lists them.

See `BUILD_PLAN.md` for the design and `PROGRESS.md` for every technical decision.
