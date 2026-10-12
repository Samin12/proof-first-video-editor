#!/usr/bin/env python3
"""Build the YouTube "Chapters" block for a finished video from its chapter-card and sub-chapter positions.

usage: chapters.py --config CHAPTERS.json [--edl EDL.json] [--words WORDS.json ... | --words-dir DIR] [--duration-s S]
                   [--out youtube-chapters.txt] [--json DETAILS.json] [--strict] [--strict-style]
       chapters.py --card 104963="Why It Matters" --card 516633="Level 1: Setting It Up" --sub 229420="How It Works" ...
       chapters.py --from-borumi WORK [--config OVERRIDES.json] ...   (card times read live, read-only; needs the
                                                                      edit-borumi-video skill and its bridge)

Output (the creator's description format; "Chapters" header, no dashes, no emoji):
    Chapters
    0:00 Intro
    1:45 Why It Matters
    3:49 What Is Reverse Engineering
    8:37 Level 1: What It Can't Do
    ...
  M:SS per line, H:MM:SS past an hour.

Rounding (the reason this script exists):
  * CARD chapters: card start + lead-in (default 1 ms: the card take starts 1 ms after its scene start), mapped to the
    delivered timeline, rounded UP to the next whole second. YouTube seeks to the exact second you write: a card at
    1:44.963 written as "1:44" plays the tail of the previous sentence first; "1:45" lands ON the card (a 1.6 s card
    always contains the rounded-up second). Checked: the second must fall inside [card start, card start + card_ms).
    (edit-borumi-video's youtube_chapters.py floors the time; do not paste its output into the description.)
  * SUB-chapters (a topic change with no card): their sentence start, rounded DOWN, so the click lands just before
    the first word, never mid-word. With --words the start snaps to the first kept word at or after (start - 50 ms).
  * A card in the first second replaces "0:00 Intro" (the video opens with a card).

YouTube rules checked (a failure makes YouTube ignore ALL chapters): first chapter at 0:00, at least 3 chapters,
every chapter at least 10 s long (including the last one, when the duration is known), no two in the same second.
Style lint (warnings): "Intro" first, "Why It Matters" second, "Level N: Title" for the levels, Title Case with small
words lowercase (a, an, the, and, or, of, to, on, in, at, by, for, vs), no dashes/emoji, a sane chapter count
(8-9 for a 20-25 min video, about one per 5 min for long courses).

CHAPTERS.json (all times are ms on the MASTER timeline, i.e. the timeline the card positions were read from):
  {
    "intro_title": "Intro",            optional (default Intro)
    "card_ms": 1601,                   card length on the timeline (1.6 s render + 1 ms lead-in)
    "card_lead_ms": 1,                 the card's first frame is this far after its recorded start
    "duration_ms": 1202536,            master duration (optional; used for the last-chapter check without an EDL)
    "cards_from": "final_positions.json",  optional: read "cards" (and duration_ms) from this file (relative to the
                                           config), e.g. a positions file written from the live project before export
    "cards": [ {"start_ms": 104963, "title": "Why This Matters", "chapter_title": "Why It Matters"},
               {"start_ms": 516633, "title": "What It Can't Do", "level": 1, "name": "02-what-it-cant-do"} ],
    "chapter_titles": {"Why This Matters": "Why It Matters"},   by card name or card title (card text != chapter text)
    "levels": {"02-what-it-cant-do": 1},                         by card name or title -> "Level 1: <title>"
    "subs": [ {"start_ms": 229420, "title": "What Is Reverse Engineering"} ]
  }
  Card positions must be read from the project right before export (scene/layout starts), never from apply receipts
  written earlier: any later cut or insert moves them.
  The v-next config (scripts/tighten tighten.json) is accepted as is: card "chapter" = chapter title (null = this
  card is not a chapter), "chapter_at_ms" = time to use instead of start_ms, "sub_chapters": [{"at_ms", "title"}],
  "words_dir": folder of <scene>.json word lists (used when no --words is given; *_sentences.json are skipped).

--edl (only for a v-next cut made from the finished master): maps master times to the new cut. Accepted shapes:
  {"keep_frames": [[f0, f1], ...], "fps": 60}           the EDL a frame-exact rebuild writes (kept [f0, f1) pieces)
  {"keep_ms": [[a, b], ...]}                           kept pieces in ms
  {"remove_ms": [[a, b], ...]} or {"ranges": [{"start_ms": a, "end_ms": b}, ...]}   removed ranges (+ optional "fps"
                                                       to snap them to frames the way the rebuild did, "duration_ms")
  A time inside a removed range maps to the start of the next kept piece (and is reported).
--words: word timings on the master timeline: [[start_ms, end_ms, "word"], ...], [{"start_ms","end_ms","word"}...],
  Borumi get_transcript output ({"layers":[{"segments":[{"items":[[s,e,text]]}]}]}), or whisper-cli -ojf JSON
  (token offsets; only valid if the whisper run was on the master). Repeatable (one file per scene is fine).

Exit: 0 ok; 1 a YouTube rule / card-landing problem with --strict (or a style issue with --strict-style); 2 bad input.
Requires: Python 3.8+ (stdlib only).
"""
import argparse
import json
import math
import os
import re
import sys

