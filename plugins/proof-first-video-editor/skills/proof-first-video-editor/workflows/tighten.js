export const meta = {
  name: 'proof-first-tighten',
  description: 'Plan a shorter, punchier v-next cut of a finished master: section editors + narrative editor, then judge, skeptic and revise per section',
  whenToUse: 'Step 10 of proof-first-video-editor (optional tighter cut), after make_sections.py wrote the numbered section scripts',
  phases: [
    { title: 'Propose', detail: 'one section editor per section + one whole-video narrative editor' },
    { title: 'Judge', detail: 'final cut list per section' },
    { title: 'Skeptic', detail: 'try to break each cut list' },
    { title: 'Revise', detail: 'fix what the skeptic found' },
  ],
}
/*
 * Workflow: plan the tighter cut (READ-ONLY: agents only read section scripts, look at frames and dry-run the snapper).
 *
 * args:
 *   skill_dir      string   REQUIRED  absolute skill root (scripts at <skill_dir>/scripts/tighten)
 *   work           string   REQUIRED  absolute scratch dir for this cut (frames/, dry runs); nothing else is written
 *   master         string   REQUIRED  the finished master being cut (what frame.sh shows)
 *   video_title    string   REQUIRED  e.g. "REA"
 *   sections       array    REQUIRED  make_sections.py index.json "sections_list": [{key, scene, title, range:[a,b], file}]
 *   cards          array    REQUIRED  chapter cards no cut may touch: [{start_ms, end_ms, title}] or [[start, end, title]]
 *   config         string   optional  tighten.json; lets editors dry-run snap_cuts.py on their list
 *   python         string   optional  python with numpy for the dry runs (default "python3")
 *   creator        string   optional  default "the creator"
 *   about          string   optional  one line on what the video is
 *   master_label   string   optional  default "master" (e.g. "v2")
 *   master_length  string   optional  e.g. "20:02"
 *   request        string   optional  the creator's words for this cut
 *   audience       string   optional  default "curious beginners (no GitHub/API knowledge)"
 *   keep           string[] optional  what must survive (hook, credibility line, roadmap, each claim WITH its proof visual...)
 *   accuracy_notes string[] optional  known overstatements in the creator's words, with the facts (cut the clause if clean)
 *   target         string   optional  default "about 25% shorter, but quality first: never cut something that breaks the story"
 *   fps            number   optional  default 60 (pins must be multiples of 1000/fps ms)
 *
 * Returns { globalPlan, proposals, results }; results = [{key, scene, final, verdict, revised}] per section.
 * Next: scripts/tighten/collect_cuts.py <result.json> cuts.json --config tighten.json --pins-draft pins.draft.json
 * Resume after an interruption (session limits happen on long runs): relaunch with the same scriptPath + args +
 * resumeFromRunId; finished agents replay from cache, only the unfinished skeptic/revise steps run again.
 */
const A = args || {}
for (const k of ['skill_dir', 'work', 'master', 'video_title']) {
  if (!A[k]) throw new Error(`tighten: args.${k} is required`)
}
if (!Array.isArray(A.sections) || !A.sections.length) throw new Error('tighten: args.sections is required (index.json "sections_list")')
if (!Array.isArray(A.cards)) throw new Error('tighten: args.cards is required (use [] when the video has no chapter cards)')
const SK = A.skill_dir
const W = A.work
const SECTIONS = A.sections
const CARDS = (A.cards || []).map(c => Array.isArray(c) ? { start_ms: c[0], end_ms: c[1], title: c[2] } : c)
const CREATOR = A.creator || 'the creator'
const LABEL = A.master_label || 'master'
const PY = A.python || 'python3'
const FPS = A.fps || 60
const FRAME = `${SK}/scripts/tighten/frame.sh`
const SNAP = A.config
  ? `- Dry-run the snapper on a scratch copy of your list (never on the real files): write {"cuts": [... each with "section"
  and "scene" ...]} to ${W}/scratch_<you>/cuts.json, then run
  ${PY} ${SK}/scripts/tighten/snap_cuts.py --config "${A.config}" ${W}/scratch_<you>/cuts.json ${W}/scratch_<you>/out.json
  (that python needs numpy). It prints the snapped ms of every edge, the RMS at each edge and warnings (loud edge = possible
  word sliver).`
  : ''
