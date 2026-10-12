#!/usr/bin/env python3
"""Full finished-mix Auphonic pass: run a finished video's audio through Auphonic, mux the result back as
STEREO (ffmpeg -3 dB pan law, loudness preserved), verify, and optionally supersede the previous version.

usage:
  master_mix.py VIDEO.mp4 [--out OUT.mp4] [--preset UUID | --no-preset [--loudness -16]]
                [--from-work DIR] [--keep-original-track] [--expect-size 1920x1080]
                [--supersede GLOB ...] [--superseded-dir DIR] [--copy-srt OLD.srt] [--phone PHONE.mp4]
                [--config PATH] [--project ALIAS] [--preset-name NAME] [--poll 15]

  VIDEO.mp4          the finished master (e.g. remix.py's output, or any final export)
  --out              default "<VIDEO stem> (auphonic).mp4"; work files in "<OUT>.auphonic_work/"
  --preset UUID      Auphonic preset (default: config auphonic.preset / $AUPHONIC_PRESET), e.g. "Broadcast Voice"
  --no-preset        use built-in settings instead: Adaptive Leveler, filtering, denoise, --loudness LUFS
                     (an INTEGER from Auphonic's list), max peak -1 dBTP
  --from-work DIR    skip Auphonic: re-use DIR/input.wav + DIR/auphonic.wav (e.g. the work dir left by
                     edit-borumi-video's auphonic_master.py, whose mux is mono) and only mux + verify
  --supersede GLOB   after ALL checks pass, move files matching GLOB (relative to OUT's folder unless absolute;
                     e.g. "My Video - final edit v1*") into --superseded-dir (default "<OUT folder>/superseded").
                     Nothing is ever deleted; OUT and its work dir are never moved.
  --copy-srt OLD     copy OLD.srt to "<OUT stem>.srt" (timing is unchanged by this pass)
  --phone PHONE      also make the 720p phone copy with edit-borumi-video's finalize_export.py --phone
                     (edit_skill_dir in the config / $BORUMI_EDIT_SKILL, default ~/.borumi-edit/skill)

Steps: extract the audio as mono 48 kHz 24-bit WAV (explicit pan: a dual-mono stereo track becomes ONE channel's
level) -> Auphonic production with a mono-mixdown WAV output -> upload -> start -> poll -> download (S3 redirect
followed WITHOUT the Authorization header) -> checks -> mux (video stream copied, AAC 320 kbps 48 kHz, -ac 2).

Checks (all must pass; receipt "<OUT>.master_mix.json"):
  * Auphonic output length == uploaded length (samples)
  * Auphonic output in sync with the upload: every confident 20 s window (every 90 s) at lag 0 samples
  * OUT video duration == VIDEO video duration; OUT audio/video lengths within 50 ms
  * OUT streams: video + AAC 48 kHz stereo (and --expect-size if given)
  * Auphonic WAV at the loudness target +-0.6 LU (measured as MONO, i.e. per channel), true peak <= -0.9 dBTP
  * stereo mux preserves the WAV's integrated loudness +-0.3 LU (ffmpeg's mono->stereo upmix puts -3.01 dB on
    each channel, BS.1770 sums the two channels' power -> same LUFS; true peak per channel drops ~3 dB) and
    true peak <= the configured ceiling (-1 dBTP)
Do NOT duplicate the mono WAV at unity into L and R: that is +3 dB (-16 -> -13 LUFS) and puts the true peak
over the ceiling.

The API key comes from $AUPHONIC_API_KEY or the macOS Keychain (auphonic_api.py), never printed or stored.
Requirements: ffmpeg >= 6, numpy.
"""
import argparse, glob, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_lib as al  # noqa: E402
import audio_config  # noqa: E402
import auphonic_api as aph  # noqa: E402
import numpy as np  # noqa: E402

SR = al.SR
LOUDNESS_TARGETS = (-13, -14, -15, -16, -18, -19, -20, -23, -24, -26, -27, -31)
checks = []


def check(name, ok, info):
    checks.append({"check": name, "pass": bool(ok), "info": info})
    print(("[PASS] " if ok else "[FAIL] ") + name + " - " + str(info)[:400], flush=True)


