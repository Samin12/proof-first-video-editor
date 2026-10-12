# Delivery: captions, chapters and the final files

The last mile of a proof-first edit: a mastered final in the right place under the right name, captions with the
names spelled right, a chapter block that YouTube accepts and that lands on the cards, a phone copy for review, and a
receipt that proves the uploaded file is the file that passed QA.

Scripts (all in `scripts/deliver/`, Python 3.8+ stdlib, ffmpeg/ffprobe >= 6 on PATH):

| Script | Does |
|---|---|
| `fix_srt.py` | Fixes brand and name spellings in the exported SRT from a JSON name map. Text lines only. |
| `chapters.py` | Builds the `Chapters` block from card and sub-chapter positions (master or v-next timeline). |
| `deliver.py` | Versions, checks, phone copy, captions, chapters, `superseded/`, sha256 receipt. |

Upstream pieces this builds on (not copied here):
[Samin12/edit-borumi-video](https://github.com/Samin12/edit-borumi-video) (default install `~/.borumi-edit/skill`,
override with `$BORUMI_EDIT_SKILL`): `finalize_export.py` (Borumi export + post-process), `auphonic_master.py`,
`youtube_chapters.py` (reads card positions live), `render_cards.py` / `apply_card.py` (the cards themselves).
The audio rebuild lives in `scripts/audio/` (see `references/audio.md`), the v-next cut in `scripts/tighten/`
(see `references/tighten.md`).

---

## 1. What ships

Everything goes into one deliverables folder next to the project, by convention `<Project> exports/`:

| File | What it is |
|---|---|
| `<Name> - final edit vN.mp4` | The upload. 1920x1080, 60 fps H.264, AAC 320k 48 kHz **stereo**, -16 LUFS integrated, true peak <= -1 dBFS. |
| `<Name> - final edit vN (720p phone).mp4` | For the creator to review on a phone. Same audio stream. Never upload it. |
| `<Name> - final edit vN.srt` | Captions with fixed spellings. Upload as the video's subtitles. |
| `youtube-chapters.txt` | The `Chapters` block for the description, for the current version only. |
| `superseded/` | Every older version, moved, never deleted. Older chapter files are `youtube-chapters (vK).txt`. |
| `_work/export/delivered_vN.sha256` | `<sha256>  <file name>` per delivered file. |
| `_work/export/delivered_vN.json` | Paths, sizes, hashes, probe, loudness, caption fixes, chapters, moves, every check. |

Version numbers only go up and are shared by every kind of revision: an audio-only remaster of v1 is v2, the
shorter v-next cut made from v2 is v3. A number is consumed only by a successful delivery (a failed run is retried
under the same N).

## 2. Order of operations

```
1. Export + audio  edit-borumi-video finalize_export.py --out-dir WORK/export/baseline --version N --no-auphonic
                     --keep-raw --no-phone --no-chapters (raw export + post-processed baseline + SRT, kept out of the
                     deliverables folder), then the per-source rebuild on the RAW export (scripts/audio/remix.py) and
                     the full-mix Auphonic pass (master_mix.py)   (references/audio.md; SKILL.md step 7)
2. Captions        fix_srt.py --names caption-names.json on the exported SRT (Borumi export_transcript {format: "srt"})
3. Chapters        card positions read from the project NOW -> chapters.py --strict
4. QA              the layered QA passes on the candidate file  (before deliver; deliver is the technical gate)
5. Deliver         deliver.py on the full-mix output -> vN (it can also deliver in place on a vN that
                     finalize_export wrote into the deliverables folder)
6. v-next          cut from the finished master (references/tighten.md), captions remapped, chapters.py --edl,
                   deliver.py again (vN+1)
```

Typical commands:

```bash
D=path/to/skills/proof-first-video-editor/scripts/deliver
OUT="$HOME/Videos/My Video exports"

# captions: fix spellings (in place keeps a pristine .orig; --dry-run first to read the changes)
python3 $D/fix_srt.py --names receipts/caption-names.json "export.srt" --dry-run
# chapters: from a config (see examples/chapters.example.json), strict = fail on any YouTube rule
python3 $D/chapters.py --config receipts/chapters.json --strict --out receipts/youtube-chapters.txt
# deliver: the mastered video + the Auphonic WAV muxed as stereo, captions fixed on the way in
python3 $D/deliver.py "mastered.mp4" --out-dir "$OUT" --name "My Video" --audio auphonic.wav \
    --srt export.srt --names receipts/caption-names.json --chapters receipts/youtube-chapters.txt --expect-fps 60
# later, before uploading: prove nothing changed since QA
python3 $D/deliver.py --verify "$OUT/_work/export/delivered_v1.sha256"
```

## 3. Captions (`fix_srt.py`)

Borumi's transcript (and whisper) mishears product and people names. The SRT is exported from the project
(`export_transcript {format: "srt"}`), then fixed from a JSON name map (`scripts/deliver/examples/names.example.json`):

- `{"to": "Ghidra", "from": ["Gidra", "Hydra"]}`: literal, whole word, case-sensitive unless `"ignore_case": true`.
- `{"pattern": "^Cloud right now\\b", "replace": "Claude right now"}`: a regex per caption line (`^` = line start).
- `"review": ["\\bcloud\\b"]`: ambiguous words, reported for a human, never replaced.

What it guarantees: only caption TEXT lines change (lines after a timing line, up to the blank line), cue numbers and
timings are untouched, CRLF and a BOM survive, every change is printed, an in-place run keeps `IN.srt.orig` and never
overwrites an existing `.orig`. Several maps can be chained (`--names brands.json --names this-video.json`).

Lessons:

- **The creator's own name is the most common miss** (a common lookalike first name). Put it first in every map,
  scoped to the self-introduction ("I'm ...") when the lookalike is also a real name.
- **Never blanket-replace an ambiguous word.** A creator who says "Claude" fifty times still says "cloud" for real
  sometimes. Anchor the rule with context ("cloud code", "your claw", a line-start "Cloud right now") and list the bare
  word under `review`.
- **Transcripts glue and stutter.** "ChatGPTKenCloud" (= "ChatGPT and Claude"), "recreate Recreate": one rule each.
- **Order matters.** Rules run top to bottom on the output of the previous ones: a misheard full name before the
  rule for the first name alone, or the first-name rule eats half of the full-name match.
- **Keep one map per video** in the project's receipts folder and promote recurring rules to a shared brand map.
  The real per-video maps for two published videos were 14 and 22 rules.
- The proof that the rules did not over-reach is a read of the printed diff, not the count (on one video 14 rules
  changed 16 lines). Run `--dry-run` first and read every line.
- After a **v-next cut**, do not run the master's SRT through `fix_srt.py` and ship it: the cues must first be mapped
  through the cut list (cues fully inside a removed range dropped; partly cut cues lose exactly the words whose
  midpoint fell in a removed range, aligned to the word timings; end punctuation of a removed sentence end moves to the
  last kept word; a kept word that now starts a sentence is capitalised; cue end <= next cue start - 1 ms; minimum cue
  300 ms). `deliver.py` fails a delivery whose last cue ends after the video.

## 4. Chapters (`chapters.py`)

House format (exactly what goes into the description):

```
Chapters
0:00 Intro
1:45 Why It Matters
3:49 What Is Reverse Engineering
4:43 How REA Works
6:27 Rebuilding My Rabbit
8:37 Level 1: What REA Can't Do
12:19 Level 2: Clone Any App
15:22 Level 3: Sell Your Version
15:57 Why Open Source Wins
```

`M:SS Title` (H:MM:SS past an hour), no dashes, no emoji. `0:00 Intro`, then `Why It Matters`, then the levels as
`Level N: <section name>`, plain Title Case sub-chapters between them (small words lowercase: a, the, to, on, ...).
About 8-9 chapters for a 20-25 minute video; about one per 5 minutes for long courses.