const KEEP = (A.keep && A.keep.length) ? A.keep : [
  'the hook', "the creator's credibility line (short)", 'the level / roadmap structure', 'each claim WITH its proof visual',
  'every demo payoff', 'the walkthrough steps a beginner needs to follow along', 'the closing argument and the call to action',
  'every chapter card']
const ACC = (A.accuracy_notes && A.accuracy_notes.length)
  ? `- Accuracy: where the creator's words overstate, prefer cutting the overstated clause if the cut is clean and the sentence still
  works. Known overstatements: ${A.accuracy_notes.map(x => `"${x}"`).join('; ')}. Do NOT invent new claims; we can only remove.`
  : `- Accuracy: where the creator's words overstate (a license, a price, a free trial, what a tool can do), prefer cutting the
  overstated clause if the cut is clean and the sentence still works, and flag it. Do NOT invent new claims; we can only remove.`

const COMMON = `
You are editing ${CREATOR}'s YouTube video "${A.video_title}"${A.about ? ` (${A.about})` : ''}. The finished ${LABEL === 'master' ? '' : LABEL + ' '}master
(${A.master_length ? A.master_length + ', ' : ''}all visuals, chapter cards and mastered audio) is "${A.master}".
The request: "${A.request || 'make a new version: go through the full video, make it shorter, punchier and with no redundancy, and create a better flow and narrative with what we have'}".

HOW THE CUT IS MADE: we cut the finished master directly. Any removed time range removes picture (visual panel, camera tile, badge)
and audio together, so remaining visuals stay in sync with remaining words. Cuts are INCLUSIVE word-index ranges [from_word, to_word]
in ONE scene's word list. A snapper then finds the true silence before from_word and after to_word on the real audio (${FPS} fps grid).
Whisper word times are ±30 ms and often contiguous, so removing words in the MIDDLE of a fast phrase can leave slivers; prefer cuts at
sentence or clause boundaries (a comma or period before/after, or a gap_to_next > 0). A single filler word ("So", "Okay,", "Right?",
"uh", "actually") at the START of a sentence is fine to cut; mid-phrase single words are risky (mark them priority "could").
Adjacent cuts that would leave < ~100 ms between two sentences crush the sentence break: don't.

TOOLS (read-only; never write to the editor project, the exports folder or the section files):
- Section scripts with sentences and numbered words: ${SECTIONS.map(s => s.file).join(', ')}
- See the picture at any time: bash ${FRAME} <ms> ${W}/frames/<you>_<ms>.jpg "${A.master}"  (or f<frame> for an exact frame),
  then Read the jpg. Use it to understand which visual is on screen for a sentence (proof screenshot, diagram, demo footage) before
  cutting it, to check that a cut won't leave something on screen that the kept narration never explains, and that it won't remove
  the proof visual of a kept claim.
${SNAP}

EDITING PRINCIPLES
- Audience: ${A.audience || 'curious beginners (no GitHub/API knowledge)'}. Keep what they need to follow; cut what repeats, rambles,
  previews then repeats, restarts/stumbles ("It's It's"), meta talk ("in this video I'm gonna..."), throat-clearing openers,
  over-explaining, tangents.
- Keep: ${KEEP.join('; ')}.
- NEVER cut inside a chapter card range: ${CARDS.length ? CARDS.map(c => `${c.title} [${c.start_ms}-${c.end_ms}]`).join(', ') : '(this video has no cards)'}.
${ACC}
- Pace: long pauses can be tightened mechanically later; you may list ranges where pause tightening is SAFE (talking head or static
  visual) and where it is NOT (demo footage or screen recording where something happens on screen during the pause).
- EDGES: the snapper only picks the quietest point; it cannot follow a note like "the out-point must be after the removed card has
  faded". When a snapped edge would show a flash of a removed visual (a card or diagram still fading in/out at the join), a frame of the
  previous visual right before a chapter card, or crush the pause at the join, give pin_start_ms / pin_end_ms on that cut: exact
  multiples of 1000/${FPS} ms (k x ${(1000 / FPS).toFixed(3)}, written with 3 decimals), in silence, checked with frame.sh on both sides; pin both edges when one moves
  (~250-300 ms of pause at a join reads naturally; ~170 ms sounds rushed). Explain the pin in "reason".
- Target: ${A.target || 'about 25% shorter, but quality first: never cut something that breaks the story'}.`