SMALL_WORDS = {"a", "an", "the", "and", "or", "nor", "but", "of", "to", "on", "in", "at", "by", "for", "vs", "vs.", "via"}
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
DASH_RE = re.compile(r"[‒-―]|\s-\s|\s--\s")
LEVEL_RE = re.compile(r"^Level (\d+): \S")


def die(msg, code=2):
    sys.stderr.write("chapters.py: %s\n" % msg)
    sys.exit(code)


def fmt(sec):
    sec = int(sec)
    h, m, s = sec // 3600, (sec // 60) % 60, sec % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        die("cannot read %s: %s" % (path, e))


# ------------------------------------------------------------------ timeline mapping

class Timeline:
    """Master ms -> delivered ms. Identity without an EDL."""

    def __init__(self, keep=None):
        self.identity = keep is None
        self.pieces, off = [], 0.0
        for a, b in keep or []:
            if b > a:
                self.pieces.append((float(a), float(b), off))
                off += b - a
        self.total = off

    def removed(self, t):
        return False if self.identity else not any(a <= t < b for a, b, _ in self.pieces)

    def map(self, t):
        if self.identity:
            return float(t)
        for a, b, o in self.pieces:
            if a <= t < b:
                return o + (t - a)
            if t < a:
                return o  # inside a removed range: snap to the next kept piece
        return self.total


def load_edl(path):
    d = load_json(path)
    if not isinstance(d, dict):
        die("%s: EDL must be a JSON object" % path)
    if "keep_frames" in d:
        fps = float(d.get("fps") or die("%s: keep_frames needs fps" % path))
        return Timeline([(s * 1000.0 / fps, e * 1000.0 / fps) for s, e in d["keep_frames"]])
    if "keep_ms" in d:
        return Timeline(sorted((float(a), float(b)) for a, b in d["keep_ms"]))
    rem = d.get("remove_ms") or d.get("ranges")
    if rem is None:
        die("%s: expected keep_frames+fps, keep_ms, remove_ms or ranges" % path)
    rem = [(r["start_ms"], r["end_ms"]) if isinstance(r, dict) else (r[0], r[1]) for r in rem]
    fps = d.get("fps")
    if fps:  # snap to the frame grid exactly like the rebuild did: round(ms * fps / 1000)
        rem = [(round(a * fps / 1000.0) * 1000.0 / fps, round(b * fps / 1000.0) * 1000.0 / fps) for a, b in rem]
    end = float(d["duration_ms"]) if d.get("duration_ms") else float("inf")
    rem = sorted((max(0.0, float(a)), min(end, float(b))) for a, b in rem if b > a)
    keep, cur = [], 0.0
    for a, b in rem:
        if a > cur:
            keep.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        keep.append((cur, end))
    return Timeline(keep)


