export const meta = {
  name: 'design-visuals',
  description: 'Design proof-first motion graphics for every section of a Borumi video (one designer per animation job), check each render independently and revise until it passes',
  whenToUse: 'After the cut is final and the proof pack exists: visualize every talking stretch (BEHIND jobs) and add callouts over real footage (FRONT jobs), in the left panel next to the camera tile.',
  phases: [
    { title: 'Prepare', detail: 'optional: create missing jobs one by one, ceil-size them, register their drift phase in one edit' },
    { title: 'Design', detail: 'one motion designer per job: Scene.tsx, stills at every beat, render' },
    { title: 'Check', detail: 'an independent checker per render: frames, sync, proof, overlaps, cut-offs, privacy' },
    { title: 'Revise', detail: 'numbered fixes from the checker, re-render, re-check (bounded rounds)' },
    { title: 'Continuity', detail: 'one pass across all renders for cross-section consistency' },
  ],
}

/*
design-visuals.js: the visual design workflow of the proof-first-video-editor skill.

Run with the Workflow tool: Workflow({ scriptPath: "<skill>/workflows/design-visuals.js", args: {...} }).
Everything video-specific comes from args (see templates/design-visuals.args.example.json):

  title            REQUIRED short video title used in prompts
  proof_dir        REQUIRED absolute path of the proof pack (README.md + proof-index.json; see workflows/research-proof.js)
  tasks            REQUIRED [{ key, scene, start, end, mode: "behind"|"front", brief, job?, depends_on?, revision?, words?, frames? }]
                   key        short unique id ("I1", "C2", "S1"); start/end = the slot in PROJECT ms on the final timeline
                   scene      Borumi scene id (selects the word file); job = an existing animation job id (else args.prepare)
                   brief      the section brief (templates/section-brief.example.md)
                   depends_on key of an EARLIER task whose illustration/diagram this one must match; it waits for that design
                   revision   { of_version: N } when the job already has render vN and the brief lists fixes only
                   frames     defaults to ceil((end - start) * 30 / 1000), the ceil sizing the placement step expects
  creator, topic   used in prompts
  direction        the creator's own words about the look (quoted to designers)
  designer_brief   path of the filled designer brief (templates/designer-brief.md)
  project_id       Borumi project id for READ-ONLY calls (get_transcript / get_timeline / inspect_timeline)
  project_path     the .bmprojbundle path (needed by args.prepare)
  kit_dir          animation workspace (default "~/.borumi-agent/animation-kit"). job.mjs does NOT read this arg: it finds the kit at
                   $BORUMI_AGENT_HOME/animation-kit. So kit_dir must end in "/animation-kit"; when it is given, every job.mjs
                   command in the prompts runs with BORUMI_AGENT_HOME=<its parent folder>, so prompts and job.mjs use ONE kit.
  plugin_dir       opencutagent-borumi plugin (default "~/.borumi-agent/plugin"; job.mjs and borumi_mcp.py live in scripts/)
  style            animation style id (default "velour": templates/velour-style, installed as <kit>/styles/velour). Any style
                   works if it honours the canvas contract (PANEL_MODE panel + scaling, drifting dot grid, phase.ts JOB_START_MS,
                   transparent FRONT renders); prove it with scripts/style/velour_check.py --style <id> --canvas <component>
  canvas_component the style's canvas component named in prompts (default "VelourCanvas" for velour)
  palette_ref      how prompts name the style's palette tokens (default "C.*" for velour)
  public_subdir    folder under <kit>/public for this video's assets (default: a slug of the title)
  words            { "<scene id>": "<abs path>" } FINAL word files, [start_ms, end_ms, word] in project ms
  sentences        { "<scene id>": "<abs path>" } optional sentence files, [start_ms, end_ms, text]
  reference_jobs   [ "anim-xxxx: what to learn from it" ] scenes to study for patterns
  broll            [ { src, desc } ] clips under <kit>/public (src relative to public/), with caveats
  honesty_notes    [strings] facts the visuals must respect (from the proof pack's "fix before publishing")
  privacy_rules    [strings] extra privacy rules
  accent           default "#E2774E"
  prepare          true to create jobs for tasks without `job` (one agent, sequential job.mjs create, ceil sizing,
                   one phase.ts edit), or { words_dir } to choose where job-shaped word files go
  max_rounds       revise/re-check rounds per job (default 2)
  check_effort     checker effort (default "medium")
  continuity       run the cross-section pass (default true)

Returns { results, placements: { behind: {mode, slots}, front: {mode, slots} }, needs_attention, continuity }.
placements use the SLOTS shape {name, render, start, end} expected by the placement script (references/borumi-writer.md).
Read references/visual-system.md for the layout, BEHIND vs FRONT, timing, and every failure mode the checkers caught.
*/

