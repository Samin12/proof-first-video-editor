#!/usr/bin/env python3
"""Shared audio helpers: decode/encode through ffmpeg, EBU R128 loudness, sample-accurate lag measurement.

Imported by every script in this folder; not meant to be run directly (running it prints a self-description).

Requirements: ffmpeg/ffprobe >= 6 on PATH (ebur128 peak=true, alimiter latency=true), numpy, and scipy
(recommended: the 60 Hz envelope lag in verify_pair() and the coherence in remix.py's null test use it; both
are informational and are skipped without scipy).

If numpy is missing from the running interpreter, the calling script re-executes itself ONCE (guarded by the
environment variable PFVE_AUDIO_REEXEC) under the first that exists: $PFVE_AUDIO_PYTHON, <this folder>/.venv/bin/python,
or `uv run --no-project --with numpy --with scipy python` when uv is on PATH. Create the venv with:
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
`--help` works without numpy (a placeholder module is used; any real numpy use then stops with the install hint).
(A venv made by `python3 -m venv` symlinks to the base interpreter, so comparing resolved executables cannot tell the
two apart: that is why the guard is an environment variable.)

Level conventions used everywhere (see references/audio.md, "Lessons"):
  * load(path) decodes to mono FLOAT. ffmpeg's stereo->mono downmix with a float output is (L+R)/sqrt(2),
    i.e. +3.01 dB on a dual-mono file; with an integer output (pcm_s16/s24 WAV) it is normalized to (L+R)/2.
    To measure the level of a stereo deliverable, load(path, channels=2)[:, 0] (one channel).
  * ebur128(..., dualmono=True) measures a mono signal as the dual-mono stereo file it will become (+3.01 dB).
"""
import json, os, re, shutil, subprocess, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
_REEXEC_ENV = "PFVE_AUDIO_REEXEC"
_NEED_NUMPY = ("numpy is required. In scripts/audio: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt "
               "(or point $PFVE_AUDIO_PYTHON at a python with numpy + scipy, or install uv)")


def _venv_candidates():
    return [p for p in (os.environ.get("PFVE_AUDIO_PYTHON"), os.path.join(HERE, ".venv", "bin", "python")) if p]


def _reexec_with_numpy():
    """Re-run the calling script once under a python that has numpy. The env guard (not a comparison of resolved
    executables) stops a loop: a `python3 -m venv` venv resolves to the same binary as the python that made it."""
    script = sys.argv[0] if sys.argv else ""
    if os.environ.get(_REEXEC_ENV) == "1" or script in ("", "-c") or not os.path.exists(script):
        return
    env = dict(os.environ, **{_REEXEC_ENV: "1"})
    argv = [os.path.abspath(script)] + sys.argv[1:]
    for cand in _venv_candidates():
        if os.path.exists(cand):
            os.execve(cand, [cand] + argv, env)  # keep the venv path (not its realpath) so pyvenv.cfg is found
    uv = shutil.which("uv")
    if uv:
        os.execve(uv, [uv, "run", "--no-project", "--with", "numpy", "--with", "scipy", "python"] + argv, env)


def _numpy_placeholder():
    """For `--help` without numpy: argparse prints usage before any numpy use; anything else stops with the hint."""
    mod = types.ModuleType("numpy")
    mod._pfve_placeholder = True

    def _missing(name):
        raise SystemExit(_NEED_NUMPY)
    mod.__getattr__ = _missing
    sys.modules["numpy"] = mod
    return mod


try:
    import numpy as np
    os.environ.pop(_REEXEC_ENV, None)  # children started from here decide for themselves
except ModuleNotFoundError:  # re-exec under a python that has numpy (and ideally scipy)
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        np = _numpy_placeholder()
    else:
        _reexec_with_numpy()
        raise SystemExit(_NEED_NUMPY)
HAVE_NUMPY = "_pfve_placeholder" not in vars(np)
try:
    if not HAVE_NUMPY:
        raise ModuleNotFoundError("numpy placeholder (--help)")
    from scipy import signal
except ModuleNotFoundError:  # optional: envelope lag + coherence are informational only
    signal = None

SR = 48000


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=duration:format=duration", "-of", "json", path], capture_output=True, text=True).stdout
    d = json.loads(out)
    s = (d.get("streams") or [{}])[0].get("duration")
    return float(s if s not in (None, "N/A") else d["format"]["duration"])


def channels(path, stream="a:0"):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", "stream=channels",
                          "-of", "json", path], capture_output=True, text=True).stdout
    return int(json.loads(out)["streams"][0]["channels"])


