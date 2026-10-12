#!/usr/bin/env python3
"""Independent checks of remix.py's final against Borumi's own exports (read-only on Borumi).

usage:
  verify_remix.py FINAL.mp4 POSTPROCESSED.mp4 RAW_EXPORT.mp4 [--cards T ...] [--json OUT.json] [project options]

  FINAL.mp4          remix.py output (new audio at timeline 0)
  POSTPROCESSED.mp4  Borumi's export with its audio moved to timeline 0 (edit-borumi-video finalize_export.py
                     --postprocess: atrim=start_sample=2112 + adeclick); the "what Borumi would have shipped" baseline
  RAW_EXPORT.mp4     the unprocessed Borumi export (loudness/stream comparison only)
  --cards T ...      times (s) of chapter-card clicks to compare; default: every sound_effect segment start
  project options    --bundle PATH | --project ALIAS [--config PATH] [--project-id ID] (see audio_config.py)

1. A/V sync: lag of the final's audio vs the post-processed export over the most active 20 s windows and per
   mic segment (GCC-PHAT, voice content). Expect a small NEGATIVE lag on voice (about -1 ms in one project, up
   to about -7 ms in the scenes of another): the post-processed export only removes the constant 2112-sample
   priming, not Borumi's extra per-clip voice latency, so the remix (placed on the timeline) is earlier on
   voice by exactly that latency. Non-voice content (clicks, playback) must be ~0 ms: see edit_point_check.py.
2. Pop/click scan: at every mic segment edge, peak of the 2nd difference within +-2 ms vs the RMS of the
   2nd difference in the surrounding +-50 ms (centre excluded), in dB. Baselines: the same metric at
   2000 random non-edge speech positions, and at the same edges in the post-processed export.
   Edges over 20 dB are explained (or not) by edit_point_check.py.
3. Card clicks: RMS/peak in [t, t+0.93 s] in final and baseline, relative to program loudness.
4. Loudness / true peak (EBU R128) and streams of all three files. The final is dual mono: L == R is checked
   and levels are measured on ONE channel (ffmpeg's float -ac 1 downmix would add +3 dB).
"""
import argparse, json, math, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_lib as al  # noqa: E402
import audio_config  # noqa: E402
import borumi_read as br  # noqa: E402
import remix as rm  # noqa: E402
import numpy as np  # noqa: E402

SR = al.SR


def click_score(x, j, half=96, ctx=2400, excl=240):
    """(score_db, peak): peak |2nd difference| within +-half samples of j vs RMS of the 2nd difference over
    +-ctx samples with the centre +-excl excluded. None score when either is ~0 (digital silence)."""
    d2 = x[max(0, j - ctx - 2):j + ctx].astype(np.float64)
    d2 = d2[2:] - 2 * d2[1:-1] + d2[:-2]
    c = min(j, ctx + 2) - 2  # index of j inside d2
    lo, hi = max(0, c - half), min(len(d2), c + half)
    peak = float(np.max(np.abs(d2[lo:hi]))) if hi > lo else 0.0
    m = np.ones(len(d2), bool)
    m[max(0, c - excl):c + excl] = False
    bg = float(np.sqrt(np.mean(d2[m] ** 2))) if m.any() else 0.0
    if bg <= 1e-12 or peak <= 1e-12:
        return None, peak
    return 20 * math.log10(peak / bg), peak


