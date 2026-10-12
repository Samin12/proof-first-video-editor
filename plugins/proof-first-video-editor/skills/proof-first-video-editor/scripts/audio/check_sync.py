#!/usr/bin/env python3
"""Verify a deliverable's audio by correlating it against the mastered stem it was built from.

usage:
  check_sync.py FINAL.(mp4|m4a|wav) REFERENCE.(wav|mp4) [--keep EDL.json] [--win 20] [--step 90] [--start 20]
                [--max-lag-ms 100] [--tol-samples 0.5] [--min-peak 0.05] [--min-piece-s 1.0]
                [--max-len-diff-ms 50] [--json OUT.json]

Typical references:
  * remix.py final                -> <audio_dir>/stems/voice.wav (the mastered voice stem; it dominates the mix)
  * master_mix.py final           -> "<OUT>.auphonic_work/auphonic.wav" (the Auphonic master WAV)
  * a shorter v-next cut made from the finished master -> the master's auphonic.wav + --keep EDL.json, where
    the EDL lists the kept pieces of the master in order; the reference is rebuilt by concatenating them
    (joins without crossfade: an 8 ms crossfade at the joins does not move a 20 s window's peak).

EDL formats accepted by --keep:
  {"keep_frames": [[f0, f1], ...], "fps": 60}   frame ranges [f0, f1) of the master (end exclusive)
  {"keep_s": [[t0, t1], ...]}                     seconds
  {"keep_ms": [[t0, t1], ...]}                    milliseconds

Report (exit 0 only if every check passes):
  * windows: integer + sub-sample GCC-PHAT lag of FINAL vs REFERENCE on 20 s windows every 90 s (short files:
    the most active window); confident = peak > --min-peak; all confident |lag| <= --tol-samples
  * per-piece lag (with --keep): one lag per kept piece >= --min-piece-s, i.e. a per-segment lag report
  * length: |len(FINAL) - len(REFERENCE)| <= --max-len-diff-ms (AAC adds/trims a few ms; 10 ms is typical)
  * channels: whether L == R; the level comparison uses ONE channel of FINAL
  * gain: least-squares level of FINAL vs REFERENCE on the confident windows, with the inferred stereo
    convention: about -3.01 dB = ffmpeg -ac 2 upmix of a mono master (pan law, loudness preserved);
    about 0 dB = the mono master duplicated at unity (dual mono, +3 dB louder as stereo). Informational:
    against a voice-only stem the gain also reflects the mix/master-bus gain.
  * loudness (EBU R128) of both files
Requirements: ffmpeg >= 6, numpy.
"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_lib as al  # noqa: E402
import numpy as np  # noqa: E402

SR = al.SR


def load_keep(path):
    d = json.load(open(path))
    if "keep_frames" in d:
        fps = float(d.get("fps") or 60)
        return [(int(round(a * SR / fps)), int(round(b * SR / fps))) for a, b in d["keep_frames"]]
    if "keep_s" in d:
        return [(int(round(a * SR)), int(round(b * SR))) for a, b in d["keep_s"]]
    if "keep_ms" in d:
        return [(int(round(a * SR / 1000)), int(round(b * SR / 1000))) for a, b in d["keep_ms"]]
    raise SystemExit(f"{path}: expected keep_frames(+fps), keep_s or keep_ms")


def window_lags(ref, sig, win_s, step_s, start_s, max_lag, min_peak):
    n = min(len(ref), len(sig))
    ts = list(range(int(start_s), int(n / SR) - 30, int(step_s)))
    win = win_s
    if not ts:
        win = max(1.0, min(win_s, n / SR / 2))
        ts = al.active_windows(ref[:n], SR, win, 1)
    out = []
    for t in ts:
        i, j = int(t * SR), int((t + win) * SR)
        if j > n or j - i < SR // 2:
            continue
        g, pk = al.gcc_phat(ref[i:j], sig[i:j], max_lag)
        out.append({"t_s": round(float(t), 3), "lag_samples": round(float(g), 3), "peak": round(float(pk), 4),
                    "confident": bool(pk > min_peak)})
    return out


def ls_gain_db(ref, sig, spans):
    num = den = 0.0
    for i, j in spans:
        r = ref[i:j].astype(np.float64); s = sig[i:j].astype(np.float64)
        num += float(np.dot(r, s)); den += float(np.dot(r, r))
    if den <= 0 or num <= 0:
        return None
    return round(20 * math.log10(num / den), 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("final"); ap.add_argument("reference")
    ap.add_argument("--keep", help="EDL of kept master pieces (v-next cut verification)")
    ap.add_argument("--win", type=float, default=20.0)
    ap.add_argument("--step", type=float, default=90.0)
    ap.add_argument("--start", type=float, default=20.0)
    ap.add_argument("--max-lag-ms", type=float, default=100.0)
    ap.add_argument("--tol-samples", type=float, default=0.5, help="max |lag| of confident windows (default 0.5 sample)")
    ap.add_argument("--min-peak", type=float, default=0.05)
    ap.add_argument("--min-piece-s", type=float, default=1.0)
    ap.add_argument("--max-len-diff-ms", type=float, default=50.0)
    ap.add_argument("--json")
    a = ap.parse_args()

    rep = {"final": os.path.abspath(a.final), "reference": os.path.abspath(a.reference), "checks": []}

    def check(name, ok, info):
        rep["checks"].append({"check": name, "pass": bool(ok), "info": info})
        print(("[PASS] " if ok else "[FAIL] ") + name + " - " + json.dumps(info)[:300], flush=True)

    ch = al.channels(a.final)
    fa = al.load(a.final, channels=ch)
    if ch >= 2:
        d = float(np.max(np.abs(fa[:, 0] - fa[:, 1]))) if len(fa) else 0.0
        rep["final_channels"] = {"channels": ch, "LR_max_abs_diff": d, "LR_identical": d < 1e-4}
        f = fa[:, 0].copy()
    else:
        rep["final_channels"] = {"channels": 1}
        f = fa
    del fa
    ref = al.load_one_channel(a.reference)
    pieces = None
    if a.keep:
        pieces = load_keep(a.keep)
        ref = np.concatenate([ref[s:e] for s, e in pieces if e > s]) if pieces else ref[:0]
        rep["keep_pieces"] = len(pieces)
    max_lag = int(a.max_lag_ms * SR / 1000)

    wins = window_lags(ref, f, a.win, a.step, a.start, max_lag, a.min_peak)
    conf = [w for w in wins if w["confident"]]
    rep["windows"] = wins
    check(f"FINAL in sync with REFERENCE (all confident windows |lag| <= {a.tol_samples} samples)",
          bool(conf) and all(abs(w["lag_samples"]) <= a.tol_samples for w in conf),
          {"windows": len(wins), "confident": len(conf),
           "lags_samples": sorted({w["lag_samples"] for w in conf})[:12]})
    if pieces:
        per, o = [], 0
        for k, (s, e) in enumerate(pieces):
            L = e - s
            if L >= a.min_piece_s * SR and o + L <= len(f):
                g, pk = al.gcc_phat(ref[o:o + L], f[o:o + L], min(max_lag, L // 4))
                per.append({"piece": k, "out_s": round(o / SR, 3), "src_s": round(s / SR, 3), "dur_s": round(L / SR, 3),
                            "lag_samples": round(float(g), 3), "peak": round(float(pk), 4)})
            o += L
        good = [p for p in per if p["peak"] > a.min_peak]
        rep["per_piece_lag"] = per
        check(f"every kept piece in sync (|lag| <= {max(a.tol_samples, 1.0)} samples)",
              bool(good) and all(abs(p["lag_samples"]) <= max(a.tol_samples, 1.0) for p in good),
              {"pieces": len(per), "confident": len(good),
               "worst": sorted(good, key=lambda p: -abs(p["lag_samples"]))[:3]})
    dl = (len(f) - len(ref)) * 1000 / SR
    check(f"length matches (|diff| <= {a.max_len_diff_ms:g} ms)", abs(dl) <= a.max_len_diff_ms,
          {"final_s": round(len(f) / SR, 3), "reference_s": round(len(ref) / SR, 3), "diff_ms": round(dl, 2)})
    W = int(a.win * SR)
    spans = [(int(w["t_s"] * SR), min(len(f), len(ref), int(w["t_s"] * SR) + W)) for w in conf]
    g = ls_gain_db(ref, f, spans)
    conv = None
    if g is not None:
        conv = ("ffmpeg -ac 2 upmix of a mono master (-3.01 dB per channel, loudness preserved)" if abs(g + 3.01) < 0.5 else
                "unity (dual mono duplicate, or mono)" if abs(g) < 0.5 else "other (mix/master gain differs)")
    rep["gain_final_one_channel_vs_reference_db"] = g
    rep["stereo_convention_inferred"] = conv
    rep["loudness"] = {"final": al.ebur128(a.final), "reference": al.ebur128(a.reference)}
    print(f"gain {g} dB -> {conv}; loudness final {rep['loudness']['final']} reference {rep['loudness']['reference']}")
    ok = all(c["pass"] for c in rep["checks"])
    rep["ok"] = ok
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
