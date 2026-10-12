# Borumi timeline writer: safe operations, quirks, recipes

This is the operating manual for `scripts/borumi/`: the scripts that WRITE to a Borumi timeline (canvas, base layouts,
chapter cards, step badges, gap fills, proof / motion-graphic renders) and the read-only checks around them (project
snapshots, coverage, word timings). Every rule here was learned on live projects (Borumi, October 2026): the first
production run of this workflow (the "REA" video, about 20 minutes, 13 placed renders, 6 cards, 7 badges, 33 verified
commits, zero post-commit surprises) and the video before it. When Borumi behaves differently from what is written
here, trust the tool, verify in an aborted transaction, and update this file.

It builds on [Samin12/edit-borumi-video](https://github.com/Samin12/edit-borumi-video) (bridge, `bm`, `applylib.py`,
`references/borumi-mcp.md`) and complements it. Where the two disagree, this file is newer: see §3 (untrim on layouts,
create-scenes-before for cards).

---

## 0. The toolchain

| Script | Writes? | What it does |
|---|---|---|
| `bwlib.py` | - | shared helpers: config / context, geometry, layout builders, 30/60 fps grid, frame rendering, exact-length copies |
| `snapshot.py` | no | project-wide snapshot + strict diff (library and CLI: `save`, `diff`, `gaps`, `peek`, `wait-stable`) |
| `txrun.py` | (runner) | lock + committed-hash + tx + checks + dry/commit + collision check + post-commit re-diff; CLI `status`, `log` |
| `imgcheck.py` | no | numpy/Pillow frame analyses (PSNR vs render, tile box, composite, panel/tile measurement, coverage) |
| `mp4_set_duration.py` | no | makes an MP4/MOV last exactly N ms (last-sample trim) |
| `canvas.py` | `set` | dot-grid PNG, drifting clips phase-locked to project time, set the canvas background |
| `base_layouts.py` | yes | the standard look on scenes / ranges without layouts (camera tile; screen in the left panel) |
| `chapter_card.py` | yes | one chapter card + click + fullscreen layout, at a scene boundary or mid-scene |
| `card_lead_in.py` | yes | covers a card's 1 ms lead-in when a 30/60 fps frame lands in it |
| `gap_fill.py` | yes | fills layout gaps (default-layout flashes) with the neighbouring layout's props |
| `make_badges.py` | no | renders the step-badge PNGs |
| `make_card.py` | no | renders a silent white 1.6 s chapter card (+ rows.json row) and a quiet click WAV, no private template |
| `plans_from_kept.py` | no | edit-borumi-video kept-text files -> `plans.json` for `scene_words.py` (removed words stripped) |
| `step_badges.py` | yes | adds the badge overlays between the cards |
| `place_render.py` | yes | BEHIND / FRONT render placement with the insert-quirk preflight |
| `swap_render.py` | yes | same-length replacement of a placed BEHIND render |
| `coverage.py` | no | whole-video frame audit (cards, renders, footage, tile, badge, gaps) |
| `scene_words.py` + `align_ctc.py` | no | word / sentence timings per scene (whisper + plan text + CTC alignment + RMS snapping) |

Cutting (ripple trims of retakes and dead air) is done with edit-borumi-video's `apply_cuts.py`; the cut lessons from
the production run are in §4.6 because they shape everything the writer does afterwards.

## 1. Setup

**Dependencies.** Python 3.9+ (stdlib for the writers), `ffmpeg`/`ffprobe` >= 6, numpy + Pillow for the image checks
(`uv` is used automatically when the interpreter lacks them), `whisper-cli` (whisper.cpp) + a ggml model and `uv` for
`scene_words.py` (the CTC step runs `torch==2.5.1` / `torchaudio==2.5.1` on Python 3.12 via `uv`).
edit-borumi-video at `~/.borumi-edit/skill` (or `--edit-skill DIR` / `$BORUMI_EDIT_SKILL`) with its bridge running:
`bash ~/.borumi-edit/skill/scripts/bridge/bridgectl.sh start` (port `$BORUMI_BRIDGE_PORT`, default 47811).
The chapter click comes from a private chapter-transitions repo through `applylib.click_wav()`; override it
with the config key `click_wav` or `--click`. Without access, `make_card.py --make-click WORK/cards/click-quiet.wav` makes
one from Borumi's built-in click at a -18 dBFS peak (the private quiet click's level), and `make_card.py --title ...`
renders the white 1.6 s cards in the same layout as edit-borumi-video's `render_cards.py`.