const PROPOSAL = {
  type: 'object',
  properties: {
    section: { type: 'string' },
    summary: { type: 'string' },
    cuts: { type: 'array', items: { type: 'object', properties: {
      from_word: { type: 'integer' }, to_word: { type: 'integer' }, first_word: { type: 'string' }, last_word: { type: 'string' },
      removed_text: { type: 'string' }, kind: { type: 'string' }, priority: { type: 'string', enum: ['must', 'should', 'could'] },
      reason: { type: 'string' }, join_reads: { type: 'string' },
      pin_start_ms: { type: 'number' }, pin_end_ms: { type: 'number' } },
      required: ['from_word', 'to_word', 'first_word', 'last_word', 'removed_text', 'kind', 'priority', 'reason', 'join_reads'] } },
    pause_tighten_safe: { type: 'array', items: { type: 'object', properties: { from_ms: { type: 'integer' }, to_ms: { type: 'integer' }, note: { type: 'string' } }, required: ['from_ms', 'to_ms'] } },
    pause_tighten_unsafe: { type: 'array', items: { type: 'object', properties: { from_ms: { type: 'integer' }, to_ms: { type: 'integer' }, note: { type: 'string' } }, required: ['from_ms', 'to_ms'] } },
    est_removed_s: { type: 'number' },
    kept_script: { type: 'string' },
    flags: { type: 'array', items: { type: 'string' } },
  },
  required: ['section', 'summary', 'cuts', 'est_removed_s', 'kept_script'],
}
const GLOBAL = {
  type: 'object',
  properties: {
    arc: { type: 'string' },
    section_goals: { type: 'array', items: { type: 'object', properties: { section: { type: 'string' }, goal: { type: 'string' }, target_s: { type: 'number' } }, required: ['section', 'goal'] } },
    cross_redundancies: { type: 'array', items: { type: 'object', properties: { keep: { type: 'string' }, cut: { type: 'string' }, section: { type: 'string' }, from_word: { type: 'integer' }, to_word: { type: 'integer' }, reason: { type: 'string' } }, required: ['keep', 'cut', 'section', 'reason'] } },
    notes: { type: 'array', items: { type: 'string' } },
  },
  required: ['arc', 'section_goals', 'cross_redundancies'],
}
const VERDICT = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'object', properties: { cut_index: { type: 'integer' }, severity: { type: 'string', enum: ['blocking', 'minor'] }, problem: { type: 'string' }, fix: { type: 'string' } }, required: ['severity', 'problem', 'fix'] } },
    missed_redundancy: { type: 'array', items: { type: 'string' } },
  },
  required: ['ok', 'issues'],
}

phase('Propose')
const [globalPlan, ...proposals] = await parallel([
  () => agent(`${COMMON}\n\nYOUR JOB: WHOLE-VIDEO NARRATIVE EDITOR. Read ALL section scripts (${SECTIONS.map(s => s.file).join(', ')}).
Find the narrative arc and what each section must accomplish, give each section a target length, and find CROSS-SECTION redundancy (the same
point made twice in different sections, previews that are later repeated in full, recaps that restate). For each cross redundancy say which
occurrence to keep and which to cut (with section and word range if you can). Don't propose small in-section trims; the section editors do that.`,
    { label: 'narrative-editor', phase: 'Propose', schema: GLOBAL }),
  ...SECTIONS.map(s => () => agent(`${COMMON}\n\nYOUR JOB: SECTION EDITOR for ${s.key} (${s.title}), scene ${s.scene}, ${LABEL} range ${s.range[0]}-${s.range[1]} ms.
Read ${s.file} completely, look at the picture at key moments (frame.sh), and propose every cut that makes this section tighter and
punchier while keeping it clear for the audience. Word indices are from THIS scene's word list. Give the kept script (the section's text after
your cuts) so a reader can check the flow.`, { label: `edit:${s.key}`, phase: 'Propose', schema: PROPOSAL })),
])
log(`narrative plan ${globalPlan ? 'ok' : 'MISSING'}; proposals ${proposals.filter(Boolean).length}/${SECTIONS.length}`)