const A = args || {}
const need = (ok, msg) => { if (!ok) throw new Error(`design-visuals: ${msg}`) }
need(typeof A.title === 'string' && A.title, 'args.title is required')
need(typeof A.proof_dir === 'string' && A.proof_dir.startsWith('/'), 'args.proof_dir must be an absolute path')
need(Array.isArray(A.tasks) && A.tasks.length > 0, 'args.tasks must be a non-empty array')

const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'video'
const KIT = (A.kit_dir || '~/.borumi-agent/animation-kit').replace(/\/+$/, '')
need(/\/animation-kit$/.test(KIT), 'args.kit_dir must be "<BORUMI_AGENT_HOME>/animation-kit" (job.mjs finds the kit there, not via this arg)')
const PLUGIN = (A.plugin_dir || '~/.borumi-agent/plugin').replace(/\/+$/, '')
const AGENT_HOME_ENV = A.kit_dir ? `BORUMI_AGENT_HOME="${KIT.replace(/\/animation-kit$/, '').replace(/^~(?=\/|$)/, '$HOME')}" ` : ''
const JOB = `${AGENT_HOME_ENV}node ${PLUGIN}/scripts/job.mjs`
const MCP = `python3 ${PLUGIN}/scripts/borumi_mcp.py call`
const STYLE = A.style || 'velour'
const CANVAS = A.canvas_component || (STYLE === 'velour' ? 'VelourCanvas' : `the "${STYLE}" style's canvas component`)
const PALETTE = A.palette_ref || (STYLE === 'velour' ? 'C.*' : `the "${STYLE}" style's palette tokens`)
const PUB = A.public_subdir || slug(A.title)
const ACCENT = A.accent || '#E2774E'
const CREATOR = A.creator || 'the creator'
const PROOF = A.proof_dir.replace(/\/+$/, '')
const MAX_ROUNDS = Number.isFinite(A.max_rounds) ? A.max_rounds : 2
const CHECK_EFFORT = A.check_effort || 'medium'
const SCRATCH = `/tmp/design-visuals-${slug(A.title)}`
const bullets = (xs) => (xs || []).map(x => `- ${typeof x === 'string' ? x : JSON.stringify(x)}`).join('\n')
const ceilFrames = (start, end) => Math.ceil(((Math.round(end) - Math.round(start)) * 30) / 1000)

// Normalise and validate the tasks.
const keys = new Set()
const TASKS = A.tasks.map((t, i) => {
  need(t && t.key && !keys.has(t.key), `task ${i}: a unique key is required`)
  keys.add(t.key)
  need(Number.isFinite(t.start) && Number.isFinite(t.end) && t.end > t.start, `task ${t.key}: start/end (project ms) are required, end > start`)
  need(t.mode === 'behind' || t.mode === 'front', `task ${t.key}: mode must be "behind" or "front"`)
  need(typeof t.brief === 'string' && t.brief.length > 0, `task ${t.key}: a brief is required`)
  if (t.depends_on) need(A.tasks.slice(0, i).some(p => p.key === t.depends_on), `task ${t.key}: depends_on "${t.depends_on}" must be the key of an EARLIER task`)
  const words = t.words || (A.words && A.words[t.scene]) || null
  const sentences = (A.sentences && A.sentences[t.scene]) || null
  return { ...t, words, sentences, frames: Number.isFinite(t.frames) ? t.frames : ceilFrames(t.start, t.end) }
})
const noWords = TASKS.filter(t => !t.words).map(t => t.key)
if (noWords.length) log(`no final word file for: ${noWords.join(', ')} (designers fall back to get_transcript, which is phrase-level and can be misaligned)`)

