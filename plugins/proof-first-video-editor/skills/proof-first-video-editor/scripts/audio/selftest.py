#!/usr/bin/env python3
"""Offline self-test of the audio toolkit on synthetic signals: no Borumi, no Auphonic, no network.

usage:
  selftest.py [--workdir DIR] [--keep]

Writes a few short synthetic files into --workdir (default: a new temp dir, removed afterwards unless --keep)
and checks the facts the pipeline relies on:
  1. gcc_phat / xcorr_lag recover known delays (sub-sample) and verify_pair() flags a 1 ms shift
  2. ffmpeg mono -> stereo upmix (-ac 2) puts -3.01 dB on each channel and keeps integrated loudness
  3. ffmpeg stereo -> mono downmix gain depends on the OUTPUT sample format: float (L+R)/sqrt(2), integer (L+R)/2
  4. an ffmpeg AAC/mp4 round trip is sample-aligned (edit list honoured): check_sync passes, a 2 ms delay fails
  5. remix.render(): butt-cut crossfade keeps a continuous source seamless; fit_gains() recovers mix gains
  6. remix.master_bus() lands on the loudness target (dual-mono convention) under the true-peak ceiling
  7. check_sync --keep rebuilds a cut from its EDL and reports per-piece lag 0
Exit 0 when everything passes. Requirements: ffmpeg >= 6, numpy.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_lib as al  # noqa: E402
import numpy as np  # noqa: E402
import remix as rm  # noqa: E402

SR = al.SR
results = []


def ok(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + (f" - {info}" if info != "" else ""), flush=True)


def speechy(seconds, seed=1):
    """Amplitude-modulated band-limited noise with pauses: speech-like enough for GCC-PHAT and EBU R128."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = rng.standard_normal(n)
    k = np.exp(-np.arange(64) / 8.0)
    x = np.convolve(x, k / k.sum(), mode="same")
    t = np.arange(n) / SR
    env = 0.5 * (1 + np.sin(2 * np.pi * 3.1 * t)) * (np.sin(2 * np.pi * 0.21 * t) > -0.3)
    return (0.25 * x * env).astype(np.float32)


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def run_tool(script, *args):
    p = subprocess.run([sys.executable, os.path.join(HERE, script), *args], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    wd = os.path.abspath(a.workdir) if a.workdir else tempfile.mkdtemp(prefix="pfve-audio-selftest-")
    os.makedirs(wd, exist_ok=True)
    try:
        x = speechy(150)
        src = os.path.join(wd, "src.wav"); al.write_wav(src, x, codec="pcm_s24le")

        # 1. lag estimators + verify_pair
        for d in (37, -5):
            y = rm.shift(x, d)
            g, pk = al.gcc_phat(x[SR * 10:SR * 30], y[SR * 10:SR * 30], 4800)
            ok(f"gcc_phat recovers a {d}-sample delay", abs(g - d) < 0.25, f"{g:.3f} (peak {pk:.2f})")
        frac = np.interp(np.arange(len(x)) - 0.5, np.arange(len(x)), x).astype(np.float32)
        g, _ = al.xcorr_lag(x[SR * 10:SR * 30].astype(np.float64), frac[SR * 10:SR * 30].astype(np.float64), 480)
        ok("xcorr_lag sub-sample estimate (0.5 sample)", abs(g - 0.5) < 0.2, f"{g:.3f}")
        same = os.path.join(wd, "same.wav"); al.write_wav(same, x * 0.5, codec="pcm_s24le")
        late = os.path.join(wd, "late.wav"); al.write_wav(late, rm.shift(x, 48), codec="pcm_s24le")
        ok("verify_pair accepts a gain-only change", al.verify_pair(src, same)["ok"])
        ok("verify_pair rejects a 1 ms shift", not al.verify_pair(src, late)["ok"])

        # 2. mono -> stereo pan law
        mono_m4a = os.path.join(wd, "mono_to_stereo.m4a")
        ff("-i", src, "-ac", "2", "-c:a", "aac", "-b:a", "320k", "-ar", str(SR), mono_m4a)
        st = al.load(mono_m4a, channels=2)
        gdb = 20 * np.log10(np.std(st[SR:-SR, 0]) / np.std(x[SR:len(st) - SR]))
        ok("-ac 2 upmix = -3.01 dB per channel", abs(gdb + 3.01) < 0.15, f"{gdb:.2f} dB")
        L_src, L_st = al.ebur128(src)["integrated_lufs"], al.ebur128(mono_m4a)["integrated_lufs"]
        ok("-ac 2 upmix keeps integrated loudness", abs(L_src - L_st) <= 0.3, f"{L_src} -> {L_st} LUFS")

        # 3. downmix gain depends on output sample format
        dual = os.path.join(wd, "dual.wav"); al.write_wav(dual, np.repeat(x[:, None], 2, axis=1))
        flt = al.load(dual)  # float pipe
        i24 = os.path.join(wd, "dual_to_mono_s24.wav")
        ff("-i", dual, "-ac", "1", "-c:a", "pcm_s24le", i24)
        r_f = 20 * np.log10(np.std(flt) / np.std(x)); r_i = 20 * np.log10(np.std(al.load(i24)) / np.std(x))
        ok("dual mono -> -ac 1 FLOAT output = +3.01 dB", abs(r_f - 3.01) < 0.05, f"{r_f:.2f} dB")
        ok("dual mono -> -ac 1 INTEGER output = 0 dB", abs(r_i) < 0.05, f"{r_i:.2f} dB")
        pan = os.path.join(wd, "dual_pan.wav")
        ff("-i", dual, "-af", "pan=mono|c0=0.5*c0+0.5*c1", "-c:a", "pcm_s24le", pan)
        r_p = 20 * np.log10(np.std(al.load(pan)) / np.std(x))
        ok("explicit pan 0.5/0.5 = 0 dB (master_mix.py extraction)", abs(r_p) < 0.05, f"{r_p:.2f} dB")

        # 4. AAC/mp4 round trip alignment via check_sync
        rc, outp = run_tool("check_sync.py", mono_m4a, src)
        ok("check_sync: ffmpeg AAC round trip in sync (upmix inferred)", rc == 0 and "upmix" in outp, outp.strip().splitlines()[-1])
        delayed = os.path.join(wd, "delayed.m4a")
        ff("-i", src, "-af", "adelay=2", "-ac", "2", "-c:a", "aac", "-b:a", "320k", delayed)
        rc, outp = run_tool("check_sync.py", delayed, src)
        ok("check_sync: a 2 ms delay fails", rc == 1)

        # 5. render + fit_gains
        n = 20 * SR
        segs = [rm.Seg(layer="mic", kind="mic", id="a", media="m", path=src, t0=1.0, t1=7.0, s0=10.0, speed=1.0,
                       gain_db=0.0, oneshot=False, fade_in_s=0, fade_out_s=0, loop=False, comp=0),
                rm.Seg(layer="mic", kind="mic", id="b", media="m", path=src, t0=7.0, t1=12.0, s0=16.0, speed=1.0,
                       gain_db=0.0, oneshot=False, fade_in_s=0, fade_out_s=0, loop=False, comp=0)]
        segs[0].next_join = segs[1].prev_join = "contig"  # s1 of a (16.0) == s0 of b
        cache = rm.SourceCache()
        stem, lab = rm.render(segs, n, lambda s: s.path, cache)
        expect = np.zeros(n, np.float32); expect[SR:12 * SR] = x[10 * SR:21 * SR]
        mid = slice(int(1.02 * SR), int(11.98 * SR))
        ok("render: contiguous join is sample-identical to the source", np.max(np.abs(stem[mid] - expect[mid])) < 1e-6)
        segs[1].s0 = 40.0
        segs[0].next_join = segs[1].prev_join = "cut"
        stem2, lab2 = rm.render(segs, n, lambda s: s.path, cache)
        j = 7 * SR
        jump = np.max(np.abs(np.diff(stem2[j - 480:j + 480])))
        ok("render: butt cut is crossfaded (no step larger than the material)", jump < 4 * np.max(np.abs(np.diff(x[:SR]))))
        other = speechy(20, seed=5) * 0.3
        stems = {"voice": stem2, "raw_voice": stem2, "system": other, "audio": np.zeros(n, np.float32),
                 "sfx": np.zeros(n, np.float32), "builtin": np.zeros(n, np.float32), "music": np.zeros(n, np.float32)}
        labels = {"voice": lab2, "raw_voice": lab2, "system": np.zeros(n, np.int16),
                  "audio": np.full(n, -1, np.int16), "sfx": np.full(n, -1, np.int16),
                  "builtin": np.full(n, -1, np.int16), "music": np.full(n, -1, np.int16)}
        comps = [{"stem": "voice", "label": 0, "key": "m", "segments": 2, "assumed_gain_db": 0.0},
                 {"stem": "system", "label": 0, "key": "s", "segments": 1, "assumed_gain_db": 0.0}]
        y = 0.5 * stem2 + 2.0 * other
        gains = rm.fit_gains(y, stems, labels, comps)
        ok("fit_gains recovers 0.5 x voice + 2.0 x system",
           abs(gains[("raw_voice", 0)] - 0.5) < 1e-3 and abs(gains[("system", 0)] - 2.0) < 1e-3,
           {k[0]: round(v, 4) for k, v in gains.items()})

        # 6. master bus
        rm.TARGET_LUFS, rm.TARGET_TP = -16.0, -1.0
        mb_wd = os.path.join(wd, "mb"); os.makedirs(mb_wd, exist_ok=True)
        fin, mb = rm.master_bus(x * 0.2, mb_wd, dualmono=True)
        m = al.ebur128(fin, dualmono=True)
        ok("master_bus: -16 LUFS as dual mono, true peak <= -1.4 dBTP",
           abs(m["integrated_lufs"] + 16) <= 0.1 and m["true_peak_dbtp"] <= -1.4, m)
        ok("master_bus: limiter adds no latency", abs(mb["limiter_lag_samples"]) < 0.5, mb["limiter_lag_samples"])

        # 7. v-next cut verification with --keep
        keep = [[600, 1800], [2400, 4200], [4800, 7200]]  # frames at 60 fps
        cut = np.concatenate([x[s * 800:e * 800] for s, e in keep])
        cut_m4a = os.path.join(wd, "cut.m4a"); al.write_wav(os.path.join(wd, "cut.wav"), cut)
        ff("-i", os.path.join(wd, "cut.wav"), "-ac", "2", "-c:a", "aac", "-b:a", "320k", cut_m4a)
        edl = os.path.join(wd, "edl.json"); json.dump({"keep_frames": keep, "fps": 60}, open(edl, "w"))
        rc, outp = run_tool("check_sync.py", cut_m4a, src, "--keep", edl, "--json", os.path.join(wd, "cut.json"))
        per = json.load(open(os.path.join(wd, "cut.json"))).get("per_piece_lag", [])
        ok("check_sync --keep: cut in sync, per-piece lag reported", rc == 0 and len(per) == 3,
           [p["lag_samples"] for p in per])
    finally:
        if not a.keep and not a.workdir:
            shutil.rmtree(wd, ignore_errors=True)
        elif not a.keep:
            for f in os.listdir(wd):
                p = os.path.join(wd, f)
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
    bad = [n for n, c in results if not c]
    print(f"{len(results) - len(bad)}/{len(results)} passed" + (f"; FAILED: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
