#!/usr/bin/env python3
"""plans_from_kept.py - build scene_words.py's plans.json ({"<scene id>": {"text": "<kept words>"}}) from
edit-borumi-video's kept-text files, with every removed range stripped out. Read-only on WORK; stdlib only.

usage: plans_from_kept.py [--work WORK] [--out plans.json] [--draft] [FILE ...]

  FILE ...   kept-text files (default: WORK/cuts/kept_scene*.final.txt; with --draft WORK/cuts/kept_scene*.txt)
  --work     WORK folder (default $BORUMI_WORK)
  --out      default WORK/writer/words/plans.json; "-" prints to stdout

A kept file (edit-borumi-video kept_text.py) is: line 1 a JSON summary {scene, scene_id, removed_ms, ...}, line 2
"ranges: [...]", then the scene's words in order where every removal appears INLINE as
    ✂[kind a-b 1.23s: [[cut words]]]   or   ✂[pause a-b 0.84s]
Pasting such a file as plan text would feed the removed words back in, so this script drops the two header lines and
every ✂[...] span, keeps the plain words and collapses whitespace. The scene id comes from the summary line, else from
WORK/cuts/plan_sceneNN.final.json ("scene_id"; NN = the scene's position at dump time). Scenes whose id cannot be found
are reported and skipped. Plan text only fixes spelling in scene_words.py (whisper + CTC alone still work without it).
"""
import argparse
import glob
import json
import os
import re
import sys

CUT = re.compile(r"✂\[[^\[\]]*(?::\s*\[\[.*?\]\])?\]", re.S)


def kept_text(raw):
    lines = raw.split("\n")
    summary = {}
    body = lines
    if lines and lines[0].lstrip().startswith("{"):
        try:
            summary = json.loads(lines[0])
        except ValueError:
            summary = {}
        body = lines[1:]
    if body and body[0].startswith("ranges:"):
        body = body[1:]
    text = CUT.sub(" ", "\n".join(body))
    if "✂" in text:
        raise ValueError("an unrecognised ✂ marker survived: %r" % text[text.index("✂"):text.index("✂") + 80])
    return summary, re.sub(r"\s+", " ", text).strip()


def scene_id_for(path, summary, work):
    if summary.get("scene_id"):
        return summary["scene_id"]
    m = re.search(r"kept_scene(\d+)", os.path.basename(path))
    if m and work:
        for cand in ("plan_scene%s.final.json" % m.group(1), "plan_scene%s.json" % m.group(1)):
            p = os.path.join(work, "cuts", cand)
            if os.path.exists(p):
                try:
                    with open(p) as fh:
                        sid = json.load(fh).get("scene_id")
                except ValueError:
                    sid = None
                if sid:
                    return sid
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--work", default=os.environ.get("BORUMI_WORK"))
    ap.add_argument("--out")
    ap.add_argument("--draft", action="store_true", help="use kept_sceneNN.txt instead of the .final.txt files")
    a = ap.parse_args()
    work = os.path.abspath(os.path.expanduser(a.work)) if a.work else None
    files = a.files
    if not files:
        if not work:
            ap.error("pass --work WORK (or set $BORUMI_WORK) or list the kept files")
        pat = "kept_scene*.txt" if a.draft else "kept_scene*.final.txt"
        files = sorted(f for f in glob.glob(os.path.join(work, "cuts", pat)) if a.draft is False or not f.endswith(".final.txt"))
    if not files:
        sys.exit("plans_from_kept.py: no kept files found")
    plans, skipped = {}, []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            summary, text = kept_text(fh.read())
        sid = scene_id_for(f, summary, work)
        if not sid or not text:
            skipped.append(os.path.basename(f))
            continue
        if sid in plans:
            print("WARNING: %s: scene %s already taken from another file; keeping the first" % (os.path.basename(f), sid))
            continue
        plans[sid] = {"text": text}
        print("%s -> scene %s: %d words" % (os.path.basename(f), sid, len(text.split())))
    if skipped:
        print("skipped (no scene id or no text): %s" % ", ".join(skipped))
    out = a.out or (os.path.join(work, "writer", "words", "plans.json") if work else "-")
    data = json.dumps(plans, indent=1, ensure_ascii=False)
    if out == "-":
        print(data)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(data + "\n")
        print("wrote %s (%d scenes)" % (out, len(plans)))
    return 0 if plans else 2


if __name__ == "__main__":
    sys.exit(main())