**Project + WORK.** Every script takes `--work WORK` (or `$BORUMI_WORK`, or `--project NAME`). WORK follows the
edit-borumi-video convention `"<bundle folder>/<name> exports/_work"`; `WORK/project.json` (written by
`find_project.py --open`) gives the project id and the bundle's `medias` folder. `--project-id` overrides.

**Config** (optional, JSON; `examples/writer.example.json`): first of `--config`, `$PFVE_WRITER_CONFIG`,
`./pfve-writer.json`, `WORK/pfve-writer.json`, `~/.config/proof-first-video-editor/writer.json`. Keys: `camera_rect`,
`panel_rect`, `badge_props` (the standard look by default), `camera_layer`, `card_ms` (1601), `canvas_png`,
`click_wav`, `medias_dir`, `out_dir`, `strict_footage_media`, `tile_bottom_min_frac`. Placeholder strings that start
with `<` and keys that start with `_` are ignored. No secrets ever go into it.

**Outputs** go to `<out>` = `WORK/writer` by default: `place/` (exact copies, render frames), `frames/<step>/` (every
checked frame + montage JPGs to look at), `snaps/after_<step>.json`, `coverage/`, `words/`, `commits.log` (one JSON
line per commit: step, commit id, info, post-commit diff problems). `coverage.py` reads `commits.log` to know which
takes are renders.

**Exit codes** (every writer): `0` committed or dry ok, `2` a check failed (aborted, nothing changed), `3` error
(aborted), `4` collision (the project changed during the tx; aborted: re-read and redo). Always run `--dry` first and
read the check table and the montage.

## 2. The method: why every write looks the same

1. **One logical change per transaction**, run first with `--dry` (stage + verify + ABORT). A batch of renders is placed
   one slot per tx.
