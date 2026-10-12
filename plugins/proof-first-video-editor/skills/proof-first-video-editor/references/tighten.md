# Tighter cut (v-next) straight from the finished master

When the creator says "make it shorter and punchier, no redundancy, better flow", don't re-edit the editor project. Cut
the **finished master** directly: every removed time range takes picture (left-panel visual, camera tile, badge) and
audio out together, so whatever is left stays in sync. The cut is decided on words, placed on real audio silence,
fixed by eye where a visual would flash, built in one encode, and checked in layers.

Reference run (the "REA" video): 20:02 master, six sections, six chapter cards. 69 proposed cuts, 64 kept after
reviewing the risky ones, 56 removal ranges after merging, 16 hand pins (18 after the final QA), 3 panel patches,
4 builds (preview, final, r2 and r3 after the final QA). Result: 14:02, frame-exact, 0 clicks, 0 loud joins, word match 0.97 against the expected
script, chapters and captions mapped through the cut.

Nothing here writes to the editor project or the exports folder. Delivery (versioned names, moving the previous
version to `superseded/`) is a separate step with its own checks: `references/delivery.md`.

## Requirements

| Tool | Used by | Notes |
|---|---|---|
| ffmpeg + ffprobe >= 6 (7+ recommended), libx264 | all | `-/filter_complex FILE` needs ffmpeg 7+; on 6 `build_cut.py` falls back to `-filter_complex_script` (removed in ffmpeg 9) |
| python3 >= 3.9 | all | stdlib only for make_sections, apply_pins, build_captions, collect_cuts |
| numpy | snap_cuts, build_cut, qa_cut | or run them with `uv run --no-project --with numpy python3 <script>` |
| whisper-cli (whisper.cpp) + a ggml model | qa_cut, words_from_whisper | `--whisper-model`, `$WHISPER_MODEL` or config `whisper_model`; the small model is enough |
| Claude Code Workflow tool | workflows/tighten.js, workflows/final-qa.js | the multi-agent editing and QA passes |

Smoke test (synthetic media, ~5 s), run from the repository root (it is not part of the installed skill folder):
`bash tests/tighten_smoke.sh` (`PYTHON=...` to pick the interpreter, `KEEP=1` to keep the temp folder).

## Files

| File | What it does |
|---|---|
| `scripts/tighten/tighten.example.json` | the project config every script reads (`--config`) |
| `scripts/tighten/words_from_whisper.py` | fallback: per-scene word + sentence files from a whisper pass over the master |
| `scripts/tighten/make_sections.py` | one numbered markdown script per section (sentences with word ranges + word/gap table) |
| `workflows/tighten.js` | narrative editor + section editors -> judge -> skeptic -> revise, per section |
| `scripts/tighten/collect_cuts.py` | workflow result -> `cuts.json` (+ a pins draft), prints blocking issues, pin flags, "could" cuts |
| `scripts/tighten/snap_cuts.py` | word-index cuts -> frame-grid removal ranges at the quietest audio point (+ pause tightening) |
| `scripts/tighten/apply_pins.py` | hand pins: per-cut edge overrides, start/end by-value nudges, grid + overlap checks |
| `scripts/tighten/build_cut.py` | one-encode build: frame cuts, 8 ms crossfaded mastered audio, tail-render overlay, panel patches |
| `scripts/tighten/build_captions.py` | master SRT + chapters mapped through the cut |
| `scripts/tighten/qa_cut.py` | whisper diff, join clicks, join contact sheets, loudness, join transcriptions, PSNR, flash scan |
| `workflows/final-qa.js` | three independent reviewers: join visuals, narrative flow, watch-through |
| `scripts/tighten/frame.sh` | one frame by ms or exact frame index, for agents to look at |
| `scripts/tighten/tlib.py` | shared helpers (config, words, grid, ffprobe) |

## Inputs and data shapes

- **Master**: the delivered mp4, CFR, integer fps (60), 48 kHz audio. Frame `n` sits at `n * 1000 / fps` ms; every cut
  edge lives on that grid.
- **Mastered audio WAV** (`audio_wav`): the Auphonic output the master was muxed from (`scripts/audio/master_mix.py`
  leaves it in `<OUT>.auphonic_work/auphonic.wav`, i.e. `<name> exports/_work/export/<name> - final edit vN (mastered)
  (auphonic).mp4.auphonic_work/auphonic.wav` for the vN made in SKILL step 7f), sample-aligned with the master: same start, same length, zero lag
  (the audio pipeline verifies this with GCC-PHAT). Cutting the WAV instead of the master's AAC saves a generation.
  Without it `build_cut.py` cuts the master's own track (channel count preserved so the level doesn't drop 3 dB).