def load(path, start=None, dur=None, sr=SR, channels=1, stream="a:0"):
    """Decode to float32 numpy, shape (n,) for mono or (n, ch). start/dur in seconds.
    channels=1 on a stereo file = ffmpeg float downmix (L+R)/sqrt(2) (+3.01 dB on dual mono)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{start:.6f}"]
    cmd += ["-i", path]
    if dur is not None:
        cmd += ["-t", f"{dur:.6f}"]
    cmd += ["-map", f"0:{stream}", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4").copy()
    return x if channels == 1 else x.reshape(-1, channels)


def load_one_channel(path, sr=SR):
    """Channel 0 of the first audio stream (a mono file is returned as is). Use this to measure a deliverable's
    per-channel level without ffmpeg's downmix gain."""
    ch = channels(path)
    x = load(path, sr=sr, channels=ch)
    return x if ch == 1 else x[:, 0].copy()


def write_wav(path, x, sr=SR, codec="pcm_f32le"):
    x = np.asarray(x, dtype="<f4")
    ch = 1 if x.ndim == 1 else x.shape[1]
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", str(ch), "-i", "-",
                        "-c:a", codec, path], input=x.tobytes(), capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode()[-500:])


def ebur128(path_or_array, sr=SR, dualmono=False):
    """Integrated loudness (LUFS), true peak (dBTP), LRA via ffmpeg ebur128.
    dualmono=True measures a mono signal as it will measure once duplicated to L+R (+3.01 dB)."""
    tmp = None
    path = path_or_array
    if not isinstance(path_or_array, str):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        write_wav(tmp, path_or_array, sr)
        path = tmp
    try:
        err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path, "-map", "0:a:0",
                              "-af", "ebur128=peak=true:framelog=quiet" + (":dualmono=true" if dualmono else ""), "-f", "null", "-"],
                             capture_output=True, text=True).stderr
    finally:
        if tmp:
            os.remove(tmp)
    summ = err[err.rfind("Summary:"):]
    g = lambda pat: (lambda m: float(m.group(1)) if m else None)(re.search(pat, summ))
    return {"integrated_lufs": g(r"I:\s+(-?[\d.]+|-inf) LUFS"), "lra_lu": g(r"LRA:\s+(-?[\d.]+) LU"),
            "true_peak_dbtp": g(r"Peak:\s+(-?[\d.]+|-inf) dBFS")}


def rms_db(x):
    x = np.asarray(x, dtype=np.float64)
    return float(10 * np.log10(np.mean(x ** 2) + 1e-20))


def _parabolic(c, i):
    if 0 < i < len(c) - 1:
        a, b, d = c[i - 1], c[i], c[i + 1]
        den = a - 2 * b + d
        if den != 0:
            return i + 0.5 * (a - d) / den
    return float(i)


def gcc_phat(ref, sig, max_lag):
    """Delay of sig relative to ref in samples (sig[n] ~ ref[n - d]); sub-sample by parabola.
    Returns (lag_samples, peak). Peak ~0.05-0.9: higher = more confident; level-independent."""
    n = len(ref) + len(sig)
    nfft = 1 << (n - 1).bit_length()
    R = np.fft.rfft(sig, nfft) * np.conj(np.fft.rfft(ref, nfft))
    R /= np.abs(R) + 1e-12
    cc = np.fft.irfft(R, nfft)
    cc = np.concatenate((cc[-max_lag:], cc[:max_lag + 1]))
    i = int(np.argmax(np.abs(cc)))
    return _parabolic(np.abs(cc), i) - max_lag, float(np.abs(cc[i]))


def xcorr_lag(ref, sig, max_lag):
    """Plain normalized cross-correlation delay (samples) of sig vs ref. Returns (lag_samples, coefficient)."""
    n = len(ref) + len(sig)
    nfft = 1 << (n - 1).bit_length()
    cc = np.fft.irfft(np.fft.rfft(sig, nfft) * np.conj(np.fft.rfft(ref, nfft)), nfft)
    cc = np.concatenate((cc[-max_lag:], cc[:max_lag + 1]))
    i = int(np.argmax(cc))
    den = np.sqrt(np.sum(ref ** 2) * np.sum(sig ** 2)) + 1e-20
    return _parabolic(cc, i) - max_lag, float(cc[i] / den)


def envelope(x, sr=SR, dec=12):
    """Rectified, 60 Hz low-passed envelope decimated to sr/dec (4 kHz). Needs scipy."""
    sos = signal.butter(4, 60, fs=sr, output="sos")
    e = signal.sosfiltfilt(sos, np.abs(x).astype(np.float64))
    return e[::dec] - np.mean(e[::dec])


