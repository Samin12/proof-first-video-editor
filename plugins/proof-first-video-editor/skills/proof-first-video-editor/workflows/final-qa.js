export const meta = {
  name: 'proof-first-final-qa',
  description: 'Independent final QA of a finished video (the master, or a tighter cut): joins and cards, narrative flow, full watch-through',
  whenToUse: 'mode "master": after step 7 on the exported master (SKILL step 9.5); mode "cut": after build_cut.py + qa_cut.py passed on the tighter cut (and again after every fix round)',
  phases: [{ title: 'Review', detail: 'three independent reviewers in parallel' }],
}
/*
 * Workflow: final QA of a finished video (READ-ONLY: reviewers never modify or move files; scratch under args.scratch_dir).
 *
 * Two modes:
 *   "cut"    (default when qa_dir is given) the tighter v-next cut: reviewers read qa_cut.py's join sheets, report and
 *            whisper transcript.
 *   "master" (default without qa_dir) the finished master straight from step 7: there is no qa_cut.py output, so the
 *            joins reviewer extracts frames around every chapter card and every edit point itself, the flow reviewer
 *            reads a transcript (args.transcript, else it transcribes the audio with whisper-cli), and the watch-through
 *            reviewer also checks every on-screen number against the proof pack.
 *
 * args:
 *   video / cut_video string  REQUIRED  the video to review (mp4); cut_video is the historical name
 *   video_title      string   REQUIRED
 *   mode             string   optional  "cut" | "master" (default: "cut" when qa_dir is given, else "master")
 *   qa_dir           string   cut mode: REQUIRED  qa_cut.py OUTDIR (qa_report.json with join_times_s, joins_NN.jpg, cut_whisper.json)
 *   card_starts_s    number[] master mode: chapter card start times in the video (s), e.g. from WORK/cards/<name>/applied.json
 *                             (project ms = video ms for a full export); without them the reviewer finds the white cards itself
 *   edit_points_s    number[] master mode: times (s) of the cut joins to inspect (e.g. edit_point_check.py's edges); optional
 *   transcript       string   master mode: a transcript of the video ([start_ms, end_ms, text] JSON, whisper JSON or SRT);
 *                             default: the reviewer runs whisper-cli on the extracted audio ($WHISPER_MODEL)
 *   proof_index      string   master mode: proof-index.json of the proof pack (every on-screen number is checked against it)
 *   creator          string   optional  default "the creator"
 *   cut_label        string   optional  e.g. "v3" (default "the tighter cut")
 *   master_label     string   optional  e.g. "v2" (default "the master")
 *   cut_length       string   optional  e.g. "14:02"
 *   master_length    string   optional  e.g. "20:02"
 *   n_ranges         number   optional  how many ranges were removed
 *   fps              number   optional  default 60
 *   scratch_dir      string   optional  default <qa_dir>/finalqa (reviewers use <scratch_dir>_<name>/)
 *   layout           string   optional  what every non-card frame must show (default: the proof-first layout below)
 *   badges           string[] optional  step badge texts in order, e.g. ["INTRO", "WHY IT MATTERS 1/6", ...]
 *   cards            string[] optional  chapter card titles in order
 *   chapters_file    string   optional  youtube-chapters.txt of the cut
 *   master_transcript string  optional  transcript of the master for comparison ([start_ms, end_ms, text] JSON or whisper JSON)
 *   creator_rules    string[] optional  never-show rules (e.g. "never show the creator's paid-community price or its landing page")
 *   ending_check     string   optional  what the last seconds must show
 *   pinned_joins_s   number[] optional  cut times (s) of hand-pinned joins / panel patches to check at exact frames +-2
 *   audio_note       string   optional  what audio QA already established (default below)
 *
 * Returns [joins, flow, watch]: each { ok, summary, issues: [{t_s, severity, problem, fix}] }.
 * Fixes map back to the master through OUT.mp4.work/edl.json: a pin (apply_pins.py start/end_by_value), a panel patch
 * (build_cut.py --panel-patches), or a changed cut; then rebuild, re-run qa_cut.py and this workflow.
 */
