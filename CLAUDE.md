# autocut — project rules

autocut is a fully offline, local AI video editor. Raw footage + reference videos + a typed or
spoken instruction ("funny 60 second tiktok with captions") → it picks the best moments and edits
them in the style of the references. The user drives everything from a **phone browser**; their
computer does all processing.

## PERMANENT RULES (never break these)

1. **Fully offline processing** on the user's computer. No cloud APIs, no LLMs. Downloading model
   weights once during setup is fine.
2. **FFmpeg for all media work.** Analysis runs on **480p proxies**; the final render uses the
   **originals**.
3. **Every analysis result is cached in SQLite keyed by file hash**, so nothing is analysed twice.
4. **Each stage is a separate module with its own Typer CLI command** so it can be tested alone.
5. **Instructions are always converted into a structured `EditSpec` JSON** before any editing logic
   runs.
6. **Never cut mid-word.** Every cut point is snapped to word/sentence boundaries from the transcript.
7. **The web interface is mobile-first**: it must work well on a phone screen with touch, then scale
   up to desktop.
8. **The app requires a password login**, because it is reachable over the network.
9. **Everything automatic must be changeable by the user afterwards** — manually in the app (clips,
   trims, order, caption text, captions on/off and style, aspect, colour, punch-ins…) or by giving
   the app context it can act on. Decisions are stored as editable data (`RenderOptions`, the clip
   list, transcript corrections), never baked in.

## User preferences (defaults)

- Vlog style, not TikTok style: keep the footage's original (horizontal) aspect ratio unless the
  instruction explicitly asks for another shape.
- Captions are plain lines that appear whole — no word-by-word highlighting.
- Don't change the colour unless asked (colour matching is opt-in).
- Punch-in zoom cuts are sparse: no closer than ~4 s apart by default (every ~2 s was too frequent).
- Captions only show the user's own voice (speaker check); other people's voices aren't captioned.
- Main workflow: drop videos into a Google Drive folder (synced to the computer), describe the
  edit/style in words, autocut edits automatically; everything adjustable afterwards.

## How to work

- The full plan is in `BUILD_PLAN.md`; progress and every technical decision go in `PROGRESS.md`.
- One phase at a time. After each phase: run it on `test_footage/`, verify the output yourself
  (read transcripts, inspect scores, extract and look at frames, check renders with ffprobe), fix
  errors, then git commit and tick it off in `PROGRESS.md`.
- Don't ask the user about technical choices; pick sensible defaults and note them in `PROGRESS.md`.
- Stop and ask the user only at checkpoints marked **[ME]** in the plan.

## Conventions

- Python 3.11+ (dev venv uses 3.12), virtualenv at `.venv/`. Always run tools via `.venv/bin/...`.
- Package: `autocut/` (one module per stage). Entry point: `autocut` (Typer) → `autocut/cli.py`.
- Runtime data lives in `data/` (gitignored): `data/autocut.db` (SQLite cache), `data/proxies/<hash>/`,
  `data/models/`, `data/projects/`. Override with the `AUTOCUT_HOME` env var.
- Cache keys are `(file_hash, stage, version)`. Bump a stage's `VERSION` constant when its output
  format or algorithm changes so stale results are recomputed.
- Timestamps everywhere are seconds (float) on the **original** file's timeline. Proxies keep the
  same timeline (same start, constant frame rate), so proxy times map 1:1 to originals.
- Media in `test_footage/`, `references/`, `output/` is never committed.
- Tests: `.venv/bin/pytest -q`.
