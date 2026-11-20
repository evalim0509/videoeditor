# autocut — progress

Legend: [x] done and verified on `test_footage/` · [ ] not started · **[ME]** = needs the user

## Phases

- [x] 1. Project skeleton, requirements, CLI (`autocut --help`)
- [x] 2. Ingest: hashing, ffprobe metadata, 480p CFR proxies, SQLite, HDR/rotation/VFR handling
- [ ] 3. Transcription (faster-whisper, word timestamps, hardware-based model size)
- [ ] 4. Audio + shot analysis, `autocut inspect` — **[ME]** confirm top laughter/energy moments
- [ ] 5. Visual analysis: CLIP embeddings at 1 fps, `autocut search`, mediapipe faces
- [ ] 6. Style analyzer → StyleProfile preset (+ plain-language explanation)
- [ ] 7. Intent parser → EditSpec (rule-based, YAML synonyms, 20 tests)
- [ ] 8. Voice input (server endpoint + CLI)
- [ ] 9. Clip selector — **[ME]** confirm clips for "funny 60 seconds"
- [ ] 10. Renderer (frame-accurate cuts, crossfades, -14 LUFS)
- [ ] 11. Style matching — **[ME]** watch the output
- [ ] 12. `autocut run` one-command pipeline
- [ ] 13. Mobile-first web app / PWA
- [ ] 14. Review screen
- [ ] 15. FCPXML + EDL export
- [ ] 16. Phone access via Wi-Fi + Tailscale HTTPS — **[ME]** phone walkthrough
- [ ] 17. Auto-start, launcher, desktop shortcut

## Environment notes

- Development and testing currently happen in a **cloud Linux container** (Ubuntu 24.04, 4 CPU
  cores, 16 GB RAM, no GPU). Phases 16–17 must be done on the user's own computer.
- The test footage is a 2:29 iPhone HEVC clip (1080p, VFR ~29.99 fps, SDR). The reference is a 61 s
  phone screen recording (stored portrait with rotation=90, VFR ~47 fps nominal 60).

## Technical decisions (defaults chosen without asking)

### Phase 1
- **Python 3.12** venv (3.13 is too new for mediapipe/torch wheels on some platforms). Requires 3.11+.
- Packaging: `pyproject.toml` (setuptools), editable install, console script `autocut`.
- CLI: Typer + Rich; heavy imports are inside commands so `--help` is instant.
- Runtime data in `data/` (`AUTOCUT_HOME` overrides): SQLite at `data/autocut.db`, proxies, model
  weights (HF_HOME/TORCH_HOME redirected into `data/models/`), projects, presets.
- SQLite cache: tables `media`, `paths` (path→hash with size+mtime, so unchanged files are never
  re-hashed), `analysis` (JSON) and `blobs` (binary, e.g. embeddings), keyed by
  `(hash, stage, version)`. WAL mode for concurrent web + worker access.
- Hardware → Whisper model: NVIDIA ≥8 GB → large-v3 fp16; Apple Silicon → medium int8 on CPU
  (CTranslate2 has no Metal); CPU-only → small int8 (medium if ≥12 cores). Override with
  `AUTOCUT_WHISPER_MODEL`.
- PyTorch installed from PyPI (the CPU-only index was unreachable here); README documents the
  smaller CPU wheel for CPU-only machines.
- AudioSet class-label CSV is bundled in `autocut/assets/` so PANNs never needs `wget` at runtime
  (it isn't available on Windows).

### Phase 2 — ingest
- Content hash: full-file SHA-256 (8 MB chunks). `paths` table caches path→hash by size+mtime, so
  re-running ingest on unchanged files takes ~0.2 s total. Same content at a new path is reused.
- Proxy: H.264 `veryfast` CRF 26, **short side 480 px**, max 30 fps CFR, 1 keyframe/second (fast web
  seeking), `+faststart`, stereo AAC 96k, BT.709 tags, metadata stripped (no GPS in proxies).
- Analysis audio: 32 kHz mono 16-bit WAV (PANNs' native rate; Whisper resamples internally).
- VFR → CFR: output rate = nearest standard rate to the nominal rate (23.976…60). Audio uses
  `aresample=async=1000:first_pts=0` so it's padded/stretched against its timestamps — no drift.
  The same rules (`autocut/media.py`) are reused by the final render on originals.
- Rotation: FFmpeg auto-rotates on decode; metadata stores `rotation` and `display_width/height`.
- HDR (HLG/PQ): `zscale` → linear → `tonemap=hable` → BT.709 8-bit. Fallback `colorspace` filter if
  FFmpeg lacks zimg. Verified with a synthetic 10-bit HLG, rotated, VFR HEVC .MOV test clip.
- Results on test footage: IMG_0875.MOV (VFR 29.993 → 30 CFR, proxy 854x480, durations match to
  4 ms); reference_01.mp4 (rotation 90 handled → 1044x480, VFR 46.8 → 30 fps proxy).
- Noted for phase 6: the reference is a phone screen recording with black pillarbox bars; the style
  analyzer will auto-crop them (cropdetect) before measuring.
