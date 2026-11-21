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
- [x] 9. Clip selector — **[ME]** confirmed by user, weights tuned from feedback
- [x] 10. Renderer (frame-accurate cuts, crossfades, -14 LUFS)
- [x] 11. Style matching — **[ME]** approved by user after feedback 3–4
- [x] 12. `autocut run` one-command pipeline
- [x] 13. Mobile-first web app / PWA
- [x] 14. Review screen
- [x] 15. FCPXML + EDL export
- [x] 16. Phone access via Wi-Fi + Tailscale HTTPS — built & tested in the container; runs on the user's computer after the double-click install
- [x] 17. Auto-start, launcher, desktop shortcut — built & tested (Linux); Windows/macOS variants run during install

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

#### [ME] feedback 2 (phase 9)
- User removed #6 (1:05–1:12 "Great. Nice. Nice. You guys. I'm out.") and #10 (2:17–2:23 silent
  filler); kept the other nine, including the two I had flagged (#7 1:24, #11 2:24).
- Analysis: #6 had word confidence 0.07 (kept clips: 0.42–0.79) and only 19 % speech → mostly
  noise transcribed as words; #10 had no speech and no sound events (kept silent #5 has the
  strongest laughter). Also found 0:47–0:54 (user-confirmed lively moment) was wrongly blocked by
  the duplicate check because it shared "it's a good one" with clip #1.
- Fixes: new features `dead_air` (no speech and no laughter/cheering/applause/music/shouting ≥ 0.3)
  and `garbled` (words present but mean confidence < 0.25), negative weights in every mood
  (funny: −1.5 / −2.0); duplicates now need ≥ 3 shared *content* words (stopwords ignored).
- Result: both removed clips gone, all nine kept clips still selected, 0:47–0:54 and 1:19–1:23 fill
  the gap; 63.3 s (range 54–66). Locked in by `test_user_feedback_checkpoint2_funny60`.

### Phase 10 — renderer
- `autocut render selection.json -o out.mp4` → `render.render_timeline(clips, out, ...)`.
- Per clip from the **original**: input seek + exact frame count (`-frames:v n`, n = round(len·fps)),
  same normalisation as proxies (upright, HLG/PQ→SDR, CFR) + optional per-clip filters (phase 11
  crop/colour), scale/pad to the output frame, near-lossless x264 CRF 10 intermediate.
- Audio per clip with a tail ≤ 80 ms taken from the silence after the cut (limited by the next word
  in the cached transcript → never mid-word), joined with equal-power (`qsin`) crossfades; the
  last clip has no tail, so audio length == video length exactly.
- Loudness: two-pass `loudnorm` (I=-14, TP=-1, LRA=11, linear) then, if still > 0.3 LU off (quiet
  sources hit the peak ceiling), gain correction into a latency-compensated `alimiter`.
- Final: concat → optional ASS subtitles → H.264 High CRF 18 `fast`, AAC 192k 48 kHz, BT.709 tags,
  `+faststart`; plus a 540p ~1.5 Mbps phone preview (`*.preview.mp4`).
- Verified on the funny-60 selection: 1897 frames = expected exactly; video 63.2333 s / audio
  63.233 s; each clip's first frame matches the original frame at its start (mean diff 0.3 vs 3–21
  for neighbouring frames); -14.1 LUFS integrated, true peak -1.3 dBFS; joins have no clicks
  (max sample jump at joins 0.07 vs 0.25 typical audio). 107 s render on 4 CPU cores.

### Phase 11 — style matching
- `autocut render sel.json --style NAME` → `stylematch.render_styled()` (`--plain` skips styling).
- **Pacing**: selector blends the mood's preferred clip length with the preset (median·2.5 …
  p90·2.5, clamped 3–15 s). Clips longer than ~1.6× the target segment (preset median·1.8, clamped
  1.6–4 s; paris_vlog → 2.2 s) get **punch-in cuts** at word gaps, alternating 1.0×/1.15× framing;
  split points snapped to the frame grid, contiguous pieces joined without crossfade. Funny-60
  TikTok: 11 clips → 29 shots (18 punch-ins). Skipped when the reference is ~one take (<6 cuts/min).