2. **Lock.** `WORK/borumi.lock` (mkdir lock, shared with edit-borumi-video; released on every exit path).
3. **Committed hash.** The runner reads the committed `timeline_hash` before opening the tx and again right before
   committing. The user edits in the Borumi app while agents write, and **app commits do not take the lock**: a
   changed hash aborts with exit 4. Never undo the user's edits; re-read and redo. `snapshot.py wait-stable
   --seconds 180` waits until the user has stopped editing.
4. **Project-wide snapshot + strict diff.** Ripple trims, layout deletes / untrims and content deletes can make Borumi
   rewrite layouts ELSEWHERE in the project (merges, 1 ms shifts, screen_1/screen_2 swaps). So every write snapshots
   the WHOLE project (scenes, layouts, overlays, content, controls) inside the tx and asserts that everything not
   explicitly expected equals its predicted (shifted) value. New ids must be explicitly allowed.
5. **Frames are the truth.** Stored layout names lie (Borumi re-derives them) and rendering resolves them differently
   again (§4.3). Every write renders frames inside the tx (`inspect_timeline` with the tx id previews staged edits)
   and compares them with the render's own frames / the pre-edit frames / the canvas.
6. **30 vs 60 fps.** `inspect_timeline` renders only the 30 fps grid, but exports run at 60 fps. A gap or sliver is
   harmless only if neither a 30 nor a 60 fps frame lands in it (`bwlib.grid_hits`, +-0.5 ms because Borumi rounds frame
   times to integer ms).
7. **Post-commit re-diff.** After the commit the committed project is snapshotted and diffed again; problems are logged
   to `commits.log` (the user may have edited in between) but never auto-reverted.

## 3. Safe vs unsafe operations

Verified in aborted transactions with project-wide diffs. "Safe" means: no change outside what was asked, every time it
was tried. Even safe operations are always verified.

| Operation | Verdict | Notes |
|---|---|---|
| `add_segments` layout over an empty range | SAFE | no global rewrites. Stored `screen_N` names are re-derived from the takes active at the layout START. Add layouts AFTER `move_scenes` (one added before it stretched to the project end). |
| `update_segments` layout props | SAFE | MERGE semantics (omitted fields kept); property-only, the hash stays valid. Refuses a layer name that is not an active visual layer in the layout's range. `transition_sound: null` removes a whoosh. |
| `split_segments` (content group, overlay, layout) | SAFE | pieces >= 3 ms; a 1 ms piece is refused (`split_too_close_to_boundary`). A layout's right piece may get its screen name re-derived (render-identical). |
| targeted `trim_timeline` `ripple:false` on an overlay or ONE take | SAFE | how swaps remove a take and how overlay tails are cut. Ranges < 10 ms are refused (`invalid_trim_range`): for a < 10 ms tail, trim `[T-10, end]` then `untrim_segments right_ms 10` (overlays only). |
| `untrim_segments` on an OVERLAY | SAFE | |
| `move_scenes` | SAFE | exact shifts. Layouts whose scene is the PREVIOUS scene stay behind. |
| `create_scenes destination:end` + take at E+1 | SAFE | the scene collapses to the take length (1601 ms for a card). Build cards here, then move them. |
| integral ripple trim strictly INSIDE a content segment | SAFE | exact shift of everything after it |
| `add_segments` take mid-scene on an empty screen slot | SAFE | stacks on a screen layer, no ripple, as long as it does not start where another segment starts (see below) |
| `move_timeline_segments at_time` (1 ms or more) | SAFE with care | worked on overlays and a card take. A block whose first segment starts at a FRACTIONAL time moves fractionally (+-1 ms display noise): split at an integral time first. It does NOT carry sound effects. |
| `add_segments` take exactly at a scene start / where a same-layer segment starts | QUIRK | can INSERT and ripple by its length instead of stacking, depending on sub-ms alignment; START+1 then a move can insert too. Preflight every placement in an aborted tx (`place_render.py` does). |
| ripple `trim_timeline` across many layers / at boundaries | QUIRK | see §4.6: repacking of stacked screen takes, double-shrunk layouts at scene ends, fractional ends |
| `delete_segments` on a LAYOUT | UNSAFE in general | can trigger a project-wide layout rebuild (merges, a 1 ms layout dropped and its neighbours shifted, screen_2 renamed screen_1). Used only where a project-wide diff proves the result (re-placing re-anchored card layouts after a mid-scene block move; a scene's layouts in a cut rebuild). |
| `untrim_segments` on a LAYOUT | UNSAFE | triggered the global layout rebuild (merges, a dropped 1 ms layout, renamed screens). The older "untrim the right piece that ends 1601 ms early" card fix corrupted layouts elsewhere. Re-add a layout over the range instead. |
| `delete_segments` on CONTENT | UNSAFE in general | deletes or truncates the layout attached to the take, merges identical adjacent layouts project-wide, can repack screen layers; deleting a scene's only content pads the scene to 3000 ms. Use a targeted non-ripple trim to remove a take. Verified exception: deleting a split-off piece of a recording with `expand_groups:false, ripple:false` to clear a hole under stacked footage (§4.6), checked by the diff. |
| `delete_scenes` | NEVER | needs `delete_recordings:true`, which destroys recordings (other scenes may use the same media) |
| a full ripple trim of a whole scene | UNSAFE | the empty scene is padded back to 3000 ms and the next scene's layout got mangled; see §4.7 |
| `create_scenes before_scene` + fill (old card flow) | UNSAFE | the 3000 ms padded scene collapses and shifts LATER card scenes by +1 ms; use build-at-end + `move_scenes` |
| `update_canvas` background image | SAFE | timeline untouched; the canvas accepts only solid / gradient / image |
| slip (change a video segment's media in-point in place) | UNKNOWN | the `video` update schema has no media / timing fields; trim+untrim combinations were never tested |

## 4. Quirks and their workarounds

### 4.1 Time, frames, rounding
- **Rendering one frame.** `inspect_timeline` returns the first 30 fps grid frame STRICTLY after `start_ms`. To render
  grid time g ask for `[ceil(g) - 1, ceil(g)]`, `max_frames 1` (`bwlib.frame`). 60 fps-only times cannot be rendered.
  `sampling.max_frames` is capped at 8 (older docs say 255).
- **Windowed reads.** `get_timeline` / `get_transcript` refuse big ranges (`range_too_large` + `suggested_end_ms`): read
  in windows of <= 180 s (applylib does). A summary read of `0-1000` still returns the GLOBAL hash and duration.
- **Half-millisecond positions.** An app edit at a sub-ms time leaves everything after it at x.5 ms. Later exact shifts
  then DISPLAY as +-1 ms (all layers of a group show the same offset). Checks tolerate +-1 ms only when every layer of a
  group moved together (`snapshot.take_moved`, `chapter_card.py`); unrelated +-1 ms flips of layouts / overlays are
  accepted explicitly (`snapshot.tol_exp`) and listed in the log.
- **A scene that starts at x.5.** A take added at x lands in the PREVIOUS scene and ripples. Start the slot at x + 1
  with a 1 ms shorter copy (a 1 ms sliver with no 30/60 fps frame is invisible), and start the layout piece after the
  take (place_render splits it 3 ms in) or its screen names collapse onto the footage.
- **Float artifacts.** One cut point moved +1600 instead of +1601 under a +1601 shift; the next +1601 restored it.
  Diffs must expect exact values but report, not "fix", single 1 ms oddities.
- **Gaps that hold no frame are fine.** 1-2 ms layout or screen gaps (from rounding) are invisible unless a 30 or 60 fps
  frame lands inside: `snapshot.py gaps` lists only those by default.

### 4.2 Takes (screen content you add)
- **Insert-vs-stack.** A take added exactly at a scene start or where a same-layer segment starts may INSERT (ripple the
  timeline by its length) instead of stacking. `place_render.py` preflights strategies in ABORTED txs: `exact` (at
  START), `plus1-move` (START+1 then `move_timeline_segments` to START), `plus1-stay` (START+1, only when neither 1 ms
  sliver holds a 30/60 fps frame), and keeps the first that leaves every other segment, the scenes and the duration in
  place. In the production run every slot whose start was a whole millisecond went in `exact`.
- **Exact length.** Borumi takes the take length from the file's duration. A take longer than its slot stretched the
  scene in a probe. Copies are cut to `ceil(L*30/1000)` frames and their last sample shortened so they last exactly L
  ms (`mp4_set_duration.py`; needs `-bf 0`, `-use_editlist 0`, `-video_track_timescale 30000`, moov after mdat).
  `import_media` of a video can overhang by a few ms otherwise.
- **Import status.** `import_media` can come back `failed` WITHOUT `isError`; poll `get_media_import_status` and check the
  media id (applylib does).
- **Removing a take.** Targeted `trim_timeline` over its exact range with `ripple:false`, not `delete_segments`.

### 4.3 Layouts and screen names
- **Names are re-derived at the layout START.** A layout's `screen_N` references are resolved from the screen takes
  active at the layout segment's START. A layout that starts before a new take gets its reference renamed; asking for
  `screen_2` where only one screen take is active stores `screen_1`. Split layouts exactly at the take edges: inside the
  piece the recording stays `screen_1`, the added take `screen_2`.
- **Rendering resolves names to the TOP active screen take.** A full-frame `screen_1` layout still shows an animation
  that sits on `screen_2`. Check a rendered frame before "fixing" names. When the render does not show, place_render
  switches the slot's pieces to the other screen name and re-checks (the fallback can be refused: "screen_2 is not an
  active visual Layer in the Layout range").
- **A layout that starts EXACTLY where a stacked take ends** resolves `screen_1` to the ENDING take and renders an EMPTY
  panel for its whole range. Start it 1 ms later (no 30/60 fps frame in the 1 ms gap; re-check after ripple edits), or
  it needs the misleading name `screen_2`.
- **Default layout flash.** Wherever no layout covers a frame Borumi falls back to its default (a fullscreen camera).
  Short gaps after cuts are typical (0.4-1.8 s in the production run). `gap_fill.py` adds layouts with the neighbour's
  props; an intentionally empty panel source (no `layer_id`) must be dropped from copied props because `add_segments`
  needs every source to name an active layer.
- **Identical neighbours merge.** Ripples merge adjacent layouts with byte-identical props; an added layout may merge
  into an identical neighbour. Visual no-ops; the strict diff accepts them explicitly.
- **Panel source geometry.** `lock_aspect_ratio:true` letterboxes; `false` cover-crops (never stretches). Device / phone
  footage looked right as cover; screen recordings as letterbox, with the panel rect matched to the recording's aspect
  (so it fills the panel; the frame checks expect the panel box filled).
- **The step badge sits right above the tile.** Measuring the tile box from a canvas diff must ignore the rows of the
  badge, or the badge merges into the tile box (the geometry payload's `tile_y_lo`).
- **Sources draw in order**: later sources in front. The camera tile goes LAST.

### 4.4 Overlays, alpha and z-order
- `media_overlay` is picture only (no audio), draws ABOVE every layout source (so above the camera tile), gets a
  default drop shadow (send a zero-alpha shadow to disable it), and Borumi REPACKS overlay layers on every overlay
  change, so the z-order between two overlays (a render vs a badge) cannot be pinned. Overlay ranges cannot exceed the
  media length.
- **Render over footage but under the camera tile and the badge** (FRONT): add the alpha render (ProRes 4444) as a
  SCREEN take (screen sources respect alpha) and make the slot's layout `[footage panel, render full frame, camera
  tile]`. Badges are media overlays, so they stay above everything. Name the footage panel by the footage's ACTUAL
  layer: adding the take makes Borumi re-derive the old layout's names.
- **Stills over 1 ms slivers** (`card_lead_in.py`): a full-frame PNG media overlay with instant in/out and zero shadow.
- **Badges** are PNG media overlays with fade-in / instant-out, from a card's end to the next card's start (an end up to
  2 ms before the next card is fine when that remainder holds no frame).

### 4.5 Two stacked screen takes
- Any operation that makes pieces of BOTH stacked screen takes START at the same instant (a joint ripple trim, even
  `split_segments`) repacks them in arbitrary order (the overlay footage on `screen_1`, the recording on `screen_2`).
- Splitting a stacked background clip at the same instant as a narration cut repacks `screen_1` / `screen_2` too.
- A ripple trim that targets only the narration layers does NOT shift independent `screen_2` pieces inside the same
  scene; the scene end follows the max content end, so a spanning `screen_2` clip must be shortened at its TAIL first
  (non-ripple trim, no split).

### 4.6 Ripple trims and cuts (lessons that shape the later writes)
- Apply a scene's cuts LATEST -> EARLIEST, and scenes from the last to the first: earlier coordinates stay valid.
- Trim ALL content layers present in the scene (camera, mic, system sound, the screen recording AND any synced overlay
  footage on screen_2 / audio_1), or the edit sets drift apart.
- When a scene has synced overlay footage stacked on the recording, avoid joint trims that split both screen takes at one
  instant. Three working fixes: trim the overlay's edit set and the narration set separately per cut (overlay first);
  or cut a HOLE in the recording under the overlay span first (split it at the span edges, `delete_segments
  expand_groups:false ripple:false`) so no cut ever splits two screen layers; or delete the scene's layouts, remove the
  recording under the overlay (split 1 ms past the overlay's end), trim, then re-add the layouts.
- A ripple trim that ends EXACTLY at a scene end shrinks a layout spanning that boundary TWICE (-3268 ms for a 1634 ms
  tail): split the layout at the boundary first. The next scene's first pieces then show +-1 ms media rounding.
- A narration-set ripple over-shortened some layout segments once: rebuild the layout track at the expected positions
  inside the same transaction, verified by the layout map.
- Ripple trims ending at a FRACTIONAL time remove N + fraction ms: ~100 later segments then show -N / -N-1.
- Word-level cuts padded by 40 ms leave audible word slivers: cut on the full word bounds.
- Verify a scene cut by rebuilding its audio from the committed mic pieces and transcribing it (whisper similarity to
  the plan text >= 0.90), by the camera/mic/system pieces staying in lock-step, and by frames at the joins (the panel
  must show the source frame at the expected media time).

### 4.7 Scenes and cards
- **Cards at a scene boundary: build at the END, then move.** `create_scenes destination end` -> take at E+1 (the scene
  becomes exactly 1601 ms) -> `move_scenes before_scene` (exact +1601 from T). Add the CLICK after the move (added at
  the project end it belongs to the last scene, stays behind on `move_scenes` and falls off) and the fullscreen layout
  after the move too.
- **Cards mid-scene** at an integral T in a sentence gap: split content / overlays / layouts at T, move the scene's
  right block to T+1601 with `move_timeline_segments`, then move the sound effects that did not ride along (+1601),
  re-place later card layouts that came back re-anchored +1 ms (delete + add the same props at the exact range), check
  the gap is empty on EVERY layer, then add the take at T+1 and the click at T.
- Layouts with a < 10 ms piece on either side of T cannot be split: the card layout absorbs that sliver.
- **The 1 ms lead-in.** A card's take starts at T+1, so the frame at a 30/60 fps time inside `[T, T+1)` shows the bare
  canvas (a one-frame dark flash; luma 19 instead of 255). `card_lead_in.py` covers `[T, T+10]` with the card's own
  frame 0.
- **Removing a standalone scene** without destroying media: ripple-trim `[A+R, B]` on its layers and leave an R ms
  remainder (2 ms) that holds NO 30/60 fps frame now AND after the later card inserts shift it (+k * 1601). (A short
  dark remainder kept on an earlier video to hold a fractional scene end WAS rendered over a card for one frame:
  check every remainder with frames.) edit-borumi-video's `remove_scene.py` is the alternative (80 ms + a still).
- Insert cards from the LATEST to the EARLIEST. Unnamed scenes are renumbered by position after an insert ("Scene 7"
  becomes "Scene 8"): compare scenes by id.

### 4.8 Canvas and the moving background
- The canvas background accepts only solid, gradient or image. The still dot grid (#121212, grey dots, 40 px pitch at
  1080p) is the canvas image; `canvas.py png` reproduces the production PNG pixel for pixel.
- The DRIFT (2.5 px/s diagonally, one pitch every 16 s) is phase-locked to PROJECT time: `offset = (t_ms/1000 * 2.5) %
  40`. Renders that paint their own canvas use the same formula with their project start, so cuts between renders and
  drifting clips never jump. A moving background needs takes: drifting clips (`canvas.py clip`) placed as BEHIND
  renders on camera-only stretches, or (on an earlier video) clips on a screen layer referenced as the full-frame
  BOTTOM source of the layouts under screen recordings. The latter is not automated here; verify it in an aborted tx.

### 4.9 Concurrency and the app
- The user edits in the Borumi app while agents write; those commits bypass the lock. Re-read before every write, rely
  on the hash, never undo the user's edits; the runner's collision check exits 4.
- Borumi can quit mid-session (`app_not_running`): `open -a Borumi`, wait ~12 s, `list_open_projects`, re-open, restart
  the bridge if needed (`bmlib.ensure_app`).
- Exports read the COMMITTED state only; `inspect_timeline` with a tx id previews staged edits.
- Not this toolchain, but related: Borumi's exported audio sits 44-51 ms behind the picture and the lag varies by
  scene; the audio pipeline places the mastered voice at exact timeline positions instead of shifting by a constant.

## 5. Recipes, in order of use

Set once (bash):
```bash
SKW="<skill dir>/scripts/borumi"
export BORUMI_WORK="<bundle folder>/<name> exports/_work"      # has project.json (edit-borumi-video find_project.py --open)
python3 "$SKW/txrun.py" status                                  # lock owner, committed hash, duration
python3 "$SKW/snapshot.py" save "$BORUMI_WORK/writer/snaps/baseline.json"
```
Every writer: run with `--dry`, read the check table and the montage in `WORK/writer/frames/`, then run without
`--dry`. Before a batch while the user is active: `python3 "$SKW/snapshot.py" wait-stable --seconds 180`.

**1. Canvas** (first; the frame checks of later steps compare against it)
```bash
python3 "$SKW/canvas.py" png "$BORUMI_WORK/writer/canvas/dot-grid-dark-4k.png"
python3 "$SKW/canvas.py" set --dry && python3 "$SKW/canvas.py" set
```

**2. Base layouts** (before cuts; ripples carry layouts along)
```bash
python3 "$SKW/base_layouts.py" --scene <talk scene>:cam --scene <demo scene>:screen:screen_1 --dry
```
Camera-only stretches get the tile and an EMPTY panel (renders go there later); recordings sit in the panel.
It needs ranges WITHOUT layouts: a fresh recording has none (the fullscreen camera is Borumi's implicit default). If the
check `no layouts yet` fails, never delete or untrim the existing layouts: drop ranges an earlier base_layouts commit
already covered (`writer/commits.log`), and restyle anyone else's layouts with `update_segments` props (SAFE, §3) in a
hand-verified tx, or ask (SKILL.md step 4b).
Synced overlay footage (a device close-up over a desk recording) is a separate, hand-verified tx: add the footage take
at the synced position (screen_2 + audio_1), mute its audio (`volume_filter -60`), one layout per piece, and start the
layout after the footage 1 ms after its end (§4.3).

**3. Cuts** with edit-borumi-video `apply_cuts.py NN --work "$BORUMI_WORK" --no-layout-fix` (`--dry` first), last scene
first; mind §4.5-4.6. `--no-layout-fix` is required: without it apply_cuts repairs a mislanded layout with
`untrim_segments` on the layout (UNSAFE, §3); with it the check only reports, and `gap_fill.py` (recipe 5) fills the
flashes. Skip edit-borumi-video's `layout_audit.py` untrim fixes. Re-check `snapshot.py gaps` after each scene.

**4. Chapter cards**, latest first (render them with edit-borumi-video `render_cards.py`, or without its private template
with `python3 "$SKW/make_card.py" --title "<Title>" --name NN-<slug>`; 2-5 word titles):
```bash
python3 "$SKW/chapter_card.py" --name 06-<slug> --before <scene id> --dry
python3 "$SKW/chapter_card.py" --name 05-<slug> --mid <integral ms in a sentence gap> --dry
python3 "$SKW/card_lead_in.py" <T> --name 02-<slug> --dry     # when chapter_card printed a lead-in NOTE
```
Pick T for a mid card from `scene_words.py` sentences: the gap after a full stop, no word spanning T.

**5. Gap fill**
```bash
python3 "$SKW/snapshot.py" gaps            # layout gaps holding a 30/60 fps frame
python3 "$SKW/gap_fill.py" --dry           # all of them, or --gap A:B
```

**6. Step badges** (after every card is in)
```bash
python3 "$SKW/make_badges.py" --titles "<intro title>" "Why This Matters" ... --eyebrows "INTRO" "WHY IT MATTERS" ... \
    --out-dir "<name> Agent/chapters"
