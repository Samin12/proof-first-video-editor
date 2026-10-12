# Creator style guide: the proof-first look

These are the style decisions behind the reference edits (Samin Yasar's long-form AI tutorials, 2026): a talking head
plus a screen recording, turned into a tight video where every claim is backed by something real on screen. They are
the defaults of this skill. Another creator changes them here and in the per-video config, not in the scripts.

Each rule says what to do and why. Most of them came from the creator watching a draft and correcting it.

---

## 1. Layout: visual aid next to the creator

All fractions are of the 1920x1080 frame.

| Element | Rect (x, y, w, h) | Notes |
|---|---|---|
| Left panel | 0.01, 0.0178, 0.7676, 0.9644 | The slot the screen recording uses. Visuals for talking-head stretches go INSIDE it. |
| Camera tile | 0.7966, 0.2126, 0.1843, 0.5747 | The creator, right side, same place in every section. |
| Step badge | 0.7966, 0.045, 0.184375, 0.138889 | Above the camera tile: which step/level the viewer is in. |

- Visuals are rendered at 1920x1080 and scaled into the panel (the velour style's `PANEL_MODE`; the style ships in
  `templates/velour-style/` and is installed into the opencutagent-borumi animation kit), placed with
  `BORUMI_CAMERA_RECT` set to the camera tile.
- **Not** the full-frame render with the camera pinned bottom-right (the opencutagent-borumi default).
- Why: the camera must stay where it is during the screen recording, so the video never jumps between two layouts, and
  the visuals read as a visual aid next to the person explaining, not as a separate animated video.

## 2. Canvas: dark, quiet, alive

- Background `#121212` with a grey dot grid, widely spaced (40 px pitch at 1080p), drifting very slowly (~2.5 px/s),
  in phase with PROJECT time so it is continuous across cuts and across separately rendered pieces.
- The creator first tried a white dot grid and asked for the dark one mid-edit; dark is the default since.
- Light-mode screenshots and footage sit in a frame or are dimmed so they do not glare on the dark canvas.
- Borumi's canvas background accepts only solid, gradient or image. The still grid is the canvas image; the drift
  comes from takes: BEHIND renders paint their own drifting grid, and camera-only stretches without a render get a
  drifting clip (`scripts/borumi/canvas.py clip`, placed like a BEHIND render). A drifting clip as the full-frame bottom
  source UNDER a screen recording was used once but is not automated or re-verified. Implementation and caveats:
  `references/borumi-writer.md` §4.8 and `references/visual-system.md` §2.

## 3. Structure: hook, why, roadmap, levels

1. **Hook** (no card on the hook).
2. **Why It Matters**: the first chapter card right after the intro. Every claim in it gets a proof (section 6).
3. **A 3-level roadmap up front**: "Step 1, Step 2, Step 3" (or Level 1-3) shown before the first level starts, the way
   other tutorial channels do it, so the viewer knows the path and how far along they are.
4. **Levels**: one chapter card per level; the step badge above the camera tile updates at each card and stays on
   screen, so a viewer who skips in always knows where they are.
5. A closing section with its own card.

## 4. Chapter cards and every other chapter element: 2 seconds max

- **White card**: white grid background, black Inter title, typed in 3 reveal groups (frames 6/13/19), 1.6 s render,
  1601 ms on the timeline (1 ms lead-in). Rendered by edit-borumi-video's `render_cards.py`.
- **A quiet click** as a separate sound effect under the card (never baked into the render), no whoosh.
- **Card titles**: 2-5 words, Title Case, they name the section. Never "Level N" on the card (the description adds it).
  The first card after the intro is "Why This Matters".
- **2 seconds max for every chapter element**: card, in-panel step slate, pause. The creator trimmed a 2.8 s pause
  himself and asked for "like two seconds max". The 1.6 s cards fit.
- Check the frames around every card in the delivered file: a frame landing in the card's 1 ms lead-in shows the bare
  canvas (fix in `references/delivery.md`, lesson 7).

## 5. YouTube chapters

```
Chapters
0:00 Intro
1:45 Why It Matters
6:27 <Sub-chapter in Title Case>
8:37 Level 1: <Section Name>
12:19 Level 2: <Section Name>
15:22 Level 3: <Section Name>
```

- `Chapters` header, `M:SS Title` lines, no dashes, no emoji. Intro, Why It Matters, then `Level N: <Title>` with plain
  Title Case sub-chapters between (small words lowercase: to, on, a).
- About 8-9 chapters for a 20-25 minute video; about one per 5 minutes for a long course.
- Card times are rounded UP so a click lands on the card, sub-chapters DOWN so it lands before the first word.
  `scripts/deliver/chapters.py` does the math and the YouTube checks (`references/delivery.md`, section 4).

## 6. Proof over animation

- **When the creator makes a claim, show the real thing while he says it**: a screenshot of the live page, post,
  article, report or contract, with THE number or name highlighted (one ring, box or underline, on the word).
- His own screen recording is the best proof there is (it is genuine and already in the project): when he says a
  payout figure, show the real contract row from his recording, highlighted, instead of an animated counter.
- Script brackets like `[show X]` mark what MUST appear on screen at that line.
- **Never show a number the real source does not support.** A small source pill under each proof ("domain · captured
  <date>").
- Animation is for concepts that have no screenshot (how a pipeline flows, what a tool does). Not everything,
  everywhere: one clear visual aid at a time, big enough to read at the panel's scale.

## 7. Flags go to the creator; claims are never silently changed

The editor's job is to make what the creator said land, not to rewrite it. Anything that might be wrong goes into the
delivery report with a timestamp, what was found, the source, and a suggested fix (re-record a line, cut a sentence,
add an on-screen correction). The creator decides.

- **Accuracy**: a figure the source does not support, an outdated price or limit, an overstatement, a product name
  that is wrong. Do not quietly show a different number, and do not show a proof that contradicts the line without
  telling him.
- **Legal**: third-party footage or music (it can draw a Content ID claim that restricts monetization or regions;
  list every third-party clip), statements about other companies, anything that reads as legal advice (fair use,
  reverse engineering, terms of service).
- **Privacy**: anything private visible in a screen recording or screenshot: email addresses, other people's names or
  faces, private messages, calendar entries, account ids, API keys, order numbers, home address. Mechanical fixes
  that do not change meaning (blur, crop, freeze the left panel on a nearby clean frame for the few frames where it
  flashes up) can be applied and are still listed in the report. Anything that changes what is shown or said is his call.

## 8. Never show the creator's paid-community price

When the video shows the creator's paid community (a membership group, a course platform), show the community itself:
the inside, the posts, the classroom, the members' wins. Never its landing or about page with the price. Search every
visual AND every screen-recording frame where the community appears, including quick tab switches. The creator asked
for exactly this: show the actual community, not the price.

## 9. Sound

- The full final always goes through Auphonic (the creator's preset; -16 LUFS integrated, true peak <= -1 dBFS) and
  that audio is the main track.
- Card clicks are quiet, one per card; no whoosh (remove the layout's transition sound on card layouts).
- No audible slivers at cuts: word-level cuts snap to real silence (a cut padded by a fixed 40 ms leaves fragments of
  the neighbouring word).

## 10. The cut

- **Tight**: restarts, repeated takes and dead air go; keep the clearest take of each line.
- **v-next**: when the creator wants it shorter and punchier, cut the finished master directly (frame-exact, the
  mastered audio crossfaded at each join), not by re-editing the project: `references/tighten.md`. Same captions and
  chapters rules, remapped through the cut.

## 11. Packaging (context)

Thumbnails composite the creator's real face (a real expression frame found in the camera footage, not an AI-edited
face) and real logos; image generation is for backgrounds and props only. The YouTube packaging itself is outside this
skill.

## 12. What the creator gets at the end

1. The phone copy (to watch on the phone), with the full-size final next to it for upload.
2. The `Chapters` block, ready to paste.
3. The flags (section 7), each with a timestamp and a suggested fix.
4. A short list of what changed since the last version he saw.
