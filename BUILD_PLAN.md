# autocut — build plan

**autocut** is a fully offline, local video editor. You drop in raw footage and reference videos,
type or speak an instruction like *"funny 60 second tiktok with captions"*, and it picks the best
moments and edits them in the style of the references.

The whole app is usable from a **phone's web browser**: upload footage from the phone, give
instructions (typed or voice), review and edit clips, download the result. The computer does all
the processing; the phone is just the interface.

## Permanent rules (also in CLAUDE.md)

- Fully offline processing on the user's computer. No cloud APIs, no LLMs. Downloading model weights
  once during setup is fine.
- FFmpeg for all media work. Analysis runs on 480p proxies; final render uses originals.
- Every analysis result is cached in SQLite keyed by file hash, so nothing is analysed twice.
- Each stage is a separate module with its own CLI command (Typer) so it can be tested alone.
- Instructions are always converted into a structured EditSpec JSON before editing logic runs.
- Never cut mid-word.
- The web interface is mobile-first: it must work well on a phone screen with touch, then scale up
  to desktop.
- The app must require a login (password) because it is reachable over the network.
- Everything automatic must be changeable afterwards — manually in the app, or by telling the app
  some context so it adjusts itself (added by the user at checkpoint 3).

## How we work

- Progress is tracked in `PROGRESS.md` with checkboxes; technical decisions are noted there.
- One phase at a time. After each phase: run it on `test_footage/`, verify the output (read
  transcripts, inspect scores, extract and look at frames, check renders with ffprobe), fix errors,
  then git commit and tick it off in `PROGRESS.md`.
- No questions about technical choices — sensible defaults, noted in `PROGRESS.md`.
- Stop and ask the user only at checkpoints marked **[ME]**.

## Architecture

```
phone browser (PWA) ──HTTPS (Tailscale)──▶ FastAPI server (autocut serve) on the computer
                                                │
             ┌──────────────────────────────────┼─────────────────────────────────────┐
             ▼                                  ▼                                     ▼
   ingest ─▶ transcribe ─▶ audio/shots ─▶ visual ─▶ style     intent (EditSpec) ◀─ voice
             (all cached in SQLite by file hash; analysis on 480p proxies)
                                                │
                                   select (clips + reasons) ─▶ review (web) ─▶ render + stylematch
                                                                                  │
                                                                     output MP4 / FCPXML / EDL
```

Module per stage (`autocut/<stage>.py`), each with its own `autocut <command>`:

| Stage | Module | CLI |
|---|---|---|
| hardware detection | `hardware.py` | `autocut hardware` |
| ingest + proxies | `ingest.py` | `autocut ingest <paths>` |
| transcription | `transcribe.py` | `autocut transcribe <file>` |
| shots | `shots.py` | `autocut shots <file>` |
| audio events/energy | `audio.py` | `autocut audio <file>` |
| top moments | `inspect_cmd.py` | `autocut inspect <file>` |
| CLIP + faces | `visual.py` | `autocut visual <file>`, `autocut search "query"` |
| style profiles | `style.py` | `autocut style <refs>` |
| intent → EditSpec | `intent/` | `autocut parse "instruction"` |
| voice | `voice.py` | `autocut voice <audio>` |
| clip selection | `select.py` | `autocut select ...` |
| render | `render.py` | `autocut render ...` |
| style matching | `stylematch.py` | (used by render) |
| one command | `pipeline.py` | `autocut run --footage --refs "instruction"` |
| web app | `web/` | `autocut serve` |
| NLE export | `export.py` | `autocut export ...` |

## Phases

1. Project skeleton, requirements, CLI (`autocut --help`).
2. Ingest: hash files, ffprobe metadata, 480p proxies, SQLite. Skip already-ingested files. Handle
   phone footage properly: HEVC/H.265, iPhone .MOV, HDR (tone-map to SDR for output), rotation
   metadata, and variable frame rate (convert to constant frame rate to prevent audio drift).
