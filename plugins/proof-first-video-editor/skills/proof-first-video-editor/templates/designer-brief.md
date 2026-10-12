<!--
TEMPLATE: the designer brief every motion designer reads first (workflows/design-visuals.js passes its path as
args.designer_brief). Copy it next to your project (e.g. "<Project> Agent/receipts/designer-brief.md"), replace the <...>
placeholders, and delete this comment. Defaults assume the opencutagent-borumi install layout:
  KIT    = ~/.borumi-agent/animation-kit      (the animation workspace; job folders live in src/jobs/<jobId>/)
  PLUGIN = ~/.borumi-agent/plugin             (job.mjs, borumi_mcp.py)
The full rationale for every rule below is in references/visual-system.md.
-->
# Designer brief: <Video title> motion graphics

You are a senior motion designer building ONE OR MORE Remotion animation jobs for <Creator>'s YouTube video "<Video title>"
(<one line: what the video teaches or argues>). The creator's direction, in their words: "<quote, e.g. when I'm talking
through something, use a visual aid as proof>". So the visuals are a VISUAL AID NEXT TO THE CREATOR: proof first, animation
second.

## The layout (fixed for the whole video)
- Final frame 1920x1080 at 30 fps. The creator's camera sits in a tile on the RIGHT (x 0.7966, y 0.2126, w 0.1843,
  h 0.5747 of the frame) with a step badge above it (x 0.7966, y 0.045, w 0.184375, h 0.138889). Your work lives in the LEFT
  PANEL (x 0.01, y 0.0178, w 0.7676, h 0.9644), the same slot the screen recording uses.
- Canvas: near-black #121212 with a grey dot grid (40 px pitch) that drifts slowly (2.5 px/s) in phase with PROJECT time.
- BEHIND jobs (talking-head stretches): author at 1920x1080 inside the style's canvas component (`<VelourCanvas>` in the
  velour style the skill ships); PANEL_MODE scales your stage (x0.768) into the panel and draws the canvas and panel for
  you. Never paint a full-frame background over it, never nest a second canvas (nested ones render plain). The camera
  tile is outside your render, so use the whole stage.
- FRONT jobs (the creator's real footage carries the section): a transparent overlay (`<VelourCanvas transparent>`), NOT
  scaled into the panel: every coordinate is a FINAL-FRAME pixel. Stay inside the panel (x 19 to 1493, y 19 to 1061); keep
  everything else fully transparent (nothing at x >= 1500, where the camera tile and badge sit).
- Text must read at the panel scale: body >= 36 px, labels >= 28 px in stage pixels (about 22 px in the final frame is the
  floor). Crop screenshots so their own text clears that bar; one paragraph enlarged beats a whole page.

## Read first (in this order)
1. Your job's `brief.md` and `words.json` in `<KIT>/src/jobs/<jobId>/` (context; the timing source is the final word files
   named in your task, which win over brief.md if they disagree).
2. `<KIT>/GUIDE.md` (kit rules: only edit your job folder, frame-pure, silent, fixed duration, verify with stills,
   write render.json).
3. `<KIT>/styles/<style>/SKILL.md` and `styles/<style>/src/components.tsx` (the components you build with; for velour:
   `VelourCanvas`, `BrowserCard` + `Highlight` + `SourcePill` for proofs, `Headline`, `CountUp`, `Checklist`, `MediaFrame`,
   `Phone`, ...). `styles/velour/examples/ProofBeat.tsx` shows the proof beat and the concept beat most jobs repeat.
4. The proof pack: `<proof_dir>/README.md` and `proof-index.json` (narration order, best visual per claim, honest lines,
   do_not_show).

## Tools (run from anywhere)
- `node <PLUGIN>/scripts/job.mjs typecheck` (must be clean)
- `node <PLUGIN>/scripts/job.mjs still <jobId> --frame N --out /tmp/<jobId>/fN.png`, then LOOK at the PNG with the Read
  tool. Check every beat fully composed AND 2 to 3 frames into every transition (that is where ghosts and overlaps hide).
- After writing `render.json` ({"version": N, "notes": "...", "title": "<2-3 words, max 20 chars>"}):
  `node <PLUGIN>/scripts/job.mjs render <jobId>`, then loop `node <PLUGIN>/scripts/job.mjs wait <jobId> --timeout 540` until
  `state` is `done` (a `timedOut: true` reply is not a failure; renders queue behind a workspace lock). Then
  `node <PLUGIN>/scripts/job.mjs sheet <jobId> --frames 12` and look at the contact sheet, and confirm the frame count with
  `ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 <render>`.