def load_words(paths):
    """-> sorted [(start_ms, end_ms, word)]."""
    words = []

    def take(items):
        for it in items:
            if isinstance(it, (list, tuple)) and len(it) >= 3:
                words.append((float(it[0]), float(it[1]), str(it[2])))
            elif isinstance(it, dict) and "start_ms" in it:
                words.append((float(it["start_ms"]), float(it["end_ms"]), str(it.get("word") or it.get("text") or "")))

    for p in paths:
        d = load_json(p)
        if isinstance(d, list):
            take(d)
        elif isinstance(d, dict) and "words" in d:
            take(d["words"])
        elif isinstance(d, dict) and "layers" in d:  # Borumi get_transcript shape
            for L in d["layers"]:
                for s in L.get("segments") or []:
                    take(s.get("items") or [])
        elif isinstance(d, dict) and "transcription" in d:  # whisper-cli -ojf: merge sub-word tokens into words
            for seg in d["transcription"]:
                cur = None
                for tok in seg.get("tokens") or []:
                    txt = tok.get("text", "")
                    off = tok.get("offsets") or {}
                    if not txt.strip() or txt.strip().startswith("[_") or "from" not in off:
                        continue
                    if cur is None or txt.startswith(" "):
                        if cur:
                            words.append(tuple(cur))
                        cur = [float(off["from"]), float(off["to"]), txt.strip()]
                    else:
                        cur[1], cur[2] = float(off["to"]), cur[2] + txt
                if cur:
                    words.append(tuple(cur))
        else:
            die("%s: unrecognised words format" % p)
    words.sort()
    return words


# ------------------------------------------------------------------ inputs

def parse_cli_points(vals, flag):
    out = []
    for v in vals or []:
        if "=" not in v:
            die("%s expects MS=TITLE, got %r" % (flag, v))
        ms, title = v.split("=", 1)
        try:
            out.append({"start_ms": float(ms), "title": title.strip()})
        except ValueError:
            die("%s: %r is not a number of ms" % (flag, ms))
    return out


def from_borumi(work, skill_dir, project_id, intro_title):
    """Card starts read live from the Borumi project via edit-borumi-video (read-only calls)."""
    scripts = os.path.join(os.path.expanduser(skill_dir), "scripts")
    if not os.path.isfile(os.path.join(scripts, "youtube_chapters.py")):
        die("edit-borumi-video not found at %s (set --borumi-skill or $BORUMI_EDIT_SKILL)" % skill_dir)
    sys.path.insert(0, scripts)
    import applylib as al  # noqa: E402  (edit-borumi-video)
    import youtube_chapters as yc  # noqa: E402

    work = os.path.abspath(os.path.expanduser(work))
    pid = al.project_id(argparse.Namespace(project_id=project_id), al.load_project(work))
    ch, dur, guessed = yc.chapters(work, pid, intro_title)
    cards = [{"start_ms": float(t), "title": title, "source": why} for t, title, why in ch if why != "start"]
    return cards, float(dur), [(t, title, why) for t, title, why in guessed]


def words_in_dir(d):
    return sorted(os.path.join(d, f) for f in os.listdir(d)
                  if f.endswith(".json") and not f.endswith("_sentences.json") and not f.startswith("_"))


def normalize_cards(cards):
    """Accept the tighten.json card shape too: "chapter" (null = not a chapter), "chapter_at_ms"."""
    out = []
    for c in cards:
        c = dict(c)
        if "chapter" in c:
            if c["chapter"] is None:
                continue
            c.setdefault("chapter_title", c["chapter"])
        if c.get("chapter_at_ms") is not None:
            c["start_ms"] = c["chapter_at_ms"]
        if "start_ms" not in c:
            die("every card needs start_ms: %r" % c)
        out.append(c)
    return out