const A = args || {}
const MODE = A.mode || (A.qa_dir ? 'cut' : 'master')
if (MODE !== 'cut' && MODE !== 'master') throw new Error('final-qa: args.mode must be "cut" or "master"')
const V = A.video || A.cut_video
if (!V) throw new Error('final-qa: args.video (or args.cut_video) is required')
if (!A.video_title) throw new Error('final-qa: args.video_title is required')
if (MODE === 'cut' && !A.qa_dir) throw new Error('final-qa: args.qa_dir (qa_cut.py OUTDIR) is required in cut mode')
const Q = A.qa_dir
const FPS = A.fps || 60
const slugT = String(A.video_title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'video'
const SCR = A.scratch_dir || (Q ? `${Q}/finalqa` : `/tmp/final-qa-${slugT}`)
const CUT = MODE === 'master' ? (A.cut_label || 'the finished master') : (A.cut_label || 'the tighter cut')
const MASTER = A.master_label || 'the master'
const LAYOUT = A.layout || `visuals in the LEFT panel, the creator's camera tile on the right with a step badge above it, a dark dot-grid
canvas behind, and full-screen white chapter cards (~1.6 s) between sections`
const RULES = (A.creator_rules && A.creator_rules.length) ? `\nThe creator's standing rules: ${A.creator_rules.join('; ')}.` : ''
const WHAT = MODE === 'master'
  ? `${CUT}${A.cut_length ? ' (' + A.cut_length + ')' : ''}: the edited video exported from the Borumi project (cuts, chapter cards, step badges,
proof visuals in the left panel, mastered audio). File: "${V}" (${FPS} fps).`
  : `${CUT}: a tighter${A.cut_length ? ' ' + A.cut_length : ''} cut
of the${A.master_length ? ' ' + A.master_length : ''} ${MASTER}, made by removing ${A.n_ranges ? A.n_ranges + ' ' : ''}time ranges (picture + audio together) from
the finished master. Cut file: "${V}" (${FPS} fps).`
const CTX = `You are doing final QA on ${A.creator || "the creator"}'s YouTube video "${A.video_title}", ${WHAT} Read-only: never modify or move any file; scratch dir ${SCR}_<yourname>/.
Look at images with Read. Extract frames with ffmpeg -ss <t> -i "${V}" -frames:v 1 (for an exact frame n use -ss (n-0.3)/${FPS}),
and run frame loops under bash (zsh aborts "rm *.png" on an empty folder); don't rely on the drawtext filter (often not built in).
Layout: ${LAYOUT}.${A.badges && A.badges.length ? `\nStep badges in order: ${A.badges.map(b => `"${b}"`).join(', ')}.` : ''}${A.cards && A.cards.length ? `\nChapter cards in order: ${A.cards.join(', ')}.` : ''}${RULES}
Report only real, specific problems with ${CUT} timestamps (seconds), each with severity blocking/minor and a concrete fix.
${A.audio_note || (MODE === 'master'
    ? 'Audio QA already passed (verify_remix.py, edit_point_check.py, check_sync.py, loudness). Report audible problems you notice anyway.'
    : 'Audio QA already passed (no clicks, no word slivers). Whisper differences next to joins were checked by transcribing each side of the join on its own (qa_report.json "join_checks"); they are whisper merging phrases or the speaker\'s own words, not cut damage.')}`
const OUT = { type: 'object', properties: { ok: { type: 'boolean' }, summary: { type: 'string' },
  issues: { type: 'array', items: { type: 'object', properties: { t_s: { type: 'number' }, severity: { type: 'string', enum: ['blocking', 'minor'] }, problem: { type: 'string' }, fix: { type: 'string' } }, required: ['severity', 'problem', 'fix'] } } },
  required: ['ok', 'summary', 'issues'] }

const PINNED = (A.pinned_joins_s && A.pinned_joins_s.length)
  ? `Also check these hand-pinned joins / patched spots at exact frames 2 before / 2 after: ${A.pinned_joins_s.map(t => `${t} s`).join(', ')}.`
  : 'Also check any join that looks suspicious at exact frames 2 before / 2 after.'

const JOINS_CUT = () => `${CTX}\n\nYOUR JOB: JOIN VISUALS. ${Q}/joins_NN.jpg (all of them) are contact sheets: each consecutive PAIR of tiles is
(last frame before a cut, first frame after it), in time order; join times are in ${Q}/qa_report.json ("join_times_s", frames in
"join_frames"; "flash_suspects" lists joins whose panel jumps once more right after the cut). Look at every pair. Flag: a flash of a
half-faded/removed visual right after a cut, one frame of the previous visual right before a chapter card, a removed visual's fade-out
residue that lingers after the join, an empty or near-empty panel lasting > 1 s after a cut (check a few frames after with ffmpeg),
mismatched badge vs chapter, a missing camera tile or badge. Normal slide changes and small jump cuts of the camera are fine. ${PINNED}`
const FLOW_CUT = () => `${CTX}\n\nYOUR JOB: NARRATIVE FLOW. Read the full transcript of the cut in ${Q}/cut_whisper.json (whisper segments with
offsets in ms; names may be misspelled)${A.chapters_file ? ` and the chapter list ${A.chapters_file}` : ''}.${A.master_transcript ? ` Also read the ${MASTER} transcript for comparison (${A.master_transcript}).` : ''}
Judge the story end to end for the audience: does each section land once, is anything now confusing because setup was removed, any orphan
references ("this", "that", "like I said") to removed material, orphaned sentence starters left between two cuts, broken sentences at
joins, abrupt topic jumps, remaining redundancy, promises made early (e.g. in the roadmap) that the cut no longer delivers, a spoken level
that no longer matches its badge, and does the ending / call to action work. Give the overall verdict in summary (is ${CUT} better and
punchier than ${MASTER}?) and list only concrete problems with timestamps; for a leftover fragment say which cut edge to move and where
(it must land in silence).`
const WATCH_CUT = () => `${CTX}\n\nYOUR JOB: WATCH-THROUGH. Sample the video every 4 s (e.g. ffmpeg -i "${V}" -vf "fps=1/4,scale=480:-1,tile=6x5" sheets) and look
at every sheet; zoom into anything suspicious with single full-res frames. Check: camera tile + badge present on every non-card frame,
badge step matches the section (badge changes at each card), every chapter card exists (find each), no panel empty for more than ~2 s,
no frozen/duplicated stretch, no visual from one section stranded in another (a visual whose narration was cut).${A.creator_rules && A.creator_rules.length ? ' Verify every standing rule above on every sheet.' : ''}${A.ending_check ? ` The ending must show: ${A.ending_check}.` : ''}`

const CARDS = (A.card_starts_s && A.card_starts_s.length) ? A.card_starts_s.map(t => `${t} s`).join(', ') : null
const EDITS = (A.edit_points_s && A.edit_points_s.length) ? A.edit_points_s.map(t => `${t} s`).join(', ') : null
const JOINS_MASTER = () => `${CTX}\n\nYOUR JOB: CARDS, JOINS AND EDGES of the finished master. ${CARDS ? `Chapter cards start at: ${CARDS}.` : 'Find every white chapter card yourself (ffmpeg -vf "fps=2,scale=320:-1,tile=8x6" sheets; a card is a full-frame white title card, ~1.6 s).'}
For every card, extract the exact frames 2 before and 2 after its start and end (frame n: -ss (n-0.3)/${FPS}) and look at them: flag a dark or
canvas-only frame right before a card (the 1 ms lead-in flash: fix with card_lead_in.py), a badge on a card, a missing camera tile or badge right
after a card, a badge that does not match the section that starts. ${EDITS ? `Also check these edit points at frames 2 before / 2 after: ${EDITS}.` : 'Also sample frames every 0.5 s around a dozen visible cuts in talking-head stretches.'}
Flag: a one-frame flash of the default fullscreen camera layout (a layout gap: gap_fill.py), a panel that empties for > 1 s, a render that
starts or ends a frame late against its cut, dots that jump at a join between renders (a missing JOB_START entry in phase.ts), a FRONT overlay
covering the real UI being discussed. ${PINNED}`
const FLOW_MASTER = () => `${CTX}\n\nYOUR JOB: NARRATIVE FLOW. ${A.transcript ? `Read the transcript ${A.transcript}.` : `Make a transcript first: ffmpeg -i "${V}" -vn -ac 1 -ar 16000 ${SCR}_flow/audio.wav, then whisper-cli -m "$WHISPER_MODEL" -f ${SCR}_flow/audio.wav -oj -of ${SCR}_flow/whisper (names may be misspelled).`}${A.chapters_file ? ` Read the chapter list ${A.chapters_file}.` : ''}
Judge the story end to end for the audience: does the hook land, does each section land once, leftover retakes or repeated lines, broken
sentences or clipped words at cuts, orphan references to removed material, abrupt topic jumps, promises made early (the roadmap) that the
video does not deliver, a spoken level that does not match its badge or card, and does the ending / call to action work. Give the overall
verdict in summary and list only concrete problems with timestamps and a fix (which cut to move, which line to flag to the creator).`
const WATCH_MASTER = () => `${WATCH_CUT()}${A.proof_index ? `
Also read the proof pack index ${A.proof_index}: every number, price, date, name and quote you can read on screen must match an entry (its
status and honest line), and nothing in an entry's do_not_show may be visible. Zoom into every proof card with single full-res frames.` : ''}`

phase('Review')
const r = await parallel([
  () => agent(MODE === 'master' ? JOINS_MASTER() : JOINS_CUT(), { label: MODE === 'master' ? 'qa:cards-joins' : 'qa:joins', phase: 'Review', schema: OUT }),
  () => agent(MODE === 'master' ? FLOW_MASTER() : FLOW_CUT(), { label: 'qa:flow', phase: 'Review', schema: OUT }),
  () => agent(MODE === 'master' ? WATCH_MASTER() : WATCH_CUT(), { label: 'qa:watch', phase: 'Review', schema: OUT }),
])
return r