- **Beat sync**: if the preset is beat-synced, clip boundaries move to a source beat within ±0.25 s
  that lies in a word-free zone (paris_vlog isn't, so 0 moves).
- **Face-following crop**: target aspect from EditSpec, else preset, else source. Face centre track
  (4 fps, gaps interpolated) → median filter → dead zone (22 % of crop width) → exponential easing
  (τ 0.6 s) → RDP-simplified keyframes → piecewise-linear FFmpeg `crop` expressions in t. Face
  placed at ~42 % height. Output sizes: 9:16 → 1080x1920, 4:5 → 1080x1350, 1:1 → 1080x1080.
  Verified: camera static while the face stays in frame, smooth pans otherwise.
- **Captions**: ASS karaoke (`\kf`, each word fills as spoken), bundled Inter SemiBold (OFL,
  `autocut/assets/fonts/`), size from the preset's caption height (clamped 2.8–6 % of height),
  position from the preset (portrait sits 24 % above the bottom, clear of TikTok's UI). Lines:
  ≤ 6 words / 30 chars / 3.5 s, break at sentence ends and pauses > 0.9 s; continuation tokens
  merged ("2" + ".20"); repeated lines dropped; flashes < 0.5 s merged; min on-screen 0.5 s.
- **Colour**: footage Lab mean/std (proxy frames inside the selected clips) → 60 % of the way to the
  preset's (std ratio clamped 0.75–1.33, mean shift ≤ 10 L / 8 a,b) baked into a 33³ `.cube` LUT
  → `lut3d` (tetrahedral). Identity LUT verified when the looks already match.
- Captions default: EditSpec `captions` if given, else the preset's `captions_present`.
- Output (funny 60 second tiktok with captions, paris_vlog): 1080x1920, 63.27 s, 1898 frames,
  A/V equal, -14.1 LUFS, 28 caption lines, 130 s render on 4 cores. 16:9 path verified too.

#### [ME] feedback 3 (phase 11)
- User: no word-by-word highlight; keep the original horizontal ratio (vlog style, not TikTok);
  "Ywansuk" caption is wrong ("you want some?"); don't change the colour; and in the app everything
  must be changeable manually later, or adjustable by telling the app some context.
- Changes:
  - `RenderOptions` (`autocut/options.py`): aspect (None = source), captions, caption_style
    (static default / karaoke), caption_case, caption_scale, caption_position, punch_ins,
    face_follow, color_match (**off by default**), beat_sync, loudness. Stored in the selection JSON
    as `options`; `autocut render` flags override and are saved back.
  - Aspect now defaults to the footage's own shape; only an explicit request (e.g. "tiktok",
    "vertical") crops. New "vlog" platform word = keep source shape.
  - **Transcript corrections** (`autocut/corrections.py`, SQLite `transcript_edits`): manual
    replace by time range or fuzzy find & replace; re-listen a moment (short window, 2 s audio
    context — found to fix the error even without a hint); **context** — quoted phrases paired with
    the nearest time mentioned are re-listened in their sentence with the quote as hint (trying 2 /
    3 / 1.5 / 4 s of context), falling back to writing the user's words; extra general context
    (names/places) re-listens unclear sentences with it as a prompt. `effective_transcript()` feeds
    captions and the selector. CLI: `autocut correct`, `autocut context`.
  - Applied `autocut context IMG_0875.MOV 'At 1:43 I said "you want some?"'` → "Yeah, it is. Do you
    want some?" (Whisper consistently hears the "Do"; left as heard, editable).
  - Added rule 9 + user preferences to CLAUDE.md; phase 13/14 scope extended (settings panel,
    caption editor, context box).
- Re-render (funny 60 second vlog with captions): 1920x1080, 63.23 s, same 11 approved clips,
  plain captions, colour diff vs original 0.9/255 (encoding noise only), -14.1 LUFS.

#### [ME] feedback 4 (phase 11)
- User: zoom cuts too frequent. Auto spacing changed from preset median·1.8 (2.2 s) to median·3.3,
  clamped 4–10 s (→ 4.1 s for paris_vlog); new editable `RenderOptions.punch_in_spacing`
  (`--punch-in-spacing`), plus `--no-punch-ins`. Funny-60 vlog: 18 → 4 zoom cuts (zoomed at
  0:04–0:07, 0:10–0:15, 0:38–0:41 of the edit). Preference recorded in CLAUDE.md.

### Phase 12 — one command
- `autocut run --footage F --refs R "instruction" [--style NAME] [--out-dir output] [--name X]`
  → `pipeline.run()`: ingest footage → ingest references + StyleProfile (stable auto name
  `refs_<sha1 of ref hashes>`, reused if already analysed) → parse instruction to EditSpec →
  analyse (cached) → select → render styled. Writes `<name>.mp4`, `<name>.preview.mp4` and the
  editable edit list `<name>.json` (spec, options, clips, runner-ups).
- Multiple versions ("2 versions"): each later version scores down candidates overlapping earlier
  versions' clips (×0.6). Test: "funny 30 second vlog with captions, 2 versions" → v1 31.1 s / v2
  32.1 s, **0 s overlap**, 117 s total (analysis cached).
- Progress callback (stage text + 0..1) used by the CLI progress bar and, next, by web jobs.

### Phase 13 — mobile-first web app
- `autocut serve [--host 0.0.0.0 --port 8765]`: prints the LAN address; generates the first-login
  passphrase if none; resumes unfinished jobs; one background worker thread.
- Backend (`autocut/web/`): `projects.py` (folder + project.json per project), `uploads.py`
  (chunked resumable protocol: POST start → PUT ?offset=N raw chunks → 409 with server offset on
  mismatch; same fingerprint resumes; 64 MB max chunk; extension whitelist), `jobs.py` (analyze /
  ref / style / edit / render jobs, live stage + progress, errors reported to the phone, mirrored
  into project.json, re-queued after restart), `app.py` (all /api routes need login; video served
  with HTTP Range for streaming; `download=1` → attachment).
- Edits run `pipeline.run()` with the project's footage, style and **saved settings as base**
  (explicit instruction requests on top). Extra versions become separate edits.
- Frontend (`autocut/web/static/`, vanilla JS, no build): login, projects list, project screen
  (footage / style / instruction / settings / edits), sticky "Make my edit" bar, toasts, bottom
  sheets, light/dark. Upload manager: 4 MB chunks, one at a time, infinite retry with backoff
  while offline ("reconnecting"), top-bar progress pill, wake lock, warns before closing; keeps
  running while you move between screens (SPA). Polling 2 s while busy / 8 s idle, paused when
  the tab is hidden and refreshed on return → lock the phone, come back, see progress.
  "Your edit is ready" toast + vibration (+ system notification where allowed).
- Hold-to-talk: MediaRecorder (webm/opus or mp4/aac), hold to record / release to send, or tap to
  start + tap to stop; disabled with an explanation on non-HTTPS origins (fixed in phase 16).
- "What I understood" panel: live /api/parse while typing; voice shows "I heard: …".
- Settings panel for every RenderOptions field (shape, captions, caption style/case/size/position,
  zoom cuts + spacing, colour match, face follow, loudness), saved per project.
- PWA: manifest (standalone, icons incl. maskable + apple-touch-icon), service worker caching only
  the shell (never API/video).
- Verified with Playwright (iPhone 13 + Pixel 7 emulation, and 1280 px desktop): wrong password
  rejected, login, create project, upload 174 MB footage + reference via the chunked uploader,
  footage ready, style explained, instruction understood, lowercase caption setting applied, edit
  rendered (~55 s), preview plays (960x540), full-quality download = 206 partial + attachment;
  hold-to-talk records with a fake mic and the server answers "didn't hear an instruction".
  Bug found & fixed: `.icon-btn{display:grid}` overrode the `hidden` attribute.
- Tests: `tests/test_web_app.py` (public shell vs protected data, CRUD + settings validation,
  resumable upload incl. resume, 409 re-sync, 415, auto-queued analysis).

### Phase 14 — review screen
- `autocut/review.py`: operations on the editable edit list (selection JSON): remove → "removed"
  (restorable), restore (refused if it would overlap), swap (best runner-up that doesn't overlap;
  the old clip becomes the first runner-up), move up/down, add a runner-up (refused if overlapping),
  trim start/end ±0.5 s **snapped to the next word gap in that direction** and clamped so it never
  overlaps a neighbouring clip from the same file (1–30 s). Clip text refreshed from the corrected
  transcript.
- Decision log: SQLite `feedback` table (hash, start, end, decision, mood, instruction, features);
  remove / restore / swap_in / swap_out / add / trim_* logged on each action, "keep" for every
  remaining clip when the user renders. Ready for future automatic weight tuning.
- API: review data with thumbnail + proxy URLs; clip operations; per-edit settings; thumbnails
  (cached JPEGs from the proxy); proxy streaming (Range) for clip previews (`#t=start,end`, auto-stops
  at the end); caption lines per clip, retype (manual correction), re-listen (optional hint),
  project-wide context; re-render. Footage hashes are checked against the project.
- UI (`static/review.js`): current render on top, touch clip cards (thumbnail, times, reason, ▶
  preview sheet, Start −/+ and End −/+, Captions, ⇄ Swap, ↑/↓, ✕ Remove, swipe left to remove),
  removed list with Restore, "Add a moment" sheet with 10 runner-ups, "Fix captions by telling me"
  context box, "Settings for this edit" sheet, sticky "Render my changes".
- Verified on iPhone 13 emulation: remove, swap, move, trim (end 26.7 → 27.2 s, in a word gap), add,
  swipe-left remove, caption edit; re-render → 19.37 s video = edited list total. Decision log rows
  written. Bugs found & fixed: unconstrained `<video>` on the review page overflowed horizontally
  (now global video/img sizing + no horizontal scroll); sheets had no visible close → "Done" button.

### Phase 15 — export to editing apps
- `autocut/export.py` + `autocut export sel.json -o DIR [--name X]`; web: "Export for editing apps"
  sheet on each edit (zip with everything + README, or single files) via
  `GET /api/projects/{pid}/edits/{eid}/export?format=zip|fcpxml|premiere|edl|srt`.
- Formats (all reference the ORIGINAL files, frame-accurate in/outs identical to the renderer):
  - **FCPXML 1.8** (Final Cut Pro, DaVinci Resolve) — 1.8 chosen over 1.9 because the file path
    lives on the asset (`src`), which more importers read (1.9's `media-rep` wasn't picked up by
    OpenTimelineIO's reader).
  - **CMX3600 EDL** (Premiere, Resolve, Avid…), NDF, record timeline from 01:00:00:00,
    `FROM CLIP NAME` + source path comments. iPhone files carry no timecode → source TC 00:00:00:00.
  - **FCP7 XML / xmeml v5** (Premiere's most reliable timeline import), linked V + 2 A tracks.
  - **SRT** captions (same chunking as burned-in captions, corrected transcript).
  - Punch-ins / crops / colour LUT are render-time effects and not exported (one event per clip).
- Verification: exports read back with **OpenTimelineIO** (independent implementation; dev-only
  dependency): FCPXML, Premiere XML and EDL all give the same 11 clips with frame-exact cut points and
  63.233 s total = the rendered video; media path resolves. Tests: `tests/test_export.py`.
- Found & fixed: SRT export needlessly loaded the full analysis stack (models) — analysis is now
  only loaded when beat sync / punch-ins need it.

### Phases 16–17 — phone access, auto-start, launcher (built; final steps need the user's computer)
- `autocut/system.py` + commands:
  - `autocut install [--firewall]`: auto-start at log-in, background running, desktop shortcut +
    double-click launcher, then starts the server. Per-user, no admin (except the optional Windows
    firewall rule for Wi-Fi access, which asks once).
    Windows: hidden VBScript in the Startup folder → `pythonw -m autocut daemon`; desktop `.lnk`
    (icon from the app icon) → `autocut-launch.bat` → `autocut open`.
    macOS: LaunchAgent `com.autocut.server` (RunAtLoad + KeepAlive); `~/Desktop/autocut.command`.
    Linux: systemd --user unit (Restart=always), fallback `~/.config/autostart`; `.desktop` launcher.
  - `autocut daemon`: runs `serve` and restarts it on crash (backoff 2→60 s). Verified: killed the
    server, daemon restarted it within seconds.
  - `autocut open`: start in background if needed, open the browser. `autocut uninstall`.
  - `autocut phone`: prints the home-Wi-Fi URL and runs `tailscale serve --bg --https=443
    http://127.0.0.1:8765`, printing the private `https://<computer>.<tailnet>.ts.net` URL (HTTPS
    certificate from Tailscale → the phone mic works; only devices in your tailnet can reach it;
    no port forwarding). Explains what to do if Tailscale is missing / logged out.
- `setup/windows.ps1` (winget), `setup/mac.sh` (Homebrew), `setup/linux.sh` (apt): install Python
  3.12, FFmpeg, Git, Tailscale; venv; the right PyTorch (CUDA + cuBLAS/cuDNN for Whisper on NVIDIA,
  default/MPS on Mac, CPU wheel otherwise); `autocut models`; `autocut install`; `autocut phone`.
- `python -m autocut` entry point added (used by auto-start). README rewritten for end users.
- Tested in the Linux container: install (autostart fallback + launchers), background start, daemon
  restart, `autocut phone` without Tailscale. Windows/macOS paths and Tailscale need the user's
  computer → [ME] checkpoint.

### User request after phase 15 — finish everything, "only my voice", Google Drive, style in words
- **Only my voice in captions** (`autocut/speakers.py`): SpeechBrain ECAPA-TDNN speaker embeddings per
  sentence (cached), voice profile auto-learned from the dominant speaker, refined by every voice
  instruction recorded in the app and by "me / not me" taps in the review screen. Each sentence is
  assigned to the nearer of "you" vs an "other voices" profile built per file (single fixed
  threshold fell short on short noisy lines). Test clip: 14 lines yours (narration + short
  reactions), 18 from a distinct second voice ("Nice to meet you", "You guys"…) — not captioned.
  `RenderOptions.caption_speakers` = "me" (default) | "all"; applied to burned-in captions + SRT.
- **Style in words**: parser maps wishes to RenderOptions (`style_options` + `caption_modifiers` in
  intent.yaml): no/few/more zoom cuts, lowercase/UPPERCASE, big/small, word-by-word/plain,
  top/middle/bottom, keep/match colours, caption everyone/only my voice. EditSpec gets `options` +
  `option_labels` (shown in "What I understood"). 2 new intent tests (22 total).
- **Google Drive drop folder** (`autocut/web/watch.py`): detects Google Drive for desktop folders
  (Windows `X:\My Drive`, macOS `~/Library/CloudStorage/GoogleDrive-*/My Drive`, `~/Google Drive`),
  suggests `<Drive>/autocut/<project>`, watches every 10 s; a file is imported (in place, no copy)
  once its size is unchanged across scans; Drive temp files ignored. "Edit automatically when new
  videos arrive" makes an edit with the project's instruction when everything is ready and nothing
  new arrived for 30 s (once per batch). Drive *links* (file or folder via gdown) can be pasted too.
  Removing an item never deletes files outside the project folder (your Drive stays intact).
- Voice recognition runs as part of background analysis; model fetched by `autocut models`.

### Final end-to-end demo (end product)
- Server started like on the user's computer; a stand-in for Google Drive for desktop at
  `~/Google Drive` (auto-detected). Playwright (iPhone 13) recorded the whole flow: login → new
  project → reference video uploaded → style learned → "Use folder autocut/Airport lunch vlog" →
  "Edit automatically when new videos arrive" → typed "funny 60 second vlog with captions, lowercase
  captions, keep my colours" (understood incl. style wishes) → video dropped into the Drive folder →
  imported in ~6 s → **automatic edit** started and finished (~2 min, 59.5 s, 16:9, colours
  untouched) → review: caption sheet shows the other voice's lines as "not me" → removed a clip →
  re-render → export sheet. No page errors.
- Captions in the automatic edit: only the user's lines, lowercase (e.g. "is that chili oil?",
  "yeah, it is. do you want some?"); the second voice ("nice to meet you", "you guys") excluded.
- Demo video: `output/autocut_app_demo.mp4` (static waiting compressed). Watch interval/quiet time
  can be shortened with `AUTOCUT_WATCH_SECONDS` / `AUTOCUT_QUIET_SECONDS` (defaults 10 s / 30 s).
- Install on the user's computer: download the branch ZIP → double-click `INSTALL-Windows.bat` /
  `INSTALL-Mac.command` (setup script installs everything, models, auto-start, desktop icon, Tailscale
  HTTPS, prints first password + phone address).

### Effects from reference videos (sound effects, stickers, visual effects)
- Decisions from the user: library = built-in **and** your own packs (yours preferred); runs on
  your own computer; effects follow the references **and** the instruction can add/remove them; all
  editable afterwards.
- `autocut/library.py`: 13 built-in sound effects synthesised locally with FFmpeg (whoosh,
  swoosh up/down, pop, click, ding, chime, boom, thud, riser, boing, shutter, cash) — royalty-free,
  generated into `data/library/builtin_sfx/`; 42 emoji stickers rendered from Noto Color Emoji (OFL)
  into `autocut/assets/stickers/`. Your packs: `data/library/sfx/`, `data/library/stickers/`
  (name files by kind, e.g. `whoosh_2.wav`). PANNs recognises the built-in whoosh/ding/chime/cash.
- `autocut/effects.py` (cached `effects_ref`), added to StyleProfile as `effects` + explanation:
  - sound effects: PANNs labels (1 s windows, 0.25 s hop) + **cut-aligned burst detector** (bursts
    ≥10 dB over the local 2 s level within −0.45/+0.25 s of a cut, ≥40 % of cuts and ≥2.5× the rate
    at random moments) — model labels alone missed whooshes mixed under music + voice;
  - flashes: luma spike brighter than *both* sides of a cut;
  - zoom-ins (positive optical-flow divergence ≥0.4 s), shakes (jittery global motion, not near cuts);
  - stickers: colourful, sharp, still ≥1 s, surroundings moving (≥75 % of the ring changes), and it
    **popped in** (spot looked different 2 s earlier), not caption text, ≤28 % of the frame.
  - effect types are only used when the references show them often enough (sfx/stickers ≥2/min,
    flash ≥15 % of cuts, zoom/shake ≥1/min) — stray detections (e.g. YouTube's "More videos" thumbnail
    in a screen recording) don't count.
- Verified: synthetic reference with whooshes on cuts + flashes + emoji stickers → whoosh on 8/9 cuts,
  9 flashes, 6/6 stickers, all enabled; user's Paris reference and the test clip → nothing enabled
  ("the edit stays clean"). Test: `tests/test_effects.py`.

- [x] 18. Effects from references (see above)
- [x] 19. CapCut-style timeline editor

### Phase 19 — timeline document + CapCut-style editor
- `autocut/timeline.py`: every edit is an editable document — clips (main track, play order),
  texts (captions + titles: position, scale, rotation, colour/outline/bold/box), stickers (asset,
  position, size, rotation), sounds (asset, gain), effects (zoom cut / flash / shake). Captions are
  anchored to the source time of their words (they follow the speech when clips are reordered or
  trimmed); other items to their clip + offset. Items you add/edit are flagged so regenerating the
  automatic ones keeps them. `normalize()` validates edits from the app (clip edges snapped to word
  gaps — never mid-word — canvas recomputed when the shape changes, orphans dropped).
- Rendering: `render_document()` splits clips at zoom/shake boundaries (crop + face-follow, shake =
  oscillating crop), colour LUT if enabled; `render_timeline()` gained sticker overlays (scaled,
  rotated PNGs with enable windows), sound effects mixed *before* loudness normalisation, flash
  frames; texts → ASS events with `\pos`, size, colours, `\frz`. All renders (auto-edits, quick
  review, editor) go through the document; `<edit>.timeline.json` is saved next to the video.
- Auto placement (`effect_items`): zoom cuts at word gaps; sound effects on cuts in the reference's
  kinds/rate; flashes on every k-th cut by the reference's flash rate; stickers at each clip's most
  expressive moment (laughter → 😂, "oh my…" → 😱, food words → 😋🍜🥟, else 🔥✨) beside the face,
  alternating sides; shakes at peaks. Switches: reference → instruction ("add sound effects",
  "no stickers", "clean edit"…) → editor.
- Editor (`static/editor.js` + `editor.css`, route `#/p/<pid>/edit/<eid>`): dark CapCut-like layout —
  ✕ / Saved / Export; preview box with live overlays (texts, stickers), zoom/flash/shake preview;
  selected overlay shows delete / edit / duplicate / rotate-scale handles; drag to move. Timeline:
  centre playhead, scroll = scrub, pinch / ctrl-wheel / buttons to zoom, thumbnail strips,
  orange text, purple sticker, blue sound, green effect rows; tap to select; drag a selected block
  to move it in time, drag its edges to change duration; drag a selected clip to reorder, trim with
  its white edge handles; "+" adds a runner-up moment at the playhead. Context toolbar: Add text,
  Stickers (grid + upload your own), Sounds (listen + upload your own), Effects, Captions (case,
  position, remove, re-create automatic items), Settings; clip: Split / Move ◀▶ / Duplicate / Delete;
  text: Edit / Style (size, colour, outline, bold, box, apply to all captions) / Split / Duplicate /
  Delete; sticker: Replace / Duplicate / Delete; sound: Replace / Quieter / Louder / Listen / Delete;
  effect: Change / Duplicate / Delete. Undo/redo (80 steps); auto-save (debounced PUT, server
  normalises); Export renders exactly the timeline.
- Verified on iPhone 13 emulation: open, add "test" text, drag (+40,+60 px), rotate/scale, colour,
  split clip (13→14), move, trim, add sticker/sound/flash, undo/redo, reload (all persisted), export →
  final video shows the yellow "test" text at the dragged spot, the sticker at its time, captions.
  Fixed during testing: moved clip scrolled out of view (playhead now follows it); preview box
  overlapped the controls after layout changes (auto-fit + controls above); handles covered small
  text (minimum selection box); undo/redo stacked (now side by side).
- Tests: `tests/test_timeline.py` (anchoring, normalisation/never mid-word, ASS positions, effect
  switches), `tests/test_effects.py`.