def env_lag_ms(ref, sig, sr=SR, max_ms=300):
    """Envelope lag (ms) and correlation; (None, None) without scipy. Informational only (see verify_pair)."""
    if signal is None:
        return None, None
    dec = 12
    a, b = envelope(ref, sr, dec), envelope(sig, sr, dec)
    lag, c = xcorr_lag(a, b, int(max_ms * sr / dec / 1000))
    return lag * dec * 1000 / sr, c


def active_windows(x, sr=SR, win_s=20.0, n=4):
    """Start times (s) of n non-overlapping windows with the most energy, spread over the file."""
    w = int(win_s * sr)
    if len(x) <= w:
        return [0.0]
    hop = sr  # 1 s grid
    c = np.concatenate(([0.0], np.cumsum(x.astype(np.float64) ** 2)))
    starts = np.arange(0, len(x) - w + 1, hop)
    e = (c[starts + w] - c[starts]) / w
    picks = []
    for i in np.argsort(e)[::-1]:
        if e[i] < 1e-4 * e.max() or e[i] <= 0:
            break
        if all(abs(i - p) * hop >= w for p in picks):
            picks.append(int(i))
        if len(picks) == n:
            break
    return sorted(p * hop / sr for p in picks)


def _r(v, nd):
    return None if v is None else round(float(v), nd)


def verify_pair(src, dst, sr=SR, win_s=20.0, n=4):
    """Duration + lag check of a processed file (e.g. an Auphonic output) against its source (lag must be 0 ms).

    ok = |duration diff| <= 20 ms, every GCC-PHAT window |lag| < 0.5 ms, and every plain-xcorr window with
    coefficient > 0.3 |lag| < 0.5 ms. The envelope lag is informational only: leveling + speech isolation
    (reverb tails removed) shift the envelope's centre of mass by a few ms while the waveform is sample-exact."""
    ds, dd = duration(src), duration(dst)
    a, b = load(src, sr=sr), load(dst, sr=sr)
    wins = []
    for t in active_windows(a, sr, win_s, n):
        i, j = int(t * sr), int((t + win_s) * sr)
        g, gpk = gcc_phat(a[i:j], b[i:j], int(0.3 * sr))
        x, xc = xcorr_lag(a[i:j].astype(np.float64), b[i:j].astype(np.float64), int(0.3 * sr))
        e, ec = env_lag_ms(a[i:j], b[i:j], sr)
        wins.append({"t_s": round(float(t), 1), "gcc_phat_lag_ms": round(float(g) * 1000 / sr, 3), "gcc_peak": round(float(gpk), 3),
                     "xcorr_lag_ms": round(float(x) * 1000 / sr, 3), "xcorr_coef": round(float(xc), 3),
                     "env_lag_ms_info": _r(e, 2), "env_corr": _r(ec, 3)})
    lags = [w["gcc_phat_lag_ms"] for w in wins]
    lag = float(np.median(lags))
    lin, lout = ebur128(src), ebur128(dst)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels,codec_name",
                                       "-of", "json", dst], capture_output=True, text=True).stdout)["streams"][0]
    # waveform-level alignment decides (sample-exact); the envelope lag is informational only, because
    # leveling + speech isolation (reverb tails removed) shift the envelope's centre of mass by a few ms.
    ok = abs(dd - ds) * 1000 <= 20 and all(abs(l) < 0.5 for l in lags) and all(
        abs(w["xcorr_lag_ms"]) < 0.5 for w in wins if w["xcorr_coef"] > 0.3)
    return {"ok": bool(ok), "source_duration_s": round(ds, 6), "output_duration_s": round(dd, 6),
            "duration_diff_ms": round((dd - ds) * 1000, 3), "lag_ms": round(lag, 3),
            "lag_method": "GCC-PHAT + plain waveform xcorr on 20 s windows (sub-sample parabolic peak); envelope lag info only",
            "lag_windows": wins,
            "input_lufs": lin["integrated_lufs"], "input_true_peak_dbtp": lin["true_peak_dbtp"],
            "output_lufs": lout["integrated_lufs"], "output_true_peak_dbtp": lout["true_peak_dbtp"],
            "output_format": probe}


if __name__ == "__main__":
    print(__doc__)
    if HAVE_NUMPY:
        print(f"numpy {np.__version__}, scipy {'available' if signal is not None else 'MISSING (optional)'}")
    else:
        print("numpy MISSING: " + _NEED_NUMPY)