const results = await pipeline(
  SECTIONS,
  (s, _, i) => agent(`${COMMON}\n\nYOUR JOB: JUDGE / FINAL EDITOR for ${s.key} (${s.title}), scene ${s.scene}, ${LABEL} range ${s.range[0]}-${s.range[1]} ms.
Section script: ${s.file}. Produce the FINAL cut list for this section by weighing:
(1) the section editor's proposal: ${JSON.stringify(proposals[i] || null).slice(0, 30000)}
(2) the whole-video narrative editor's plan (goals, targets, cross-section redundancy; apply the items that concern ${s.key}): ${JSON.stringify(globalPlan || null).slice(0, 14000)}
Re-read the section yourself. Verify every from_word/to_word against the script (first_word/last_word must match the words at those
indices), make sure each join reads naturally aloud (join_reads = the words just before + just after the cut), check visuals with frame.sh
at both sides of the bigger cuts, and drop or adjust cuts that are risky (mid-phrase) or that remove needed setup. Output the final list in
the same schema (ascending, non-overlapping), with the full kept script.`, { label: `judge:${s.key}`, phase: 'Judge', schema: PROPOSAL }),
  (final, s) => final ? agent(`${COMMON}\n\nYOUR JOB: SKEPTIC. Try hard to find problems in this FINAL cut list for ${s.key} (${s.title}), scene ${s.scene}.
Section script: ${s.file}. Final list:\n${JSON.stringify(final).slice(0, 40000)}\n
Check every cut: indices really match first_word/last_word; the join is grammatical and natural; no orphan references ("this", "that",
"as I said", "like I showed") pointing at removed content; no orphaned sentence starter left between two cuts ("For me," then a new
sentence); no kept claim lost its proof visual (check frames with frame.sh at the cut edges); no removed setup that a kept payoff needs;
nothing a beginner now can't follow; no mid-phrase cut likely to leave an audible sliver (contiguous whisper times with no punctuation
boundary); no card range touched; no snapped edge that shows a flash of a removed visual (dry-run the snapper if you can, then look at the
frames at the snapped ms); nothing left visibly on screen (listed item, label) that the kept narration no longer mentions in a confusing
way. Also name redundancy that is still left. ok=true only if no blocking issue.`,
    { label: `skeptic:${s.key}`, phase: 'Skeptic', schema: VERDICT }).then(v => ({ s, final, v })) : null,
  (r) => {
    if (!r) return null
    const { s, final, v } = r
    if (v && v.ok && !(v.issues || []).some(x => x.severity === 'blocking')) return { key: s.key, scene: s.scene, final, verdict: v, revised: false }
    return agent(`${COMMON}\n\nYOUR JOB: REVISE the final cut list for ${s.key} (${s.title}), scene ${s.scene}, to fix the skeptic's issues.
Section script: ${s.file}.\nCurrent list:\n${JSON.stringify(final).slice(0, 40000)}\nSkeptic verdict:\n${JSON.stringify(v).slice(0, 12000)}\n
Fix every blocking issue (adjust, split or drop cuts; set pin_start_ms/pin_end_ms where an edge needs a hand pin; you may add cuts for
clearly missed redundancy). Re-verify indices against the script. Output the corrected final list in the same schema with the full kept
script.`, { label: `revise:${s.key}`, phase: 'Revise', schema: PROPOSAL })
      .then(rev => ({ key: s.key, scene: s.scene, final: rev || final, verdict: v, revised: !!rev }))
  },
)
return { globalPlan, proposals, results }