The rounding rules (the reason the script exists):

- **Card chapters round UP to the next whole second.** YouTube seeks to exactly the second written. A card that starts
  at 1:44.963 written as `1:44` plays the last word of the previous sentence first; `1:45` lands on the card. A card is
  1.6 s long, so the rounded-up second is always on it; the script checks it anyway (`[start, start + card_ms)`).
- **The card's first frame is 1 ms after its scene start** (cards are 1601 ms on the timeline: the 1.6 s render plus a
  1 ms lead-in), so the math uses `start + 1 ms` (`card_lead_ms`).
- **Sub-chapters round DOWN** from the sentence start, so the click lands just before the first word, never mid-word.
  With word timings (`--words` / `--words-dir`) the start snaps to the first kept word at or after (start - 50 ms).
- **A card in the first second replaces `0:00 Intro`** (the video opens on a card).
- **Card text and chapter text differ on purpose**: the card says "Why This Matters", the description says
  "Why It Matters"; level cards never say "Level N" on screen, the description adds it (`chapter_titles`, `levels`).

YouTube's rules, all checked (`--strict` exits 1): first chapter at 0:00, at least 3 chapters, every chapter at least
10 s long including the last one. **Breaking any of them makes YouTube silently drop ALL chapters**, so two chapters in
the same second, or a sub-chapter 8 s after a card, is a delivery failure, not a style issue.

Where the times come from:

- Read the card positions from the project **right before export** (scene starts for card scenes, the 1601 ms
  fullscreen layout for mid-scene cards). `chapters.py --from-borumi WORK` does that through edit-borumi-video's
  `youtube_chapters.py` (read-only calls) and then applies the rounding above. Times written by `apply_card.py`
  receipts are **stale** after any later cut or insert.
- Do not paste `youtube_chapters.py`'s own output into the description: it **floors** the times (lands up to 1 s
  before the card). It is the right tool for finding the cards and for the receipt check (every card identified from a
  receipt, `--strict`), not for the final numbers.
- For a **v-next cut**, give the master positions plus the cut's `edl.json` (`--edl`, written by
  `scripts/tighten/build_cut.py`: `keep_frames` + `fps`) and the master word timings. The same config then produces
  both blocks; on the reference video (20:02 master, 14:02 cut, 56 removals) it reproduced both published blocks exactly.
- `examples/chapters.example.json` is that reference video's config; `chapters.py --config` on it prints the block above.

## 5. The final files (`deliver.py`)

What it checks, and why each check exists:

| Check | Why |
|---|---|
| 1920x1080, fps (`--expect-fps 60`), duration (`--expect-duration`, +-0.25 s) | A wrong preset or a range export ships silently otherwise. |
| audio present, 48 kHz, **stereo** | Borumi exports mono AAC (1 ch). The delivered masters are dual-mono stereo muxed from the Auphonic WAV (`--audio`), which costs one AAC generation instead of two. `--stereo` upmixes an existing mono track; `--allow-mono` opts out. |
| audio/video lengths within 0.1 s | A muxed WAV of the wrong length (or a truncated encode) shows up here first. |
| -16 LUFS +-1, true peak <= -1.0 dBFS | The Auphonic target. A file that missed it was not the mastered file. |
| SRT has cues, ends inside the video | Catches the master's SRT shipped with a shorter cut. |
| chapters: 0:00 first, >= 3, >= 10 s each (incl. the last) | YouTube's rules, re-checked on the exact file that ships. |
| phone copy 1280x720, same duration | The review copy must be the same cut. |

Then, only if everything passed: older `<Name> - final edit vK*` files move to `superseded/`, the previous
`youtube-chapters.txt` becomes `superseded/youtube-chapters (vK).txt` (K = the version whose receipt hashes match it),
and the sha256 receipt is written. On a failure nothing moves, the vN files stay for inspection and the JSON receipt
says `"ok": false`; the next run retries vN (files of the failed vN are first moved to `superseded/replaced-vN-<time>/`).

