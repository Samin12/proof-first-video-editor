#!/usr/bin/env python3
"""Turn word-index cuts into frame-exact removal ranges on the finished master, snapped to real silence.

usage: snap_cuts.py --config tighten.json CUTS.json OUT.json [--tighten-pauses] [--wav WAV] [--words-dir DIR]
                    [--total-ms MS] [tuning flags, see --help]

CUTS.json: {"cuts": [{"section", "scene", "from_word", "to_word", "first_word", "last_word", "priority", "kind", ...}],
            "pause_safe": [{"from_ms", "to_ms", "note"}]}
  from_word/to_word are INCLUSIVE indices into the scene's word list (words_dir/<scene>.json, MASTER ms).
  "scene" may be omitted when "section" names a configured section.

Why snap at all: whisper word times are only +-30 ms and usually CONTIGUOUS (one word ends exactly where the next starts),
so cutting at a word boundary from the transcript leaves slivers of the neighbouring word. Instead each cut's start is
placed at the QUIETEST frame-grid point between word from_word-1 and from_word (window: end of the previous word - 40 ms
to start of the first cut word + 40 ms), and its end at the quietest grid point between to_word and to_word+1, measured
as 20 ms RMS on the mastered WAV (the same audio build_cut.py uses).
Edge cases kept from production:
  * a window narrower than one frame is widened symmetrically so it holds a grid point;
  * a cut that runs to the scene end ends exactly on the grid point at/before the scene end;
  * a cut from the scene start begins on the grid frame at/before the scene start (<= 1 frame of the card tail;
    rounding UP there left a -31 dB sliver of the first word);
  * ranges overlapping a chapter card are clamped to the card edges (floor to the grid) and warned about;
  * a range shorter than one frame after snapping is dropped (warned);
  * overlapping/adjacent ranges are merged ("merged": [[from_word, to_word], ...] lists the later cuts).
Warnings: first/last word mismatch (the editor's indices are off), loud edge (> --loud-edge-db: possible sliver).
--tighten-pauses: inside pause_safe ranges, every kept silence longer than --pause-min-ms (RMS < --pause-thr-db) is
shortened to --pause-keep-ms by removing its middle; never within --card-guard-ms of a card. Only use it on ranges where
the picture is static (talking head, still slide); never over demo footage where something moves during the pause.

OUT.json: {"ranges": [{"section","scene","from_word","to_word","text","start_ms","end_ms","rms_in_db","rms_out_db",
           "gap_before_ms","gap_after_ms","priority","kind"[,"merged"]}], "warnings": [...], "removed_ms",
           "new_duration_ms", "n_word_cuts", "n_pause_cuts", "total_ms", "fps"}
Every start_ms/end_ms is on the 1000/fps ms grid. Hand pins come AFTER this step (apply_pins.py).
Requires numpy and ffmpeg.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tlib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cuts", help="CUTS.json")
    ap.add_argument("out", help="OUT.json (ranges)")
    ap.add_argument("--config", required=True, help="tighten.json")
    ap.add_argument("--tighten-pauses", action="store_true", help="also shorten long pauses inside pause_safe ranges")
    ap.add_argument("--wav", help="mastered audio (default: config audio_wav)")
    ap.add_argument("--words-dir", help="default: config words_dir")
    ap.add_argument("--total-ms", type=float, help="master duration (default: master frame count * 1000/fps)")
    ap.add_argument("--edge-pad-ms", type=float, default=40, help="search window reaches this far into the words (40)")
    ap.add_argument("--rms-half-ms", type=float, default=10, help="half width of the RMS window (10 -> 20 ms RMS)")
    ap.add_argument("--loud-edge-db", type=float, default=-38, help="warn when an edge is louder than this (-38)")
    ap.add_argument("--pause-min-ms", type=float, default=700, help="pauses longer than this get shortened (700)")
    ap.add_argument("--pause-keep-ms", type=float, default=400, help="what a shortened pause keeps (400)")
    ap.add_argument("--pause-thr-db", type=float, default=-45, help="silence threshold for pauses (-45)")
    ap.add_argument("--card-guard-ms", type=float, default=200, help="no pause cuts this close to a card (200)")
    a = ap.parse_args()
    tlib.require_numpy()
    import numpy as np  # noqa: F401  (decode_audio returns numpy arrays)

    cfg = tlib.load_config(a.config)
    FPS, SR = cfg["fps"], cfg["sample_rate"]
    WAV = a.wav or cfg.get("audio_wav") or tlib.die("no --wav and no audio_wav in the config")
    if a.total_ms:
        TOTAL_MS = a.total_ms
    else:
        if not cfg.get("master"):
            tlib.die("no --total-ms and no master in the config")
        TOTAL_MS = tlib.nframes(cfg["master"]) * 1000 / FPS
    CARDS = [(s, e) for s, e, _ in tlib.card_list(cfg)]
    SCENE_RANGE = tlib.scene_ranges(cfg)
    PAD = a.edge_pad_ms
    audio = tlib.decode_audio(WAV, SR, 1)

    def rms_db(t_ms, half_ms=a.rms_half_ms):
        c = int(t_ms * SR / 1000); h = int(half_ms * SR / 1000)
        x = audio[max(0, c - h):c + h]
        return 20 * math.log10(math.sqrt(float((x * x).mean()) + 1e-12)) if len(x) else -120.0

    def grid(lo_ms, hi_ms):
        lo_k, hi_k = math.ceil(lo_ms * FPS / 1000), math.floor(hi_ms * FPS / 1000)
        return [k * 1000 / FPS for k in range(lo_k, hi_k + 1)]

    widen = math.ceil(1000 / FPS / 2 + 0.5)  # 9 ms at 60 fps: always holds one grid point

    def quietest(lo_ms, hi_ms):
        pts = grid(lo_ms, hi_ms)
        if not pts:  # window narrower than a frame: widen symmetrically
            mid = (lo_ms + hi_ms) / 2
            pts = grid(mid - widen, mid + widen)
        best = min(pts, key=lambda t: rms_db(t))
        return best, rms_db(best)

    with open(a.cuts, encoding="utf-8") as fh:
        cuts_doc = json.load(fh)
    words = {}
    res, warns = [], []
    for c in cuts_doc["cuts"]:
        sid = c.get("scene") or tlib.section_scene(cfg, c.get("section"))
        if not sid:
            tlib.die(f"cut {c} has no scene and its section is not in the config")
        if sid not in SCENE_RANGE:
            tlib.die(f"cut {c.get('section')} w{c.get('from_word')}: scene {sid} is not in the config sections")
        w = words.setdefault(sid, tlib.load_words(cfg, sid, a.words_dir))
        i, j = c["from_word"], c["to_word"]
        if not (0 <= i <= j < len(w)):
            tlib.die(f"cut {c.get('section')} w{i}-w{j}: indices outside scene {sid} (0..{len(w) - 1})")
        chk = lambda k, s: (s or "").strip().strip(".,?!\"'").lower() == str(w[k][2]).strip().strip(".,?!\"'").lower()
        if not chk(i, c.get("first_word")) or not chk(j, c.get("last_word")):
            warns.append(f"{c.get('section')} w{i}-w{j}: first/last word mismatch: {c.get('first_word')!r}/{c.get('last_word')!r} "
                         f"vs {w[i][2]!r}/{w[j][2]!r}")
        s0, s1 = SCENE_RANGE[sid]
        lo = w[i - 1][1] - PAD if i > 0 else s0
        hi = w[i][0] + PAD
        cin, din = quietest(lo, hi)
        lo2 = w[j][1] - PAD
        hi2 = w[j + 1][0] + PAD if j + 1 < len(w) else s1
        cout, dout = quietest(lo2, hi2)
        if j + 1 >= len(w):  # cut runs to the scene end: end exactly at the grid point at/before the scene end
            cout = math.floor(s1 * FPS / 1000) * 1000 / FPS
            dout = rms_db(cout)
        if i == 0:  # cut from the scene start: begin on the grid frame at/before the scene start (<= 1 frame of a card tail)
            cin = math.floor(s0 * FPS / 1000) * 1000 / FPS
            din = rms_db(cin)
        r = {"section": c.get("section"), "scene": sid, "from_word": i, "to_word": j, "text": " ".join(str(x[2]) for x in w[i:j + 1]),
             "start_ms": round(cin, 3), "end_ms": round(cout, 3), "rms_in_db": round(din, 1), "rms_out_db": round(dout, 1),
             "gap_before_ms": (w[i][0] - w[i - 1][1]) if i > 0 else None,
             "gap_after_ms": (w[j + 1][0] - w[j][1]) if j + 1 < len(w) else None,
             "priority": c.get("priority"), "kind": c.get("kind")}
        if din > a.loud_edge_db or dout > a.loud_edge_db:
            warns.append(f"{r['section']} w{i}-w{j} ({r['text'][:40]}...): loud edge (in {din:.1f} dB, out {dout:.1f} dB): possible sliver")
        for ca, cb in CARDS:
            if cin < math.floor(cb * FPS / 1000) * 1000 / FPS and cout > ca:
                warns.append(f"{r['section']} w{i}-w{j}: overlaps card {ca}-{cb}: clamped")
                if cin < ca < cout: cout = min(cout, math.floor(ca * FPS / 1000) * 1000 / FPS)
                if cin < cb < cout: cin = max(cin, math.floor(cb * FPS / 1000) * 1000 / FPS)
                r["start_ms"], r["end_ms"] = round(cin, 3), round(cout, 3)
        if r["end_ms"] - r["start_ms"] < 1000 / FPS:
            warns.append(f"{r['section']} w{i}-w{j}: empty after snapping, dropped"); continue
        res.append(r)
    res.sort(key=lambda r: r["start_ms"])
    merged = []
    for r in res:
        if merged and r["start_ms"] <= merged[-1]["end_ms"] + 1e-6:
            m = merged[-1]; m["end_ms"] = max(m["end_ms"], r["end_ms"]); m["text"] += " | " + r["text"]
            m.setdefault("merged", []).append([r["from_word"], r["to_word"]])
        else:
            merged.append(dict(r))
    pauses = []
    if a.tighten_pauses:
        safe = cuts_doc.get("pause_safe", [])
        hop = 10; thr = a.pause_thr_db; G = a.card_guard_ms
        for s in safe:
            t = s["from_ms"]; run = None
            while t <= s["to_ms"]:
                quiet = rms_db(t, 10) < thr and not any(m["start_ms"] <= t < m["end_ms"] for m in merged) \
                    and not any(ca - G <= t < cb + G for ca, cb in CARDS)
                if quiet and run is None: run = t
                if (not quiet or t + hop > s["to_ms"]) and run is not None:
                    L = t - run
                    if L > a.pause_min_ms:
                        mid = run + L / 2; rem = L - a.pause_keep_ms
                        pa = math.ceil((mid - rem / 2) * FPS / 1000) * 1000 / FPS
                        pb = math.floor((mid + rem / 2) * FPS / 1000) * 1000 / FPS
                        if pb - pa >= 2 * 1000 / FPS:
                            pauses.append({"section": "pause", "start_ms": round(pa, 3), "end_ms": round(pb, 3),
                                           "text": f"(pause {L} ms -> ~{a.pause_keep_ms:g})",
                                           "rms_in_db": round(rms_db(pa), 1), "rms_out_db": round(rms_db(pb), 1)})
                    run = None
                t += hop
    allr = sorted(merged + pauses, key=lambda r: r["start_ms"])
    final = []
    for r in allr:
        if final and r["start_ms"] <= final[-1]["end_ms"]:
            final[-1]["end_ms"] = max(final[-1]["end_ms"], r["end_ms"])
        else:
            final.append(r)
    removed = sum(r["end_ms"] - r["start_ms"] for r in final)
    doc = {"ranges": final, "warnings": warns, "removed_ms": round(removed, 1), "new_duration_ms": round(TOTAL_MS - removed, 1),
           "n_word_cuts": len(merged), "n_pause_cuts": len(pauses), "total_ms": round(TOTAL_MS, 3), "fps": FPS}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
    print(f"{len(merged)} word cuts + {len(pauses)} pause cuts; removed {removed/1000:.1f} s -> {(TOTAL_MS-removed)/1000:.1f} s "
          f"({(TOTAL_MS-removed)/60000:.2f} min); {len(warns)} warnings")
    for x in warns: print("  WARN", x)


if __name__ == "__main__":
    main()