def run(cmd, **k):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **k)


def stream_duration(p, sel):
    out = run(["ffprobe", "-v", "error", "-select_streams", sel, "-show_entries", "stream=duration",
               "-of", "csv=p=0", p]).stdout.strip().splitlines()
    if out and out[0] not in ("", "N/A"):
        return float(out[0])
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).stdout.strip())


def extract_mono(src, wav):
    """Mono 48 kHz pcm_s24le. Explicit pan for stereo = (L+R)/2, the same as ffmpeg -ac 1 with an integer output
    (with a FLOAT output ffmpeg would use (L+R)/sqrt(2), +3 dB on dual mono). A dual-mono track keeps one channel's level."""
    ch = al.channels(src)
    af = [] if ch == 1 else (["-af", "pan=mono|c0=0.5*c0+0.5*c1"] if ch == 2 else ["-ac", "1"])
    run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-map", "0:a:0", *af, "-ar", str(SR), "-c:a", "pcm_s24le", wav])


def sync_windows(a, b, win_s=20, step_s=90, start_s=20, max_lag=4800, min_peak=0.05):
    """Integer-lag GCC-PHAT on 20 s windows every 90 s (the check that proved Auphonic sample-exact):
    returns [[t_s, lag_samples, peak], ...]. Short files fall back to one window at the most active spot."""
    n = min(len(a), len(b))
    ts = list(range(start_s, int(n / SR) - 30, step_s))
    win = win_s
    if not ts:
        win = max(1, min(win_s, int(n / SR / 2)))
        ts = [int(t) for t in al.active_windows(a[:n], SR, win, 1)]
    out = []
    for t in ts:
        x = a[t * SR:(t + win) * SR].astype(np.float64)
        y = b[t * SR:(t + win) * SR].astype(np.float64)
        if len(x) < SR or len(y) < SR:
            continue
        nf = 1 << int(np.ceil(np.log2(2 * len(x))))
        X = np.fft.rfft(x, nf); Y = np.fft.rfft(y, nf)
        R = X * np.conj(Y); R /= np.abs(R) + 1e-12
        c = np.fft.irfft(R, nf)
        c = np.concatenate([c[-max_lag:], c[:max_lag + 1]])
        k = int(np.argmax(c))
        out.append([t, k - max_lag, round(float(c[k]), 4)])
    return out


