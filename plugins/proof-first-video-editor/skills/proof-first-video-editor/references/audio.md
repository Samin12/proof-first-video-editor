# Audio: Auphonic-mastered, timeline-exact, verified

This is how the finished audio of the REA video was made, generalized for any Borumi project. The voice is
mastered **per source file** by Auphonic. The timeline audio is then **rebuilt from those masters at the
exact timeline positions** instead of shifting Borumi's export. The finished mix gets **one more Auphonic
pass**, is muxed as **stereo with loudness preserved**, and every step leaves a receipt with measured checks.

Scripts: `scripts/audio/` (Python 3 + numpy, ffmpeg ≥ 6). The pipeline builds on two upstream repos. It
references them and does not copy them:

- **Samin12/edit-borumi-video** (default install `~/.borumi-edit/skill`): export, the 2112-sample
  post-process, the 720p phone copy (`scripts/finalize_export.py`), and the original full-mix pass
  (`scripts/auphonic_master.py`).
- **Samin12/opencutagent-borumi** (default install `~/.borumi-agent/plugin`): `scripts/borumi_mcp.py`, the
  persistent Borumi MCP session that the read-only timeline reader uses.

## 0. The flow at a glance

```
Borumi timeline (talking head + screen + clicks)
 │
 ├─ 1. master_sources.py ── each mic SOURCE file → Auphonic preset → mastered/<media-uuid>.wav + receipts.json
 ├─ 2. export the RAW video from Borumi (+ baseline, out of exports) ── finalize_export.py --out-dir WORK/export/baseline
 ├─ 3. remix.py (dry run) ── stems, transcript and mic-coverage checks, preview mix
 ├─ 4. remix.py --export RAW ── null test vs Borumi → voice(mastered)+other → −16 LUFS → mux at timeline 0
 ├─ 5. verify_remix.py + edit_point_check.py ── sync vs Borumi, pop scan, card clicks, loudness
 ├─ 6. master_mix.py ── full-mix Auphonic pass (house standard) → stereo mux (−3 dB pan law) → checks
 ├─    deliver.py ── vN into the exports folder, phone copy, SRT, chapters, supersede (SKILL.md step 11)
 ├─ 7. check_sync.py ── final vs the mastered stem it was built from (sample-exact)
 └─ 8. (optional) v-next cut from the finished master: cut its Auphonic WAV, never re-master
```

The REA video followed this flow. The remix shipped as `v1`, the full-mix pass on `v1` as `v2`, and the
shorter v-next cut as `v3`, cut directly from `v2`'s Auphonic WAV.

## 1. Requirements and install

| Need | Notes |
|---|---|
| ffmpeg / ffprobe ≥ 6 | needs `ebur128=peak=true`, `alimiter ... latency=true`, `pan`. `aac_at` (macOS AudioToolbox) is used by remix.py when present, otherwise the native `aac`. |
| Python 3 + numpy | scipy is recommended but optional: the envelope lag (info only) and the null-test coherence are skipped without it. |
| A venv for the deps | `cd scripts/audio && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. When the running python lacks numpy, each script re-executes itself ONCE under the first of `$PFVE_AUDIO_PYTHON`, `scripts/audio/.venv/bin/python`, `uv run --with numpy --with scipy python`, so `python3 remix.py ...` just works. The re-exec is guarded by the environment variable `PFVE_AUDIO_REEXEC`, not by comparing interpreters: a `python3 -m venv` venv symlinks to the very python that made it, so a resolved-path comparison skipped the re-exec and the scripts stopped with "numpy is required" although the venv existed (fixed; see lesson 18). `--help` works without numpy. |
| Borumi | Open, with Settings › AI › Enable MCP. Only read tools are called, and the project DB is read from a temp copy. |
| borumi_mcp.py daemon | `nohup python3 ~/.borumi-agent/plugin/scripts/borumi_mcp.py start >/dev/null 2>&1 &`, then wait for `~/.borumi-agent/sess/ready` (SKILL.md step 1 has the loop). `start` runs the daemon in the foreground and never returns, so never run it un-backgrounded. Folder configurable: `borumi_mcp_dir` / `$BORUMI_MCP_DIR`. |
| Auphonic | An account with credits, plus a **preset**. The example preset is named "Broadcast Voice". For `master_sources.py` the preset must have exactly one *WAV 24-bit* output, and it must not be a mono mixdown. |
| Auphonic API key | `$AUPHONIC_API_KEY`, or the macOS Keychain: `security add-generic-password -U -a "$USER" -s auphonic-api-key -w '<key>'`. It is never put in a config, receipt or log. Check with `python3 auphonic_api.py --check`. |
| Offline self-test | `python3 selftest.py`: 18 synthetic checks of every DSP fact below. It needs no Borumi and no Auphonic. |

## 2. Configuration (no hard-coded paths)

The scripts that read the project (`master_sources.py`, `remix.py`, `verify_remix.py`, `edit_point_check.py`,
`borumi_read.py`) take these project options (`audio_config.py`); `master_mix.py` takes `--config`, `--project`,
`--preset` and `--preset-name` (it needs no bundle: it works on a finished file), and `check_sync.py` takes only files:

```
--config PATH       default, first that exists: $PFVE_AUDIO_CONFIG, ./pfve-audio.json,
                    ~/.config/proof-first-video-editor/audio.json
