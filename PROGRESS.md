# autocut — progress

Legend: [x] done and verified on `test_footage/` · [ ] not started · **[ME]** = needs the user

## Phases

- [x] 1. Project skeleton, requirements, CLI (`autocut --help`)
- [x] 2. Ingest: hashing, ffprobe metadata, 480p CFR proxies, SQLite, HDR/rotation/VFR handling
- [x] 3. Transcription (faster-whisper, word timestamps, hardware-based model size)
- [x] 4. Audio + shot analysis, `autocut inspect` — **[ME]** confirmed by user, energy re-tuned from feedback
- [x] 5. Visual analysis: CLIP embeddings at 1 fps, `autocut search`, mediapipe faces
- [x] 6. Style analyzer → StyleProfile preset (+ plain-language explanation)
- [x] 7. Intent parser → EditSpec (rule-based, YAML synonyms, 20 tests)
- [x] 8. Voice input (server endpoint + CLI)
- [ ] 9. Clip selector — **[ME]** confirm clips for "funny 60 seconds"  ← built & verified, waiting for user feedback
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

### Phase 5 — visual analysis
- CLIP: open_clip **ViT-B-32 / LAION-2B** (fast on CPU, 512-d). One frame per second sampled at the
  middle of each second (t = i + 0.5) from the proxy, full frame (CLIP transform resizes/crops).
  Stored as float16 blob (`clip / 1:ViT-B-32:laion2b_s34b_b79k`). Query = mean of "q" and
  "a photo of q" text embeddings; cosine similarity; peaks with 3 s suppression.
- `autocut search "query" [--file F] [--top N] [--preview out.mp4]` across all ingested footage.
  Verified: "close-up of food in a bowl" → 0:19 / 0:26 (holding dumplings to the camera, no face);
  "eating a dumpling with chopsticks" → 1:26, 1:08, 1:20, 1:39 — all four frames checked visually ✔.
- Faces: MediaPipe Tasks `FaceDetector` (BlazeFace short-range, suited to selfie/vlog distance) at
  4 fps on the proxy; normalised boxes sorted by size; per-second face count + largest face area.
  Verified by drawing boxes on 6 frames — all on the face, incl. partially covered by food ✔.
  Face visible 145/149 s; the gaps (0:17–0:26) are exactly the food close-ups.
- MediaPipe 1.2 only ships the Tasks API (no `solutions`); on Linux it needs `libegl1` (installed in
  the container; desktop OSes already have it).
- **Offline guarantee**: model cache env vars (HF_HOME/TORCH_HOME) are now set at import time —
  previously huggingface_hub was imported first and cached to ~/.cache. CLIP weights are resolved
  to a local file path before open_clip sees them. Verified by loading CLIP, Whisper, PANNs and the
  face detector with the network proxy pointed at a dead port ✔.

### Phase 6 — style analyzer
- `autocut style <refs...> --name NAME` → `data/presets/NAME.json` (pydantic `StyleProfile`) +
  plain-language explanation; `--show NAME`, `--list`. Per-reference measurements cached
  (`style_ref / 2`).
- **Content crop**: FFmpeg `cropdetect` failed on the reference because its side bars are YouTube
  "ambient mode" glow (coloured, blurred), not black. New detector uses *detail* (Sobel magnitude
  median over ~30 frames) instead of darkness, requires symmetric bar pairs (a low-detail table at
  the bottom of the test clip was otherwise cropped), snaps to standard aspects within 3 %.
  Reference → 856x480 @ x=94 (16:9) ✔; test clip → no crop ✔.
- **Captions**: tried (1) gradient/Otsu blobs — found nothing; (2) CLIP zero-shot — too weak
  (0.02 vs 0.00 with overlap). Final: top-hat/black-hat thin strokes → character blobs → lines with
  ≥5 characters, width ≥10 % of frame (rejects teeth with braces and tiny background signage),
  uniform near-white/near-black stroke colour, and persistence (same box in an adjacent 0.5 s
  sample). Reference: text in 22 % of frames at 91–93 % height ✔; test clip: 1.3 % (one blip) ✔.
  `captions_present` = share ≥ 12 % and ≥ 60 % of boxes in one vertical band.
- Beat sync: a cut within 80 ms of a beat counts; compared with the chance rate
  (2·80 ms / beat interval); synced if music share > 30 % and on-beat ≥ max(35 %, 1.6× chance).
- Look: mean luma, luma std (contrast), HSV saturation, Lab mean/std (for colour matching in
  phase 11), warmth = Lab b*.
- Result `paris_vlog`: fast pacing (median shot 1.2 s, 35 cuts/min, most shots 0.9–2.9 s), 16:9,
  music 84 % of the time at ~134 BPM, cuts *not* beat-synced (49 % vs 36 % chance), small captions
  at the bottom 22 % of the time, -17 LUFS, evenly exposed, moderate contrast, natural colours,
  warm tint.