def pick_output(outputs):
    dl = [x for x in outputs if x.get("download_url")]
    wavs = [x for x in dl if x.get("ending") == "wav"]
    mono = [x for x in wavs if x.get("mono_mixdown")]
    pick = (mono or wavs or dl)
    if not pick:
        raise aph.AuphonicError(f"no downloadable output: {[(x.get('format'), x.get('ending')) for x in outputs]}")
    return pick[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out")
    ap.add_argument("--no-preset", action="store_true", help="built-in Auphonic settings instead of a preset")
    ap.add_argument("--loudness", type=int, default=-16, choices=LOUDNESS_TARGETS,
                    help="with --no-preset: loudness target LUFS, integer (default -16)")
    ap.add_argument("--from-work", help="re-use DIR/input.wav + DIR/auphonic.wav (no Auphonic call)")
    ap.add_argument("--keep-original-track", action="store_true", help="keep the original mix as a 2nd, non-default track")
    ap.add_argument("--expect-size", help="e.g. 1920x1080: fail if the video stream has another size")
    ap.add_argument("--supersede", nargs="*", default=[], help="globs of older versions to move after all checks pass")
    ap.add_argument("--superseded-dir", help="default '<OUT folder>/superseded'")
    ap.add_argument("--copy-srt", help="copy this SRT to '<OUT stem>.srt'")
    ap.add_argument("--phone", help="also make a 720p phone copy here (edit-borumi-video finalize_export.py --phone)")
    ap.add_argument("--poll", type=int, default=15)
    audio_config.add_args(ap, bundle=False)
    a = ap.parse_args()
    P = audio_config.resolve(a, need_bundle=False)
    src = os.path.abspath(a.video)
    stem = os.path.splitext(src)[0]
    out = os.path.abspath(a.out or f"{stem} (auphonic).mp4")
    work = os.path.abspath(a.from_work) if a.from_work else out + ".auphonic_work"
    os.makedirs(work, exist_ok=True)
    inwav, wav = os.path.join(work, "input.wav"), os.path.join(work, "auphonic.wav")
    target = float(a.loudness) if a.no_preset else P["target_lufs"]
    rec = {"input": src, "output": out, "work": work, "target_lufs": target, "true_peak_ceiling_dbtp": P["target_tp"]}

    if a.from_work:
        for p in (inwav, wav):
            if not os.path.exists(p):
                sys.exit(f"--from-work: missing {p}")
        prev = work[:-len(".auphonic_work")] + ".auphonic.json" if work.endswith(".auphonic_work") else None
        if prev and os.path.exists(prev):
            pr = json.load(open(prev))
            rec.update({"auphonic_production": pr.get("production"), "auphonic_settings": pr.get("settings"),
                        "auphonic_receipt": prev})
        rec["mode"] = "from-work (no Auphonic call)"
    else:
        try:
            key = aph.api_key()
        except aph.AuphonicError as e:
            sys.exit(str(e))
        extract_mono(src, inwav)
        body = {"title": os.path.basename(stem), "output_files": [{"format": "wav", "mono_mixdown": True}]}
        if a.no_preset:
            body["algorithms"] = {"leveler": True, "normloudness": True, "loudnesstarget": a.loudness, "maxpeak": -1,
                                  "filtering": True, "denoise": True, "denoiseamount": 0}
        else:
            body["preset"] = audio_config.require_preset(P)
        try:
            prod = aph.create_production(key, body)
            print("production", prod, flush=True)
            aph.upload(key, prod, inwav)
            aph.start(key, prod)
            d = aph.wait(key, prod, a.poll, log=lambda m: print(time.strftime("%H:%M:%S"), m, flush=True))
            aph.download(key, pick_output(d["output_files"])["download_url"], wav)
        except aph.AuphonicError as e:
            sys.exit(str(e))
        a_in, a_out = aph.level_stats(d)
        rec.update({"mode": "auphonic", "auphonic_production": prod, "auphonic_settings": body,
                    "preset_name": None if a.no_preset else P["preset_name"],
                    "used_credits": d.get("used_credits"), "auphonic_input": a_in, "auphonic_output": a_out,
                    "warning": d.get("warning_message") or None})

    # 1. length + sync of the Auphonic WAV vs what was uploaded
    x_in, x_out = al.load(inwav), al.load(wav)
    wins = sync_windows(x_in, x_out)
    lags = [w[1] for w in wins if w[2] > 0.05]
    rec["lag_windows"] = wins
    check("Auphonic output length == input length", len(x_in) == len(x_out), (len(x_in), len(x_out)))
    check("Auphonic output in sync with input (all confident windows 0 samples)", bool(lags) and all(l == 0 for l in lags),
          {"windows": len(wins), "confident": len(lags), "lags": sorted(set(lags))})
    del x_in, x_out

    # 2. stereo mux (video stream copied); ffmpeg's mono->stereo upmix = -3.01 dB per channel
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", src, "-i", wav, "-map", "0:v:0", "-map", "1:a:0"]
    if a.keep_original_track:
        cmd += ["-map", "0:a:0", "-c:v", "copy", "-ac:a:0", "2", "-c:a:0", "aac", "-b:a:0", "320k", "-ar:a:0", str(SR),
                "-c:a:1", "copy", "-disposition:a:0", "default", "-disposition:a:1", "0",
                "-metadata:s:a:0", f"title=Auphonic {P['preset_name'] if not a.no_preset else 'master'}",
                "-metadata:s:a:1", "title=Original mix"]
    else:
        cmd += ["-c:v", "copy", "-ac", "2", "-c:a", "aac", "-b:a", "320k", "-ar", str(SR)]
    cmd += ["-movflags", "+faststart", out]
    run(cmd)
    dv, da, dv0 = stream_duration(out, "v:0"), stream_duration(out, "a:0"), stream_duration(src, "v:0")
    check("output video duration == input video duration", abs(dv - dv0) < 0.001, (dv, dv0))
    check("output audio/video lengths match (+-50 ms)", abs(dv - da) < 0.05, (dv, da))
    st = json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                         "stream=codec_type,codec_name,width,height,channels,sample_rate", "-of", "json", out]).stdout)["streams"]
    v0 = next((s for s in st if s["codec_type"] == "video"), {})
    a0 = next((s for s in st if s["codec_type"] == "audio"), {})
    ok = bool(v0) and a0.get("codec_name") == "aac" and a0.get("channels") == 2 and str(a0.get("sample_rate")) == str(SR)
    if a.expect_size:
        w, h = (int(z) for z in a.expect_size.lower().split("x"))
        ok = ok and v0.get("width") == w and v0.get("height") == h
    check("output streams: video + AAC 48 kHz stereo" + (f" ({a.expect_size})" if a.expect_size else ""), ok, st)
    Lw, L, L0 = al.ebur128(wav), al.ebur128(out), al.ebur128(src)
    rec.update({"loudness_input_video": L0, "loudness_uploaded_wav": al.ebur128(inwav), "loudness_auphonic_wav": Lw,
                "loudness_output": L})
    check(f"Auphonic WAV at its {target:g} LUFS target (mono = per channel) +-0.6 LU, true peak <= -0.9 dBTP",
          Lw["integrated_lufs"] is not None and abs(Lw["integrated_lufs"] - target) <= 0.6 and Lw["true_peak_dbtp"] <= -0.9, Lw)
    check(f"stereo mux preserves the WAV's loudness (ffmpeg -3 dB pan law; +-0.3 LU), true peak <= {P['target_tp']:g} dBTP",
          L["integrated_lufs"] is not None and abs(L["integrated_lufs"] - Lw["integrated_lufs"]) <= 0.3
          and L["true_peak_dbtp"] <= P["target_tp"], {"wav": Lw, "out": L})

    # 3. deliverables (SRT copy, phone copy), then supersede, only if everything passed
    moved = []
    if all(c["pass"] for c in checks):
        if a.copy_srt:
            dst = os.path.splitext(out)[0] + ".srt"
            if os.path.abspath(a.copy_srt) != dst:
                shutil.copy2(a.copy_srt, dst)
            rec["srt"] = dst
        if a.phone:
            fe = os.path.join(P["edit_skill_dir"], "scripts", "finalize_export.py")
            if not os.path.exists(fe):
                check("phone copy", False, f"finalize_export.py not found at {fe} (set edit_skill_dir / $BORUMI_EDIT_SKILL)")
            else:
                run([sys.executable or "python3", fe, "--phone", out, os.path.abspath(a.phone)])
                ps = run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,channels", "-of", "json", a.phone]).stdout
                check("phone copy 1280x720 with stereo audio", '"width": 1280' in ps and '"channels": 2' in ps, a.phone)
                rec["phone"] = os.path.abspath(a.phone)
    if all(c["pass"] for c in checks) and a.supersede:
        base = os.path.dirname(out)
        sup = os.path.abspath(a.superseded_dir or os.path.join(base, "superseded"))
        os.makedirs(sup, exist_ok=True)
        keep = {out, out + ".master_mix.json", os.path.abspath(a.phone) if a.phone else None,
                rec.get("srt")}
        for pat in a.supersede:
            for f in sorted(glob.glob(pat if os.path.isabs(pat) else os.path.join(base, pat))):
                f = os.path.abspath(f)
                if f in keep or f == work or f.startswith(work + os.sep) or os.path.dirname(f) == sup:
                    continue
                shutil.move(f, os.path.join(sup, os.path.basename(f)))
                moved.append(os.path.basename(f))
        rec["superseded_dir"] = sup
    elif a.supersede:
        print("checks failed: nothing superseded; output kept for inspection", flush=True)
    rec["moved_to_superseded"] = moved
    rec["checks"] = checks
    json.dump(rec, open(out + ".master_mix.json", "w"), indent=1)
    ok = all(c["pass"] for c in checks)
    print("ALL PASS" if ok else "SOME CHECKS FAILED", "->", out)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