--project ALIAS     an entry under "projects" (default: "default_project" or the only entry)
--bundle PATH       the .bmprojbundle; with $AUPHONIC_PRESET set, enough on its own (no config file needed)
--audio-dir DIR     default "<bundle folder>/<bundle name> Agent/audio" (holds mastered/ and stems/)
--project-id ID     optional; verified against list_open_projects
--preset UUID       Auphonic preset UUID (else config auphonic.preset, else $AUPHONIC_PRESET)
--preset-name NAME  label for receipts and track titles, e.g. "Broadcast Voice"
```

The preset UUID: `python3 auphonic_api.py --presets` lists your presets (name, UUID, outputs; "OK for master_sources"
marks the ones with exactly one WAV 24-bit, non-mixdown output); the UUID is also the last part of the preset's edit URL
on auphonic.com. Without one, `master_sources.py` and `master_mix.py` stop with "no Auphonic preset".

See `scripts/audio/config.example.json`. Other keys: `borumi_mcp_dir`, `borumi_builtin_sounds_dir` (default
`/Applications/Borumi.app/Contents/Resources`), `edit_skill_dir` (default `~/.borumi-edit/skill`), and
`loudness.target_lufs` / `loudness.true_peak_dbtp` (default −16 / −1). Run `python3 audio_config.py [options]`
to print the resolved settings.

The open project is matched by **bundle path**, not by name. `db_copy()` and `media_file()` read the bundle on
disk, so a project with the same name opened from another folder is refused.

## 3. Step by step

### Step 1: master every mic source (`master_sources.py`)

```bash
python3 master_sources.py --project demo --dry      # lists the mic sources on the timeline, checks credits
python3 master_sources.py --project demo            # one Auphonic production per file, all concurrent
python3 master_sources.py --project demo --reverify # re-run the local checks on existing masters
```

- The sources are the microphone media **used on the current timeline**. Use `--media <prefix> ...` to name
  them explicitly and `--only` to restrict. A source that is already verified is skipped unless `--force`.
- The credit guard requires remaining hours ≥ total hours × 1.15 + 0.05 h per file.
- Each output is checked against its source:
  - duration within 20 ms;
  - **lag 0**: GCC-PHAT and plain cross-correlation on the 4 most active 20 s windows, every window
    |lag| < 0.5 ms.
- REA example: 6 sources, 27 min of audio (0.45 h of credits), done in about 95 s with uploads staggered by 1 s.
  Every file came back at 0.00 ms duration difference and 0.0 ms lag, going from about −37…−39 LUFS in to
  −16.0…−16.3 LUFS / −0.8…−0.9 dBTP out.

### Step 2: export the raw video

Use edit-borumi-video, with the baseline kept OUT of the deliverables folder:
`python3 ~/.borumi-edit/skill/scripts/finalize_export.py --work WORK --out-dir WORK/export/baseline --version N
--no-auphonic --keep-raw --no-phone --no-chapters` (N = the version you will deliver). This gives:

- `WORK/export/<name> - final edit vN (raw).mp4`: Borumi's untouched export. remix.py needs **this** file.
- `WORK/export/baseline/<name> - final edit vN.mp4` (+ its SRT): the same export with its audio moved to timeline 0
  (`atrim=2112` + `adeclick`). verify_remix.py uses it as the "what Borumi would ship" baseline.

Why `--out-dir`: finalize_export names its output vN and, once its checks pass, moves every older "final edit vK*" in its
out dir to `superseded/` by itself. Pointed at the deliverables folder, a re-export silently supersedes the version the
creator already has, and the remix then ships as vN+1. `deliver.py` does the delivery and the moves (SKILL.md step 11).

### Step 3: remix dry run (`remix.py` without `--export`)

```bash
python3 remix.py --project demo
```

Writes `stems/voice.wav` (mastered), `stems/raw_voice.wav`, `stems/other.wav` (system sound, audio,
sound effects, built-in sounds, music), `stems/mix_preview.wav` and `stems/dry_run.json`. Check:

- `voice_vs_raw_voice.lag_samples` ≈ 0. Auphonic must not move anything; REA measured 0.001 samples.
- `transcript_check`: a handful of words below −50 dBFS in the voice stem is normal (breaths, very short
  words; REA had 14 of 3144). `"of_those_voice_below_-40dBFS (real gaps)"` must be **0**.
- `transcript_words_outside_mic_segments` is 0, and `mic_coverage_by_scene` has no scene missing mic audio.
- `muted_segments_ignored` lists only segments you really muted.

### Step 4: remix against the raw export

```bash
python3 remix.py --project demo --export "WORK/export/<name> - final edit vN (raw).mp4" \
                 --out "WORK/export/<name> - final edit vN (mastered).mp4"
