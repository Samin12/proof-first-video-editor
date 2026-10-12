<!--
TEMPLATE: the README.md of a proof pack (<proof_dir>/README.md), written by the assembler/verifier of
workflows/research-proof.js. Keep the section order: the creator reads "Fix before publishing" first, the designers read
"All claims in narration order", and the per-claim details back both. Replace every <...>. The example rows describe a
FICTIONAL video and are only there to show the tone and the level of detail. Delete this comment.

Layout of a proof pack:

  <proof_dir>/
    README.md                 this file
    proof-index.json          the same data, machine-readable (templates/proof-index.schema.json)
    01-<topic>/               one folder per capture group (numbered in narration order)
      sources.json            one record per claim (templates/sources.schema.json)
      NN-<what>-full.png      the whole viewport, as captured
      NN-<what>-crop.png      a tight readable crop (>= 1600 px wide where possible)
      NN-<what>-highlight.png the crop + ONE accent box (ffmpeg drawbox, ~6 px) around the proof text
      raw/                    text evidence: page markdown, API JSON, PDF text
    02-<topic>/ ...
    09-made-graphics/         optional: graphics built ONLY from verified numbers (math tables, comparisons)
    90-gaps/                  optional: claims the completeness pass found uncovered
    _contact-sheets/          one ffmpeg tile per folder, for a quick look

Naming: NN is the capture order inside the folder (01, 02, ...); <what> is a short kebab/snake slug of what the page
shows ("repo_header", "pricing_launch_price"). Re-shoots at a higher device scale get a suffix (`_2x_full.png`).
-->
# <Video title> proof pack: verifier index

Independent check of all <N> proof folders, <date> (re-checks run <start>Z to <end>Z). Every best visual below was opened and
read, and the key numbers were re-checked live. Machine-readable version: `proof-index.json`.

Order follows the narration (<which transcript / word files>). Times are approximate positions on the current timeline and
shift with every cut.

Legend: ✓ matches · ✗ wrong as said · ~ partly right (use the honest line) · – context only

## Fix before publishing

Every ✗ and ~ claim, plus anything with a legal or privacy risk. The on-screen visual already shows the honest source; these
lines are for the creator to re-record, re-word in the description, or consciously keep.

| Time | They say | | Problem | Honest line |
|---|---|---|---|---|
| 0:12 | "and it's completely free" | ✗ | The pricing page shows a 14-day refund, not a free plan or trial. | "You can try it for two weeks, and they'll refund you if it's not for you." |
| 3:40 | "it made over 20 billion last year" | ✓ (rounded) | The annual report gives $20.6B for fiscal 2025. | "They reported $20.6 billion in revenue for fiscal 2025." |
| 7:05 | "you can resell whatever you build with it" | ✗ | The tool's license covers the tool itself; its disclaimer puts authorization on the user. Legal risk, not a verdict. | "You can customize what you build, but check you have the rights before you sell anything." |

## All claims in narration order

| # | Time | They say | | Best on-screen file | Source |
|---|---|---|---|---|---|
| 1 | 0:00 | "This open-source project called <name>" | ✓ | `01-the-repo/01_repo_header_highlight.png` | https://github.com/<owner>/<repo> |
| 2 | 0:12 | "and it's completely free" | ✗ | `03-pricing/02_faq_refund_2x_highlight.png` | https://<vendor>/pricing |
| ... | | | | | |

### Scope items (not narrated)

Context a designer may need (release dates, license files, tool catalogs), marked –  or ✓.

| # | What | | Best on-screen file | Source |
|---|---|---|---|---|
| 40 | Release date of <tool> | ✓ | `02-tools/02_release_note_highlight.png` | <url> |

## Details per claim

### 1. repo-01-identity ✓

- **Says (0:00):** This open-source project called <name>
- **Verified value:** <exact page text, figures, and for live counters: the reading AND its time, e.g. "Star 102k at 22:53Z;
  API at 23:01Z: 102,186">
- **Best visual:** `01-the-repo/01_repo_header_highlight.png` (also: `01-the-repo/02_readme_title_highlight.png`)
- **Source:** <url> (captured <ISO time>)
- **Verifier re-check:** <what was re-checked, how (API, second browser, PDF text), when>
- **Suggested line:** <safe on-screen wording, e.g. "100K+ stars" for a counter that moves by the minute>
- **Notes:** <traps: "new" is relative (created N months ago); counts rise by the minute so round on screen>

### 2. pricing-01-free-claim ✗

- **Says (0:12):** ...
- **Verified value:** FAQ (live <time>): "Is there a free trial? No, but there is a 14-day refund."
- **Best visual:** ...
- **Honest line:** ...
- **Do not show:** <e.g. the checkout panel with the creator's account email>

## What the verifier changed

- Re-shot at 2x/3x with tight crops where the originals were 1x captures with small text: `<files>`. Originals kept.
- New evidence added: `<files>` (new items in `<folder>/sources.json`).
- Best visual changed for `<id>` (the original was <W>x<H>, too tall for the panel).
- Captures used an isolated headless Chrome (fresh temp profile, own port), killed by PID with the profile deleted.

## Re-checked live (all match the pack)

- <source>: <figure> at <time> (screenshot shows <rounded figure>).
- ...

## Gaps and cautions

- **Questions for the creator:** <e.g. which of two same-named apps is in the footage, and how they got it>.
- **Legal risk, not a verdict:** <quote the clause; suggest a reword or a qualified review>.
- **Privacy:** <footage or captures that show third-party names, meetings, members, emails; the creator's paid-community
  price never goes on screen (show the real community instead)>.
- **Not captured:** <claim> (why: blocked, gated, too general to prove).
- **Rendering caveats:** <e.g. "rendered from the official PDF because the site blocks headless Chrome"; "1x capture
  because Cloudflare blocked Chrome; text small but readable">.
- **Transcript slips to check in the audio:** <words the transcript may have misheard (product names, the creator's
  community name)>.
