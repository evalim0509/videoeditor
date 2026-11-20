# autocut — progress

Legend: [x] done and verified on `test_footage/` · [ ] not started · **[ME]** = needs the user

## Phases

- [x] 1. Project skeleton, requirements, CLI (`autocut --help`)
- [x] 2. Ingest: hashing, ffprobe metadata, 480p CFR proxies, SQLite, HDR/rotation/VFR handling
- [x] 3. Transcription (faster-whisper, word timestamps, hardware-based model size)
- [x] 4. Audio + shot analysis, `autocut inspect` — **[ME]** confirmed by user, energy re-tuned from feedback
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
- Hardware → Whisper model (revised in phase 3): NVIDIA ≥10 GB → large-v3 fp16, ≥5 GB →
  large-v3-turbo fp16; Apple Silicon / CPU with ≥8 GB RAM → large-v3-turbo int8 (CTranslate2 has no
  Metal); smaller machines → small int8. Override with `AUTOCUT_WHISPER_MODEL`.
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

### Phase 3 — transcription
- faster-whisper, `word_timestamps=True`, beam 5, `condition_on_previous_text=False` (avoids
  repetition loops). Audio is decoded by **FFmpeg** (PyAV in faster-whisper 1.2 is incompatible with
  the installed `av`, and the rules say FFmpeg does media work anyway).
- The test clip is hard: quiet speech, restaurant noise, accented English. Measured on it:
  - default Silero VAD threw most speech away (54 words). Fix: speech clean-up filter before Whisper
    (`highpass 90 Hz, lowpass 7.5 kHz, dynaudnorm`) + gentler VAD (threshold 0.25, 400 ms padding).
  - single-window language detection said "en 40 %" / "Javanese" depending on the window. Fix: vote
    over 5 windows spread across the file → en 87 %.
  - `large-v3-turbo` int8 beat `medium` on both accuracy and speed (29 s vs 69 s per 75 s audio on
    4 CPU cores) → new default on any machine with ≥8 GB RAM. Full 2:29 clip: 69 s, 121 words.
- Hallucination filter: drop segments with compression ratio > 2.4, avg logprob < -1.5, or
  no-speech > 0.6 with logprob < -1.0; drop the 3rd+ identical line in a row.
- Sentences are built from words (end punctuation or ≥0.8 s pause; run-ons > 15 s split at the
  largest pause). They're the snapping units for "never cut mid-word".
- Cache key `transcript / 2:<model>:<lang>`; re-running is instant (0.2 s).
- Known limitation: a stretch around 1:04–1:32 of the test clip (eating, noisy) yields short
  low-confidence lines that may be partly invented; word probabilities are stored so the selector
  can down-weight them.

### Phase 4 — audio + shots + inspect
- Shots: PySceneDetect `AdaptiveDetector` on the proxy, min shot 0.6 s. Test clip → 1 shot (correct:
  one continuous selfie take, 4.5 s). Reference → 36 shots (sanity check that cuts are detected).
- PANNs `Cnn14` (mAP 0.431) clip-wise on 2 s windows centred on each second, hop 1 s; category score
  = max over member AudioSet classes (laughter = Laughter/Giggle/Snicker/Belly laugh/Chuckle/Baby
  laughter; cheering = Cheering/Crowd; applause = Applause/Clapping; shouting = Shout/Yell/Scream/
  Bellow/Whoop/Children shouting; speech = Speech/Conversation/Narration). Checkpoint is loaded
  directly (no `wget`), 17 s for the 2:29 clip on CPU.
- Tried and rejected: loudness normalisation before PANNs (no change), and the frame-level
  `Cnn14_DecisionLevelMax` SED model (same ranking, lower scores).
- librosa per second: RMS dBFS, onset strength, dynamics (dB swing within ±1 s), integrated LUFS
  (pyloudnorm), beat tracking (tempo + beat times).
- `energy` (v3, tuned on user feedback): **0.5·rank(onset) + 0.5·rank(dynamics)**, first/last 1.5 s
  of each clip damped ×0.5 (phone handling noise). Raw loudness is excluded on purpose.
- Test clip findings: very quiet recording (-35.7 LUFS), speech in 141/150 s, music clearly present
  around 0:10. Laughter is only *faint* everywhere (max 0.046 at 0:39, then 1:00) — both PANNs models
  agree on the ranking, so the selector will use laughter **relative to the file** plus an absolute
  bonus when it's clearly present (≥0.3).
- `autocut inspect <file> [--preview out.mp4]`: tables of top moments with strength labels
  (clear/possible/faint for events; high/medium/low for energy) and the words spoken around each;
  `--preview` renders a labelled montage of the top moments from the proxy for quick checking.

#### [ME] feedback 1 (phase 4)
- User: laughter #9 (1:10) not real; energy #2 (2:28), #5 (0:12), #7 (1:26), #10 (0:38) not real;
  everything else correct. Real energy = 1:43, 1:05, 0:47, 1:51, 0:56.
- Analysis: false moments were loud-but-flat narration (0:12, dynamics 12 dB vs 21–27 dB for real
  ones), the loudest sustained talking at the clip's end (2:28, handling noise), and voiceless clatter.
  Old formula ranked by loudness → 3 false in top 10.
- Fix: energy = eventfulness + dynamics, edge damping. Now all 5 real moments rank top 6, 2:28/0:12
  gone; 0:38 and 1:25 remain only at ranks 8/10 and are labelled "weak" (the clip simply has ~6
  lively moments). Watch item: 2:24 ("boarding at 2…") ranks 4th — may be the same flat moment.
- Laughter: scores below ~0.01 are noise (1:10 = 0.009, same level as accepted #6–#10), so the
  selector treats faint laughter (<0.1) as a minor signal only.