`deliver.py` also delivers in place: point it at a `<Name> - final edit vN.mp4` that `finalize_export.py` already
wrote into the deliverables folder and it adds the checks, phone copy, chapters and receipt under that same N.

Phone copy settings (unchanged from production): `scale=1280:720, libx264 -crf 23 -preset medium, yuv420p,
-c:a copy, +faststart`. About a quarter of the master's size.

## 6. Lessons (failure modes and fixes)

1. **Borumi's export audio lags the picture, and not by a constant.** The fixed part is 2112 samples (44 ms of AAC
   priming); the microphone adds a voice-enhancement latency of about 0.5-7.5 ms that differs per clip and scene
   (44-51 ms total on one 20-minute video). `finalize_export.py`'s post-process (`atrim=start_sample=2112,
   asetpts=PTS-STARTPTS, adeclick=t=10`) fixes the constant part and the single-sample clicks at cut joins; the
   audio rebuild (`scripts/audio/remix.py`) places every mastered clip at its exact timeline position and removes
   the variable part. Feed the rebuild the UNPROCESSED export.
2. **The full final always goes through Auphonic** and Auphonic's audio is the main track. Verify the result, do not
   trust it: output length == input length, lag 0 samples in every 20 s window across the video (GCC-PHAT),
   integrated -16 LUFS +-0.6 and true peak <= -0.9 dBFS on the WAV. ffmpeg's mono-to-stereo mux (`-ac 2`) kept the
   integrated loudness within 0.3 LU of the mono WAV; measure it, then mux once.
3. **The Auphonic API key is never in the repo, a receipt or a log.** Code reads `$AUPHONIC_API_KEY`, then the macOS
   Keychain service `auphonic-api-key`
   (`security add-generic-password -U -a "$USER" -s auphonic-api-key -w '<key>'`).
4. **Never export while a write is in progress.** `finalize_export.py` waits while `WORK/borumi.lock` is held. The
   creator's own edits in the Borumi app do not take the lock: re-read the project right before exporting and before
   reading card positions.
5. **Export the whole project, never a range** (`export_video` preset `balanced`, no range): about 7 minutes for a
   22-minute video. Delete the raw export only after every check passed (it is ~650 MB per 20 minutes).
6. **An audio-only remaster keeps the timeline**, so it reuses the SRT and the chapters (`--reuse-previous-srt`; the
   existing `youtube-chapters.txt` stays). A cut that changes the timeline needs both remapped (section 3, 4).
7. **A 30/60 fps frame that lands in a card's 1 ms lead-in renders the bare canvas.** Cover it with a 10 ms still of the
   card's first frame (`scripts/borumi/card_lead_in.py`; `chapter_card.py` prints a NOTE when a card needs it), and
   check the frames around every card in the delivered file, not in the editor.
8. **The phone copy is for review only.** The creator watches it on the phone; uploads use the full-size final.
9. **sha256 receipts close the loop.** Run `deliver.py --verify` (or `cd OUT && shasum -a 256 -c
   _work/export/delivered_vN.sha256`) right before uploading: a file touched after QA (re-saved, re-encoded,
   accidentally trimmed) fails here, not on YouTube.
10. **A whisper pass on the delivered file is a caption check too.** A word match ratio around 0.97 against the
    expected words is normal (whisper writes "going to" for "gonna", "read me" for "README"); the differences that
    matter are names (add them to the map) and anything clustered at a cut join (a QA issue, not a caption issue).
11. **Receipts hold absolute local paths.** Keep `_work/` out of anything public; it is for the editor and the creator.

## 7. The description

Paste `youtube-chapters.txt` as is (the `Chapters` header included) into the description. If the creator has published
videos before, read their latest descriptions first (for example with the `ytstudio` CLI:
`ytstudio videos list -n 20 -o json`, which returns `{"videos": [...]}`) and match the block they actually use. The
block must be regenerated for every version whose timeline changed; YouTube re-reads chapters when the description is
saved.
