#!/usr/bin/env python3
"""Make the per-scene word and sentence files the tighten scripts need, by transcribing the finished master.

usage: words_from_whisper.py --config tighten.json [--video MASTER.mp4] [--whisper-model ggml-small.bin]
                             [--whisper-json existing.json] [--out-dir DIR] [--language en] [--dtw small]

Writes DIR/<scene>.json = [[start_ms, end_ms, word], ...] and DIR/<scene>_sentences.json = [[start_ms, end_ms, text]]
in MASTER milliseconds (DIR defaults to the config's words_dir), plus DIR/_whisper/ (16 kHz wav + raw whisper JSON).
  * whisper-cli runs with -ml 1 -sow (one word per segment, punctuation attached); tokens without a leading space are
    glued to the previous word ("can" + "'t", "integr" + "ations", " .").
  * each word goes to the configured scene whose range holds its midpoint; words inside chapter-card ranges or outside
    every scene are dropped and counted (whisper likes to hallucinate over silent cards).
  * sentences are split on . ? ! at word ends.
Whisper word times are only about +-30 ms and usually contiguous (one word ends where the next begins): that is fine
for choosing WHICH words to cut, never for WHERE to cut (snap_cuts.py snaps on the audio). If you already have better
word timings (a forced/CTC alignment of the edit's script, e.g. from the Borumi toolchain), write them in the same
[start_ms, end_ms, word] shape instead and skip this script.
Requires ffmpeg and whisper-cli with a ggml model (--whisper-model, $WHISPER_MODEL or config whisper_model).
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tlib  # noqa: E402


def parse_whisper(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    words = []
    for s in d.get("transcription", []):
        t = s.get("text", "")
        a, b = int(s["offsets"]["from"]), int(s["offsets"]["to"])
        if not t.strip():
            continue
        if words and not t.startswith(" "):
            words[-1][1] = max(words[-1][1], b)
            words[-1][2] += t.strip()
        else:
            words.append([a, b, t.strip()])
    return words


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--video", help="default: config master")
    ap.add_argument("--whisper-model", help="default: $WHISPER_MODEL or config whisper_model")
    ap.add_argument("--whisper-json", help="reuse an existing whisper-cli -ml 1 -sow -oj output instead of transcribing")
    ap.add_argument("--out-dir", help="default: config words_dir")
    ap.add_argument("--language", default="en")
    ap.add_argument("--dtw", help="pass -dtw MODEL to whisper-cli (token-level timestamps, e.g. small, base.en)")
    a = ap.parse_args()
    cfg = tlib.load_config(a.config)
    out = a.out_dir or cfg.get("words_dir") or tlib.die("no --out-dir and no words_dir in the config")
    os.makedirs(out, exist_ok=True)
    wj = a.whisper_json
    if not wj:
        video = a.video or cfg.get("master") or tlib.die("no --video and no master in the config")
        model = a.whisper_model or os.environ.get("WHISPER_MODEL") or cfg.get("whisper_model")
        if not (model and os.path.exists(model)):
            tlib.die("whisper model not found: --whisper-model, $WHISPER_MODEL or config whisper_model")
        wd = os.path.join(out, "_whisper"); os.makedirs(wd, exist_ok=True)
        wav = os.path.join(wd, "master_16k.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-ac", "1", "-ar", "16000", wav], check=True)
        cmd = ["whisper-cli", "-m", model, "-f", wav, "-l", a.language, "-ml", "1", "-sow", "-oj", "-of",
               os.path.join(wd, "master_words"), "-np"]
        if a.dtw:
            cmd += ["-dtw", a.dtw]
        print("transcribing (this takes a while for a long video)...", flush=True)
        subprocess.run(cmd, check=True, capture_output=True)
        wj = os.path.join(wd, "master_words.json")
    words = parse_whisper(wj)
    ranges = tlib.scene_ranges(cfg)
    cards = tlib.card_list(cfg)
    per = {sid: [] for sid in ranges}
    in_card = outside = 0
    for w in words:
        mid = (w[0] + w[1]) / 2
        if any(ca <= mid < cb for ca, cb, _ in cards):
            in_card += 1; continue
        sid = next((s for s, (ra, rb) in ranges.items() if ra <= mid < rb), None)
        if sid is None:
            outside += 1; continue
        per[sid].append(w)
    for sid, w in per.items():
        with open(os.path.join(out, f"{sid}.json"), "w", encoding="utf-8") as fh:
            json.dump(w, fh)
        with open(os.path.join(out, f"{sid}_sentences.json"), "w", encoding="utf-8") as fh:
            json.dump(tlib.sentences_from_words(w), fh)
        print(f"scene {sid}: {len(w)} words")
    print(f"{len(words)} words transcribed; dropped {in_card} inside cards and {outside} outside every scene")


if __name__ == "__main__":
    main()
