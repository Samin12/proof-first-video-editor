#!/usr/bin/env python3
"""Write one numbered markdown script per section of the finished master: sentences with word-index ranges + a word table.

usage: make_sections.py --config tighten.json [--out-dir DIR] [--words-dir DIR]

These files are what the tighten workflow's editors read. Cuts are later given as INCLUSIVE word-index ranges
[from_word, to_word] into ONE scene's word list, so the indices printed here are scene-level indices.

Inputs (all from the config, see tlib.py / tighten.example.json):
  sections   [{key, scene, title, range:[start_ms, end_ms]}]   one file per section: <out-dir>/<key>_<scene>.md
  cards      [{start_ms, end_ms, title}]                         listed in every file as "must stay untouched"
  words_dir  <scene>.json = [[start_ms, end_ms, word], ...]      MASTER ms (whisper word times, +-30 ms)
             <scene>_sentences.json = [[start_ms, end_ms, text]] optional; without it sentences are split on . ? !
Output per section:
  # <key> · scene <scene> · <title>
  Section range in the <label> video: a–b ms (m:ss.mmm–m:ss.mmm). Word times are <label> video ms (whisper; ±30 ms).
  Cards that must stay untouched: <title> [s–e]; ...
  ## Sentences (word index ranges)
  - [w0–w25] 0:00.098–0:08.552  <sentence text>
  ## Words (index · start_ms · end_ms · gap_to_next_ms · word)
  w0<TAB>98<TAB>380<TAB>0<TAB>This          (gap_to_next = next start - this end; -1 for the last word)
plus <out-dir>/index.json = {"sections": {key: {scene,title,range,words,file}}, "cards": [[s,e,title]...], "video": master,
"sections_list": [...]} where sections_list is ready to pass as args.sections to workflows/tighten.js.

When one scene is split over several sections, each file lists only the words whose midpoint lies inside that
section's range (indices stay scene-level). With one section per scene (the usual case) the whole scene is listed.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tlib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="tighten.json")
    ap.add_argument("--out-dir", help="default: config sections_dir")
    ap.add_argument("--words-dir", help="default: config words_dir")
    a = ap.parse_args()
    cfg = tlib.load_config(a.config)
    out_dir = a.out_dir or cfg.get("sections_dir") or tlib.die("no --out-dir and no sections_dir in the config")
    os.makedirs(out_dir, exist_ok=True)
    label = cfg["master_label"]
    cards = tlib.card_list(cfg)
    shared = {}
    for s in cfg["sections"]:
        shared[s["scene"]] = shared.get(s["scene"], 0) + 1

    fmt = tlib.fmt_ms
    index, listing = {}, []
    for sec in cfg["sections"]:
        key, sid, title = sec["key"], sec["scene"], sec["title"]
        a0, b0 = sec["range"]
        w = tlib.load_words(cfg, sid, a.words_dir)
        sents = tlib.load_sentences(cfg, sid, a.words_dir)
        if sents is None:
            sents = tlib.sentences_from_words(w)
        clip = shared[sid] > 1  # only filter when the scene is split over several sections
        inside = (lambda ws, we: a0 <= (ws + we) / 2 < b0) if clip else (lambda ws, we: True)
        L = [f"# {key} · scene {sid} · {title}",
             f"Section range in the {label} video: {a0}–{b0} ms ({fmt(a0)}–{fmt(b0)}). "
             f"Word times are {label} video ms (whisper; ±30 ms).",
             "Cards that must stay untouched: " + ("; ".join(f"{t} [{s}–{e}]" for s, e, t in cards) or "(none)"), "",
             "## Sentences (word index ranges)"]
        # map sentence -> word index range by time (5 ms slack for rounding)
        for s0, s1, text in sents:
            if clip and not (a0 <= (s0 + s1) / 2 < b0):
                continue
            idx = [i for i, (ws, we, t) in enumerate(w) if ws >= s0 - 5 and we <= s1 + 5]
            rng = f"w{idx[0]}–w{idx[-1]}" if idx else "?"
            L.append(f"- [{rng}] {fmt(s0)}–{fmt(s1)}  {text}")
        L += ["", "## Words (index · start_ms · end_ms · gap_to_next_ms · word)"]
        nw = 0
        for i, (ws, we, t) in enumerate(w):
            if not inside(ws, we):
                continue
            gap = (w[i + 1][0] - we) if i + 1 < len(w) else -1
            L.append(f"w{i}\t{ws}\t{we}\t{gap}\t{t}")
            nw += 1
        path = os.path.join(out_dir, f"{key}_{sid}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(L) + "\n")
        index[key] = {"scene": sid, "title": title, "range": [a0, b0], "words": nw, "file": path}
        listing.append({"key": key, "scene": sid, "title": title, "range": [a0, b0], "file": path})
        print(f"{key}: scene {sid}, {nw} words, {len(sents)} sentences -> {path}")
    doc = {"sections": index, "cards": [[s, e, t] for s, e, t in cards], "video": cfg.get("master"),
           "sections_list": listing}
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    print(f"index -> {os.path.join(out_dir, 'index.json')}")


if __name__ == "__main__":
    main()
