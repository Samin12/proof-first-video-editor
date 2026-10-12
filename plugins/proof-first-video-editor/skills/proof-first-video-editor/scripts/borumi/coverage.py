#!/usr/bin/env python3
"""coverage.py - WHOLE-VIDEO coverage check (read-only): render a frame ~every 2 s plus both sides of every join and test
each one against what must be there.

usage: coverage.py [--renders renders.json] [--window-ms 16000] [--reuse] [common args]

Frames: inspect_timeline, uniform 8 frames per 16 s window (max_frames is capped at 8), plus the 30 fps grid frames at
j - 34 ms and j for every join j (card edges, render edges, scene starts, layout starts) that has no sample within 40 ms.
Regions:
  card     (a fullscreen layout)            -> the frame is white (mean luma >= 200): no canvas flash, no badge
  behind   (a placed BEHIND render)         -> left of the camera column = the render's own frame (PSNR >= 30)
  front    (a placed FRONT render)          -> the panel is filled, the render's opaque pixels are drawn over the footage
  footage  (everything else)                -> the panel is filled (>= 50 % differs from the bare canvas)
  every non-card frame                      -> the camera tile is there (tile region differs from the background), nothing
                                               spills past the tile edges (non-behind), the step badge is drawn where a
                                               badge overlay covers the frame; a frame with no badge overlay while the
                                               project has badges is flagged too.
Renders are found in this writer's commits.log (place_* and swap_* steps record media id, exact copy, mode) and/or
--renders [{"media": id, "copy": path, "mode": "behind"|"front"}, ...].

Writes <out>/coverage/report.json (+ rows.json, samples.json, snap.json) and problems.jpg (a montage of failing frames).
A frame sampled exactly ON a join can be flagged spuriously (the frame belongs to the neighbour); re-render the frames
around it before acting. Exit 0 = no problems, 1 = problems listed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwlib as B  # noqa: E402
import snapshot as S  # noqa: E402
import txrun  # noqa: E402

al = B.al
log = B.log


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--renders", help="JSON list of {media, copy, mode} (in addition to commits.log)")
    ap.add_argument("--window-ms", type=int, default=16000, help="sampling window (8 frames each; default 16000 = one frame / 2 s)")
    ap.add_argument("--reuse", action="store_true", help="reuse the frames sampled by the previous run (same hash only)")
    B.add_common_args(ap, dry=False)
    args = ap.parse_args()
    ctx = B.context(args)
    cfg = ctx.cfg
    OUT = ctx.dir("coverage")
    os.makedirs(os.path.join(OUT, "frames"), exist_ok=True)
    k = ctx.key
    snap = S.snapshot(k)
    S.save(snap, os.path.join(OUT, "snap.json"))
    D = snap["duration"]
    cards = [(a, b) for a, b, _ in S.fullscreen_layouts(snap)]
    m2copy = {}
    for d_ in txrun.commits(ctx):
        st, info = str(d_.get("step", "")), d_.get("info") or {}
        if not d_.get("commit"):
            continue
        if st.startswith("place_"):
            for sl in info.get("slots", []):
                if sl.get("media") and sl.get("copy"):
                    m2copy[sl["media"]] = (sl["copy"], info.get("mode"))
        elif st.startswith("swap") and info.get("media") and info.get("copy"):
            m2copy[info["media"]] = (info["copy"], info.get("mode", "behind"))
    if args.renders:
        for r in B.load_json(args.renders):
            m2copy[r["media"]] = (os.path.abspath(os.path.expanduser(r["copy"])), r.get("mode", "behind"))
    renders = []   # (start, end, media, mode, copy, media_start)
    for i, v in snap["content"].items():
        if v[0].startswith("screen") and v[3] in m2copy and os.path.exists(m2copy[v[3]][0]):
            renders.append((v[1], v[2], v[3], m2copy[v[3]][1], m2copy[v[3]][0], v[4] or 0))
    renders.sort()
    badge_pos = B.norm_numbers(cfg["badge_props"].get("position"), 4)
    badges = sorted((v[1], v[2]) for v in snap["overlays"].values() if v[0].startswith("media_overlay")
                    and B.norm_numbers(json.loads(v[3] or "{}").get("position"), 4) == badge_pos)
    log("cards %s" % cards)
    log("renders %s" % [(a, b, m, md) for a, b, m, md, _, _ in renders])
    log("badge overlays: %d" % len(badges))
    nf_cache = {}

    def nframes(path):
        if path not in nf_cache:
            o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets",
                                "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
            nf_cache[path] = int(o)
        return nf_cache[path]

    def region(t):
        for a, b in cards:
            if a <= t < b:
                return ("card", None)
        for r in renders:
            if r[0] <= t < r[1]:
                return (r[3], r)
        return ("footage", None)
    samples = {}
    sj = os.path.join(OUT, "samples.json")
    if args.reuse and os.path.exists(sj):
        prev = B.load_json(sj)
        if prev.get("hash") == snap["hash"]:
            samples = {float(t): p for t, p in prev["samples"].items()}
            log("reusing %d frames" % len(samples))
    if not samples:
        for a in range(0, D, args.window_ms):
            b = min(D, a + args.window_ms)
            r = B.call("inspect_timeline", dict(k, range={"start_ms": a, "end_ms": b}, presentation="frames", quality="high", save=True,
                                                include_images=False, sampling={"type": "uniform", "max_frames": 8}))
            for fr in r.get("frames", []):
                samples[round(fr["time_ms"], 2)] = fr["path"]
        joins = sorted({t for a, b in cards for t in (a, b)} | {t for r in renders for t in (r[0], r[1])} |
                       {x[2] for x in snap["scenes"]} | {v[0] for v in snap["layouts"].values()})
        for j in joins:
            if any(abs(t_ - j) <= 40 for t_ in samples):
                continue
            for g in (B.grid(j - 34), B.grid(j)):
                if 1 <= g < D:
                    t_, p = B.frame(k, g, "high")
                    samples[round(t_, 2)] = p
        log("%d frames sampled (%d joins)" % (len(samples), len(joins)))
    saved = {}
    rows = []
    for t, p in sorted(samples.items()):
        dst = os.path.join(OUT, "frames", "%d%s" % (round(t), os.path.splitext(p)[1] or ".jpg"))
        if p != dst:
            shutil.copyfile(p, dst)
        saved[t] = dst
        kind, r = region(t)
        row = {"t": t, "frame": dst, "kind": kind, "badge": any(a <= t < b for a, b in badges)}
        if r:
            n = int(round(((t - r[0]) + r[5]) * 30 / 1000.0))
            n = min(n, nframes(r[4]) - 1)
            row.update({"render": r[4], "n": n, "mode": r[3]})
        rows.append(row)
    B.write_json(os.path.join(OUT, "rows.json"), rows)
    B.write_json(sj, {"hash": snap["hash"], "samples": {str(t): v for t, v in saved.items()}})
    G = B.geometry_payload(cfg, canvas=B.canvas_png(cfg, create=True, out=ctx.out))
    res = B.imgcheck("coverage", dict(G, rows=rows))
    if badges:
        for r_ in res:
            if r_["kind"] != "card" and not any(a <= r_["t"] < b for a, b in badges):
                r_["problems"].append("no step badge overlay here")
    bad = [r_ for r_ in res if r_["problems"]]
    log("frames analysed: %d; with problems: %d" % (len(res), len(bad)))
    log("by kind: %s" % dict(Counter(r_["kind"] for r_ in res)))
    for r_ in bad[:60]:
        log("  %.2f %-8s %s" % (r_["t"], r_["kind"], r_["problems"]))
    g_ = S.gaps(snap, 0, D)
    lgaps = [(a, b, len(fr)) for a, b, fr in g_["layout"]]
    log("layout gaps holding a 30/60 fps frame: %s" % [x for x in lgaps if x[2]])
    B.write_json(os.path.join(OUT, "report.json"), {
        "duration": D, "hash": snap["hash"], "cards": cards, "renders": [(a, b, m, md) for a, b, m, md, _, _ in renders],
        "badges": badges, "n_frames": len(res), "problems": bad, "layout_gaps": lgaps, "results": res})
    if bad:
        B.montage([r_["frame"] for r_ in bad[:24]], os.path.join(OUT, "problems.jpg"), 4, 480, 270)
        log("problem frames montage: %s" % os.path.join(OUT, "problems.jpg"))
    log("report: %s" % os.path.join(OUT, "report.json"))
    return 1 if bad or [x for x in lgaps if x[2]] else 0


if __name__ == "__main__":
    sys.exit(main())