def resolve_title(card, chapter_titles, levels):
    keys = [k for k in (card.get("name"), card.get("title")) if k]
    title = card.get("chapter_title") or next((chapter_titles[k] for k in keys if k in chapter_titles), None) \
        or card.get("title")
    if not title:
        die("card at %s ms has no title" % card.get("start_ms"))
    lvl = card.get("level")
    if lvl is None:
        lvl = next((levels[k] for k in keys if k in levels), None)
    if lvl is not None and not LEVEL_RE.match(title):
        title = "Level %d: %s" % (int(lvl), title)
    return title


# ------------------------------------------------------------------ build + checks

def build(cards, subs, tl, words, intro_title="Intro", card_ms=1601, lead_ms=1, sub_tol_ms=50):
    """-> (chapters, issues). chapters: [{sec, title, kind, src_ms, mapped_ms}] sorted; issues: [(level, msg)]."""
    issues = []
    ch = [{"sec": 0, "title": intro_title, "kind": "intro", "src_ms": 0.0, "mapped_ms": 0.0}]
    for c in cards:
        t0 = float(c["start_ms"])
        first = t0 + lead_ms
        if tl.removed(first):
            issues.append(("rule", "card %r at %.0f ms falls inside a removed range" % (c["title"], t0)))
        m = round(tl.map(first), 3)
        sec = int(math.ceil(m / 1000.0))
        card_start = round(tl.map(t0), 3)
        if not (m <= sec * 1000.0 < card_start + card_ms):
            issues.append(("rule", "card %r: %s does not land on the card (card %.0f-%.0f ms)" % (
                c["title"], fmt(sec), card_start, card_start + card_ms)))
        ch.append({"sec": sec, "title": c["title"], "kind": "card", "src_ms": t0, "mapped_ms": m})
    for s in subs:
        t0 = float(s["start_ms"])
        if words:
            w = next((w for w in words if w[0] >= t0 - sub_tol_ms and not tl.removed((w[0] + w[1]) / 2.0)), None)
            if w is None:
                issues.append(("rule", "sub-chapter %r at %.0f ms: no kept word at or after it" % (s["title"], t0)))
                continue
            start = w[0]
        else:
            start = t0
            if tl.removed(t0):
                issues.append(("rule", "sub-chapter %r at %.0f ms falls inside a removed range" % (s["title"], t0)))
        m = round(tl.map(start), 3)
        ch.append({"sec": int(math.floor(m / 1000.0)), "title": s["title"], "kind": "sub", "src_ms": t0, "mapped_ms": m})
    ch.sort(key=lambda c: (c["sec"], c["mapped_ms"]))
    # the video opens with a card: no separate Intro line, the card becomes 0:00
    if len(ch) > 1 and ch[1]["kind"] == "card" and ch[1]["mapped_ms"] < 1000:
        ch = ch[1:]
        ch[0]["sec"] = 0
    return ch, issues


def check(ch, duration_ms=None):
    issues = []
    if not ch or ch[0]["sec"] != 0:
        issues.append(("rule", "the first chapter must be at 0:00"))
    if len(ch) < 3:
        issues.append(("rule", "YouTube needs at least 3 chapters (have %d)" % len(ch)))
    for a, b in zip(ch, ch[1:]):
        if b["sec"] == a["sec"]:
            issues.append(("rule", "%r and %r land in the same second %s" % (a["title"], b["title"], fmt(a["sec"]))))
        elif b["sec"] - a["sec"] < 10:
            issues.append(("rule", "chapter %r is only %d s long (YouTube minimum 10 s)" % (a["title"], b["sec"] - a["sec"])))
    if duration_ms and math.isfinite(duration_ms) and ch:
        last = ch[-1]
        if duration_ms / 1000.0 - last["sec"] < 10:
            issues.append(("rule", "last chapter %r is only %.1f s long (YouTube minimum 10 s)" % (
                last["title"], duration_ms / 1000.0 - last["sec"])))
        if duration_ms > 0 and ch[-1]["sec"] * 1000 > duration_ms:
            issues.append(("rule", "chapter %r is after the end of the video" % last["title"]))
    return issues