- **Word files** `words_dir/<scene>.json`: `[[start_ms, end_ms, "word"], ...]` in MASTER ms, one file per scene, plus
  optional `<scene>_sentences.json`: `[[start_ms, end_ms, "text"], ...]`. Best source: `scripts/borumi/scene_words.py`
  (whisper + plan spelling + CTC alignment; project ms equal master ms when the master is a full export of the
  project). Fallback: `words_from_whisper.py` on the master. Example: `[[98, 380, "This"], [380, 601, "new"], ...]`.
- **Config** (`tighten.example.json`): master, audio_wav, fps, sample_rate, words_dir, sections_dir, srt,
  sections `[{key, scene, title, range:[a,b]}]` (a section = what one editor owns; usually one scene), cards
  `[{start_ms, end_ms, title, chapter, chapter_at_ms}]` (ranges no cut may touch, and the chapter titles),
  sub_chapters `[{at_ms, title}]`. Relative paths resolve against the config's folder.
- **cuts.json**: `{"cuts": [{section, scene, from_word, to_word, first_word, last_word, removed_text, kind, priority
  (must|should|could), reason, join_reads, pin_start_ms?, pin_end_ms?}], "pause_safe": [{from_ms, to_ms, note}]}`.
  `from_word`/`to_word` are INCLUSIVE indices into one scene's word list.
- **ranges.json** (snap_cuts / apply_pins): `{"ranges": [{section, scene, from_word, to_word, text, start_ms, end_ms,
  rms_in_db, rms_out_db, gap_before_ms, gap_after_ms, priority, kind, merged?}], "warnings", "removed_ms",
  "new_duration_ms", "total_ms", "fps"}`.
- **pins.json**: `{"edge_overrides": [{section, from_word, start_ms?, end_ms?, why}], "end_by_value": [{from_ms, to_ms,
  why}], "start_by_value": [...]}`.
- **patches.json**: `[{"from": f0, "to": f1, "src": fs, "why": "..."}]` in MASTER frames, `[f0, f1)`.
- **edl.json** (`OUT.mp4.work/`): `{"keep_frames": [[f0, f1], ...], "remove_frames": [...], "fps": 60, "master": ...}`:
  the input of `build_captions.py`, `qa_cut.py`, `scripts/deliver/chapters.py --edl` and
  `scripts/audio/check_sync.py --keep`.

## Playbook

```bash
SK=<skill dir>; T=$SK/scripts/tighten
PY="python3"                      # must have numpy for snap/build/qa (or: PY="uv run --no-project --with numpy python3")
V=<work dir for this cut>         # e.g. "<exports>/_work/v3"; config at $V/tighten.json
```

0. **Prepare.** Write `$V/tighten.json` (copy `tighten.example.json`). Word files from `scene_words.py`, or
   `$PY $T/words_from_whisper.py --config $V/tighten.json`. Check the card ranges against the master with frame.sh
   (first and last white frame of each card).
1. **Section scripts.** `$PY $T/make_sections.py --config $V/tighten.json` -> `sections/<key>_<scene>.md` + `index.json`.
2. **Plan the cut** with `workflows/tighten.js`. Args: `skill_dir`, `work: $V`, `master`, `video_title`,
   `sections` (= `index.json` `sections_list`), `cards`, `config`, `python`, plus `creator`, `request`, `audience`,
   `keep`, `accuracy_notes`, `target` (header of the file). A session limit mid-run is fine: relaunch with
   `resumeFromRunId`; finished agents replay from cache.
3. **Collect.** `$PY $T/collect_cuts.py <result.json> $V/cuts.json --config $V/tighten.json --pins-draft $V/pins.draft.json`.
   Read every blocking issue (the reviser should have handled it), every pin flag, and every "could" cut.
4. **Snap.** `$PY $T/snap_cuts.py --config $V/tighten.json $V/cuts.json $V/snapped.json` (add `--tighten-pauses` only
   if the editors listed `pause_safe` ranges). Zero warnings is the goal; a loud-edge warning means listen to it.
   Dry runs are cheap: run it on judge lists while the skeptics are still working.