```

Output: the export's video stream copied, plus the new audio as the only (default) track: AAC 320 kbps,
48 kHz, dual-mono stereo, placed at **timeline 0**. Also `<out>.remix.json` (section 4). Options:
`--keep-original-track` (A/B in a player), `--other-gains fitted`, `--range S E` (fast partial null test, no
mix), `--keep-export-offset`, `--target-lufs`, `--true-peak`. The run aborts when the timeline hash changed
while it worked; override with `--allow-timeline-change`.

### Step 5: independent checks

```bash
python3 verify_remix.py "<...> (mastered).mp4" "<exports>/<name> - final edit vN.mp4" "<...> (raw).mp4" \
        --project demo --json WORK/export/verify_vN.json
python3 edit_point_check.py "<...> (mastered).mp4" "<exports>/<name> - final edit vN.mp4" --project demo \
        --verify-json WORK/export/verify_vN.json [--points 123.4 ...] [--range-worst 600 700 5]
```

`--cards` defaults to every sound_effect start, which covers the chapter-card clicks. See section 5 for what
"good" looks like.

### Step 6: full-mix Auphonic pass and stereo delivery (`master_mix.py`)

```bash
python3 master_mix.py "WORK/export/<name> - final edit vN (mastered).mp4" \
        --out "<exports>/<name> - final edit vN+1.mp4" --expect-size 1920x1080 \
        --copy-srt "<exports>/<name> - final edit vN.srt" \
        --phone "<exports>/<name> - final edit vN+1 (720p phone).mp4" \
        --supersede "<name> - final edit vN*"