C="<name> Agent/chapters"
python3 "$SKW/step_badges.py" --auto "$C/badge-0-intro.png" "$C/badge-1-why-this-matters.png" "$C/badge-2-<slug>.png" ... --dry
```
`--auto` derives one range per chapter from the current cards; the PNG count must be cards + 1 (intro first). List the
PNGs explicitly in chapter order: a shell glob (`badge-*.png`) sorts `badge-10` before `badge-2` and silently assigns
the wrong badge to every chapter after the ninth (`ls -v` sorts them naturally if you must generate the list).

**7. Word timings** (for render timing, captions, card gaps)
```bash
python3 "$SKW/plans_from_kept.py"                     # WORK/cuts/kept_scene*.final.txt -> WORK/writer/words/plans.json
python3 "$SKW/scene_words.py" --plans "$BORUMI_WORK/writer/words/plans.json" --names words-names.json --prompt "Names, jargon."
```
`words-names.json` is the flat `{"whisper spelling": "Canonical"}` map (`examples/names.example.json`), not the caption
map of `scripts/deliver/fix_srt.py`. `--plan-dir` only helps with plan files that carry `{scene_id, text}`;
edit-borumi-video's `plan_scene*.json` have no text.

**8. Renders.** Plan slots on the final timeline: integer ms, one scene per slot (card edges and scene starts make
natural slot edges), the render at least as long as the slot, 1920x1080 at 30 fps.
- BEHIND (talking head; the render paints its own panel + drifting canvas):
  `python3 "$SKW/place_render.py" slots.behind.json --dry`
- FRONT (alpha ProRes 4444 over footage already in the panel): `python3 "$SKW/place_render.py" slots.front.json --dry`
A slot whose scene starts at a half ms: start the slot 1 ms later (§4.1).

**9. Swaps** (a new version of a placed BEHIND render, same length)
```bash
python3 "$SKW/swap_render.py" --at <slot start> --render v3.mp4 --same-before <ms where it changes> --changed <ms> --dry
```
For a FRONT render: remove the old take with a targeted non-ripple trim in its own verified tx, then place again.

**10. Coverage** (read-only; re-run after every fix)
```bash
python3 "$SKW/coverage.py"        # WORK/writer/coverage/report.json + problems.jpg; exit 1 when anything is flagged
```
Re-render the frames around any flag that sits exactly on a join before acting (§7.6).

Then export, audio and captions with the rest of the skill: `references/audio.md` (per-source mastering, remix,
full-mix pass) and `references/delivery.md` (captions, chapters, versioned delivery). SKILL.md has the end-to-end order.

## 6. Check thresholds (and why)

| Check | Threshold | Why |
|---|---|---|
| BEHIND frame = the render's own frame | PSNR >= 30 dB left of the camera column (x < 1500) | the tile + badge differ by design; production values 38-45 dB (35 at worst) |
| ... and specific | >= 2 dB better than the render's frame 15 away, or >= 38 dB, or a static hold (>= floor and >= the far frame) | a near-static render (a held proof screenshot) looks the same 15 frames away |
| FRONT frame = pre frame composited with the render's RGBA frame | PSNR >= 30 dB outside tile + badge | production 57-99 dB; a wrong layer order reads ~2 dB |
| camera tile box (behind) | left / top / right +-8 px; bottom >= ~650 px and <= tile bottom + 8 | a dark shirt on a dark render reads short at the bottom |
| tile + badge unchanged (front) | mean abs diff < 1.5 | they must draw ABOVE the render |
| frames outside a slot | mean abs diff < 1.0 (the tile excluded) | the face-tracking crop restarts at a layout boundary, so the tile itself may differ slightly |
| badge present / absent | badge box diff > 8 / < 0.5 | |
| card frames | mean luma > 200 | white card; the lead-in flash reads ~19 |
| after-card frames | 96x54 thumbnail diff < 8, or the panel = the expected source frame (PSNR >= 22 recordings, >= 26 camera-like footage) | a moving source defeats a pixel diff across the 30 fps grid offset; Borumi draws the cursor separately |
| source frame identity | the expected frame scores within 0.5 dB of the best candidate | proves sync, not just similarity |
| coverage panel fill | >= 50 % of the panel differs from the bare canvas | an empty panel is the most common visual bug |
| gaps | no 30/60 fps frame inside | 1-2 ms rounding gaps are invisible |
| words / cuts | whisper similarity of the rebuilt audio vs the plan >= 0.90 | |
| saturated test-bar dummies | `--psnr 22` | only for synthetic test renders |

## 7. Failure modes seen in production (and what fixed them)

1. **Half-ms scene start under a FRONT slot.** The slot started at a scene start of x.5; `exact` and `plus1-move`
   preflights were not clean, `plus1-stay` was, but the dry run showed the next card's fullscreen layout renamed
   `screen_1 -> screen_2` and every composite at ~2 dB. Fix: slot start x+1 with a 1 ms shorter copy -> `exact` clean,
   composites 57-99 dB.
2. **Static render failed "specific".** A render holding a still for seconds matched its frame 15 away equally well.
   Fix: the static-hold rule (§6).
3. **Layout-map false alarms** from +-1 ms rounding at card edges. Fix: accept rounding flips explicitly
   (`snapshot.tol_exp`) and ignore frame-free slivers in the map comparison.
4. **Layer-name fallback refused** ("screen_2 is not an active visual Layer in the Layout range"): update_segments
   validates names against the active layers. The first name was right; the frames had failed for another reason.
5. **One-frame canvas flash** at a card's 1 ms lead-in (frame at T + 0.33 ms): `card_lead_in.py`.
6. **Coverage flags exactly on joins** (a frame sampled at the instant one render ends and the next starts, or a few ms
   after a card) can compare against the wrong neighbour's reference. Re-render the frames one grid step either side
   and look before changing anything.
7. **Default-layout flashes** (0.4-1.8 s) after cuts: `gap_fill.py`.
8. **Stacked screen takes repacked** by a joint ripple trim in a scene with synced device footage: separate edit sets /
   a hole in the recording / a layout rebuild (§4.6).
9. **A layout shrunk twice** by a ripple trim ending at a scene end: split the layout at the boundary first.
10. **Scene removal**: `delete_scenes` would destroy shared media and a full trim padded the scene to 3000 ms and mangled
    the next layout: a 2 ms frame-free remainder instead.
11. **A click fell off the project end** when added before `move_scenes`: add it after the move.
12. **Mid-scene card block move** left a later click behind and re-anchored later card layouts +1 ms: moved / re-placed
    in the same tx.
13. **An alpha render as a media overlay** covered the camera tile, got a drop shadow and could not be kept under the
    badge: screen take + layout sandwich instead.
14. **An empty panel** after a stacked take ended exactly where the next layout started: start the layout 1 ms later.
15. **Layout corruption elsewhere** from the old untrim-based card fix (previous video): never untrim layouts.

## 8. Not verified yet (test in an aborted tx first)

- Slipping a video segment's media in-point in place.
- A moving background under screen recordings via `[clip full frame, footage panel, camera tile]` (used once on an
  earlier video, not re-verified with these tools).
- Swapping a FRONT (alpha) render in place.
- Frame rates other than 30 fps renders / 30+60 fps grids, and canvases other than 1920x1080 landscape (every check
  assumes them).