def lint(ch, duration_ms=None, intro_title="Intro"):
    out = []
    titles = [c["title"] for c in ch]
    if titles and ch[0]["kind"] == "intro" and titles[0] != "Intro":
        out.append(("style", "first chapter is %r (house style: Intro)" % titles[0]))
    if len(titles) > 1 and titles[1] != "Why It Matters" and ch[0]["kind"] == "intro":
        out.append(("style", "second chapter is %r (house style: Why It Matters)" % titles[1]))
    for c in ch:
        t = c["title"]
        if EMOJI_RE.search(t) or DASH_RE.search(t):
            out.append(("style", "%r: no dashes or emoji in chapter titles" % t))
        if re.match(r"(?i)^level\s*\d", t) and not LEVEL_RE.match(t):
            out.append(("style", "%r: write levels as 'Level N: Title'" % t))
        words = t.split()
        after_colon = True
        for i, w in enumerate(words):
            core = re.sub(r"^[^\w]+|[^\w']+$", "", w)
            if not core:
                continue
            if not after_colon and core.lower() in SMALL_WORDS and core[0].isupper():
                out.append(("style", "%r: small word %r should be lowercase in Title Case" % (t, core)))
            elif (not after_colon and core.lower() not in SMALL_WORDS and core[0].islower()
                  and not any(ch_.isupper() for ch_ in core[1:])):
                out.append(("style", "%r: %r should be capitalised (Title Case)" % (t, core)))
            elif after_colon and core[0].islower() and not any(ch_.isupper() for ch_ in core[1:]):
                out.append(("style", "%r: %r starts the title, capitalise it" % (t, core)))
            after_colon = w.endswith(":")
    if duration_ms and math.isfinite(duration_ms):
        mins = duration_ms / 60000.0
        # loose on purpose: 9 chapters in a 14-minute cut was accepted; this only catches the extremes
        if mins >= 8 and not (mins / 6.0 <= len(ch) <= mins / 1.25):
            out.append(("style", "%d chapters for a %.0f-minute video (house style: 8-9 for a 20-25 min video, "
                                 "about one per 5 min for long courses)" % (len(ch), mins)))
    return out