def probe(p):
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                      "stream=codec_type,codec_name,channels,sample_rate,bit_rate,duration:stream_disposition=default:format=duration",
                                      "-of", "json", p], capture_output=True, text=True).stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("final"); ap.add_argument("v1pp", metavar="postprocessed"); ap.add_argument("raw")
    ap.add_argument("--cards", nargs="*", type=float, default=None,
                    help="card click times (s); default: every sound_effect segment start")
    ap.add_argument("--json")
    audio_config.add_args(ap)
    a = ap.parse_args()
    br.set_project(audio_config.resolve(a))
    rep = {}
    pf = probe(a.final)
    ch = int(pf["streams"][[x["codec_type"] for x in pf["streams"]].index("audio")]["channels"])
    fa = al.load(a.final, channels=ch)
    f = fa if ch == 1 else fa[:, 0].copy()   # one channel of the dual-mono final (L == R checked below)
    if ch == 2:
        rep["final_LR_identical_max_abs_diff"] = float(np.max(np.abs(fa[:, 0] - fa[:, 1])))
    del fa
    v = al.load(a.v1pp)
    n = min(len(f), len(v))
    f, v = f[:n], v[:n]

    pid = br.project_id()
    tl = br.read_timeline(pid)
    con = br.db_copy()
    plan = rm.build_plan(tl, con)
    rep["timeline_hash"] = tl["timeline_hash"]
    rep["duration_ms"] = tl["duration_ms"]
    cards_t = a.cards if a.cards is not None else sorted(s_.t0 for s_ in plan["sfx"])

    # 1. A/V sync vs the post-processed export
    lag, wins = rm.global_lag(v, f, n=10)
    rep["lag_vs_postprocessed"] = {"median_ms": round(lag * 1000 / SR, 3), "windows": wins}
    seg = []
    for s in plan["mic"]:
        if s.t1 - s.t0 < 1.0:
            continue
        i, j = int(s.t0 * SR), int(s.t1 * SR)
        if np.sum(v[i:j].astype(np.float64) ** 2) < 1e-8:
            continue
        g, pk = al.gcc_phat(v[i:j], f[i:j], int(0.1 * SR))
        seg.append({"id": s.id, "t_s": round(s.t0, 3), "lag_ms": round(float(g) * 1000 / SR, 3), "peak": round(float(pk), 3)})
    good = [x for x in seg if x["peak"] > 0.08]
    rep["per_mic_segment_lag_vs_postprocessed"] = {
        "segments": len(seg), "confident": len(good),
        "max_abs_ms": round(max(abs(x["lag_ms"]) for x in good), 3) if good else None,
        "over_1ms": [x for x in good if abs(x["lag_ms"]) > 1.0],
        "low_confidence": [x for x in seg if x["peak"] <= 0.08][:10]}

    # 2. pop/click scan at every mic segment edge
    edges = []
    for s in plan["mic"]:
        edges.append((round(s.t0 * SR), s.id, "start", s.prev_join or "free"))
        edges.append((round(s.t1 * SR), s.id, "end", s.next_join or "free"))
    seen, uniq = set(), []
    for e in sorted(edges):
        if e[0] in seen:
            continue
        seen.add(e[0]); uniq.append(e)
    rows = []
    for j, sid, side, kind in uniq:
        if j <= 2500 or j >= n - 2500:
            continue
        sf, pf_ = click_score(f, j)
        sv, pv = click_score(v, j)
        rows.append({"t_s": round(j / SR, 4), "segment": sid, "edge": side, "join": kind,
                     "final_score_db": None if sf is None else round(sf, 1), "final_peak_d2": round(pf_, 5),
                     "borumi_score_db": None if sv is None else round(sv, 1)})
    scored = [r for r in rows if r["final_score_db"] is not None]
    scored.sort(key=lambda r: -r["final_score_db"])
    rng = np.random.default_rng(7)
    starts = [(int(s.t0 * SR) + 2500, int(s.t1 * SR) - 2500) for s in plan["mic"] if s.t1 - s.t0 > 0.2]
    base = []
    edge_set = np.array(sorted(seen)) if seen else np.array([-10 ** 9])
    tries = 0
    while len(base) < 2000 and starts and tries < 200000:
        tries += 1
        a_, b_ = starts[rng.integers(len(starts))]
        if b_ <= a_:
            continue
        j = int(rng.integers(a_, b_))
        if j + 2400 > n or np.min(np.abs(edge_set - j)) < 2400:
            continue
        sc, _ = click_score(f, j)
        if sc is not None and al.rms_db(f[j - 2400:j + 2400]) > -50:
            base.append(sc)
    bv = [r["borumi_score_db"] for r in rows if r["borumi_score_db"] is not None]
    rep["pop_scan"] = {
        "edges_scanned": len(rows),
        "metric": "peak |2nd difference| within +-2 ms of the edge vs RMS of the 2nd difference over +-50 ms (centre +-5 ms excluded), dB",
        "worst5_final": scored[:5],
        "final_edges_over_20dB": sum(1 for r in scored if r["final_score_db"] > 20),
        "baseline_random_speech_positions": {"n": len(base), "p50": round(float(np.percentile(base, 50)), 1),
                                             "p95": round(float(np.percentile(base, 95)), 1),
                                             "p99": round(float(np.percentile(base, 99)), 1), "max": round(float(np.max(base)), 1)} if base else None,
        "borumi_same_edges": {"p95": round(float(np.percentile(bv, 95)), 1), "max": round(float(np.max(bv)), 1),
                              "over_20dB": sum(1 for x in bv if x > 20)} if bv else None,
    }

    # 3. card clicks
    lv = lambda x, t0, t1: (round(al.rms_db(x[int(t0 * SR):int(t1 * SR)]), 1),
                            round(20 * math.log10(float(np.max(np.abs(x[int(t0 * SR):int(t1 * SR)]))) + 1e-12), 1))
    cards = []
    Lf = al.ebur128(f)["integrated_lufs"]; Lv = al.ebur128(v)["integrated_lufs"]  # both measured as one channel
    sfx_t = sorted(s_.t0 for s_ in plan["sfx"])
    for t in cards_t:
        if (t + 1.6) * SR > n:
            continue
        fr, fp = lv(f, t, t + 0.93); vr, vp = lv(v, t, t + 0.93)
        b = lambda x: max(al.rms_db(x[int((t + k * 0.1) * SR):int((t + (k + 1) * 0.1) * SR)]) for k in range(9))
        fl, _ = lv(f, t + 1.0, t + 1.6)  # rest of the card after the click
        cards.append({"t_s": t, "sfx_segment_at_t": any(abs(x - t) < 0.002 for x in sfx_t),
                      "final_rms_dbfs_per_channel": fr, "final_peak_dbfs_per_channel": fp, "final_loudest_100ms_dbfs": round(b(f), 1),
                      "borumi_rms_dbfs": vr, "borumi_peak_dbfs": vp,
                      "click_rel_program_final_db": round(fr - Lf, 1), "click_rel_program_borumi_db": round(vr - Lv, 1),
                      "final_after_click_dbfs": fl})
    rep["card_clicks"] = {"program_lufs_one_channel": {"final": Lf, "borumi": Lv}, "cards": cards}

    # 4. loudness + durations
    rep["loudness"] = {"final": al.ebur128(a.final), "postprocessed": al.ebur128(a.v1pp), "raw_export": al.ebur128(a.raw)}
    rep["streams"] = {"final": pf, "postprocessed": probe(a.v1pp), "raw_export": probe(a.raw)}
    if a.json:
        tmp = a.json + ".tmp"
        json.dump(rep, open(tmp, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
        os.replace(tmp, a.json)
    print(json.dumps({k: rep[k] for k in ("lag_vs_postprocessed",)}, indent=0)[:600])
    print(json.dumps(rep["per_mic_segment_lag_vs_postprocessed"])[:800])
    print(json.dumps(rep["pop_scan"], indent=1))
    print(json.dumps(rep["card_clicks"], indent=0))
    print(json.dumps(rep["loudness"]))


if __name__ == "__main__":
    main()
