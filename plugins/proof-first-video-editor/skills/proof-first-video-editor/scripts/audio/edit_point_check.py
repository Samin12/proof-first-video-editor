#!/usr/bin/env python3
"""Edit-point listen-check (measured) + annotated pop scan + picture-timing lag on non-voice content.

usage:
  edit_point_check.py FINAL.mp4 POSTPROCESSED.mp4 [--points T ...] [--cards T ...] [--verify-json V.json]
                      [--range-worst START_S END_S N] [project options]

  FINAL.mp4          remix.py output;  POSTPROCESSED.mp4  Borumi's export with its audio at timeline 0
  --points T ...     edit points (s) to report in full: every mic segment edge within +-0.6 s of each point
  --cards T ...      chapter-card click times (s) for the picture-timing check; default: sound_effect starts
  --verify-json V    merge the report into verify_remix.py's JSON under "edit_point_check"
  --range-worst      rank every mic edge in [START, END] by click score and fully report the worst N
  project options    --bundle PATH | --project ALIAS [--config PATH] [--project-id ID] (see audio_config.py)

For every reported edge: join type, sources/in-out, click score (2nd-difference peak vs local RMS), absolute
step size, whether the mastered source itself carries the same transient on either side of the cut (= speech
content cut by the edit, not a discontinuity), silence/gap length around the cut, adjacent short segments
(word slivers < 150 ms), and a 10 ms RMS profile of the final vs Borumi's post-processed render.

Verdicts for edges scoring > 20 dB:
  "inaudible (step below -60 dBFS)"  a high SCORE at a near-silent cut (the local background RMS is tiny)
  "content (...)"                    the transient is in the mastered source itself: speech, not a pop
  "CHECK: ..."                       unexplained: listen, and move/extend the cut in Borumi
Picture timing: lag of final vs post-processed on clicks and playback-only stretches (content Borumi does not
delay); should be ~0 ms (|lag| < ~0.6 ms), proving the remix sits exactly on the picture.
Read-only on Borumi.
"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_lib as al  # noqa: E402
import audio_config  # noqa: E402
import borumi_read as br  # noqa: E402
import remix as rm  # noqa: E402
import verify_remix as vp  # noqa: E402
import numpy as np  # noqa: E402

SR = al.SR
db = lambda x: round(20 * math.log10(float(x) + 1e-12), 1)
d2 = lambda x: np.abs(x[2:].astype(np.float64) - 2 * x[1:-1] + x[:-2])


def src_peak(media, src_t, side):
    """|d2| peak of the mastered source within +-2 ms of src_t and RMS of the 5 ms on the given side."""
    m = al.load(os.path.join(br._P["mastered"], media + ".wav"), start=max(0.0, src_t - 0.05), dur=0.1)
    c = int(round(min(src_t, 0.05) * SR))
    dd = d2(m)
    pk = float(dd[max(0, c - 96):c + 96].max()) if len(dd) > c + 96 else 0.0
    rms = al.rms_db(m[max(0, c - 240):c]) if side == "before" else al.rms_db(m[c:c + 240])
    return pk, rms


def edge_report(f, v, plan, t, seg_by_edge):
    j = int(round(t * SR))
    sc, pk = vp.click_score(f, j)
    out_seg, in_seg = seg_by_edge.get(("end", round(t, 3))), seg_by_edge.get(("start", round(t, 3)))
    srcs = []
    for lab, s, side in (("outgoing", out_seg, "before"), ("incoming", in_seg, "after")):
        if s is None:
            continue
        st = s.s0 + (s.t1 - s.t0) if lab == "outgoing" else s.s0
        p, r = src_peak(s.media, st, side)
        srcs.append({"side": lab, "segment": s.id, "media": s.media[:8], "src_s": round(st, 3), "seg_len_s": round(s.t1 - s.t0, 3),
                     "src_d2_dbfs": db(p), "src_rms_5ms_at_cut_dbfs": round(r, 1)})
    src_max = max([10 ** (x["src_d2_dbfs"] / 20) for x in srcs] + [0.0])
    if pk < 1e-3:
        verdict = "inaudible (step below -60 dBFS)"
    elif src_max >= 0.7 * pk:
        verdict = "content (same transient in the mastered source at the cut), no discontinuity"
    else:
        verdict = "CHECK: transient not explained by the source"
    prof = lambda x: [round(al.rms_db(x[j + k * 480:j + (k + 1) * 480]), 1) for k in range(-10, 10)]
    w = f[max(0, j - 9600):j + 9600]
    quiet = np.abs(w) < 10 ** (-60 / 20)
    # longest run of near-silence within +-200 ms of the cut
    run, best = 0, 0
    for q in quiet:
        run = run + 1 if q else 0
        best = max(best, run)
    return {"t_s": round(t, 3), "join": (in_seg.prev_join if in_seg else None) or (out_seg.next_join if out_seg else None),
            "click_score_db": None if sc is None else round(sc, 1), "step_d2_dbfs": db(pk), "sources": srcs, "verdict": verdict,
            "final_rms_10ms_-100..+100ms": prof(f), "borumi_rms_10ms_-100..+100ms": prof(v),
            "longest_silence_ms_within_200ms": round(best / SR * 1000, 1),
            "short_neighbour_segments_lt_150ms": [x["segment"] for x in srcs if x["seg_len_s"] < 0.15]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("final"); ap.add_argument("pp", metavar="postprocessed")
    ap.add_argument("--points", nargs="*", type=float, default=[])
    ap.add_argument("--cards", nargs="*", type=float, default=None)
    ap.add_argument("--verify-json")
    ap.add_argument("--range-worst", nargs=3, type=float, metavar=("START_S", "END_S", "N"),
                    help="rank every mic edge in [START, END] by click score and fully report the worst N")
    audio_config.add_args(ap)
    a = ap.parse_args()
    br.set_project(audio_config.resolve(a))
    f = al.load_one_channel(a.final)
    v = al.load(a.pp)
    n = min(len(f), len(v)); f, v = f[:n], v[:n]
    pid = br.project_id(); tl = br.read_timeline(pid); plan = rm.build_plan(tl, br.db_copy())
    rep = {"timeline_hash": tl["timeline_hash"]}
    cards_t = a.cards if a.cards is not None else sorted(s_.t0 for s_ in plan["sfx"])
    seg_by_edge = {}
    for s in plan["mic"]:
        seg_by_edge[("start", round(s.t0, 3))] = s
        seg_by_edge[("end", round(s.t1, 3))] = s
    edges = sorted({round(s.t0, 3) for s in plan["mic"]} | {round(s.t1, 3) for s in plan["mic"]})

    # picture timing: lag on content Borumi does not delay (clicks, playback-only stretches)
    lags = []
    for t in cards_t:
        i, k = int(t * SR), int((t + 0.93) * SR)
        if k > n:
            continue
        g, pk = al.gcc_phat(v[i:k], f[i:k], int(0.01 * SR))
        lags.append({"what": "click", "t_s": t, "lag_ms": round(float(g) * 1000 / SR, 3), "peak": round(float(pk), 3)})
    cache = rm.SourceCache()
    for s in plan["system"]:
        s.comp = 0
    for s in plan["mic"]:
        s.comp = 0
    sysx, _ = rm.render(plan["system"], n, lambda s: s.path, cache)
    vox, _ = rm.render(plan["mic"], n, lambda s: s.path, cache)
    B = SR
    nb = n // B
    es = np.sum(sysx[:nb * B].astype(np.float64).reshape(nb, B) ** 2, 1) / B
    ev = np.sum(vox[:nb * B].astype(np.float64).reshape(nb, B) ** 2, 1) / B * 10 ** 2.1
    pb = np.where((es > 1e-5) & (es > 100 * ev))[0]
    runs = []
    for b in pb:
        if runs and b - runs[-1][1] <= 1:
            runs[-1][1] = b
        else:
            runs.append([b, b])
    for r0, r1 in runs:
        if r1 - r0 + 1 < 2:
            continue
        i, k = r0 * B, (r1 + 1) * B
        g, pk = al.gcc_phat(v[i:k], f[i:k], int(0.01 * SR))
        lags.append({"what": "playback", "t_s": int(r0), "dur_s": int(r1 - r0 + 1), "lag_ms": round(float(g) * 1000 / SR, 3), "peak": round(float(pk), 3)})
    conf = [x["lag_ms"] for x in lags if x["peak"] > 0.2]
    rep["picture_timing_lag_vs_postprocessed"] = {"windows": lags, "confident": len(conf),
                                                   "median_ms": round(float(np.median(conf)), 3) if conf else None,
                                                   "max_abs_ms": round(max(abs(x) for x in conf), 3) if conf else None}

    # annotated pop scan (all edges with score > 20 dB)
    ann = []
    for t in edges:
        j = int(round(t * SR))
        if j < 2500 or j > n - 2500:
            continue
        sc, pk = vp.click_score(f, j)
        if sc is not None and sc > 20:
            r = edge_report(f, v, plan, t, seg_by_edge)
            ann.append({k: r[k] for k in ("t_s", "join", "click_score_db", "step_d2_dbfs", "verdict", "sources")})
    ann.sort(key=lambda r: -r["click_score_db"])
    rep["pop_scan_edges_over_20dB"] = ann

    # requested edit points
    pts = []
    for p in a.points:
        near = [t for t in edges if abs(t - p) <= 0.6 and 2500 < int(round(t * SR)) < n - 2500]
        pts.append({"point_s": p, "edges": [edge_report(f, v, plan, t, seg_by_edge) for t in near]})
    rep["edit_points"] = pts
    if a.range_worst:
        r0, r1, nw = a.range_worst
        inr = [t for t in edges if r0 - 0.001 <= t <= r1 + 0.001 and 2500 < int(round(t * SR)) < n - 2500]
        scored = []
        for t in inr:
            sc_, pk_ = vp.click_score(f, int(round(t * SR)))
            scored.append((sc_ if sc_ is not None else -99.0, t))
        scored.sort(reverse=True)
        rep["range_worst"] = {"range_s": [r0, r1], "edges_in_range": len(inr),
                              "score_distribution_db": {"median": round(float(np.median([x[0] for x in scored])), 1),
                                                        "max": round(scored[0][0], 1)} if scored else None,
                              "worst": [edge_report(f, v, plan, t, seg_by_edge) for _, t in scored[:int(nw)]]}
    if a.verify_json:
        d = json.load(open(a.verify_json)) if os.path.exists(a.verify_json) else {}
        d["edit_point_check"] = rep
        tmp = a.verify_json + ".tmp"
        json.dump(d, open(tmp, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
        os.replace(tmp, a.verify_json)
    print(json.dumps(rep["picture_timing_lag_vs_postprocessed"], indent=None)[:1500])
    for r in ann:
        print("POP", r["t_s"], r["join"], r["click_score_db"], r["step_d2_dbfs"], "|", r["verdict"], "|",
              [(x["side"], x["src_d2_dbfs"], x["src_rms_5ms_at_cut_dbfs"]) for x in r["sources"]])
    if a.range_worst:
        rw = rep["range_worst"]
        print("RANGE", rw["range_s"], "edges", rw["edges_in_range"], "scores", rw["score_distribution_db"])
        pts = [{"point_s": "worst-in-range", "edges": rw["worst"]}] + pts
    for p in pts:
        print("POINT", p["point_s"], "edges:", [e["t_s"] for e in p["edges"]])
        for e in p["edges"]:
            print("   ", e["t_s"], e["join"], "score", e["click_score_db"], "step", e["step_d2_dbfs"], "|", e["verdict"],
                  "| silence", e["longest_silence_ms_within_200ms"], "ms | short", e["short_neighbour_segments_lt_150ms"])
            print("      sources", [(x["side"], x["segment"], x["media"], x["src_s"], x["seg_len_s"], x["src_d2_dbfs"], x["src_rms_5ms_at_cut_dbfs"]) for x in e["sources"]])
            print("      final ", e["final_rms_10ms_-100..+100ms"])
            print("      borumi", e["borumi_rms_10ms_-100..+100ms"])


if __name__ == "__main__":
    main()