// ---------------------------------------------------------------------------------------------- Prepare (optional)
const missing = TASKS.filter(t => !t.job)
if (missing.length) {
  need(A.prepare, `tasks without a job id (${missing.map(t => t.key).join(', ')}): pass their job ids or set args.prepare`)
  need(typeof A.project_path === 'string' && A.project_path, 'args.prepare needs args.project_path (the .bmprojbundle)')
  phase('Prepare')
  const WD = (A.prepare && A.prepare.words_dir) || `${SCRATCH}/jobwords`
  const PREP = {
    type: 'object',
    properties: {
      jobs: { type: 'array', items: { type: 'object', properties: { key: { type: 'string' }, job: { type: 'string' }, frames: { type: 'number' }, start: { type: 'number' }, end: { type: 'number' } }, required: ['key', 'job', 'frames'] } },
      phase_entries_added: { type: 'number' },
      typecheck_ok: { type: 'boolean' },
      notes: { type: 'string' },
    },
    required: ['jobs', 'typecheck_ok'],
  }
  const prep = await agent(`You PREPARE the animation jobs for ${CREATOR}'s video "${A.title}". Do NOT write to Borumi (job.mjs only writes the animation
workspace and the deliverable folder next to the bundle). Paths starting with ~ mean $HOME.
Jobs to create (key | scene | start_ms | end_ms | mode | title):
${missing.map(t => `${t.key} | ${t.scene} | ${Math.round(t.start)} | ${Math.round(t.end)} | ${t.mode} | ${(t.brief.split('\n')[0] || t.key).slice(0, 80).replace(/\|/g, '/')}`).join('\n')}
Word files per scene ([start_ms, end_ms, word], project ms): ${JSON.stringify(A.words || {})}

Steps (run them ONE AT A TIME; never in parallel: job ids come from the clock and the manifest has a lock):
1. For each scene above, write a job-shaped words file "${WD}/<scene>.json":
   {"layers":[{"id":"microphone_1","kind":"microphone","segments":[{"segment_id":"<scene>","scene_id":"<scene>","items":[[start_ms,end_ms,word],...]}]}]}
2. For each job, in order: ${JOB} create --project-path "${A.project_path}" --start-ms <start> --end-ms <end> --mode <mode>
   --style ${STYLE} --background <solid for behind | transparent for front> --words "${WD}/<scene>.json" --context "${WD}/<scene>.json"
   --scene-id <scene> --user-brief "<title>". Read the JSON it prints and record jobId. If it fails, report the error and continue.
3. CEIL SIZING: job.mjs sizes jobs with floor((end-start)*30/1000) frames, which leaves the slot's last partial frame uncovered (a frame
   of bare canvas can show there). For each new job set n = ceil((end-start)*30/1000) and write durationInFrames = n and endMs = end in
   BOTH job.json files (<kit>/src/jobs/<id>/job.json and the deliverable folder's job.json, whose path is "outDir" in the create
   output); if "endMsEffective" exists set it to start + round(n*1000/30). The placement step trims the tail to the exact slot length.
4. DRIFT PHASE: if ${KIT}/styles/${STYLE}/src/phase.ts exists, add one entry per new job to JOB_START_MS in ONE edit
   ("<jobId>": <start_ms>, // ${A.title.replace(/[*/]/g, '')}). Change nothing else in that file. Without the entry the dot grid of that
   render starts at phase 0 and visibly jumps at the cut into it.
5. ${JOB} manifest, then ${JOB} typecheck (must be clean).
${(A.broll || []).length ? `6. Confirm these B-roll files exist under ${KIT}/public/: ${(A.broll || []).map(b => b.src).join(', ')}. Report any that are missing.` : ''}
Return every job with its key, id, final frame count, start and end.`, { label: 'prepare:jobs', phase: 'Prepare', schema: PREP })
  need(prep && Array.isArray(prep.jobs), 'the prepare step returned nothing')
  for (const t of missing) {
    const j = prep.jobs.find(x => x.key === t.key)
    if (j && j.job) { t.job = j.job; if (Number.isFinite(j.frames)) t.frames = j.frames }
  }
  const still = missing.filter(t => !t.job)
  if (still.length) log(`prepare could not create: ${still.map(t => t.key).join(', ')} (skipped)`)
}
const RUN = TASKS.filter(t => t.job)