5. **Pin.** Turn the pin flags into `$V/pins.json` after checking frames on both sides of each pinned edge
   (`frame.sh <ms>`), then `$PY $T/apply_pins.py $V/snapped.json $V/pins.json $V/ranges.json`.
6. **Preview build + QA** (catches pipeline problems before the slow encode):
   `$PY $T/build_cut.py $V/ranges.json "$V/preview.mp4" --config $V/tighten.json --preset veryfast --crf 20 [tail/patch flags]`
   then `$PY $T/qa_cut.py "$V/preview.mp4" "$V/preview.mp4.work/edl.json" $V/qa_preview --config $V/tighten.json --flash-scan --psnr-joins 8`.
   Listen to every "could" join; drop the bad ones (`collect_cuts.py --skip SECTION:FROM_WORD`) and go back to 4.
7. **Final build** with `--preset medium --crf 16`, then `qa_cut.py` again on it, and optionally
   `$PY $SK/scripts/audio/check_sync.py "$V/final.mp4" <audio_wav> --keep "$V/final.mp4.work/edl.json"`.
8. **Final QA** with `workflows/final-qa.js` (`cut_video`, `qa_dir`, `video_title`, labels/lengths, `badges`, `cards`,
   `chapters_file`, `creator_rules`, `ending_check`, `pinned_joins_s`). Map every finding back to the master through
   `edl.json` and fix it as a pin (`start_by_value`/`end_by_value`), a panel patch, or a changed cut. Rebuild as
   r2, r3 ... and re-run 6-8 until clean.
