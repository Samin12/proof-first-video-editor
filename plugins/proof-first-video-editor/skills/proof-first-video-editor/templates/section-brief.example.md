<!--
EXAMPLE section briefs: the `brief` string of one task in workflows/design-visuals.js (args.tasks[].brief). The video,
product and numbers below are FICTIONAL; they only show the shape and the level of detail that worked. A good brief:
  - names the section and its slot (project time range) in the first line;
  - walks the narration in order, one bullet per beat: project SECONDS + the creator's words in quotes + what to show;
  - names the proof file for every claim (paths relative to the proof pack) and the honest line where the creator overstates;
  - says what NOT to show (do_not_show, privacy, overstatements);
  - for FRONT jobs, says what the footage shows at each moment so cards land in empty areas;
  - for REVISIONS, lists numbered fixes with frame ranges and says "keep all timing".
Write times from the FINAL word files (after every cut). Delete this comment when you copy it.
-->

## Example 1: a BEHIND job (talking to camera, full visuals)

```text
HOW IT WORKS, part 1 (2:05.4 to 3:41.2). Concepts he explains with no screen on: build each as a clean diagram, and put
every claim on a real capture.
- 125.4 'Think of it like a recipe and the cake': a friendly two-card illustration, 'The recipe' (source code) and 'The
  cake' (the app). Keep it simple and warm; part 2 (task key HOW2) reuses these exact two cards.
- 131.9 'Developers write the recipe, the source code': the recipe card lights, label 'Source code'.
- 136.2 'then a compiler bakes it into the app you download': arrow recipe -> 'Compiler' -> cake; label 'The app'.
- 142.8 'Open source means the recipe is public': the OSI definition capture (02-concepts/01_osi_definition_crop.png),
  browser card opensource.org, marker on 'free redistribution' at 'public'.
- 151.0 'and the license tells you what you can do with it': the license page capture (02-concepts/03_mit_text_crop.png),
  markers on 'use, copy, modify' on 'what you can do'.
- 163.5 'Tally has over forty thousand stars': the repo header (01-the-repo/01_repo_header_crop.png), ring on the star count.
  The capture shows 41.2k at capture time; show '41K+' in the pill, never a live-looking counter.
- 171.0 'and you can resell anything you build with it': HONEST NOTE. The license covers Tally itself, not what you build
  with it, and its disclaimer puts authorization on you (proof-index entry repo-03, status ✗). Show the disclaimer capture
  (01-the-repo/09_disclaimer_crop.png) with the marker on 'You are responsible for obtaining any required authorization'.
  Never print 'resell' as a claim.
- 180.2 to 221.2: the three-step 'How Tally runs' diagram, Input -> Parse -> Report, each node lighting on its word
  ('reads' 183.9, 'parses' 190.4, 'writes a report' 201.7). Keep holds alive: a small sample line flows through the
  pipeline while he talks (no new claims).
Do not show: the maintainer's sponsor page or any personal names from the contributors list.
```

## Example 2: a FRONT job (the screen recording is the main visual)

```text
SET IT UP (6:12.0 to 9:03.9), FRONT overlay over his screen recording. The recording carries this section: small cards and
rings only, inside the left panel, never over what he is pointing at. Check the footage at each time first (inspect_timeline
frames or frames extracted from the bundle media).
- 372.4 'grab this link': footage shows the GitHub page full width; ring on the real URL bar at 'link' (pixel position from a
  frame at 372.4). Step card 'STEP 1 · Copy the link' in the empty strip at the bottom left, about 3 s.
- 381.9 'paste this into Claude Code': footage switches to the terminal (prompt line at the top, empty lower half); a
  'STEP 2' card in the lower half; underline the real pasted command at 'paste'.
- 402.6 to 431.0 'it's reading the code now': the terminal scrolls; no cards over the scrolling text. A small 'Reading the
  code' chip in the top-right corner of the panel only.
- 455.3 'and here's the report': ring on the real report window's title at 'report'. The report window covers the right
  two thirds: nothing may sit on it while it is on screen.
- 501.8 'this is my community': show the real community post in the footage as is; never a price, checkout or join page.
Do not show: the second window behind the report (it shows his calendar with third-party names); keep cards off it and ask
the editor for a blurred cut if it is readable for more than a few frames.
```

## Example 3: a REVISION (after the independent check)

```text
REVISION of v1 (anim-xxxxxxxx-v1.mp4; keep all timing; 2306 frames is correct: ceil sizing, the editor trims the tail).
Fixes, then render v2:
1. f1150-f1222: the 'Why it matters' card shows two empty halves for ~2.4 s. Bring 'Faster' and 'Cheaper' in WITH the card.
2. f2010-f2140: the marker on 'what you can do' runs onto the next word. Stop it right after 'do'.
3. f262-f330: the B-roll uses objectFit cover plus a 1.03 zoom, which cuts the left edge of the recording's UI text. Use a
   card with the clip's own 16:9 aspect and no zoom.
4. Every scene change (f100, f170, f263) dips to near-black for 1-2 frames. Make them clean crossfades with no dip.
5. CONSISTENCY: the recipe/cake cards must match part 1 exactly (src/jobs/anim-yyyyyyyy/Scene.tsx): import or copy that
   component.
Keep everything else as it is.
```