// ---------------------------------------------------------------------------------------------- prompts
const COMMON = `
You design ONE section of the visuals for ${CREATOR}'s YouTube video "${A.title}".${A.topic ? ` ${A.topic}` : ''}
Do NOT write to Borumi. Read-only calls are fine${A.project_id ? `: ${MCP} get_transcript|get_timeline|inspect_timeline '{"project_id":"${A.project_id}", ...}' 300
(read big ranges in ~60 s chunks; inspect_timeline sampling max_frames <= 8)` : ''}. Paths starting with ~ mean $HOME.

THE LOOK
- Kit ${KIT}, style "${STYLE}". Read styles/${STYLE}/SKILL.md${A.designer_brief ? ` and the designer brief "${A.designer_brief}"` : ''} first.
- Dark #121212 canvas with the slowly drifting dot grid (${CANVAS}, wrapping the whole scene once). Your job's drift phase is registered in styles/${STYLE}/src/phase.ts
  by the editor: never edit it or any other shared file under styles/ or src/ outside your job folder.
- Accent ${ACCENT}, Inter. Colours from ${PALETTE}, never hardcoded hexes. No em dashes in on-screen text.
- PANEL_MODE: the 1920x1080 stage is scaled (x0.768) into the LEFT panel; the creator's camera tile and the step badge sit on the right,
  outside your panel. Body text >= 36 px and labels >= 28 px in stage pixels; crop screenshots so their own text clears that bar.${(A.reference_jobs || []).length ? `\n- Study these scenes for patterns (read-only): ${(A.reference_jobs || []).map(r => `src/jobs/${r}`).join('; ')}.` : ''}
${A.direction ? `\nTHE CREATOR'S DIRECTION: ${A.direction}\n` : ''}
PROOF-FIRST
- One clear visual aid at a time: a REAL screenshot in a browser card (URL bar with the real domain) or a phone frame for any claim; a
  clean, simple diagram for any concept, building on the words.