9. **Captions + chapters.** `$PY $T/build_captions.py --config $V/tighten.json "$V/final.mp4.work/edl.json" <master.srt> "$V/final.srt" --chapters-out "$V/youtube-chapters.txt"`,
   then the name fixes (`scripts/deliver/fix_srt.py`) if the master SRT wasn't fixed, and `scripts/deliver/chapters.py
   --edl ... --strict` as the YouTube-rules gate (both produced the same published block on the reference video).
10. **Deliver** with `scripts/deliver/deliver.py` (`references/delivery.md`). Ask the creator first: moving the previous
   version to `superseded/` is their call (on the reference video the creator stopped the automatic move and wanted the
   chapter titles first).

## Lessons (symptom -> cause -> fix)

### Transcript and snapping

1. **Whisper word times are +-30 ms and usually contiguous** (one word's end is the next word's start). Cutting at a
   transcript boundary leaves slivers of the neighbouring word. -> Choose cuts on words, place them on audio:
   `snap_cuts.py` puts each edge on the quietest frame-grid point (20 ms RMS on the mastered WAV) between the end of the
   previous word - 40 ms and the start of the next + 40 ms.
2. **The first/last word check matters.** Editors miscount indices. A mismatch warning (`'and'/'resell.' vs
   'customize'/'resell.'`) means the cut is not what the editor described: fix the indices, never ignore it.
3. **Loud edge warnings (> -38 dB) are "listen to this", not "wrong".** A fading "s" at -38.7 dB with an 8 ms
   crossfade (about 25 dB below speech) was fine; a -31 dB edge turned out to be a rounding bug (lesson 4).
4. **Cuts from a scene start round DOWN to the grid** (<= one frame of the card tail goes with them). Rounding up left a
   -31 dB sliver of the first word; flooring measured -70.7 dB. Card clamps floor too.
5. **The snapper breaks ties toward the earliest quiet point** (first of equally silent grid points). In digital
   silence that can remove most of a pause before a cut; if a join sounds rushed, pin.
6. **Pause tightening is mechanical: only where the picture is static.** Shortening a pause over demo footage removes
   the thing happening on screen. Editors list `pause_tighten_safe` / `pause_tighten_unsafe`; on the reference video it
   found one pause worth 0.3 s and was left out of the final.

### Choosing cuts

7. **Cut whole clauses and sentences.** Sentence or clause boundaries (punctuation, a positive gap) snap cleanly; a
   single filler at the start of a sentence is fine; single words mid-phrase are risky -> priority "could".
8. **"Could" cuts need ears.** Every one gets reviewed (reason, join, visual) and listened to in the preview build.
   The reference video kept 9 of 14.
9. **Don't merge two cuts into a crushed sentence break.** Two adjacent cuts that leave ~100 ms between sentences sound
   like a stumble; reject one. Around 250-300 ms of pause at a join reads naturally; 176 ms sounded rushed.
10. **Orphans.** Cutting a sentence but keeping its starter leaves a fragment ("For me," between two cuts). The
   narrative QA catches these; the fix is to move the cut edge earlier into the silence before the fragment.
11. **Promises vs delivery.** A roadmap line that promises something the cut no longer delivers ("we'll put our own spin
   on it") must be trimmed too. So must a spoken "Level 1" that now plays under a different badge.
12. **Accuracy is a cutting reason.** Overstated clauses (licenses, prices, free trials, resale rights) are best cut when
   the sentence still works without them. Never invent; we can only remove. Report the rest to the creator.
13. **Each kept claim keeps its proof visual; each kept visual keeps its narration.** Look at frames on both sides of
   bigger cuts: a visual on screen that the kept narration never mentions is confusing, a claim whose proof was cut
   is weaker.

### Edges and visuals

14. **The snapper only knows audio.** Skeptics found edges where the audio was silent but the picture was wrong, and
   notes like "out-point at or after 570480" can't be expressed to the snapper. -> Hand pins in `pins.json`, applied
   after snapping. Pin both edges together when one moves (keeps the join pause).
15. **Card-edge flash frames.** A cut ending ONE frame before a chapter card shows one frame of the previous visual
   right before the white card (fixed by ending on the card's first frame: 516616.667 -> 516633.333). A cut that starts
   right after a card can snap onto the card's last white frames (518233.3 was still the card) and shortens it: pin the
   start after the card (518333.3, silent). `qa_cut.py --flash-scan` catches the first kind (one big panel jump right
   after the join, then calm: 33.2 vs <= 1.0 on the reference video).
16. **Rounding flashes.** `build_cut.py` turns ms into frames with `round()`. An out point of 1130367 ms rounds to frame
   67822 and shows one full-brightness frame of the removed panel; 1130383.333 (frame 67823) is clean. Pins must be
   exact grid values (`apply_pins.py` rejects off-grid ones).
17. **Removed visuals fade out after the join.** When a cut removes the narration that introduced a card, the card's
   fade-out can still be visible for a few frames after the join (13 frames of a removed card on the reference video).
   Fix: move the out point later into silence (pin), or a **panel patch** replacing the left panel for those frames
   with the first clean frame after the fade.
18. **Near-empty panel after a cut.** A cut can land where the panel is mid-transition and nearly empty for a second.
   Fix: a panel patch that holds an earlier frame (src = the last good frame before the cut).
19. **The flash scan can't see semantic leftovers**: a faint residue, a visual stranded without its narration, a
   near-empty panel. Those need eyes on the contact sheets (final-qa.js joins reviewer). Don't skip it.
20. **Tail render.** A re-rendered full-frame visual for the end (on the reference video: the ending re-rendered to show
   the creator's real community post instead of a pricing page) can be overlaid during the cut instead of placing it
   back into the project first: `--tail-render R.mp4 --tail-start-s T0 --tail-from-frame F --tail-keep-rect X,Y,W,H`
   (the keep-rect = the camera tile + badge region where the master must stay, e.g. `1512,36,394,844`).

### ffmpeg

21. **ffmpeg 9 removed `-filter_complex_script`.** The graph file is passed as `-/filter_complex FILE` (ffmpeg 7+).
22. **The select expression parser dies above 100 `+` terms** ("Error while parsing expression", followed by a
   misleading "Cannot allocate memory"). QA contact sheets run 10 joins (20 terms) per pass; `build_cut.py` groups keep
   terms in parentheses of 50 when there are more than 100 pieces (tested to 2000).
23. **Looped still inputs must be bounded.** `-loop 1 -i still.png` never reaches EOF: the final encode sat at the
   last frame for 10 minutes at a falling 1.4x before it was killed. Every still is `-loop 1 -t <window> -itsoffset
   <start>` and the overlay uses `eof_action=pass`. The encode went back to ~7-9x real time.
24. **Half-frame lead.** With the patch input starting exactly at the window, the FIRST patched frame sometimes had no
   patch frame (timestamp rounding). Start the input half a frame early (`itsoffset (from - 0.5)/fps`) and enable from
   `from - 0.4`.
25. **Exact frames by seeking.** `-ss (n - 0.3)/fps` before `-i` lands exactly on frame n; `-ss n/fps` can land on n+1.
   `frame.sh f<N>` does this.
26. **No drawtext.** Many ffmpeg builds have no freetype; label QA frames by file name instead. Under zsh, `rm -f *.png`
   in an empty folder aborts the loop ("no matches found"): run frame loops with bash.
27. **`--limit-s 30`** checks the cut logic on the first 30 s in seconds; a `veryfast`/crf 20 preview of a 20-min master
   runs at about 9x real time (1080p60, Apple silicon); the final at medium/crf 16 similar.

### Audio

28. **Cut the mastered WAV on the frame grid in samples** (800 samples per frame at 48 kHz / 60 fps) with an 8 ms
   equal-power crossfade centred on every join. Result on the reference video: 0 click suspects, 0 loud joins, the
   loudness unchanged (-15.8 LUFS integrated, true peak -3.9 dBFS).
29. **mono -> stereo with `-ac 2` is -3 dB per channel and keeps integrated loudness**; stereo -> mono averages the
   channels. Decoding a dual-mono master to mono and muxing it back as stereo would drop 3 dB, so the fallback path
   keeps the master's channel count.

### QA

30. **Layered QA, in this order**: the build check (frames, durations), `qa_cut.py` (transcript, clicks, sheets,
   loudness, mapping), then three independent reviewers (joins, flow, watch-through), then the creator.
31. **Most whisper diffs are spelling** (names, "README" -> "read me", "gonna" -> "going to"). word_match_ratio ~0.97
   is normal with the small model. Only deletes/inserts within ~500 ms of a join are suspicious.
32. **Verify suspicious diffs by transcribing each side of the join separately** (`qa_cut.py` join checks: 3 s before
   and after the join in the cut, and the master around the cut point). On the reference video every one was benign:
   "video more" was the speaker's own words in the master, and the dropped/added "and" was whisper merging two phrases.
33. **PSNR mapping spot check**: frames next to a join vs the master frames the EDL says they come from: 43-50 dB when
   right, 5-10 dB when off by a piece. Tail renders and patches lower it locally.

### Captions and chapters

34. **Partly cut cues lose exactly the removed words** (whisper word midpoints vs the EDL, difflib alignment of cue
   tokens to words). Capitalise the first kept word only when the cut removed a sentence start; hand a removed
   sentence-final run's period to the kept word before it.
35. **Chapters**: card start mapped through the cut and rounded UP to the next second (the click lands on the card);
   sub-chapters at their first kept word, rounded down; "Chapters" header (the same as `chapters.py`, so both tools give
   identical text; deliver.py strips trailing spaces either way); >= 10 s apart, >= 3 chapters, or YouTube drops them
   all. Deliver `chapters.py --edl ... --strict` output for the v-next cut (it is the YouTube gate); build_captions'
   block is the cross-check and must match it line for line.

### Process

36. **Dry-run early.** While the skeptics work, run the judges' lists through the snapper and a preview build + QA:
   on the reference video this found the contact-sheet parser limit before the final render.
37. **Skeptics earn their keep**: they found two blocking edge problems (a 13-frame flash of a removed card, a rounding
   flash) that the judges' lists would have shipped. Let them dry-run the snapper and look at frames at the snapped ms.
38. **Final QA after the final build always finds something** (an orphaned starter, a stale promise, leftovers at joins).
   Budget a second and third build (r2, r3).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Unrecognized option 'filter_complex_script'` | ffmpeg 9 | build_cut.py uses `-/filter_complex` on ffmpeg 7+; update the script if you forked it |
| `Error while parsing expression` (select) | > 100 `+` terms | chunk the select (QA) / grouped terms (build) |
| encode stuck at the last frame, speed falling | an unbounded `-loop 1` input | bound every looped still with `-t` + `-itsoffset` |
| first frame of a patch window unpatched | timestamp rounding | half-frame lead (already in build_cut.py) |
| one bright frame of a removed visual at a join | off-grid or rounded edge, or a cut ending a frame before a card | exact grid pins; end on the card's first frame |
| a removed card fades out after the join | its fade-out lies after the snapped out point | later out point (pin) or a panel patch |
| `BUILD CHECK FAILED` | frame count or A/V length mismatch | read build_report.json; check the WAV length vs the master (sync warning) |
| `pin matched no range` | wrong section/from_word, or a merged range keyed by its first cut | use the first cut of the merged range; by-value pins for edges |
| `off the 60 fps grid` | pin like 305183.3 | write 305183.333 (k x 16.667) or `--snap-to-grid` |
| contact sheet pairs shifted | a one-frame keep piece | look at the frames around it by hand |
| whisper diff at a join | merging, spelling, or the speaker's own words | qa_cut.py join checks; listen |
