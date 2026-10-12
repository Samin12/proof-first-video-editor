#!/usr/bin/env python3
"""Map the master's captions and YouTube chapters through the cut (build_cut.py's edl.json).

usage: build_captions.py --config tighten.json EDL.json IN.srt OUT.srt [--chapters-out youtube-chapters.txt]
                         [--words-dir DIR] [--min-cue-ms 300]

IN.srt is the (already fixed) caption file of the MASTER; EDL.json = OUT.mp4.work/edl.json ({"keep_frames", "fps"}).
Captions:
  * cues fully inside removed ranges are dropped;
  * partly cut cues lose exactly the words whose whisper midpoint fell in a removed range: the cue's tokens are aligned
    to the word list (difflib, normalised a-z0-9'), unmatched tokens inherit their left neighbour's state;
  * a removed run that ended a sentence hands its end punctuation (. ? !) to the kept word before it
    ("...build your own versions." instead of "...build your own versions");
  * the first kept word is capitalised ONLY when the cut removed the start of a sentence just before it (no more
    mid-sentence capitals);
  * the trimmed cue spans its kept words (60 ms lead, 120 ms tail, inside the original cue), times are mapped through
    the kept pieces (a time inside a removed range snaps to the next kept piece);
  * cues are sorted, never overlap (end <= next start - 1 ms) and last at least --min-cue-ms.
Chapters (config "cards" with a "chapter" title, plus "sub_chapters"):
  * "0:00 <intro_title>" first;
  * each card's start (or its "chapter_at_ms") mapped through the cut and rounded UP to the next second, so a click
    on the chapter lands on the card, not on the end of the previous sentence;
  * each sub-chapter at the first kept word starting at/after at_ms - 50, rounded DOWN;
  * header "Chapters" (the same header scripts/deliver/chapters.py writes; --chapters-header to change it), then
    "M:SS Title" lines (H:MM:SS past an hour);
  * warns when two chapters are < 10 s apart or there are < 3 chapters (YouTube ignores the list then).
Run the creator's name-fix pass (scripts/deliver/fix_srt.py) on OUT.srt afterwards if the master's SRT was not fixed.
No numpy needed.
"""
import argparse
import difflib
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tlib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl"); ap.add_argument("in_srt"); ap.add_argument("out_srt")
    ap.add_argument("--config", required=True, help="tighten.json (words_dir, cards, sub_chapters, intro_title)")
    ap.add_argument("--chapters-out", help="write the YouTube chapter block here")
    ap.add_argument("--chapters-header", default="Chapters",
                    help="first line of the chapter block (default 'Chapters', as chapters.py; '' = no header line)")
    ap.add_argument("--words-dir", help="default: config words_dir")
    ap.add_argument("--min-cue-ms", type=float, default=300)
    a = ap.parse_args()
    cfg = tlib.load_config(a.config)

    with open(a.edl, encoding="utf-8") as fh:
        edl = json.load(fh)
    FPS = edl.get("fps", cfg["fps"])
    keep = edl["keep_frames"]
    pieces, off = [], 0.0
    for s, e in keep:
        pieces.append((s * 1000 / FPS, e * 1000 / FPS, off)); off += (e - s) * 1000 / FPS
    TOTAL = off

    def removed(t):
        return not any(pa <= t < pb for pa, pb, _ in pieces)

    def mapt(t):
        for pa, pb, o in pieces:
            if pa <= t < pb: return o + (t - pa)
            if t < pa: return o  # inside a removed range: snap to the next kept piece
        return TOTAL

    words = tlib.all_words(cfg, a.words_dir)
    norm = lambda s: re.sub(r"[^a-z0-9']", "", s.lower())

    def ts(ms):
        ms = max(0, int(round(ms))); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    def pt(s):
        h, m, r = s.replace(".", ",").split(":"); sec, ms = r.split(","); return ((int(h) * 60 + int(m)) * 60 + int(sec)) * 1000 + int(ms)

    with open(a.in_srt, encoding="utf-8-sig") as fh:
        raw = fh.read().replace("\r\n", "\n")
    blocks = [b for b in raw.strip().split("\n\n") if "-->" in b]
    out, dropped, edited = [], 0, 0
    for b in blocks:
        lines = b.split("\n"); tl = [i for i, l in enumerate(lines) if "-->" in l][0]
        s, e = [pt(x.strip().split(" ")[0]) for x in lines[tl].split("-->")]
        text = " ".join(lines[tl + 1:]).strip()
        ws = [w for w in words if w[0] < e and w[1] > s]
        toks = text.split()
        if not ws:
            if removed((s + e) / 2): dropped += 1; continue
            out.append((mapt(s), mapt(e), text)); continue
        wrem = [removed((w[0] + w[1]) / 2) for w in ws]
        if not any(wrem):
            out.append((mapt(s), mapt(e), text)); continue
        if all(wrem):
            dropped += 1; continue
        sm = difflib.SequenceMatcher(a=[norm(t) for t in toks], b=[norm(str(w[2])) for w in ws], autojunk=False)
        tokrem = [None] * len(toks)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size): tokrem[blk.a + k] = wrem[blk.b + k]
        last = False
        for i in range(len(toks)):
            if tokrem[i] is None: tokrem[i] = last
            last = tokrem[i]
        # a removed run that ended a sentence hands its end punctuation to the kept word before it
        for i in range(len(toks)):
            if not tokrem[i] and i + 1 < len(toks) and tokrem[i + 1] and not toks[i][-1:] in ".?!,;:":
                j = i + 1
                while j < len(toks) and tokrem[j]: j += 1
                if toks[j - 1][-1:] in ".?!": toks[i] = toks[i] + toks[j - 1][-1]
        kept = [t for t, r in zip(toks, tokrem) if not r]
        if not kept: dropped += 1; continue
        kw = [w for w, r in zip(ws, wrem) if not r]
        ns, ne = mapt(max(s, kw[0][0] - 60)), mapt(min(e, kw[-1][1] + 120))
        t2 = " ".join(kept)
        # capitalize only when the cut removed the START of a sentence just before the first kept word
        fk = tokrem.index(False)
        if fk > 0:
            k0 = fk
            while k0 > 0 and tokrem[k0 - 1]: k0 -= 1          # start of the removed run
            prev_end = toks[k0 - 1] if k0 > 0 else (out[-1][2].split()[-1] if out else ".")
            if prev_end[-1:] in ".?!" and not toks[fk - 1].endswith((".", "?", "!")):
                t2 = t2[0].upper() + t2[1:]
        out.append((ns, ne, t2)); edited += 1
    out.sort()
    fixed = []
    for i, (s, e, t) in enumerate(out):
        if i + 1 < len(out): e = min(e, out[i + 1][0] - 1)
        if e - s < a.min_cue_ms: e = s + a.min_cue_ms
        fixed.append((s, e, t))
    with open(a.out_srt, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(fixed, 1):
            f.write(f"{i}\n{ts(s)} --> {ts(e)}\n{t}\n\n")
    print(f"captions: {len(blocks)} in, {len(fixed)} out, {dropped} dropped, {edited} trimmed; cut length {TOTAL / 1000:.2f} s")

    if not a.chapters_out:
        return
    ch = [(0.0, cfg["intro_title"], "card")]
    for c in cfg["cards"]:
        name = c.get("chapter", c.get("title"))
        if not name:
            continue
        t = c.get("chapter_at_ms") if c.get("chapter_at_ms") is not None else c["start_ms"]
        ch.append((math.ceil(mapt(t + 1) / 1000), name, "card"))
    for sc in cfg.get("sub_chapters", []):
        t, name = sc["at_ms"], sc["title"]
        nxt = next((w for w in words if w[0] >= t - 50 and not removed((w[0] + w[1]) / 2)), None)
        if nxt: ch.append((math.floor(mapt(nxt[0]) / 1000), name, "sub"))
        else: print(f"WARNING: sub-chapter {name!r}: no kept word after {t} ms, skipped")
    ch.sort()

    def fmt(s):
        s = int(s)
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    txt = (a.chapters_header + "\n" if a.chapters_header else "") + "\n".join(f"{fmt(s)} {n}" for s, n, _ in ch) + "\n"
    with open(a.chapters_out, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)
    gaps = [ch[i + 1][0] - ch[i][0] for i in range(len(ch) - 1)]
    if gaps and min(gaps) < 10: print("WARNING: chapters closer than 10 s (YouTube needs >= 10 s)")
    if len(ch) < 3: print("WARNING: fewer than 3 chapters (YouTube ignores the list)")


if __name__ == "__main__":
    main()