- When the creator says a number or a name, ONE highlight (ring/box/underline) on it in the screenshot, landing on that word. Measure its
  pixel position in the image, leave a margin, and confirm in a still. Prefer *-full/*-crop images and draw your own highlight.
- A small source pill under each proof: "<domain> · captured <Mon D, YYYY>".
- Calm motion: entrance <= 12 frames, slow push <= 4%, crossfades at sentence boundaries. Beats every 2.5 to 6 s; nothing static over ~4 s.
- Never an empty panel for more than a beat, and never an EMPTY SHELL (a card, strip or tracker with no content yet): content arrives
  with its container.
- Sequence every change: the old element leaves (or the stack moves up, or the container grows) BEFORE the new one enters. No two texts in
  the same spot at any frame, including mid-crossfade. Crossfades never dip to black.
- Screen recordings and B-roll at their own aspect (or objectFit "contain") with no extra zoom, so no UI text is cut. Light-mode footage
  or captures go in a frame or get dimmed.

PROOF PACK: "${PROOF}" (README.md and proof-index.json in narration order; sources.json per folder). Use only images you opened and confirmed.
Respect each entry's status (✗/~ means show the honest line, not the claim) and do_not_show. Copy what you use into
${KIT}/public/${PUB}/proof/<your-job-id>/ and reference it relative to public/.
${(A.broll || []).length ? `B-ROLL (muted; startFrom / loopFrames as needed):\n${(A.broll || []).map(b => `- ${b.src}: ${b.desc || ''}`).join('\n')}\n` : ''}
HONESTY (never show more than the source supports)
- Every number, name, date, handle and quote on screen is in a proof file or in the creator's words for THIS stretch. If narration and
  proof disagree, show the proof's true line and report the mismatch.
- Labels never stronger than the words ("might not be possible" stays that). No concepts on screen that the creator doesn't say here.
- No invented counters (a CountUp starts at 0 or another sourced value); no mock-up embellishments the source lacks (verified badges,
  "Paid partnership" pills, fake UI).${(A.honesty_notes || []).length ? `\n${bullets(A.honesty_notes)}` : ''}

PRIVACY
- No readable third-party names, meeting titles, calendar entries, member lists, DMs or emails, in screenshots or in footage/B-roll.
- Never show the price, checkout or join page of the creator's own paid community; show the real community instead.${(A.privacy_rules || []).length ? `\n${bullets(A.privacy_rules)}` : ''}

PROCESS: write Scene.tsx with a header comment listing every beat (frame, word, what shows, proof file); ${JOB} typecheck; stills at every
beat AND 2 to 3 frames into every transition (${JOB} still <JOB> --frame N --out /tmp/<JOB>/fN.png) and LOOK at them with Read; fix; set
render.json {"version": N, "notes": "...", "title": "<max 20 chars>"}; ${JOB} render <JOB>; ${JOB} wait <JOB> --timeout 540 (repeat until
state is "done"; timedOut is not a failure, renders queue behind a lock); ${JOB} sheet <JOB> --frames 12 and look; confirm the frame count
with ffprobe -count_frames. Edit only files inside your job folder (plus your copies under public/${PUB}/proof/<job>/). Return the result.`

const timing = (t) => `TIMING: job folder ${KIT}/src/jobs/${t.job}/. JOB_START = ${Math.round(t.start)} ms; slot ${Math.round(t.start)}-${Math.round(t.end)} ms;
${t.frames} frames. durationInFrames in job.json is AUTHORITATIVE: it is ceil(slot*30/1000) on purpose (the placement step trims the tail),
so keep it even where brief.md says one frame less. Render frame of a project time: const at = (ms, lead = 3) =>
Math.round(((ms - ${Math.round(t.start)}) * 30) / 1000) - lead. Land each beat 2 to 4 frames BEFORE its word.
${t.words ? `Exact word times (PROJECT ms): "${t.words}"${t.sentences ? ` and sentences "${t.sentences}"` : ''}. These are final: trust them over the job's brief.md/words.json, which can be stale after cuts.` : `No final word file was given: read the CURRENT transcript for ${Math.round(t.start)}-${Math.round(t.end)} (read-only get_transcript) and check word times against the audio where a beat matters.`}`

const FRONT = `FRONT-MODE JOB: this renders a TRANSPARENT overlay (ProRes 4444 with alpha) over the creator's real footage in the left panel. The
footage is the main visual. In front mode the transparent ${CANVAS} is NOT scaled into the panel: every coordinate is a FINAL-FRAME pixel.
Keep everything inside the panel (x 19-1493, y 19-1061) and fully transparent elsewhere (nothing at x >= 1500: camera tile and badge).
NEVER cover the part of the screen the creator is talking about (the prompt being typed, the window or button being pointed at). Before
placing each card, look at the footage at that exact project time (inspect_timeline: view render, presentation frames, sampling uniform,
max_frames <= 8, save true; or extract frames from the bundle media with ffmpeg) and put cards in EMPTY areas, timed to when that area is
free. Small cards, short holds (~3-4 s), rings/arrows on the real UI at the word. Fully transparent when nothing is needed.`

const revisionNote = (t) => t.revision
  ? `REVISION JOB: ${t.job} already has render v${t.revision.of_version || '?'}. Read its Scene.tsx, render.json and job.json first. Change ONLY what the
brief lists; keep every other beat, its timing and the duration. Set render.json version to lastRenderedVersion + 1. Compare a 12-frame sheet
of the previous render with the new one to confirm nothing else changed.`
  : ''

const depNote = (t, dep) => {
  if (!t.depends_on) return ''
  const d = dep && dep.task
  const r = dep && dep.result
  return `CONSISTENCY: this section continues "${t.depends_on}"${d ? ` (job ${d.job}, ${KIT}/src/jobs/${d.job}/Scene.tsx${r && r.render_path ? `, rendered ${r.render_path}` : ''})` : ''}.
Shared elements (an illustration, a diagram, a card style) must look IDENTICAL: import or copy that component/geometry, same labels, colours
and arrangement.${r ? '' : ' (Its designer did not return a result: read its Scene.tsx if it exists and say in problems what you could not match.)'}`
}

const RESULT = {
  type: 'object',
  properties: {
    job: { type: 'string' }, version: { type: 'number' }, render_path: { type: 'string' }, frames: { type: 'number' },
    beats: { type: 'array', items: { type: 'object', properties: { frame: { type: 'number' }, project_ms: { type: 'number' }, word: { type: 'string' }, shows: { type: 'string' }, proof_file: { type: 'string' } }, required: ['frame', 'shows'] } },
    numbers_on_screen: { type: 'array', items: { type: 'string' } },
    mismatches: { type: 'array', items: { type: 'string' } },
    extra_outputs: { type: 'array', items: { type: 'string' } },
    problems: { type: 'string' },
  },
  required: ['job', 'version', 'render_path', 'frames', 'beats', 'numbers_on_screen', 'problems'],
}
const CHECK = {
  type: 'object',
  properties: {
    pass: { type: 'boolean' },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        properties: { severity: { type: 'string', enum: ['blocking', 'minor'] }, frames: { type: 'string' }, problem: { type: 'string' }, fix: { type: 'string' } },
        required: ['severity', 'problem'],
      },
    },
    evidence: { type: 'array', items: { type: 'string' } },
  },
  required: ['pass', 'issues'],
}

const designPrompt = (t, dep) => `${COMMON}

${t.mode === 'front' ? FRONT : ''}
${revisionNote(t)}
${depNote(t, dep)}
JOB: ${t.job} (scene ${t.scene || '?'}; ${t.mode}).
${timing(t)}
SECTION BRIEF:
${t.brief}`

const checkPrompt = (t, r, round) => `Independently CHECK this render for ${CREATOR}'s video "${A.title}". Don't modify anything; scratch dir
${SCRATCH}/chk-${t.key}-${round}/. Paths starting with ~ mean $HOME. Assume the designer missed things. Never write to Borumi${A.project_id ? `; read-only
calls: ${MCP} get_transcript|inspect_timeline '{"project_id":"${A.project_id}", ...}' 300 (inspect_timeline max_frames <= 8)` : ''}.
JOB ${t.job} (${t.mode}), slot ${Math.round(t.start)}-${Math.round(t.end)} project ms, JOB_START ${Math.round(t.start)}.
Section brief:
${t.brief}
${t.depends_on ? `\nIt must match the shared elements of task "${t.depends_on}" (see that job's Scene.tsx / latest render).\n` : ''}
Designer report:
${JSON.stringify(r, null, 1).slice(0, 12000)}

Use your OWN ffprobe -count_frames and your OWN frames (ffmpeg -ss <s> -i <render> -frames:v 1 ...; look at each with Read). Sample about
one frame per 2 s, every beat, and 2 to 3 frames into every transition. Check:
1. Frame count = ${t.frames} (job.json durationInFrames = ceil(slot*30/1000), deliberately; the placement step trims the tail). A count one
   above floor(slot*30/1000) is CORRECT. Only a count different from ${t.frames} is a defect.
2. Beats land 2-4 frames before their words (${t.words ? `"${t.words}"` : 'the current transcript, read-only'}; render frame =
   (project_ms - ${Math.round(t.start)}) * 30 / 1000). Flag beats more than ~6 frames early or more than ~3 frames late.
3. Every on-screen number, name, handle, date and quote matches a real proof file in "${PROOF}" (status and honest lines in proof-index.json)
   or the creator's words. Nothing invented: no counters, no CountUp starting from an unsourced value, no badges/pills/UI the source lacks.
4. No label or caption is stronger than the creator's words or the source; no concept on screen he doesn't say in this stretch.
5. Highlights sit exactly on their targets with a margin: no marker running onto the next word, no ring on a card edge.
6. Nothing cut off: screenshots cropped mid-word, truncated URL bars, B-roll/recording UI clipped by cover-fit or zoom, text overflow.
7. Nothing overlapping at ANY frame: crossfade ghosts (the old beat still visible under the new one), new items entering before the old
   ones moved, content drawn before its container grew, two texts on one spot; no dips to black between beats.
8. The panel is never empty or showing an empty shell for more than ~1 s; no static hold over ~4 s.
9. Legibility at the final size: text under ~22 px tall in the final 1920x1080 frame is too small.
10. Privacy: no readable third-party names, meetings, calendar entries, member lists, DMs or emails in screenshots or B-roll; never the
    creator's paid-community price, checkout or join page; each proof entry's do_not_show respected.${(A.privacy_rules || []).length ? ` Also:\n${bullets(A.privacy_rules)}` : ''}
${t.mode === 'front' ? `11. FRONT: compare with the footage at the same project times (inspect_timeline read-only, or frames extracted from the bundle media):
    the overlay must never cover the real UI being discussed; it stays inside the panel (x 19-1493, y 19-1061); everything else, and
    the whole x >= 1500 column, is fully transparent (check the alpha of a few frames: ffmpeg -pix_fmt rgba).` : '11. BEHIND: the render draws its own canvas + panel; nothing reads as a broken panel edge or a second nested canvas.'}
12. Calm, proof-first, on-style; no em dashes.${round > 0 || t.revision ? `
13. REVISION: every listed fix is done, and nothing else changed versus the previous version (compare 12-frame sheets of both renders;
    same beats, same timing, same duration).` : ''}
Each issue: severity "blocking" (false/unsourced/overstated content, privacy, overlap or ghost at any frame, cut-off text, empty panel or
shell > ~1.5 s, covering the real UI, wrong frame count, missing render) or "minor" (polish), with frame numbers and a concrete fix.
pass = true only if there are NO blocking issues. Put what you verified as OK in evidence.`

const revisePrompt = (t, r, chk, round) => `${COMMON}

${t.mode === 'front' ? FRONT : ''}
${depNote(t, null)}
REVISION ROUND ${round} of ${t.job}: an independent checker reviewed render v${r.version} (${r.render_path}). Fix every BLOCKING issue below,
and the minor ones that are cheap and safe. Keep everything else, every beat's timing and the duration (${t.frames} frames). Set render.json
version to ${Number.isFinite(r.version) ? r.version + 1 : 'lastRenderedVersion + 1'}, re-render, and confirm each fix with stills at the
listed frames (and 2-3 frames into nearby transitions). Compare a 12-frame sheet of v${r.version} with the new render for regressions.
${timing(t)}
CHECKER FINDINGS:
${chk.issues.map((i, n) => `${n + 1}. [${i.severity}] ${i.frames ? `${i.frames}: ` : ''}${i.problem}${i.fix ? ` FIX: ${i.fix}` : ''}`).join('\n')}
SECTION BRIEF (for context):
${t.brief}`

// ---------------------------------------------------------------------------------------------- Design, Check, Revise
const gate = {}
const release = {}
for (const t of RUN) gate[t.key] = new Promise(resolve => { release[t.key] = resolve })
const passed = (c) => !!c && (c.pass === true || !(c.issues || []).some(i => i.severity === 'blocking'))

phase('Design')
const out = await pipeline(
  RUN,
  async (t) => {
    const dep = t.depends_on && gate[t.depends_on] ? await gate[t.depends_on] : null
    let r = null
    try {
      r = await agent(designPrompt(t, dep), { label: `design:${t.key}`, phase: 'Design', schema: RESULT })
    } finally {
      release[t.key]({ task: t, result: r })
    }
    return r
  },
  async (r, t) => {
    const rec = { key: t.key, task: t, design: r, final: r, rounds: 0, history: [], check: null }
    if (!r) {
      rec.check = { pass: false, issues: [{ severity: 'blocking', problem: 'designer returned no result' }] }
      return rec
    }
    let chk = await agent(checkPrompt(t, r, 0), { label: `check:${t.key}`, phase: 'Check', schema: CHECK, effort: CHECK_EFFORT })
    while (chk && !passed(chk) && rec.rounds < MAX_ROUNDS) {
      rec.rounds += 1
      rec.history.push({ version: rec.final.version, issues: chk.issues })
      const next = await agent(revisePrompt(t, rec.final, chk, rec.rounds), { label: `revise:${t.key}#${rec.rounds}`, phase: 'Revise', schema: RESULT })
      if (!next) break
      rec.final = next
      chk = await agent(checkPrompt(t, next, rec.rounds), { label: `check:${t.key}#${rec.rounds}`, phase: 'Revise', schema: CHECK, effort: CHECK_EFFORT })
    }
    rec.check = chk || { pass: false, issues: [{ severity: 'blocking', problem: 'checker returned nothing: re-run the check before placing' }] }
    return rec
  },
)
const recs = out.map((rec, i) => rec || { key: RUN[i].key, task: RUN[i], design: null, final: null, rounds: 0, history: [], check: { pass: false, issues: [{ severity: 'blocking', problem: 'pipeline stage failed' }] } })

const results = recs.map(rec => ({
  key: rec.key, job: rec.task.job, mode: rec.task.mode, start: rec.task.start, end: rec.task.end, frames: rec.task.frames,
  render_path: rec.final ? rec.final.render_path : null, version: rec.final ? rec.final.version : null,
  pass: passed(rec.check), rounds: rec.rounds,
  open_issues: (rec.check.issues || []).filter(i => i.severity === 'blocking'),
  minor_issues: (rec.check.issues || []).filter(i => i.severity === 'minor'),
  mismatches: rec.final ? rec.final.mismatches || [] : [],
  numbers_on_screen: rec.final ? rec.final.numbers_on_screen : [],
}))
const ok = results.filter(r => r.pass && r.render_path)
const needsAttention = results.filter(r => !(r.pass && r.render_path)).map(r => r.key)
if (needsAttention.length) log(`still not passing (max ${MAX_ROUNDS} revision rounds; see open_issues): ${needsAttention.join(', ')}`)
const skipped = TASKS.filter(t => !t.job).map(t => t.key)

const slotsFor = (mode) => ({
  mode,
  slots: ok.filter(r => r.mode === mode).sort((a, b) => a.start - b.start).map(r => ({ name: r.key, render: r.render_path, start: Math.round(r.start), end: Math.round(r.end) })),
})

// ---------------------------------------------------------------------------------------------- Continuity
let continuity = null
if (A.continuity !== false && ok.length > 1) {
  phase('Continuity')
  const ordered = [...ok].sort((a, b) => a.start - b.start)
  continuity = await agent(`You review the motion graphics of ${CREATOR}'s video "${A.title}" ACROSS sections, in timeline order. Don't modify anything;
scratch dir ${SCRATCH}/continuity/. Paths starting with ~ mean $HOME. Renders (key | mode | slot project ms | render):
${ordered.map(r => `${r.key} | ${r.mode} | ${Math.round(r.start)}-${Math.round(r.end)} | ${r.render_path}`).join('\n')}
Make a 12-frame sheet of each (${JOB} sheet <job> --frames 12, or ffmpeg) and look at them in order. Report only real problems, with keys
and frame numbers:
- shared elements (an illustration, a diagram, a card style) that change look between sections;
- the same figure shown differently in two places, or a proof repeated back to back;
- style drift (proof card treatment, source pill format, kicker style, accent use, text sizes);
- adjacent BEHIND renders whose slots touch (one's end = the next one's start): compare the last frame of the first with the first frame
  of the next; the dot grid must continue without a jump (a jump means a missing or wrong JOB_START entry in phase.ts);
- anything that would read as a glitch at a join.`, { label: 'continuity', phase: 'Continuity' })
}

return {
  results,
  placements: { behind: slotsFor('behind'), front: slotsFor('front') },
  needs_attention: needsAttention,
  skipped_no_job: skipped,
  mismatches: results.filter(r => r.mismatches.length).map(r => ({ key: r.key, mismatches: r.mismatches })),
  continuity,
}