### Phase 7 — intent parser
- `EditSpec` (pydantic, `autocut/editspec.py`): mood (funny/hype/emotional/calm/informative/auto),
  target_duration + ±10 % tolerance, aspect_ratio, platform, captions (None = follow preset),
  include/exclude keywords, visual_query, style_preset, versions, music, warnings, source_text,
  parser name; `summary()` gives the one-line "what I understood" for the web UI.
- Interface: `IntentParser` protocol + registry (`get_parser()`, env `AUTOCUT_INTENT_PARSER`), so a
  local model parser can be registered later without touching callers.
- `RuleBasedParser`: regexes driven by `autocut/config/intent.yaml`, copied to
  `data/config/intent.yaml` on first use (user-editable, hot-reloaded by mtime). Matched phrases are
  consumed; leftover meaningful words → warning "Didn't understand: ...".
- Order: versions → duration (60 s / 1:30 / "a minute and a half" / "half a minute" / word numbers;
  "under X" → 0.9·X so +10 % stays under the limit) → platform (aspect + default length) → aspect
  words → captions on/off → music on/off → style preset (exact, then fuzzy "paris" → paris_vlog,
  else warning listing available presets) → visual query ("showing X") → exclude/include keywords →
  mood (most mentioned, ties → first) → vague length words.
- Gotcha fixed: YAML 1.1 turned unquoted `no`/`on` into booleans; they're quoted now.
- 20 tests in `tests/test_intent.py` (all pass). `autocut parse "..."` prints summary, warnings, JSON.

### Phase 8 — voice input
- `autocut/voice.py`: any recording (browser WebM/Opus, iPhone MP4/AAC, wav...) → FFmpeg decode →
  faster-whisper with a vocabulary prompt (TikTok, captions, numbers...) → intent parser → EditSpec.
- Voice model chosen separately from the footage model: **small** int8 on CPU/Apple, large-v3-turbo
  on NVIDIA (`AUTOCUT_VOICE_MODEL`). Measured on 4 synthetic spoken commands: small 2.6 s avg (auto
  language) vs turbo 7.4 s, and small was *more* accurate (turbo heard "three" as "free").
  Language auto-detected, falls back to English when unsure (`AUTOCUT_VOICE_LANGUAGE` to force).
- Parser vocabulary got common mishearings before units/"versions" (free→3, to/too→2, for→4) and
  bare "instagram/insta/ig".
- Server: FastAPI `POST /api/voice` (multipart `audio`) → {transcript, spec, summary}; also
  `POST /api/parse`. CLI: `autocut voice <file>`.
- **Login built now** (not left for phase 13) because the endpoint is network-reachable:
  scrypt-hashed password in `data/auth.json`; random 4-word passphrase generated on first start
  (printed + `data/FIRST_LOGIN.txt`, removed after the password is changed); HMAC-signed 30-day
  session cookie (HttpOnly, SameSite=Lax, Secure over HTTPS) or Bearer token; changing the password
  rotates the signing secret (logs out other devices); 8 failed logins / 10 min per IP → 429.
  `autocut password` sets it from the terminal.
- Tests: `tests/test_web_voice.py` (auth everywhere, rate limit, bearer, password change, first-run
  passphrase, real spoken WebM → EditSpec via espeak-ng).

### Phase 9 — clip selector
- `autocut select "instruction" --footage F [--out sel.json] [--preview p.mp4]`; `select_clips()`
  returns clips + 10 runner-ups + warnings; `analysis.analyze_all()` runs every cached stage.
- **Never mid-word**: safe zones = gaps between consecutive words shrunk by a 40 ms guard (smaller
  when the gap is tight; touching words leave the exact boundary point), plus leading/trailing
  silence; file edges always safe. Every boundary is validated (assertion) before returning. Found
  and fixed two real cases: a clip ending in the file's last word, and back-to-back words with no
  gap ("You guys. I'm out." at 1:11).
- Candidates (3–15 s): runs of consecutive sentences, start/end padded (0.15 s / 0.30 s) inside the
  surrounding silent zone, snapped to a shot cut if one falls in that zone, extended to include
  laughter right after the last sentence; plus grid windows (3 / 7 / 9 s, 1 s step) in stretches
  without speech. 256 candidates on the test clip.
- Features (≈0..1): laughter (½ relative to the file's max + ½ absolute /0.3), energy, music,
  cheering, applause, shouting (0.6·max + 0.4·mean), speech share, speech rate, clarity (mean word
  confidence), complete sentences, face presence, face size, length fit (mood's preferred range),
  visual-query CLIP similarity (relative), include-keyword hits (transcript or CLIP).
- Mood weights + preferred lengths + hook-first in `autocut/config/selector.yaml` (user copy in
  `data/config/`). Excludes: transcript match or strong CLIP match → dropped.
- Selection: best-first greedy aiming at the target, never over +10 %, then fill to −10 %; no
  overlaps; text-overlap duplicates removed; chronological order, strongest clip first as a hook for
  funny/hype. Reasons are generated from the top weighted contributions + the quote, with a
  "speech is hard to make out" flag for low-confidence words.