3. Transcription with faster-whisper, word timestamps, model size picked by hardware.
4. Audio and shot analysis: PySceneDetect shots; PANNs per-second scores for laughter, cheering,
   applause, music, speech, shouting; librosa loudness/energy. Add `autocut inspect <file>` listing
   top moments. **[ME]** Show the top 10 laughter and energy timestamps so the user can confirm.
5. Visual analysis: CLIP/SigLIP embeddings at 1 fps via open_clip with `autocut search "query"`;
   mediapipe face positions.
6. Style analyzer: from reference videos, save a StyleProfile preset (shot length distribution, cuts
   per minute, aspect ratio, beat-synced cuts, captions present, loudness,
   brightness/contrast/saturation). Explain the result in plain language.
7. Intent parser: rule-based (regex + editable YAML synonym file) → EditSpec with mood
   (funny/hype/emotional/calm/informative), target duration, aspect ratio, captions,
   include/exclude keywords, visual query, style preset, number of versions; unrecognised words go to
   "warnings". Behind an interface so a local model could be swapped in later. 20 tests.
8. Voice input: the server accepts an uploaded audio recording, transcribes it locally with
   faster-whisper and feeds it to the parser (browser mic arrives in phase 13; CLI version for
   testing).
9. Clip selector: candidates 3–15 s snapped to sentences and shot cuts; mood-specific weights in an
   editable YAML; CLIP similarity for the visual query; hit target duration ±10%; avoid duplicates;
   every clip gets a plain-language reason. **[ME]** Show chosen clips/timestamps for
   "funny 60 seconds"; tune weights from feedback.
10. Renderer: cut originals frame-accurately, short audio crossfades, -14 LUFS loudness.
11. Style matching: pacing toward the preset, beat-synced cuts, smooth face-following crop to the
    target aspect ratio, word-timed burned-in captions, basic colour matching. **[ME]** Watch the
    output video.
12. One command: `autocut run --footage <folder> --refs <folder> "instruction"`.
13. Mobile-first web app: FastAPI backend + web frontend, installable PWA. Password login; projects
    list/create; chunked resumable uploads from the camera roll with per-file progress that continue
    across screens; style preset picker; instruction box with hold-to-talk mic (browser recording →
    server-side local transcription); EditSpec + warnings panel; background processing with live
    progress and in-app "done" notification; streaming lightweight preview player + full-quality
    download; works on desktop too. **Settings panel for every render option** (aspect, captions
    on/off, caption style/case/size/position, punch-ins, colour matching, face-follow, loudness).
    **Transcript/caption editor**: tap a caption line to retype it, re-listen a moment, or give
    context ("at 1:43 I said 'you want some?'", names, places) so the app fixes it itself.
14. Review screen (touch-friendly): clip cards with thumbnail, timestamps and reason; tap to preview;
    swipe or tap to remove; "swap" for next-best candidate; move up/down buttons; add from 10
    runner-ups; trim start/end with +/- buttons; render. Log keep/remove decisions.
15. Export timeline to FCPXML and EDL (DaVinci Resolve, Premiere, Final Cut), downloadable from the
    web app.
16. Phone access: (a) home Wi-Fi and (b) anywhere via Tailscale (private network, no port
    forwarding), with Tailscale HTTPS so the browser mic works. **[ME]** What to install on the
    phone, the address to open, and a guided test of upload, voice, review and download.
17. Auto-start on boot, keep running in the background, double-clickable launcher and a desktop
    shortcut that opens it in the browser.

## Added after the plan (user requests)

18. Effects from references: detect sound effects, stickers, flashes, zooms, shakes in reference
    videos and add similar ones (built-in library + your own packs); instruction can add/remove them.
19. CapCut-style editor after the auto-edit: preview with draggable/resizable/rotatable text and
    stickers, scrolling multi-track timeline (clips, text, stickers, sounds, effects), drag to
    reorder/trim, split, duplicate, delete, style, undo/redo, add text/stickers/sounds/effects,
    export. Backed by an editable timeline document that the renderer renders exactly.