def render(ch, header="Chapters"):
    lines = ([header] if header else []) + ["%s %s" % (fmt(c["sec"]), c["title"]) for c in ch]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="chapters JSON (cards, subs, titles, levels)")
    ap.add_argument("--card", action="append", metavar="MS=TITLE", help="a chapter card (repeatable)")
    ap.add_argument("--sub", action="append", metavar="MS=TITLE", help="a sub-chapter (repeatable)")
    ap.add_argument("--from-borumi", metavar="WORK", help="read card starts live from the Borumi project of WORK")
    ap.add_argument("--borumi-skill", default=os.environ.get("BORUMI_EDIT_SKILL", "~/.borumi-edit/skill"),
                    help="edit-borumi-video install (default $BORUMI_EDIT_SKILL or ~/.borumi-edit/skill)")
    ap.add_argument("--project-id", help="with --from-borumi: override WORK/project.json id")
    ap.add_argument("--edl", help="map master times through this cut list (v-next cut)")
    ap.add_argument("--words", action="append", help="word timings on the master timeline (repeatable)")
    ap.add_argument("--words-dir", help="folder of word-timing JSON files (all *.json except *_sentences.json)")
    ap.add_argument("--duration-s", type=float, help="duration of the DELIVERED video (for the last-chapter check)")
    ap.add_argument("--intro-title", help="default from config or 'Intro'")
    ap.add_argument("--card-ms", type=float, help="card length on the timeline (default config or 1601)")
    ap.add_argument("--card-lead-ms", type=float, help="card first frame offset (default config or 1)")
    ap.add_argument("--header", default="Chapters", help="first line (default 'Chapters')")
    ap.add_argument("--no-header", action="store_true")
    ap.add_argument("--out", help="write the block here (default: stdout only)")
    ap.add_argument("--json", help="write chapter details + issues here")
    ap.add_argument("--strict", action="store_true", help="exit 1 on a YouTube rule or card-landing problem")
    ap.add_argument("--strict-style", action="store_true", help="exit 1 on style lint too")
    a = ap.parse_args()

    cfg = load_json(a.config) if a.config else {}
    if not isinstance(cfg, dict):
        die("--config must be a JSON object")
    if a.words_dir:
        if not os.path.isdir(a.words_dir):
            die("--words-dir not found: %s" % a.words_dir)
        a.words = (a.words or []) + words_in_dir(a.words_dir)
    cards = list(cfg.get("cards") or [])
    duration_ms = cfg.get("duration_ms")
    if cfg.get("cards_from"):
        src = load_json(os.path.join(os.path.dirname(os.path.abspath(a.config)), cfg["cards_from"]))
        cards = list((src.get("cards") if isinstance(src, dict) else src) or []) + cards
        if duration_ms is None and isinstance(src, dict):
            duration_ms = src.get("duration_ms")
    intro = a.intro_title or cfg.get("intro_title") or "Intro"
    issues = []
    if a.from_borumi:
        cards, duration_ms, guessed = from_borumi(a.from_borumi, a.borumi_skill, a.project_id, intro)
        for t, title, why in guessed:
            issues.append(("rule", "card %r at %.0f ms was not identified from a receipt (%s)" % (title, t, why)))
    cards = normalize_cards(cards) + parse_cli_points(a.card, "--card")
    subs = list(cfg.get("subs") or []) + [{"start_ms": s["at_ms"], "title": s.get("title")}
                                          for s in cfg.get("sub_chapters") or [] if "at_ms" in s]
    subs += parse_cli_points(a.sub, "--sub")
    if not cards and not subs:
        die("no cards or sub-chapters given (use --config, --card/--sub or --from-borumi)")
    ct, lv = cfg.get("chapter_titles") or {}, cfg.get("levels") or {}
    cards = [dict(c, title=resolve_title(c, ct, lv)) for c in cards]
    if not a.words and cfg.get("words_dir") and a.config:
        wd = os.path.join(os.path.dirname(os.path.abspath(a.config)), os.path.expanduser(cfg["words_dir"]))
        if os.path.isdir(wd):
            a.words = words_in_dir(wd)
    for s in subs:
        if "start_ms" not in s or not s.get("title"):
            die("every sub-chapter needs start_ms and title: %r" % s)

    tl = load_edl(a.edl) if a.edl else Timeline()
    words = load_words(a.words) if a.words else []
    card_ms = a.card_ms if a.card_ms is not None else float(cfg.get("card_ms", 1601))
    lead = a.card_lead_ms if a.card_lead_ms is not None else float(cfg.get("card_lead_ms", 1))
    ch, bi = build(cards, subs, tl, words, intro, card_ms, lead)
    issues += bi
    if a.duration_s:
        dur = a.duration_s * 1000.0
    elif not tl.identity and math.isfinite(tl.total) and tl.total > 0:
        dur = tl.total
    elif tl.identity and duration_ms:
        dur = float(duration_ms)
    else:
        dur = None
    issues += check(ch, dur)
    style = lint(ch, dur, intro)

    text = render(ch, None if a.no_header else a.header)
    for c in ch:
        how = {"intro": "start", "card": "card, rounded up", "sub": "sub-chapter, rounded down"}[c["kind"]]
        sys.stderr.write("%-48s <- %s (master %.0f ms -> %.0f ms)\n" % (
            "%s %s" % (fmt(c["sec"]), c["title"]), how, c["src_ms"], c["mapped_ms"]))
    for lvl, msg in issues:
        sys.stderr.write("PROBLEM: %s\n" % msg)
    for lvl, msg in style:
        sys.stderr.write("style: %s\n" % msg)
    sys.stdout.write(text)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stderr.write("wrote %s\n" % a.out)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"chapters": ch, "duration_ms": dur, "edl": a.edl, "problems": [m for _, m in issues],
                       "style": [m for _, m in style], "text": text}, fh, indent=1)
    if (a.strict and issues) or (a.strict_style and (issues or style)):
        sys.exit(1)


if __name__ == "__main__":
    main()
