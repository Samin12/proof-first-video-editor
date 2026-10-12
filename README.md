# proof-first-video-editor

A Claude Code skill (packaged as a plugin) that turns a raw Borumi recording into a finished
YouTube video where **every claim is backed by real proof on screen**. Borumi records a talking head and a screen
recording as separate layers. Ask Claude:

```
edit my Borumi video "My New Tutorial" like REA
```

Claude cuts the recording tight and adds chapter cards and a step badge. It researches and verifies a real proof visual
for every claim, designs the visuals in the left panel next to the camera tile, and places them on a dark drifting
dot-grid canvas. It masters the audio with Auphonic, fixes the captions, writes the YouTube chapters, and checks the
result in layers. Optionally it then makes a shorter, punchier v-next cut directly from the finished master.

It packages the workflow behind the "REA" video, a tutorial about an open-source reverse-engineering tool, and it
builds on two earlier public repos by the same author:
[edit-borumi-video](https://github.com/Samin12/edit-borumi-video) (Borumi bridge, cuts, cards, export) and
[opencutagent-borumi](https://github.com/Samin12/opencutagent-borumi) (Borumi MCP session, Remotion animation jobs).

## What it produces

The REA video, end to end:

| | |
|---|---|
| Raw recording | 29:42 (talking head + screen recording + device footage) |
| Edited master | 20:02, 6 chapter cards, 7 step badges, 33 verified Borumi commits, zero post-commit surprises |
| Proof visuals | 13 sections (10 BEHIND renders on talking-head stretches, 3 FRONT overlays on real footage), every one checked by an independent reviewer; longest truly empty panel 0.63 s |
| Proof pack | real captures of every checkable claim, independently re-verified, with a "fix before publishing" list for the creator |
| Audio | 6 mic sources mastered by Auphonic (0 ms duration difference, 0 ms lag each), the timeline audio rebuilt at exact positions, a full-mix pass, -16 LUFS, stereo |
| Delivery | 1080p60 final, 720p phone copy, fixed SRT, a `Chapters` block that lands on the cards, sha256 receipt |
| Tighter cut | 14:02 from the 20:02 master: 56 removal ranges, frame-exact, 0 clicks, 0 loud joins, captions and chapters mapped through the cut |

The look: visuals in the left rounded panel, the creator's camera tile on the right with a step badge above it, a
near-black canvas with a slowly drifting grey dot grid, white 1.6 s chapter cards with a quiet click, no whooshes. When the
creator states a fact, the screen shows the real page, with the number highlighted and a source pill. When a claim
overstates its source, the screen shows the source and the creator gets a flag. Claims are never silently changed.

## Requirements

- **macOS** with **Borumi**, its MCP server enabled (Settings > AI > Enable MCP), and the project open.
- **ffmpeg / ffprobe >= 6** (7+ recommended; libx264, prores_ks) on `PATH`.
- **python3 >= 3.9** with **numpy** and **Pillow** (the scripts re-run themselves under [uv](https://docs.astral.sh/uv/)
  when they are missing), **scipy** recommended, and **uv** for the CTC word alignment (torch/torchaudio 2.5.1 on Python
  3.12, pinned).
- **whisper-cli** ([whisper.cpp](https://github.com/ggml-org/whisper.cpp)) and a ggml model (`ggml-small.bin` is enough;
  `--whisper-model` or `$WHISPER_MODEL`).
- **node >= 18** (the Remotion animation kit), **gh**, and headless **Chrome** for proof captures.
- The two base repos, installed (see below).
- **Auphonic**: an account with credits and a preset with one WAV 24-bit output. The API key is read at run time from
  `$AUPHONIC_API_KEY` or the macOS Keychain (`security add-generic-password -U -a "$USER" -s auphonic-api-key -w '<key>'`).
  It is never stored in a config, a receipt or a log.
- Claude Code with the **Workflow** tool for the multi-agent steps (proof research, visual design, cut planning, final
  QA). Without it, Claude runs the same roles inline with parallel subagents.
- Optional: the Firecrawl and Higgsfield MCP tools (captures of pages that block headless Chrome; B-roll edits of real
  footage).

## Install

**1. The base repos** (default locations; every script also takes a flag or an environment variable):

```bash
git clone https://github.com/Samin12/edit-borumi-video.git ~/Projects/edit-borumi-video
mkdir -p ~/.borumi-edit
ln -s ~/Projects/edit-borumi-video/plugins/edit-borumi-video/skills/edit-borumi-video ~/.borumi-edit/skill   # or $BORUMI_EDIT_SKILL

git clone https://github.com/Samin12/opencutagent-borumi.git ~/.borumi-agent/plugin
bash ~/.borumi-agent/plugin/scripts/doctor.sh --install     # animation kit workspace -> ~/.borumi-agent/animation-kit
```

**The `velour` animation style** (dark canvas, panel mode, drift phase) is not part of opencutagent-borumi, so this repo
ships it (MIT) in `skills/proof-first-video-editor/templates/velour-style/`. Install it into the animation workspace once
and run its acceptance test (it never writes to your workspace):

```bash
SK=~/Projects/proof-first-video-editor/plugins/proof-first-video-editor/skills/proof-first-video-editor
KIT="${BORUMI_AGENT_HOME:-$HOME/.borumi-agent}/animation-kit"
[ -e "$KIT/styles/velour" ] || cp -R "$SK/templates/velour-style" "$KIT/styles/velour"
node ~/.borumi-agent/plugin/scripts/job.mjs typecheck
python3 "$SK/scripts/style/velour_check.py"      # 22 checks: panel, scaling, dot grid, drift, transparent FRONT renders
```

edit-borumi-video's chapter-card template and click come from a private repo. Without access,
`scripts/borumi/make_card.py` renders the silent white 1.6 s cards (in the layout `chapter_card.py` expects) and a
quiet click from Borumi's built-in click sound.

**2. This skill**, either:

(a) As a plugin:

```
/plugin marketplace add Samin12/proof-first-video-editor
/plugin install proof-first-video-editor@proof-first-video-editor
```

Plugin skills are namespaced (`/proof-first-video-editor:proof-first-video-editor <project>`); Claude also picks the
skill up from plain requests like "do a proof-first edit of My New Tutorial".

(b) As a plain skill folder (gives the short `/proof-first-video-editor` command):

```bash
git clone https://github.com/Samin12/proof-first-video-editor.git ~/Projects/proof-first-video-editor
mkdir -p ~/.claude/skills
ln -s ~/Projects/proof-first-video-editor/plugins/proof-first-video-editor/skills/proof-first-video-editor \
      ~/.claude/skills/proof-first-video-editor
```

`git pull` in the clone updates it.

**3. Audio dependencies and a self-test:**

```bash
cd ~/Projects/proof-first-video-editor/plugins/proof-first-video-editor/skills/proof-first-video-editor/scripts/audio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 selftest.py                 # 18 offline checks (no Borumi, no Auphonic); re-runs itself under .venv
python3 auphonic_api.py --check     # finds the key (never shows it), prints remaining credits
python3 auphonic_api.py --presets   # your presets and their UUIDs: export AUPHONIC_PRESET=<uuid>
```

## Usage

```
/proof-first-video-editor My New Tutorial
/proof-first-video-editor "My New Tutorial" ~/Videos/script.md        # with the script / notes
make a tighter version of My New Tutorial                             # the v-next cut from the delivered master
```

Claude follows `SKILL.md` step by step and asks only when it is truly blocked: an ambiguous project name, a missing
Auphonic preset or key, or anything that would change what the creator says or shows. All state lives next to the
project (`<name> exports/_work`, `<name> Agent/`), so an interrupted edit resumes where it stopped.

## Pipeline

| Step | What happens | Main pieces |
|---|---|---|
| 0. Setup | requirements, base repos, configs | `README.md`, `scripts/audio/selftest.py` |
| 1. Project | find, open and back up the project; project-wide snapshot | edit-borumi-video `find_project.py`, `scripts/borumi/snapshot.py`, `txrun.py` |
| 2. Cut | parallel cut editors (judge + adversarial verifier per scene group), applied last scene first | edit-borumi-video `01-research.js`, `02-first-pass-cuts.js`, `apply_cuts.py` |
| 3. Proof | one capture agent + one independent verifier per topic, a completeness critic, an assembled proof pack | `workflows/research-proof.js`, `templates/` |
| 4. Structure | dot-grid canvas, base layouts, chapter cards, gap fill, step badges, word timings | `scripts/borumi/canvas.py`, `base_layouts.py`, `make_card.py`, `chapter_card.py`, `card_lead_in.py`, `gap_fill.py`, `make_badges.py`, `step_badges.py`, `plans_from_kept.py`, `scene_words.py` |
| 5. Visuals | one motion designer per section, an independent checker, numbered-fix revisions, a continuity pass | `workflows/design-visuals.js`, `templates/designer-brief.md`, `templates/velour-style/` |
| 6. Placement | BEHIND / FRONT renders, one verified transaction per slot; whole-video coverage audit | `scripts/borumi/place_render.py`, `swap_render.py`, `coverage.py` |
| 7. Audio | per-source Auphonic mastering, timeline-exact remix, independent checks, full-mix pass, sync proof | `scripts/audio/master_sources.py`, `remix.py`, `verify_remix.py`, `edit_point_check.py`, `master_mix.py`, `check_sync.py` |
| 8. Captions + chapters | name fixes in the SRT, a `Chapters` block that lands on the cards | `scripts/deliver/fix_srt.py`, `chapters.py` |
| 9. QA | per-write checks, coverage, audio, delivered-file checks, independent reviewers (master mode), the creator | `coverage.py`, `workflows/final-qa.js` |
| 10. v-next (optional) | a shorter cut straight from the finished master, frame-exact, crossfaded mastered audio | `scripts/tighten/`, `workflows/tighten.js`, `workflows/final-qa.js` |
| 11. Delivery | versioned final, phone copy, SRT, chapters, `superseded/`, sha256 receipt; the report with accuracy, legal and privacy flags | `scripts/deliver/deliver.py` |

Safety is built in. Every write to Borumi is a locked transaction, run as a dry run first. Each one is checked with a
project-wide diff and rendered frames, and committed only when every check passes. The runner refuses to commit when
the creator edited the project in the app meanwhile, and it never undoes his edits. Nothing is ever deleted.

## Repo map

```
.claude-plugin/marketplace.json
plugins/proof-first-video-editor/
  .claude-plugin/plugin.json
  skills/proof-first-video-editor/
    SKILL.md                  the end-to-end playbook Claude follows (steps 0-11)
    references/
      creator-style.md        the look and the rules (layout, canvas, cards, chapters, proof over animation, flags)
      borumi-writer.md        Borumi timeline writer: safe/unsafe operations, quirks, recipes, thresholds, failure modes
      visual-system.md        proof research, BEHIND vs FRONT, sizing, timing, design grammar, checker loop, honesty, privacy
      audio.md                per-source mastering, timeline-exact remix, verification, stereo conventions, lessons
      delivery.md             captions, chapters, versioned delivery, receipts, lessons
      tighten.md              the v-next cut from the master: data shapes, playbook, lessons
    scripts/
      borumi/                 timeline writers + read-only checks (Python 3, stdlib + numpy/Pillow for frame checks)
      audio/                  Auphonic + remix + verification (Python 3 + numpy)
      tighten/                v-next cut (Python 3 + numpy, ffmpeg)
      deliver/                captions, chapters, delivery (Python 3 stdlib)
      style/                  velour_check.py: acceptance test of the animation style (stdlib + the kit's node)
    workflows/                research-proof.js, design-visuals.js, tighten.js, final-qa.js (Claude Code Workflow tool)
    templates/                workflow args examples, designer brief, section briefs, proof pack README + JSON schemas
      velour-style/           the velour animation style package (Remotion/TypeScript), installed into the kit
tests/tighten_smoke.sh        synthetic end-to-end test of the v-next scripts (no real media)
```

## Testing

From the repository root:

- `bash tests/tighten_smoke.sh`: the whole v-next pipeline on a synthetic 30 s master (about 5 s).
- `python3 plugins/proof-first-video-editor/skills/proof-first-video-editor/scripts/audio/selftest.py`: the DSP facts
  the audio pipeline relies on (sync, pan law, downmix gain, AAC round trip, crossfades, loudness), offline. Without
  numpy it re-runs itself under `scripts/audio/.venv`, `$PFVE_AUDIO_PYTHON` or `uv`.
- `python3 plugins/proof-first-video-editor/skills/proof-first-video-editor/scripts/style/velour_check.py --package
  plugins/proof-first-video-editor/skills/proof-first-video-editor/templates/velour-style`: the shipped style against an
  installed animation kit (needs opencutagent-borumi).
- Every script answers `--help` without Borumi, Auphonic or numpy (`bash tests/tighten_smoke.sh --help` and
  `scripts/tighten/frame.sh --help` too). The three libraries `bwlib.py`, `tlib.py` and `audio_lib.py` are not CLIs:
  run directly, they print their description.

The writers (`scripts/borumi/`) were exercised end to end against a mock Borumi bridge. The audio steps that call Borumi or
Auphonic, and the writers on a live project, carry the production logic of the REA run but have not been re-run on a live
project since they were packaged. Run them with `--dry` first, as the playbook says.

## Credits

Created by **Samin Yasar** ([@Samin12](https://github.com/Samin12)). It is distilled from the production edit of the
"REA" video and the edit before it. It builds on the author's
[edit-borumi-video](https://github.com/Samin12/edit-borumi-video) and
[opencutagent-borumi](https://github.com/Samin12/opencutagent-borumi). Third-party tools it drives:
Borumi (recorder, editor and MCP server), Auphonic (mastering), whisper.cpp and torchaudio's wav2vec2 (word timings),
Remotion (animation renders) and ffmpeg.

## License

MIT, Copyright (c) 2026 Samin Yasar. See [LICENSE](LICENSE).