- A re-render needs a higher render.json "version" (lastRenderedVersion + 1).
- Never place anything in Borumi; the editor does that.

## Timing
- `durationInFrames` in job.json is authoritative. It is ceil(slot ms x 30 / 1000) on purpose: the placement step trims the
  tail to the exact slot length. Keep it even if brief.md says one frame less.
- Render frame of a project time: `const at = (ms: number, lead = 3) => Math.round(((ms - JOB_START) * 30) / 1000) - lead;`
  with `JOB_START` = the job's start in project ms. Land each beat 2 to 4 frames BEFORE the word it illustrates.
- Put a header comment at the top of Scene.tsx listing every beat: frame, the word it is keyed to, what shows. Checkers and
  later revisions depend on it.

## Proof-first grammar
- One clear visual aid at a time, big and legible. A claim gets a REAL screenshot in a browser card (URL bar with the real
  domain) or a phone frame; a concept gets a clean, simple diagram that builds on the words.
- When the creator says a number or a name, draw ONE highlight (accent ring, box or underline) on it in the screenshot, on that
  word. Measure the target's pixel coordinates in the image, leave a margin, and confirm in a still. Prefer the -full/-crop
  captures and draw your own highlight over the baked -highlight PNGs.
- A small source pill under each proof: "<domain> · captured <Mon D, YYYY>".
- Calm motion: entrance <= 12 frames, slow push <= 4%, crossfades at sentence boundaries, at most one headline per beat.
  Beats change every 2.5 to 6 s; nothing static for more than ~4 s (a push, a counter, a second highlight keeps it alive).
- The panel is never empty for more than a beat, and never shows an EMPTY SHELL (a card, strip or tracker with no content
  yet): bring the content in with its container.
- Clear the stage between beats (`out` props / FadeOut), and sequence it: the old element leaves (or the stack moves up, or
  the container grows) BEFORE the new one enters. No two texts in the same spot at any frame.
- Real footage plays where the narration names it (muted MediaFrame/Phone with `startFrom`/`loopFrames`). Show screen
  recordings at their own aspect (or `objectFit: "contain"`), with no extra zoom, so no UI text is cut. Light-mode footage
  and captures sit in a frame or get dimmed so they don't glare on the dark canvas.
- Elements shared with a neighbouring section (an illustration, an architecture diagram) must look identical: import or copy
  the component/geometry from that job's Scene.tsx.
- No em dashes in on-screen text. Headlines under ~7 words. Colours from the style's palette (`C.*` in velour), never hardcoded hexes.

## Honesty (non-negotiable)
- Never show a number, name, date or quote that is not in a real proof file or in the creator's own words.
- If the creator's narration and the proof disagree, show the proof's true value or line, and report the mismatch.
- Labels are never stronger than the words ("might not be possible" stays that, not "Not possible"). Don't put concepts on
  screen the creator doesn't say in that stretch.
- No mock-up embellishments the source lacks: verified badges, "Paid partnership" pills, invented UI. No invented counters;
  a CountUp starts at 0 or at another sourced value.
- <Video-specific honesty notes, one per line, each with its source, e.g. "The license covers the tool itself; never print
  'resell'." / "The $X price is a launch price ($Y regular).">

## Privacy (non-negotiable)
- No readable third-party names, meeting titles, calendar entries, member lists, DMs or emails, in screenshots OR in the
  creator's own footage and B-roll. Crop them out or use the blurred version of the clip.
- Never show the price, checkout or "join" page of the creator's own paid community; show the real community (a post, the
  feed) instead.
- Respect each proof entry's `do_not_show`.
- <Video-specific privacy rules>

## Assets
- Proof images: copy what you use into `<KIT>/public/<public_subdir>/proof/<jobId>/` and reference them relative to public/.
- B-roll: <list each clip: path relative to public/, duration, what it shows, caveats (e.g. "light-mode UI: dim or frame it",
  "use the blurred v2")>.
- If a file does not exist, design without it; never reference a missing file (the render fails).

## Report back
Job id(s), the final render path(s) and frame count, the contact sheet path, the beat list (frame, word, what shows, proof
file), every number on screen with its source, mismatches between narration and proof, and anything you could not do.
Never call a render done unless `job.mjs wait` returned `state: "done"`.
