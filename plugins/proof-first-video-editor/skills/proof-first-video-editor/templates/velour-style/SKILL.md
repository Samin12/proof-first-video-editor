<!-- guide-version: 1 -->
# Velour style: the proof-first dark panel look

Shipped by the proof-first-video-editor skill (MIT). Install it once into the animation workspace as
`<kit>/styles/velour/` (the skill's SKILL.md step 0). The full rules and their reasons are in the skill's
`references/visual-system.md`; this file is the designer's short version.

## The look

- Every BEHIND scene is wrapped in ONE `<VelourCanvas>`. It paints the near-black `#121212` canvas with a grey dot grid
  (40 px pitch) that drifts 2.5 px/s in phase with PROJECT time, draws the rounded left panel at
  `PANEL = { x 19.2, y 19.2, w 1473.75, h 1041.6, r 19.2 }` and scales your 1920x1080 stage uniformly by
  `PANEL_SCALE = 0.7676` into it. The creator's camera tile and the step badge sit on the right, OUTSIDE your render.
- Author at 1920x1080 as usual. Because of the scaling, body text >= 36 px and labels >= 28 px in stage pixels
  (~28 / ~22 px in the final frame). Crop screenshots so their own text clears that bar too.
- FRONT jobs (transparent ProRes overlays over the creator's real footage): `<VelourCanvas transparent>`. Nothing is
  scaled; every coordinate is a FINAL-frame pixel. Stay inside the panel (x 19-1493, y 19-1061) and leave x >= 1500
  fully transparent (camera tile and badge).
- `panel={false}` draws the canvas full frame without the panel (only for pieces with no camera beside them).
- Never nest a second `VelourCanvas` (a nested one renders plain), never paint a full-frame background over it, and
  never edit `src/phase.ts`: the editor registers your job's start there (`JOB_START_MS`) so the dots continue across
  the cut into your render.
- Palette `C.*` (semantic, dark): `ink` light primary text, `muted` secondary, `faint` tertiary, `paper` a raised card,
  `dark` a second card tone, `accent` terracotta (ONE subject at a time), `green` / `red` for right / wrong, `gold`
  sparingly. Never hardcode hexes. Font Inter. No em dashes in on-screen text.

## Components (import from `../../../styles/velour/src`)

| Component | Use |
|---|---|
| `VelourCanvas` | the canvas + panel wrapper (props `transparent`, `panel`, `glow`, `startMs`) |
| `BrowserCard` | a REAL screenshot in a browser frame with the real domain in the URL bar; `children` draw over the image area |
| `Highlight` | ONE ring / box / underline / marker on the spoken target, drawn on 2-4 frames before the word |
| `SourcePill` | `"<domain> · captured <Mon D, YYYY>"` under every proof |
| `MediaFrame`, `MediaFill`, `Phone` | screen recordings, B-roll, vertical clips (muted; `startFrom`, `loopFrames`); slow push <= 4% |
| `Headline`, `Kicker`, `Pill`, `CodeChip` | type: a word-by-word headline, a label, a chip, a command |
| `CountUp` | a number the creator says, the biggest thing in the frame; starts at 0 or another SOURCED value |
| `NumberCard`, `Checklist`, `Stamp`, `ArrowTo` | steps, lists that land on their words, one stamped word, a pointer |
| `Reveal`, `Pop`, `FadeOut` | entrances (<= 12 frames) and exits; clear a beat before the next one enters |
| `C`, `prog`, `mix`, `geoZoom`, `EASE_OUT`, `EASE_IN_OUT`, `compact`, `money` | palette and motion helpers |

Every prop is documented in `src/components.tsx`.

## Non-negotiables

1. **Proof over pictures.** A claim gets the real page, recording or document, not an illustration of it. A concept
   gets a clean, simple diagram built on the words.
2. **One visual aid at a time, the proof is the biggest thing.** One highlight per spoken target.
3. **Sync to the words.** Land each beat 2-4 frames before its word (`const at = (ms, lead = 3) => ...` from the job's
   timing note). Beats every 2.5-6 s; nothing static over ~4 s; never an empty panel or an empty shell.
4. **Sequence every change.** The old element leaves before the new one enters; no two texts in one spot at any frame;
   crossfades never dip to black.
5. **Honesty.** Every number, name and quote on screen is in a proof file or in the creator's words for this stretch;
   no label stronger than his words; no invented counters, badges or UI.
6. **Privacy.** No readable third-party names, inboxes, calendars, member lists or DMs; never the price, checkout or
   join page of the creator's own paid community.

## Learnings log (append here; newest wins)

- (empty: the designer appends rules the creator teaches)