```

The script runs these steps:

1. Extract the mix as mono 24-bit WAV.
2. Run the preset with a mono-mixdown WAV output.
3. Check that the Auphonic output has the **same length** as the upload and is **in sync**: every confident
   20 s window, taken every 90 s, at lag 0 samples.
4. Mux with `-ac 2`.
5. Check loudness and peaks: Auphonic WAV at target ±0.6 LU with TP ≤ −0.9; stereo mux within ±0.3 LU of the
   WAV with TP ≤ −1.
6. Check streams and durations.
7. **Only if every check passed:** copy the SRT, make the phone copy, and move the older versions to
   `superseded/`. Nothing is ever deleted.

`--from-work DIR` re-uses an existing `input.wav` + `auphonic.wav`, for example the work dir that upstream
`auphonic_master.py` left behind (its mux is **mono**), and only redoes the stereo mux and the checks.

REA example: upload −19.0 LUFS (one channel of the −16 LUFS dual-mono v1). Auphonic output −15.8 LUFS /
−0.9 dBTP mono, 57,722,880 = 57,722,880 samples, 13/13 windows at 0 samples. Stereo mux −15.8 LUFS /
−3.9 dBTP. Video 1202.55 s = 1202.55 s, audio 1202.56 s.

### Step 7: correlate the final against the mastered stem (`check_sync.py`)

```bash
python3 check_sync.py "<exports>/<name> - final edit vN+1.mp4" "<out>.auphonic_work/auphonic.wav"
python3 check_sync.py "<...> (mastered).mp4" "<audio_dir>/stems/voice.wav"     # a remix.py final
```

Pass criteria:

- every confident window has |lag| ≤ 0.5 samples;
- the length is within 50 ms.

It also reports whether L == R, the per-channel gain against the reference, and the stereo convention that
gain implies: −3.01 dB means an `-ac 2` upmix, 0 dB means a unity duplicate. Against a voice-only stem the
gain also includes the mix and master-bus gain, so read it as information only.

### Step 8: the v-next cut (shorter and punchier, made from the finished master)

Do not re-master, and do not go back to Borumi. Cut the finished master's Auphonic WAV:

- joins sit on the picture's frame grid (800 samples per frame at 48 kHz / 60 fps);
- each join gets an **8 ms equal-power crossfade centred on the cut**;
- mux with `-ac 2`, and keep the audio length equal to kept frames × 800 samples.

Then verify with `check_sync.py CUT.mp4 MASTER.auphonic_work/auphonic.wav --keep edl.json`. The EDL is
`{"keep_frames": [[f0, f1], ...], "fps": 60}`, `keep_s` or `keep_ms`. It rebuilds the reference from the kept
pieces and gives a per-piece lag report, which must be 0 for every piece.

## 4. Receipts

### `mastered/receipts.json` (one record per full media id; values below are placeholders)

```json
{
  "<media-uuid>": {
    "media_id": "<media-uuid>", "source": "<bundle>/medias/<media-uuid>.<ext>", "mastered": "<audio_dir>/mastered/<media-uuid>.wav",
    "production": "<auphonic production uuid>", "preset": "<preset uuid>", "preset_name": "Broadcast Voice",
    "used_credits": {"recurring": 0.0, "onetime": 0.05, "combined": 0.05},
    "auphonic_input":  {"loudness": [...], "signal_level": [...], "noise_level": [...], "snr": [...], "lra": [...]},
    "auphonic_output": {"loudness": [...], "lra": [...], "peak": [...], "gain_mean": [...], "gain_min": [...], "gain_max": [...]},
    "warning": null,
    "ok": true, "source_duration_s": 138.35, "output_duration_s": 138.35, "duration_diff_ms": 0.0, "lag_ms": 0.0,
    "lag_method": "GCC-PHAT + plain waveform xcorr on 20 s windows (sub-sample parabolic peak); envelope lag info only",
    "lag_windows": [{"t_s": 0.0, "gcc_phat_lag_ms": 0.0, "gcc_peak": 0.56, "xcorr_lag_ms": 0.0, "xcorr_coef": 0.93,
                     "env_lag_ms_info": -2.1, "env_corr": 0.90}],
    "input_lufs": -38.0, "input_true_peak_dbtp": -16.5, "output_lufs": -16.0, "output_true_peak_dbtp": -0.9,
    "output_format": {"codec_name": "pcm_s24le", "sample_rate": "48000", "channels": 1},
    "seconds": 27.5
  },
  "<failed-media-uuid>": {"media_id": "<failed-media-uuid>", "error": "<message>"}
}
```

`auphonic_input` / `auphonic_output` are Auphonic's own `statistics.levels`, copied as returned. The receipts
are rewritten atomically after every finished file, so a crash keeps the files that completed.

### `<out>.remix.json` (remix.py), the sections to read

| Key | Look for |
|---|---|
| `voice_vs_raw_voice.lag_samples` | ≈ 0 |
| `null_test.raw_lag_ms`, `priming_offset_samples_compensated` | 44–51 ms / 2112–2450 samples (see Lesson 1) |
| `null_test.residual_fitted` | overall ≈ −0.8…−1.5 dB, speech the same, other-dominated ≈ −3.5…−5 dB |
| `null_test.segment_aligned.segment_lag_ms.per_layer` | mic: 0.5–7.5 ms after 2112 priming; sfx/system: about 0 |
| `null_test.per_segment_lag_mic.over_1ms` | runs of consecutive segments sharing one offset = a scene's voice latency |
| `null_test.voice_agc_model_segment_aligned` | block-gain residual ≈ −3 dB; per-segment coherence median ≈ 0.85 |
| `null_test.fit_gains_db` | `fitted_db` vs `assumed_db` per source: a gross mismatch means a volume assumption is wrong |
| `null_test.muted_segments_check` | every verdict "absent from export" |
| `master_bus` | `limiter_lag_samples` 0, `limiter_max_gr_db` small |
| `final.loudness` | −16.0 LUFS, TP ≤ −1.5 dBTP |
| `final.lag_vs_master_wav_samples` | 0 |
| `final.lag_vs_raw_export_after_priming_ms` | within ±1 ms |
| `final.clicks` | `final_level_change_db` similar for every click |

### `<out>.master_mix.json`

This has the input, output, work dir, production and settings, Auphonic level stats, `lag_windows`
(`[t_s, lag_samples, peak]`), and the loudness of the input video, uploaded WAV, Auphonic WAV and output.
It also lists `checks[]` (name / pass / info) and `moved_to_superseded`. The preset UUID appears in the
settings: receipts are local working files, so keep them out of public repos.

## 5. What "good" looks like (measured on real runs)

| Check | Good | Seen |
|---|---|---|
| per-source master | duration diff 0 ms, lag 0 ms | REA: 6/6 at 0.00 ms / 0.0 ms |
| mastered vs raw voice stem | ≈ 0 samples | 0.001 samples (both projects) |
| raw export lag (global) | 44–51 ms | 44.3–44.8 ms (one project), 50.7 ms (REA) |
| null residual, fitted gains | ≈ −1.2 dB | −0.8…−1.48 dB; REA −1.16 dB |
| null residual, other-dominated blocks | ≈ −4 dB | −3.6…−4.8 dB |
| final loudness | −16.0 LUFS, TP ≤ −1.5 (remix) | −16.0 LUFS / −1.5 dBTP, LRA 2.3–2.9 |
| final vs Borumi post-processed, voice | slightly negative | median −1.06 ms (one project); up to ≈ −7 ms by scene (REA) |
| final vs Borumi, clicks and playback | ≈ 0 | median −0.08 ms, max 0.54 ms |
| pop scan, random speech baseline | — | p50 5.3, p95 17.0, p99 20.8, max 33 dB |
| pop scan, edges > 20 dB | each explained by edit_point_check | 12–16 edges per run, all "inaudible" or "content" (0 "CHECK") |
| chapter click vs program | similar to Borumi | −27 dB (final) vs −29 dB (Borumi); peak −18.4 dBFS per channel |
| full-mix pass | same length, 0-sample windows, ±0.3 LU after mux | REA: equal samples, 13/13 at 0, −15.8 → −15.8 LUFS |

## 6. Lessons (quirks, failure modes, fixes)

These lessons are the most valuable part of this file. Each one was learned the hard way.

1. **Borumi's export audio lag is not constant, so rebuild at timeline positions.** The raw export's audio
   starts **2112 samples (44 ms at 48 kHz) late**. This is AAC priming, and Borumi's MP4 edit list starts at 0.
   System sound and sound effects sit exactly at that offset. The **microphone also carries Borumi's
   voice-enhancement latency, about 0.5–7.5 ms, which differs per clip and per scene**, so the total voice lag
   is **44–51 ms**.
   - In REA, the global lag was 50.67 ms (2432 samples).
   - The mic segments' own lags after 2112 priming ranged from 0.54 to 7.56 ms, with a median of 6.56 ms.
     The sound effects sat at 0.15 ms.
   - Whole scenes showed as runs of consecutive mic segments all at the same −6.04 ms offset against the
     global lag.

   The upstream post-process (`atrim=start_sample=2112`) is exact for clicks and playback, but it leaves the
   voice up to ≈ 7 ms late in some scenes. `remix.py` does not shift the export. It places every segment at
   its `get_timeline` position, so voice, clicks and screen audio all land on the picture. Prove it with
   `final.lag_vs_raw_export_after_priming_ms` ≈ 0 and with edit_point_check's picture timing on clicks and
   playback (≈ 0 ms).

2. **Read the per-segment lag report, not just the global lag.** `segment_lags()` and `aligned_null()` measure
   each segment against the export. A run of segments with an identical offset is Borumi's per-scene voice
   latency, not a remix error. `per_layer` separates mic, which shows the latency, from sfx/system, which sit
   at ≈ 0. A single segment that is off on its own, with a confident peak, is a real placement problem: check
   that segment's `start_ms` and its source in-point. Sub-ms scatter is expected, because `get_timeline`
   returns integer ms while Borumi stores sub-ms positions (error ≤ 1 ms, inaudible).

3. **A null residual of about −1.2 dB is normal.** Borumi's "extended" voice enhancement is denoise, plus
   per-clip loudness normalization (an AGC), plus a master limiter. No static gain can null that, so a raw
   re-render plus least-squares gains only reaches about −0.8…−1.5 dB on speech. Other-dominated blocks reach
   about −4…−5 dB.
   - Do not chase the residual. Prove that the content and timing are the same instead: model the AGC as a
     gain per 0.5 s block, which gets about −3 dB in REA, and look at the 100 Hz–4 kHz coherence per segment,
     with a median of about 0.85 (p10 0.78).
   - The residual is a gate only in two cases: when it is much worse than −0.5 dB, or when the
     other-dominated blocks don't null. Then something is missing or misplaced: a muted segment that Borumi
     plays, a wrong volume assumption, a missing built-in sound.

4. **Stereo conventions: pick one per file, and know which one you have.**
   - **Auphonic mono output is measured per channel.** With a −16 LUFS target, a mono WAV measures −16 LUFS
     as a single channel.
   - **ffmpeg's mono→stereo upmix (`-ac 2`) applies −3.01 dB per channel.** This is the centre mix level.
     BS.1770 sums the power of L and R, so **−16 LUFS stays −16 LUFS**, while the true peak per channel drops
     by 3 dB (REA: −0.9 → −3.9 dBTP). This is the right way to deliver Auphonic's mono master as stereo.
   - **Duplicating the mono at unity** (`pan=stereo|c0=c0|c1=c0`, numpy `repeat`) gives **+3 dB**: −16 becomes
     −13 LUFS, and the per-channel true peak stays at −0.9, which is over a −1 dBTP ceiling.
   - remix.py deliberately writes the duplicate (dual mono). It therefore measures its mono mix with
     `ebur128 dualmono=true` (+3.01 dB) and gains it so that **the stereo file** is −16 LUFS, which puts each
     channel at −19 LUFS. That is why the REA full-mix upload measured −19.0 LUFS.
   - `check_sync.py` infers the convention from the per-channel gain against the stem: about −3.01 dB means
     an upmix, about 0 dB means a duplicate.

5. **ffmpeg's stereo→mono downmix gain depends on the OUTPUT sample format.** Verified in `selftest.py`.
   - With a float output (`-f f32le`, which is what `audio_lib.load()` uses), `-ac 1` gives (L+R)/√2. That is
     **+3.01 dB** on a dual-mono file.
   - With an integer output (`pcm_s16le`/`pcm_s24le` WAV), the matrix is normalized to (L+R)/2, which is
     **0 dB** on dual mono.

   The same command can therefore read 3 dB apart. To measure a stereo deliverable, use one channel
   (`load(path, channels=2)[:, 0]` / `load_one_channel()`). To make a mono upload, use an explicit
   `pan=mono|c0=0.5*c0+0.5*c1`, as `master_mix.py` does.

6. **AAC at 320 kbps needs stereo.** AAC-LC allows at most 6144 bits per channel per frame, which is about
   288 kbps per channel at 48 kHz. Borumi exports mono AAC, and upstream `auphonic_master.py` muxes the mono
   WAV as mono. Deliver `-ac 2` (master_mix.py) or dual mono (remix.py). Always mux with `-c:v copy`, so the
   picture is never re-encoded by the audio steps.

7. **Verify Auphonic sample-exact, and treat the envelope as information only.** The per-source masters and
   the full-mix pass both came back with the same sample count and a lag of 0 on every window. The check uses
   GCC-PHAT plus plain cross-correlation; the full mix uses integer argmax with peak > 0.05. Auphonic's leveler
   and speech isolation remove reverb tails, which moves the *envelope's* centre of mass by a few ms (about
   −2 ms) while the waveform is exact. An envelope lag alone is therefore never a failure.

8. **Auphonic API quirks.**
   - The output download redirects to S3, and **S3 rejects the Authorization header**. Follow the redirect
     without it (`auphonic_api.download`).
   - `loudnesstarget` must be an **integer** from Auphonic's list (−16, not −16.0). Do not send `bitrate` as
     a string.
   - Status 3 means done; 2 or 11, or a `status_string` starting with "Error", means failed.
   - Check credits first. Each production bills at least **0.05 h** (3 min): a 2.3 min source was billed
     0.05 h, while longer files were billed their exact length. The guard in master_sources.py adds 0.05 h per
     file for this reason.
   - Run the per-source productions **concurrently**, one thread each with a 1 s stagger. A failure in one
     thread is recorded in the receipt and does not kill the others.
   - The per-source preset must output exactly one WAV 24-bit file that is not a mixdown. The script fails
     loudly with the list of outputs otherwise.
   - The full-mix pass asks for `output_files: [{"format": "wav", "mono_mixdown": true}]` together with the
     preset.

9. **Master bus: limit with margin, then re-measure after the encode.** After a static gain to the target,
   true-peak limit with `alimiter` at 4× oversampling with `latency=true`. Verify that the limiter adds no
   delay: `limiter_lag_samples` must be 0. The ceiling is the target −0.5 dB (−1.5 dBTP), because the AAC
   encode raises true peaks. If the encoded file still reads above −1 dBTP, the ceiling is lowered and the mix
   re-encoded (up to 3 tries).

10. **Edge handling at segment joins.** Every voice/system edge gets a 10 ms equal-power fade:
    - a crossfade centred on the cut, with 5 ms handles from each source, when the neighbour butts against it;
    - a fade inside the segment at a free edge;
    - **nothing** when the neighbour continues the same source contiguously (same media, same speed, source
      in-point continuous within 1.5 ms).

    One-shot sound effects are never faded. Speed changes are rendered with an `atempo` chain, each stage
    within 0.5–2.0. Borumi renders a **stereo source to mono as (L+R)/√2**: this was verified on a chapter
    click, with correlation 1.000 and +3.01 dB against (L+R)/2.

11. **Volumes and mutes.**
    - `volume_filter` on audio, sound_effect and music segments is a dB offset, with 0 = unity. Built-in sound
      `volume` is in dB as well. The cursor `click_sound_volume` is stored as −5.0, also dB.
    - Segments at or below **−30 dB** are treated as muted, because −30 dB is the floor of Borumi's UI.
      Upstream ducks use −40 dB, and stray-word mutes use −60 dB.
    - `muted_check` proves whether Borumi's export really leaves those segments out.

    Built-in transition and enter sounds start at the segment start, and exit sounds start at the segment end.
    Their files are `built-in-sound-<id>.wav` in the app's Resources folder. Music fades are assumed to be
    1.0 s linear, which has not yet been verified on an export with music: check `fit_gains_db`.

12. **Read Borumi defensively.**
    - Use read tools only; this is enforced in code.
    - Read `get_timeline` in 60 s chunks and merge segments by id.
    - Re-read the whole timeline when the hash changes mid-read (up to 12 tries).
    - Compare the hash again before mixing and abort if it moved: the export must match what was rebuilt.
    - Never open the live `project.bmproj`. Copy it and run `pragma integrity_check` on the copy first.
    - **Short ids** are shortest unique prefixes. A short id that is itself a prefix of another short id in
      use maps to the candidate **not** claimed by the longer one (`resolver()`).

13. **Pop scan: score versus step.** The score is the peak |2nd difference| within ±2 ms of the edge, against
    the RMS of the 2nd difference over ±50 ms with the centre ±5 ms excluded. Because it is relative, a cut in
    near-silence can score 40–50 dB with an absolute step below −80 dBFS, and that cut is inaudible.
    `edit_point_check.py` classifies every edge over 20 dB:
    - "inaudible (step below −60 dBFS)";
    - "content": the same transient is present in the mastered source at the cut, so the edit cut speech;
    - "CHECK": unexplained. Listen to it, then move or extend the cut in Borumi.

    Compare against the random-speech baseline (p99 ≈ 21 dB) and against Borumi's own render at the same edges.
    Also check `short_neighbour_segments_lt_150ms`: slivers of a removed word are an editing issue, not an
    audio one.

14. **Dry-run transcript gate.** A word whose raw level is at or above −50 dBFS but whose mastered voice is
    below −40 dBFS is a **real gap**: a mastered file is missing, the wrong source was resolved, or a mic
    segment lies outside the source. There must be 0 of these. Quiet words that are quiet in both are normal.

15. **Supersede only after every check passes, and never delete.** Older versions move to `superseded/` only
    when every check is green. The SRT is copied before moving: this pass doesn't change timing, so the
    previous SRT stays valid. The phone copy is always made from the final file, never from an unfixed one.

16. **Measure the levels of stereo deliverables per channel and check that L == R.**
    `verify_remix.py` reports `final_LR_identical_max_abs_diff`. Compare program loudness one channel against
    one channel: final −19.0 against Borumi −14.3 per channel in one project. This avoids a false "3 dB
    quieter" conclusion.

17. **Rebuild the v-next audio from the master WAV, not from the MP4's AAC.** Cutting the master's
    `auphonic.wav` avoids a second lossy generation and keeps the audio sample-aligned to frames. The audio is
    exactly kept frames × 800 samples long, with an 8 ms equal-power crossfade centred on every join. It is
    verified by correlating the cut against the concatenated master pieces (`check_sync.py --keep`).

18. **Guard a "re-run me under the venv" with an environment variable, not an interpreter comparison.** The first
    packaged version compared `realpath(sys.executable)` with `realpath(.venv/bin/python)` to avoid an exec loop. A venv
    made with `python3 -m venv` symlinks to the same base binary, so both resolve to one file, the re-exec was always
    skipped, and every script stopped with "numpy is required" pointing at the venv that already existed. Now the
    first exec sets `PFVE_AUDIO_REEXEC=1` (a second failure stops with the install hint), the venv is exec'd by its own
    path (so `pyvenv.cfg` is found), `uv run --with numpy --with scipy` is the last fallback, and the flag is cleared
    once numpy imports so child processes decide for themselves.
19. **The full-mix pass is the house standard, not an option.** "The full final always goes through Auphonic and its
    audio is the main track" (creator-style §9). Per-source mastering (step 7a) makes the voice clean and level; the
    full-mix pass (`master_mix.py`, step 7f) is what ships, and its `auphonic.wav` is what a v-next cut is cut from.
    Skip it only on the creator's word.

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `mastered file missing for mic sources` | a new take was added after mastering | `master_sources.py` (it skips the ones already verified) |
| `expected exactly one wav-24bit (non-mixdown) output` | the preset's outputs | add a WAV 24-bit output to the preset, or remove the extra ones |
| `not enough Auphonic credits` | credit guard | buy credits; or `--only` / `--media` to master fewer files |
| `No Auphonic API key` | env/Keychain empty | `security add-generic-password -U -a "$USER" -s auphonic-api-key -w '<key>'` |
| `is not open in Borumi` / `open as project X, config says Y` | wrong bundle open, or a copy opened from elsewhere | open the right bundle; fix `project_id` |
| `timeline kept changing while reading` / `timeline changed during the run` | someone is editing | wait for the edit (lock) to finish, then re-run |
| null residual near 0 dB at a sound effect | that click was not reconstructed | check the built-in sound path, its `volume`, and the sfx layer kind |
| `fit_gains_db` far from `assumed_db` for one source | a volume assumption is wrong (fades, loop, dB scale) | use `--other-gains fitted` for this run; report it |
| final ≈ −13 LUFS after muxing Auphonic's mono WAV | the mono was duplicated at unity | mux with `-ac 2` (master_mix.py) |
| a "3 dB too loud/quiet" reading on a stereo file | ffmpeg float downmix (`-ac 1` to f32) | measure one channel; see Lesson 5 |
| encoded true peak > −1 dBTP | AAC overshoot | remix.py lowers the ceiling automatically; master_mix fails the check: re-run Auphonic with a lower max peak |
| voice in the post-processed Borumi export sounds slightly late against the lips | Borumi's per-scene voice latency (Lesson 1) | ship the remix; it is placed on the timeline |
| `numpy is required` although the venv exists | an old copy of audio_lib.py (Lesson 18), or the venv lacks numpy | update; `.venv/bin/pip install -r requirements.txt`; or set `$PFVE_AUDIO_PYTHON` |
| `no Auphonic preset` | no preset UUID configured | `python3 auphonic_api.py --presets`, then `export AUPHONIC_PRESET=<uuid>` or `auphonic.preset` in the config |
| a read-only call hangs or says "session not running" | the borumi_mcp.py daemon is not running, or was started in the foreground and killed | start it backgrounded and wait for `sess/ready` (§1) |
