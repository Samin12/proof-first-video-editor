#!/usr/bin/env python3
"""Rebuild the audio of the CURRENT Borumi timeline from the Auphonic-mastered mic sources, at exact timeline
positions, and mux it into a raw Borumi export.

usage:
  remix.py [project options]                                 # dry run: stems (+ preview mix), checks vs transcript
  remix.py [project options] --export RAW.mp4 [--out FINAL.mp4]
                                                             # null test vs the export, mix, -16 LUFS / -1 dBTP, mux
  project options: --bundle PATH | --project ALIAS [--config PATH] [--audio-dir DIR] [--project-id ID]
                   [--preset-name NAME]                      (see audio_config.py)

RAW.mp4 must be the UNPROCESSED Borumi export (no atrim/adeclick post-process): the null test measures its
priming offset itself.

Reads the timeline at run time through Borumi MCP read tools only (get_project_overview, get_timeline,
get_transcript); never writes to Borumi or the bundle (the project DB is read from a temp copy).

Why rebuild instead of shifting the export: the raw export's audio lag is NOT constant. It is 2112 samples
(44 ms, AAC priming) for system sound and sound effects, plus a voice-enhancement latency of about 0.5-7.5 ms
that differs per clip/scene for the microphone (44-51 ms total). A constant atrim cannot fix that; placing
every segment at its timeline position does.

Stems (48 kHz mono float, timeline length) written to <audio_dir>/stems (or --stems-dir):
  voice      mic segments from the MASTERED sources (<audio_dir>/mastered/<media-uuid>.wav, master_sources.py)
  raw_voice  the same segments from the UNMASTERED bundle sources
  other      system_sound + audio + sound_effect + built-in segment sounds (+ music if any), Borumi volumes
Every voice/system segment edge gets a 10 ms equal-power fade: a crossfade centred on the cut when the
neighbour butts against it (5 ms source handles), a fade inside the segment at a free edge, nothing when
the neighbour continues the same source contiguously. One-shot sounds (sound effects) are not faded.

Null test (with --export): lag of raw_voice+other vs the export audio (GCC-PHAT + xcorr), the AAC priming
offset reported and compensated, per-source gains fitted by least squares (Borumi normalizes each mic clip
and runs a master limiter, so raw unity gains cannot null), residual in dB relative to the export overall /
in speech blocks / in other-dominated blocks / at the sound effects, per-segment lag (the per-scene voice
latency shows up here), a 0.5 s block-gain AGC model with coherence, and a check of muted segments.
A residual of about -1.2 dB overall is NORMAL (Borumi's voice AGC); judge alignment by lag and coherence.

Mix: voice + other (Borumi volumes; --other-gains fitted uses the export-fitted gains instead), static gain to
the loudness target (default -16 LUFS integrated, measured as the dual-mono stereo file it becomes), true-peak
limiter (4x oversampled alimiter, latency compensated) to target -0.5 dB margin, re-measured after AAC encode.
Output: copy of the export with the new audio as the only (default) audio track (video stream copied),
AAC 320 kbps 48 kHz dual-mono stereo, placed at timeline 0 (in sync with the picture), + <out>.remix.json.

Requirements: ffmpeg >= 6, numpy (scipy recommended), Borumi open with MCP enabled, the borumi_mcp.py daemon
running (opencutagent-borumi), and mastered sources from master_sources.py.
"""
import argparse, json, math, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_lib as al  # noqa: E402  (re-execs under a python with numpy if needed)
import audio_config  # noqa: E402
import borumi_read as br  # noqa: E402
import numpy as np  # noqa: E402

SR = al.SR
MASTERED = None  # <audio_dir>/mastered, set in main()
MUTE_DB = -30.0  # audio segments at or below this volume_filter (dB) count as muted (Borumi UI floor is -30 dB)
OTHER_STEMS = ("system", "audio", "sfx", "builtin", "music")
FIT_NAMES = ("raw_voice",) + OTHER_STEMS
BUILTIN_DIR = os.path.expanduser(audio_config.DEFAULTS["borumi_builtin_sounds_dir"])  # config borumi_builtin_sounds_dir
EDGE_MS = 10.0
TARGET_LUFS = -16.0  # config loudness.target_lufs / --target-lufs
TARGET_TP = -1.0     # config loudness.true_peak_dbtp / --true-peak
AAC_PRIMING = 2112   # samples (44 ms at 48 kHz): the raw Borumi export's audio starts this late
PRESET_NAME = "Auphonic"
ASSUMPTIONS = [
    "volume_filter on audio/sound_effect/music segments and built-in sound 'volume' are dB offsets (0 = unity); "
    "project cursor click_sound_volume is stored as -5.0, i.e. dB. Verified by the export gain fit when --export is given.",
    "built-in transition/enter sounds start at the segment start, exit sounds at the segment end.",
    "music fade_in/fade_out = 1.0 s linear (not yet verified against an export with music: check the gain fit).",
    "segment positions use get_timeline integer ms (Borumi stores sub-ms offsets; error <= 1 ms, inaudible).",
]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def db2lin(db):
    return 10.0 ** (db / 20.0)


# ----------------------------------------------------------------------------------------- plan
class Seg:
    __slots__ = ("layer", "kind", "id", "media", "path", "t0", "t1", "s0", "speed", "gain_db", "oneshot",
                 "fade_in_s", "fade_out_s", "loop", "comp", "prev_join", "next_join")

    def __init__(self, **k):
        for s in self.__slots__:
            setattr(self, s, k.get(s))

    @property
    def s1(self):
        return self.s0 + (self.t1 - self.t0) * (self.speed or 1.0)


def build_plan(tl, con):
    full_media = [r[0] for r in con.execute("select id from medias")]
    shorts = set()
    for L in tl["layers"].values():
        for s in L["segments"].values():
            if s.get("media"):
                shorts.add(s["media"]["media_id"])
    res = br.resolver(full_media, shorts)
    plan = {"mic": [], "system": [], "audio": [], "sfx": [], "builtin": [], "music": [], "muted": []}
    kinds = {"microphone": "mic", "system_sound": "system", "audio": "audio", "sound_effect": "sfx", "music": "music"}
    for lid, L in tl["layers"].items():
        k = kinds.get(L["kind"])
        for s in L["segments"].values():
            props = s.get("properties") or {}
            if k:
                m = s["media"]
                mid = res(m["media_id"])
                vol = props.get("volume_filter")
                muted = vol is not None and float(vol) <= MUTE_DB
                (plan["muted"] if muted else plan[k]).append(Seg(layer=lid, kind=k, id=s["id"], media=mid, path=br.media_file(mid),
                                   t0=s["start_ms"] / 1000, t1=s["end_ms"] / 1000, s0=m["start_ms"] / 1000,
                                   speed=m.get("speed") or 1.0, gain_db=float(vol or 0.0),
                                   oneshot=(k == "sfx"),
                                   fade_in_s=1.0 if props.get("fade_in") else 0.0,
                                   fade_out_s=1.0 if props.get("fade_out") else 0.0,
                                   loop=bool(props.get("enable_looping"))))
            for key in ("transition_sound", "enter_sound", "exit_sound"):
                snd = props.get(key)
                if not snd:
                    continue
                path = os.path.join(BUILTIN_DIR, f"built-in-sound-{snd['sound_id']}.wav")
                t = s["start_ms"] / 1000 if key != "exit_sound" else s["end_ms"] / 1000
                d = al.duration(path)
                plan["builtin"].append(Seg(layer=lid, kind="builtin", id=f"{s['id']}:{key}", media=snd["sound_id"],
                                           path=path, t0=t, t1=t + d, s0=0.0, speed=1.0,
                                           gain_db=float(snd.get("volume") or 0.0), oneshot=True,
                                           fade_in_s=0.0, fade_out_s=0.0, loop=False))
    for k in plan:
        plan[k].sort(key=lambda x: (x.t0, x.layer))
    # joins between neighbours on the same layer: None (free edge), "cut" (butt join), "contig" (same source continues)
    for k in ("mic", "system", "audio", "music"):
        by_layer = {}
        for s in plan[k]:
            by_layer.setdefault(s.layer, []).append(s)
        for segs in by_layer.values():
            for a, b in zip(segs, segs[1:]):
                if abs(a.t1 - b.t0) < 0.0005:
                    contig = a.media == b.media and a.speed == b.speed and abs(a.s1 - b.s0) < 0.0015
                    a.next_join = b.prev_join = "contig" if contig else "cut"
    return plan


# ------------------------------------------------------------------------------------ rendering
class SourceCache:
    def __init__(self):
        self.cache = {}

    def get(self, path):
        if path not in self.cache:
            if len(self.cache) > 3:
                self.cache.pop(next(iter(self.cache)))
            ch = al.channels(path)
            x = al.load(path, channels=ch)
            if ch > 1:
                # Borumi's mono render of a stereo source = (L+R)/sqrt(2): verified on an export
                # (chapter click: correlation 1.000, +3.01 dB vs (L+R)/2)
                x = (x.sum(axis=1) / np.sqrt(ch)).astype(np.float32)
            self.cache[path] = x
        return self.cache[path]


def _read(src, i0, n):
    out = np.zeros(n, dtype=np.float32)
    a, b = max(0, i0), min(len(src), i0 + n)
    if b > a:
        out[a - i0:b - i0] = src[a:b]
    return out


def _stretch(x, speed, n):
    if abs(speed - 1.0) < 1e-6:
        return x[:n]
    with tempfile.TemporaryDirectory() as d:
        i, o = os.path.join(d, "i.wav"), os.path.join(d, "o.wav")
        al.write_wav(i, x)
        chain, s = [], speed
        while s > 2.0:
            chain.append("atempo=2.0"); s /= 2.0
        while s < 0.5:
            chain.append("atempo=0.5"); s /= 0.5
        chain.append(f"atempo={s:.6f}")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", i, "-af", ",".join(chain), "-c:a", "pcm_f32le", o], check=True)
        y = al.load(o)
    return np.pad(y, (0, max(0, n - len(y))))[:n]


def render(segs, n_total, path_of, cache, edge_ms=EDGE_MS):
    """Render segments into one mono stem. Returns (stem, labels) where labels[i] = segment comp index."""
    out = np.zeros(n_total, dtype=np.float32)
    lab = np.full(n_total, -1, dtype=np.int16)
    E = int(round(edge_ms * SR / 1000))
    H = E // 2
    up = np.sin(np.linspace(0, np.pi / 2, E, endpoint=False) + np.pi / 4 / E).astype(np.float32)  # equal power
    up_c = np.sin(np.linspace(0, np.pi / 2, 2 * H, endpoint=False) + np.pi / 4 / (2 * H)).astype(np.float32)
    for s in segs:
        src = cache.get(path_of(s))
        t0, t1 = int(round(s.t0 * SR)), int(round(s.t1 * SR))
        n = t1 - t0
        if n <= 0:
            continue
        i0 = int(round(s.s0 * SR))
        ext_l = H if (not s.oneshot and s.prev_join == "cut") else 0
        ext_r = H if (not s.oneshot and s.next_join == "cut") else 0
        if s.loop and len(src):
            reps = int(math.ceil((n + i0) / len(src))) + 1
            src = np.tile(src, reps)
        if abs((s.speed or 1.0) - 1.0) < 1e-6:
            x = _read(src, i0 - ext_l, n + ext_l + ext_r)
        else:
            raw = _read(src, int(round(i0 - ext_l * s.speed)), int(math.ceil((n + ext_l + ext_r) * s.speed)) + 1)
            x = _stretch(raw, s.speed, n + ext_l + ext_r)
        w = np.ones(len(x), dtype=np.float32)
        if not s.oneshot and len(x) >= 4 * H:
            if s.prev_join == "cut":
                w[:2 * H] *= up_c
            elif s.prev_join is None:
                w[:E] *= up[:min(E, len(w))]
            if s.next_join == "cut":
                w[-2 * H:] *= up_c[::-1]
            elif s.next_join is None:
                w[-E:] *= up[::-1][-min(E, len(w)):]
        if s.fade_in_s:
            k = min(len(w), int(s.fade_in_s * SR)); w[:k] *= np.linspace(0, 1, k, dtype=np.float32)
        if s.fade_out_s:
            k = min(len(w), int(s.fade_out_s * SR)); w[-k:] *= np.linspace(1, 0, k, dtype=np.float32)
        g = db2lin(s.gain_db)
        a = t0 - ext_l
        b = a + len(x)
        ca, cb = max(0, a), min(n_total, b)
        if cb > ca:
            out[ca:cb] += (x * w * g)[ca - a:cb - a]
            lab[max(0, t0):min(n_total, t1)] = s.comp
    return out, lab


def build_stems(plan, n_total, need_raw=True):
    cache = SourceCache()
    comps = []  # (stem name, label, description, assumed gain dB)

    def assign(stem, segs, keyf):
        keys = {}
        for s in segs:
            k = keyf(s)
            if k not in keys:
                keys[k] = len(keys)
                comps.append({"stem": stem, "label": keys[k], "key": k, "segments": 0, "assumed_gain_db": s.gain_db})
            s.comp = keys[k]
            next(c for c in comps if c["stem"] == stem and c["label"] == s.comp)["segments"] += 1

    assign("voice", plan["mic"], lambda s: s.media)
    assign("system", plan["system"], lambda s: s.media)
    assign("audio", plan["audio"], lambda s: s.media)
    assign("sfx", plan["sfx"], lambda s: s.id)
    assign("builtin", plan["builtin"], lambda s: s.id)
    assign("music", plan["music"], lambda s: s.id)
    stems, labels = {}, {}
    missing = [s.media for s in plan["mic"] if not os.path.exists(os.path.join(MASTERED, s.media + ".wav"))]
    if missing:
        raise SystemExit(f"mastered file missing for mic sources: {sorted(set(missing))} (run master_sources.py)")
    log("render voice (mastered)")
    stems["voice"], labels["voice"] = render(plan["mic"], n_total, lambda s: os.path.join(MASTERED, s.media + ".wav"), cache)
    if need_raw:
        log("render raw_voice")
        stems["raw_voice"], _ = render(plan["mic"], n_total, lambda s: s.path, cache)
    log("render system / audio / sfx / builtin / music")
    for k in OTHER_STEMS:
        stems[k], labels[k] = render(plan[k], n_total, lambda s: s.path, cache)
    stems["other"] = sum(stems[k] for k in OTHER_STEMS)
    labels["raw_voice"] = labels["voice"]
    return stems, labels, comps


# ------------------------------------------------------------------------------------ null test
def windows_by_activity(x, n=8, win_s=20.0):
    return al.active_windows(x, SR, win_s, n)


def global_lag(ref, sig, n=8, win_s=20.0, max_ms=200):
    """Lag (samples) of sig vs ref: median of GCC-PHAT over the n most active windows of ref."""
    res = []
    for t in windows_by_activity(ref, n, win_s):
        i, j = int(t * SR), int((t + win_s) * SR)
        if j > len(sig):
            continue
        g, pk = al.gcc_phat(ref[i:j], sig[i:j], int(max_ms * SR / 1000))
        x, xc = al.xcorr_lag(ref[i:j].astype(np.float64), sig[i:j].astype(np.float64), int(max_ms * SR / 1000))
        res.append({"t_s": round(float(t), 1), "gcc_phat_samples": round(float(g), 3), "gcc_peak": round(float(pk), 3),
                    "xcorr_samples": round(float(x), 3), "xcorr_coef": round(float(xc), 3)})
    med = float(np.median([r["gcc_phat_samples"] for r in res])) if res else 0.0
    return med, res


def shift(x, d):
    """Return x delayed by d samples (integer), same length."""
    d = int(round(d))
    if d == 0:
        return x
    y = np.zeros_like(x)
    if d > 0:
        y[d:] = x[:-d]
    else:
        y[:d] = x[-d:]
    return y


def fit_gains(y, stems, labels, comps, names=FIT_NAMES):
    """Least-squares gain per component (stem, label) so that sum g_k * x_k ~ y."""
    idx = {}
    for c in comps:
        st = "raw_voice" if c["stem"] == "voice" else c["stem"]
        if st in names:
            idx[(st, c["label"])] = len(idx)
    K = len(idx)
    G = np.zeros((K, K)); b = np.zeros(K)
    act = {st: labels[st if st != "raw_voice" else "voice"] for st in names}
    y64 = y.astype(np.float64)
    for ia, A in enumerate(names):
        la = act[A]; xa = stems[A].astype(np.float64)
        ka = [k for k in idx if k[0] == A]
        if not ka:
            continue
        na = max(k[1] for k in ka) + 1
        va = la >= 0
        bb = np.bincount(la[va], weights=xa[va] * y64[va], minlength=na)
        for (_, l) in ka:
            b[idx[(A, l)]] = bb[l]
        for B in names[ia:]:
            lb = act[B]; xb = stems[B].astype(np.float64)
            kb = [k for k in idx if k[0] == B]
            if not kb:
                continue
            nb = max(k[1] for k in kb) + 1
            v = va & (lb >= 0)
            if not v.any():
                continue
            gg = np.bincount(la[v].astype(np.int64) * nb + lb[v], weights=xa[v] * xb[v], minlength=na * nb).reshape(na, nb)
            for (_, l1) in ka:
                for (_, l2) in kb:
                    G[idx[(A, l1)], idx[(B, l2)]] = gg[l1, l2]
                    G[idx[(B, l2)], idx[(A, l1)]] = gg[l1, l2]
    if K == 0:
        return {}
    diag = np.diag(G).copy()
    ok = diag > 1e-6 * max(1e-12, diag.max())
    g = np.zeros(K)
    sub = np.ix_(ok, ok)
    g[ok] = np.linalg.solve(G[sub] + 1e-9 * np.eye(ok.sum()) * diag[ok].max(), b[ok])
    out = {}
    for (st, l), i in idx.items():
        out[(st, l)] = (float(g[i]) if ok[i] else None)
    return out


def apply_gains(stems, labels, gains, names, default=None):
    y = np.zeros_like(stems["voice"])
    for st in names:
        lab = labels[st if st != "raw_voice" else "voice"]
        n = int(lab.max()) + 1 if lab.size and lab.max() >= 0 else 0
        if n == 0:
            continue
        gv = np.array([gains.get((st, l)) if gains.get((st, l)) is not None else (default(st, l) if default else 0.0)
                       for l in range(n)] + [0.0], dtype=np.float32)
        y += stems[st] * gv[lab]  # label -1 -> last entry (0); crossfade tails outside labels get 0 (tiny)
        # adding back the crossfade tails (label -1 but stem nonzero) with the nearest gain is negligible: <=5 ms per cut
    return y


def fit_table(comps, gains):
    rows = []
    for c in comps:
        g = gains.get(("raw_voice" if c["stem"] == "voice" else c["stem"], c["label"]))
        rows.append({"stem": "raw_voice" if c["stem"] == "voice" else c["stem"], "key": c["key"], "segments": c["segments"],
                     "assumed_db": c["assumed_gain_db"],
                     "fitted_db": round(20 * math.log10(abs(g)), 2) if g else None,
                     "fitted_sign": (1 if g > 0 else -1) if g else None})
    return rows


def _rel_db(num, den):
    return round(10 * math.log10(max(num, 1e-30) / max(den, 1e-30)), 2) if den > 0 else None


def residual_report(y, recon, stems_gained, sfx_segs, block_ms=100):
    r = y.astype(np.float64) - recon
    tot = lambda a: float(np.sum(a.astype(np.float64) ** 2))
    rep = {"residual_db_rel_export": _rel_db(tot(r), tot(y))}
    B = int(block_ms * SR / 1000)
    nb = len(y) // B
    e = lambda a: np.sum(a[:nb * B].astype(np.float64).reshape(nb, B) ** 2, axis=1) / B
    ey, er = e(y), e(r)
    ev, eo = e(stems_gained["voice"]), e(stems_gained["other"])
    floor = 10 ** (-60 / 10)
    speech = (ev > 10 * eo) & (ev > floor)
    other = (eo > 10 * ev) & (eo > floor)
    for name, m in (("speech_blocks", speech), ("other_dominated_blocks", other)):
        if m.any():
            rep[name] = {"blocks": int(m.sum()), "residual_db": _rel_db(er[m].sum(), ey[m].sum())}
    cards = []
    for s in sfx_segs:
        i, j = int(s.t0 * SR), int(s.t1 * SR)
        cards.append({"t_s": round(s.t0, 3), "export_rms_dbfs": round(al.rms_db(y[i:j]), 1),
                      "residual_db": _rel_db(tot(r[i:j]), tot(y[i:j]))})
    rep["sound_effects"] = cards
    return rep


def segment_lags(y, raw, segs, min_s=1.0):
    """Per-segment lag (ms) of the export vs the raw reconstruction, after the global offset is removed."""
    out = []
    for s in segs:
        if s.t1 - s.t0 < min_s:
            continue
        i, j = int(s.t0 * SR), int(s.t1 * SR)
        if np.sum(raw[i:j] ** 2) < 1e-9:
            continue
        g, pk = al.gcc_phat(raw[i:j], y[i:j], int(0.05 * SR))
        out.append((s.id, round(s.t0, 3), round(float(g) * 1000 / SR, 3), round(float(pk), 3)))
    lags = [abs(o[2]) for o in out if o[3] > 0.1]
    return {"segments_checked": len(out), "max_abs_lag_ms": round(max(lags), 3) if lags else None,
            "over_1ms": [o for o in out if abs(o[2]) > 1.0 and o[3] > 0.1][:20]}


def _copy_seg(s, dt=0.0):
    c = Seg(**{k: getattr(s, k) for k in Seg.__slots__})
    c.t0, c.t1 = s.t0 + dt, s.t1 + dt
    return c


def aligned_null(y, plan, n, comps, max_shift_ms=2.0, priming=0):
    """Measure each segment's own lag vs the export, re-render with those sub-ms placement corrections,
    re-fit gains and report the residual. Separates placement (Borumi stores sub-ms positions that
    get_timeline rounds to whole ms; its voice enhancement adds a small, per-clip latency) from content/gain.
    per_layer reports each layer's lag relative to the timeline after the standard AAC priming (2112 samples):
    ~0 ms for system sound / sound effects, 0.5-7.5 ms for the microphone (varies by scene)."""
    cache = SourceCache()
    out = {}
    stems, labels = {}, {}
    shifts = []
    for k, stem_name in (("mic", "raw_voice"),) + tuple((x, x) for x in OTHER_STEMS):
        ref, _ = render(plan[k], n, lambda s: s.path, cache)
        segs = []
        for s in plan[k]:
            i, j = int(s.t0 * SR), int(s.t1 * SR)
            dt = 0.0
            if s.t1 - s.t0 >= 0.3 and np.sum(ref[i:j].astype(np.float64) ** 2) > 1e-9:
                g, pk = al.gcc_phat(ref[i:j], y[i:j], int(0.05 * SR))
                if pk > 0.1 and abs(g) * 1000 / SR <= max_shift_ms:
                    dt = round(g) / SR
                shifts.append((k, s.id, round(s.t0, 3), round(float(g) * 1000 / SR, 3), round(float(pk), 3)))
            segs.append(_copy_seg(s, dt))
        lab_key = "voice" if stem_name == "raw_voice" else stem_name
        stems[stem_name], labels[lab_key] = render(segs, n, lambda s: s.path, cache)
    labels["raw_voice"] = labels["voice"]
    stems["voice"] = stems["raw_voice"]
    gains = fit_gains(y, stems, labels, comps)
    names = FIT_NAMES
    recon = apply_gains(stems, labels, gains, names)
    fv = apply_gains(stems, labels, gains, ("raw_voice",))
    out["residual_fitted"] = residual_report(y, recon, {"voice": fv, "other": recon - fv}, plan["sfx"])
    out["fit_gains_db"] = fit_table(comps, gains)
    conf = [x for x in shifts if x[4] > 0.1]
    lags = [abs(x[3]) for x in conf]
    per_layer = {}
    for k in ("mic",) + OTHER_STEMS:
        v = [x[3] for x in conf if x[0] == k]
        if v:
            per_layer[k] = {"segments": len(v), f"median_ms_vs_timeline_after_{AAC_PRIMING}_priming":
                            round(float(np.median(v)) + (priming - AAC_PRIMING) * 1000 / SR, 3),
                            "min_ms": round(min(v) + (priming - AAC_PRIMING) * 1000 / SR, 3),
                            "max_ms": round(max(v) + (priming - AAC_PRIMING) * 1000 / SR, 3)}
    out["segment_lag_ms"] = {"measured": len(lags), "max_abs_vs_global": round(max(lags), 3) if lags else None,
                             "p95_abs_vs_global": round(float(np.percentile(lags, 95)), 3) if lags else None,
                             "per_layer": per_layer,
                             "over_1ms_vs_global": [x for x in conf if abs(x[3]) > 1.0][:20]}
    return out, stems, labels, recon, fv


def voice_agc_check(y, raw, other_fit, voice_fit, block_s=0.5, mic_segs=()):
    """Borumi's 'extended' voice enhancement = denoise + per-clip loudness normalization + master limiter,
    so raw x static gain cannot null. Model it as a gain per 0.5 s block and report the speech residual
    and the magnitude-squared coherence (100 Hz-4 kHz) between raw voice and export minus other.
    Coherence needs scipy (skipped without it)."""
    try:
        from scipy.signal import coherence
    except ModuleNotFoundError:
        coherence = None
    B = int(block_s * SR)
    nb = len(y) // B
    v = raw[:nb * B].astype(np.float64).reshape(nb, B)
    t = (y[:nb * B].astype(np.float64) - other_fit[:nb * B]).reshape(nb, B)
    den = np.sum(v * v, axis=1)
    g = np.where(den > 1e-9, np.sum(v * t, axis=1) / np.maximum(den, 1e-12), 0.0)
    r = (t - g[:, None] * v)
    ev = np.sum((voice_fit[:nb * B].astype(np.float64).reshape(nb, B)) ** 2, axis=1)
    eo = np.sum((other_fit[:nb * B].reshape(nb, B)) ** 2, axis=1)
    sp = (ev > 10 * eo) & (ev > 1e-6 * B)
    yy = y[:nb * B].astype(np.float64).reshape(nb, B)
    res = {"blocks": int(sp.sum()),
           "speech_residual_db_block_gain": _rel_db(float(np.sum(r[sp] ** 2)), float(np.sum(yy[sp] ** 2))),
           "block_gain_db_median": round(float(20 * np.log10(np.median(np.abs(g[sp])) + 1e-12)), 2) if sp.any() else None,
           "block_gain_db_p10_p90": [round(float(20 * np.log10(np.percentile(np.abs(g[sp]), q) + 1e-12)), 2) for q in (10, 90)] if sp.any() else None}
    if coherence is None:
        res["coherence"] = "skipped (scipy not installed)"
        return res
    if sp.any():
        a_ = v[sp].ravel()[: SR * 600]
        b_ = t[sp].ravel()[: SR * 600]
        f, C = coherence(a_, b_, fs=SR, nperseg=4096)
        band = (f > 100) & (f < 4000)
        res["coherence_100_4000Hz_mean"] = round(float(C[band].mean()), 3)
    cs = []
    for s in mic_segs:
        i, j = int(s.t0 * SR), int(s.t1 * SR)
        if j - i < SR:
            continue
        a_ = raw[i:j].astype(np.float64); b_ = y[i:j].astype(np.float64) - other_fit[i:j]
        if np.sum(a_ ** 2) < 1e-9:
            continue
        f, C = coherence(a_, b_, fs=SR, nperseg=4096)
        band = (f > 100) & (f < 4000)
        cs.append(float(C[band].mean()))
    if cs:
        res["per_segment_coherence_100_4000Hz"] = {"segments": len(cs), "median": round(float(np.median(cs)), 3),
                                                   "p10": round(float(np.percentile(cs, 10)), 3)}
    return res


def muted_check(y, residual, plan, n):
    """For segments treated as muted: is their source audible in the export? Fit each one's gain
    against what is left of the export after the live reconstruction (unity-gain render)."""
    out = []
    cache = SourceCache()
    for s in plan["muted"]:
        c = _copy_seg(s)
        c.gain_db, c.comp = 0.0, 0
        x, _ = render([c], n, lambda q: q.path, cache)
        i, j = int(s.t0 * SR), int(s.t1 * SR)
        xs = x[i:j].astype(np.float64); rs = residual[i:j].astype(np.float64)
        den = float(np.dot(xs, xs))
        g = float(np.dot(xs, rs) / den) if den > 1e-9 else 0.0
        cc = float(np.dot(xs, rs) / np.sqrt(den * float(np.dot(rs, rs)) + 1e-20)) if den > 1e-9 else 0.0
        out.append({"layer": s.layer, "segment": s.id, "t0": round(s.t0, 3), "t1": round(s.t1, 3), "media": s.media[:8],
                    "timeline_volume_db": s.gain_db, "export_fitted_gain_db": round(20 * math.log10(abs(g)), 1) if g else None,
                    "correlation_with_export_residual": round(cc, 3),
                    "verdict": "absent from export (muted in Borumi too)" if abs(cc) < 0.1 else
                               "PRESENT in Borumi's export at about the fitted gain"})
    return out


# ------------------------------------------------------------------------------------ loudness
def tp_limit(x, ceiling_dbtp, workdir):
    """True-peak limit with ffmpeg alimiter at 4x oversampling (latency compensated)."""
    i, o = os.path.join(workdir, "lim_in.wav"), os.path.join(workdir, "lim_out.wav")
    al.write_wav(i, x)
    lim = db2lin(ceiling_dbtp)
    af = (f"aresample={SR * 4},alimiter=limit={lim:.6f}:attack=1.5:release=60:level=disabled:latency=true,"
          f"aresample={SR}")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", i, "-af", af, "-c:a", "pcm_f32le", o], check=True)
    y = al.load(o)
    y = np.pad(y, (0, max(0, len(x) - len(y))))[:len(x)]
    os.remove(i); os.remove(o)
    return y


def master_bus(mix, workdir, target=None, ceiling=None, dualmono=True):
    """Static gain to the loudness target, then true-peak limiting. dualmono=True: the mono mix is
    measured as the dual-mono stereo file it becomes (BS.1770 sums L+R, +3.01 dB vs mono).
    Defaults: target = TARGET_LUFS, ceiling = TARGET_TP - 0.5 dB (margin for the AAC encode)."""
    target = TARGET_LUFS if target is None else target
    ceiling = (TARGET_TP - 0.5) if ceiling is None else ceiling
    pre = al.ebur128(mix, dualmono=dualmono)
    gain_db = target - pre["integrated_lufs"]
    y = mix * db2lin(gain_db)
    hist = []
    for it in range(3):
        y_lim = tp_limit(y, ceiling, workdir)
        m = al.ebur128(y_lim, dualmono=dualmono)
        hist.append({"static_gain_db": round(gain_db, 3), "ceiling_dbtp": ceiling, **m})
        if abs(m["integrated_lufs"] - target) <= 0.05 and m["true_peak_dbtp"] <= ceiling + 0.1:
            break
        if m["true_peak_dbtp"] > ceiling + 0.1:
            ceiling -= (m["true_peak_dbtp"] - ceiling) + 0.1
        gain_db += target - m["integrated_lufs"]
        y = mix * db2lin(gain_db)
    lag, _ = al.gcc_phat(y[:SR * 60], y_lim[:SR * 60], int(0.05 * SR))
    gr = 20 * np.log10(np.maximum(np.abs(y_lim), 1e-9) / np.maximum(np.abs(y), 1e-9))
    active = np.abs(y) > db2lin(ceiling - 6)
    return y_lim, {"pre_mix": pre, "passes": hist, "limiter_lag_samples": round(float(lag), 3),
                   "limiter_max_gr_db": round(float(-gr[active].min()), 2) if active.any() else 0.0,
                   "limiter_samples_over_0.5dB_gr": int(np.sum(gr[active] < -0.5)) if active.any() else 0}


# ------------------------------------------------------------------------------------ main
def stem_paths(d):
    return {k: os.path.join(d, f"{k}.wav") for k in ("voice", "raw_voice", "other")}


def main():
    global MASTERED, MUTE_DB, BUILTIN_DIR, TARGET_LUFS, TARGET_TP, PRESET_NAME
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", help="raw (unprocessed) Borumi export mp4 (enables null test + final mux)")
    ap.add_argument("--out", help="final mp4 path (default: '<export stem> (mastered).mp4')")
    ap.add_argument("--stems-dir", help="default: <audio_dir>/stems")
    ap.add_argument("--mute-below-db", type=float, default=MUTE_DB,
                    help="audio segments with volume_filter at or below this (dB) are treated as muted (default %(default)s)")
    ap.add_argument("--other-gains", choices=["borumi", "fitted"], default="borumi",
                    help="other-stem gains: Borumi timeline volumes (default) or gains fitted to the export")
    ap.add_argument("--no-preview", action="store_true", help="dry run: skip the preview mix")
    ap.add_argument("--range", nargs=2, type=float, metavar=("START_S", "END_S"), help="null test only on this range (testing)")
    ap.add_argument("--keep-original-track", action="store_true", help="keep Borumi's audio as a 2nd, non-default track")
    ap.add_argument("--keep-export-offset", action="store_true",
                    help="delay the new audio by the export's measured priming offset (default: place it at timeline 0, "
                         f"i.e. in sync with the picture; the raw Borumi export's audio sits ~{AAC_PRIMING} samples late)")
    ap.add_argument("--channels", type=int, choices=[1, 2], default=2,
                    help="output channels (default 2 = dual mono: AAC 320 kbps needs stereo; loudness measured as stereo)")
    ap.add_argument("--target-lufs", type=float, help="integrated loudness target (default: config, -16)")
    ap.add_argument("--true-peak", type=float, help="true-peak ceiling after encode, dBTP (default: config, -1)")
    ap.add_argument("--check-times", nargs="*", type=float, default=[], help="extra times (s) to report click levels at")
    ap.add_argument("--allow-timeline-change", action="store_true")
    audio_config.add_args(ap)
    a = ap.parse_args()

    t_start = time.time()
    P = br.set_project(audio_config.resolve(a))
    MASTERED = P["mastered"]
    MUTE_DB = a.mute_below_db
    BUILTIN_DIR = P["builtin_dir"]
    TARGET_LUFS = a.target_lufs if a.target_lufs is not None else P["target_lufs"]
    TARGET_TP = a.true_peak if a.true_peak is not None else P["target_tp"]
    PRESET_NAME = P["preset_name"]
    a.stems_dir = a.stems_dir or P["stems"]
    pid = br.project_id()
    tl = br.read_timeline(pid)
    con = br.db_copy()
    dur_ms = tl["duration_ms"]
    n_total = int(round(dur_ms * SR / 1000))
    plan = build_plan(tl, con)
    counts = {k: len(v) for k, v in plan.items()}
    log(f"timeline {tl['timeline_hash']} duration {dur_ms} ms; segments {counts}")
    voice_props = sorted({json.dumps(s.get("properties"), sort_keys=True) for L in tl["layers"].values()
                          if L["kind"] == "microphone" for s in L["segments"].values()})
    stems, labels, comps = build_stems(plan, n_total)
    os.makedirs(a.stems_dir, exist_ok=True)
    sp = stem_paths(a.stems_dir)
    for k, p in sp.items():
        al.write_wav(p, stems[k], codec="pcm_s24le")
    rec = {"project": {"id": P["id"], "name": P["name"], "bundle": P["bundle"], "mastered_dir": MASTERED},
           "mute_below_db": MUTE_DB,
           "muted_segments_ignored": [{"layer": s.layer, "segment": s.id, "t0": round(s.t0, 3), "t1": round(s.t1, 3),
                                       "media": s.media, "volume_db": s.gain_db} for s in plan["muted"]],
           "timeline_hash": tl["timeline_hash"], "project_duration_ms": dur_ms, "segment_counts": counts,
           "mic_segment_properties": [json.loads(v) for v in voice_props], "assumptions": ASSUMPTIONS,
           "edge_fade_ms": EDGE_MS, "stems": sp, "targets": {"lufs": TARGET_LUFS, "true_peak_dbtp": TARGET_TP},
           "stem_durations_s": {k: round(al.duration(p), 6) for k, p in sp.items()},
           "components": comps, "mastered_sources": sorted({s.media for s in plan["mic"]})}
    rec["stem_duration_matches_project"] = all(abs(v * 1000 - dur_ms) <= 1 for v in rec["stem_durations_s"].values())

    # voice (mastered) vs raw voice alignment (Auphonic must not shift anything: expect ~0.001 samples)
    lag_vr, wins_vr = global_lag(stems["raw_voice"], stems["voice"], n=6)
    rec["voice_vs_raw_voice"] = {"lag_samples": lag_vr, "lag_ms": round(lag_vr * 1000 / SR, 3), "windows": wins_vr}
    log(f"mastered voice vs raw voice lag {lag_vr:.3f} samples")

    if not a.export:
        dry_run_checks(a, tl, pid, stems, plan, rec)
        out = os.path.join(a.stems_dir, "dry_run.json")
        json.dump(rec, open(out, "w"), indent=1)
        log(f"dry run done in {time.time() - t_start:.0f}s -> {out}")
        return

    # ---------------- null test against the raw export
    exp = os.path.abspath(a.export)
    out_path = os.path.abspath(a.out or os.path.splitext(exp)[0] + " (mastered).mp4")
    work = tempfile.mkdtemp(prefix="remix-")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                       "stream=index,codec_type,codec_name,channels,sample_rate,start_time,duration:format=duration",
                                       "-of", "json", exp], capture_output=True, text=True).stdout)
    a_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    ch = int(a_streams[0]["channels"])
    y = al.load(exp)  # mono downmix of the first audio track (Borumi exports mono)
    n_exp = len(y)
    log(f"export audio {n_exp / SR:.3f} s ({ch} ch), timeline {dur_ms / 1000:.3f} s")
    L = max(n_exp, n_total)
    pad = lambda x: np.pad(x, (0, L - len(x)))
    y = pad(y)
    for k in list(stems):
        stems[k] = pad(stems[k])
    for k in list(labels):
        labels[k] = np.pad(labels[k], (0, L - len(labels[k])), constant_values=-1)
    if a.range:
        i, j = int(a.range[0] * SR), int(a.range[1] * SR)
        m = np.zeros(L, bool); m[i:j] = True
        y = np.where(m, y, 0).astype(np.float32)
        for k in stems:
            stems[k] = np.where(m, stems[k], 0).astype(np.float32)
    recon0 = stems["raw_voice"] + stems["other"]
    lag0, wins0 = global_lag(recon0, y)
    priming = int(round(lag0))
    log(f"raw lag of export vs reconstruction: {lag0:.3f} samples ({lag0 * 1000 / SR:.3f} ms)")
    y_al = shift(y, -priming)  # compensate a constant offset (AAC priming) for the residual
    lag1, wins1 = global_lag(recon0, y_al)
    gains = fit_gains(y_al, stems, labels, comps)
    names = FIT_NAMES
    assumed = lambda st, l: db2lin(next(c["assumed_gain_db"] for c in comps
                                        if (("raw_voice" if c["stem"] == "voice" else c["stem"]) == st and c["label"] == l)))
    recon_fit = apply_gains(stems, labels, gains, names, default=assumed)
    fitted_voice = apply_gains(stems, labels, gains, ("raw_voice",), default=assumed)
    fitted_other = recon_fit - fitted_voice
    nt = {
        "export": exp, "export_audio_channels": ch, "export_audio_s": round(n_exp / SR, 6),
        "raw_lag_samples": lag0, "raw_lag_ms": round(lag0 * 1000 / SR, 3), "lag_windows": wins0,
        "priming_offset_samples_compensated": priming,
        "lag_after_compensation_ms": round(lag1 * 1000 / SR, 3),
        "fit_gains_db": fit_table(comps, gains),
        "residual_fitted": residual_report(y_al, recon_fit, {"voice": fitted_voice, "other": fitted_other}, plan["sfx"]),
        "residual_unity_gains": residual_report(y_al, recon0.astype(np.float64), {"voice": stems["raw_voice"], "other": stems["other"]}, plan["sfx"]),
        "per_segment_lag_mic": segment_lags(y_al, stems["raw_voice"], plan["mic"]),
        "per_segment_lag_system": segment_lags(y_al, stems["system"], plan["system"]),
        "export_loudness": al.ebur128(exp),
        "borumi_voice_lufs": al.ebur128(fitted_voice[:n_exp])["integrated_lufs"],
        "mastered_voice_lufs": al.ebur128(stems["voice"][:n_exp])["integrated_lufs"],
    }
    log("null test: per-segment aligned re-fit, voice AGC model, coherence")
    nt["segment_aligned"], st_al, lb_al, recon_al, fv_al = aligned_null(y_al, plan, len(y_al), comps, priming=priming)
    nt["voice_agc_model_segment_aligned"] = voice_agc_check(y_al, st_al["raw_voice"], recon_al - fv_al, fv_al,
                                                            mic_segs=plan["mic"])
    nt["muted_segments_check"] = muted_check(y_al, y_al - recon_al, plan, len(y_al)) if plan["muted"] else []
    del st_al, lb_al, recon_al, fv_al
    nt["voice_level_change_db"] = round(nt["mastered_voice_lufs"] - nt["borumi_voice_lufs"], 2) \
        if nt["borumi_voice_lufs"] is not None and nt["mastered_voice_lufs"] is not None else None
    rec["null_test"] = nt
    log(f"null test: residual (fitted gains) {nt['residual_fitted']['residual_db_rel_export']} dB, "
        f"other-dominated {nt['residual_fitted'].get('other_dominated_blocks')}, speech {nt['residual_fitted'].get('speech_blocks')}")
    if a.range:
        json.dump(rec, open(out_path + ".remix.json", "w"), indent=1)
        log("range test only; no mix written")
        return

    # ---------------- mix, master bus, mux
    if a.other_gains == "fitted":
        other = fitted_other.astype(np.float32)
    else:
        other = stems["other"]
    # timeline hash must not have moved while we worked (the export has to match what we rebuilt)
    h_end = br.overview(pid)["timeline_hash"]
    rec["timeline_hash_at_end"] = h_end
    if h_end != tl["timeline_hash"] and not a.allow_timeline_change:
        json.dump(rec, open(out_path + ".remix.json", "w"), indent=1)
        raise SystemExit(f"timeline changed during the run ({tl['timeline_hash']} -> {h_end}); not mixing. Re-run.")
    vid = next(s_ for s_ in probe["streams"] if s_["codec_type"] == "video")
    vdur = float(vid.get("duration") or probe["format"]["duration"])
    offset = priming if a.keep_export_offset else 0
    n_out = n_exp if a.keep_export_offset else int(round(vdur * SR))  # audio length = picture length
    if abs(n_total - vdur * SR) > 0.1 * SR:
        rec.setdefault("warnings", []).append(f"timeline {n_total / SR:.3f} s vs export video {vdur:.3f} s")
    mix = shift(np.pad(stems["voice"] + other, (0, max(0, n_out - L)))[:n_out], offset)
    rec["placement"] = {"offset_samples": offset, "audio_samples": n_out, "export_video_s": vdur,
                        "note": "timeline-aligned (in sync with the picture); the raw export's audio is "
                                f"{priming} samples late" if not a.keep_export_offset else "export offset kept"}
    dual = a.channels == 2
    final, mb = master_bus(mix, work, dualmono=dual)
    rec["master_bus"] = mb
    wav = os.path.join(work, "final.wav")
    enc = "aac_at" if "aac_at" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout else "aac"
    for attempt in range(3):
        # dual mono = the SAME samples in L and R (no -3 dB pan law): master_bus measured it that way (dualmono=True)
        al.write_wav(wav, final if not dual else np.repeat(final[:, None], 2, axis=1), codec="pcm_f32le")
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", exp, "-i", wav, "-map", "0:v:0", "-map", "1:a:0"]
        if a.keep_original_track:
            cmd += ["-map", "0:a:0", "-disposition:a:1", "0", "-metadata:s:a:1", "title=Borumi original mix"]
        cmd += ["-map_metadata", "0", "-map_chapters", "0", "-c:v", "copy", "-c:a:0", enc, "-b:a:0", "320k", "-ar:a:0", str(SR),
                "-disposition:a:0", "default", "-metadata:s:a:0", f"title=Auphonic {PRESET_NAME} voice + Borumi mix",
                "-movflags", "+faststart", out_path]
        if a.keep_original_track:
            cmd[-3:-3] = ["-c:a:1", "copy"]
        subprocess.run(cmd, check=True)
        fin = al.ebur128(out_path)
        if fin["true_peak_dbtp"] is not None and fin["true_peak_dbtp"] > TARGET_TP:
            log(f"encoded true peak {fin['true_peak_dbtp']} dBTP > {TARGET_TP}; lowering ceiling")
            final, mb = master_bus(mix, work, ceiling=mb["passes"][-1]["ceiling_dbtp"] - (fin["true_peak_dbtp"] - TARGET_TP) - 0.2,
                                   dualmono=dual)
            rec["master_bus"] = mb
            continue
        break
    rec["encoder"] = {"codec": enc, "bitrate": "320k", "channels": a.channels, "sample_rate": SR}
    verify_final(rec, exp, out_path, final, plan, y, priming, offset, a.check_times)
    json.dump(rec, open(out_path + ".remix.json", "w"), indent=1)
    shutil.rmtree(work, ignore_errors=True)
    log(f"done in {time.time() - t_start:.0f}s -> {out_path}")


def verify_final(rec, exp, out_path, final, plan, y_export, priming, offset, check_times=()):
    fa = al.load(out_path, channels=2)
    f = fa[:, 0].copy()  # one channel of the (dual-mono) new default track; -ac 1 would add +3 dB
    del fa
    pr = lambda p: json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                              "stream=codec_type,codec_name,bit_rate,channels,sample_rate,duration:stream_disposition=default:format=duration",
                                              "-of", "json", p], capture_output=True, text=True).stdout)
    lag_mix, _ = global_lag(final, f[:len(final)], n=6)
    n = min(len(f), len(y_export))
    lag_exp, wins = global_lag(y_export[:n], f[:n], n=8)
    lvl = lambda x, a_, b_: (round(al.rms_db(x[int(a_ * SR):int(b_ * SR)]), 1),
                             round(20 * math.log10(float(np.max(np.abs(x[int(a_ * SR):int(b_ * SR)]))) + 1e-12), 1))
    clicks = []
    for t in [s_.t0 for s_ in plan["sfx"]] + list(check_times):
        fr, fp = lvl(f, t + offset / SR, t + offset / SR + 0.93)
        er, ep = lvl(y_export, t + priming / SR, t + priming / SR + 0.93)
        nr, _ = lvl(f, t + offset / SR - 0.5, t + offset / SR - 0.05)
        clicks.append({"t_s": round(t, 3), "final_rms_dbfs": fr, "final_peak_dbfs": fp, "export_rms_dbfs": er,
                       "export_peak_dbfs": ep, "final_level_change_db": round(fr - er, 1),
                       "final_floor_before_dbfs": nr, "above_floor_db": round(fr - nr, 1)})
    pe, pf = pr(exp), pr(out_path)
    rec["final"] = {
        "path": out_path, "loudness": al.ebur128(out_path),
        "lag_vs_master_wav_samples": lag_mix,
        "lag_vs_raw_export_ms": round(lag_exp * 1000 / SR, 3),
        "lag_vs_raw_export_after_priming_ms": round((lag_exp + priming - offset) * 1000 / SR, 3),
        "lag_windows": wins,
        "duration_export_s": float(pe["format"]["duration"]), "duration_final_s": float(pf["format"]["duration"]),
        "streams_export": pe["streams"], "streams_final": pf["streams"], "clicks": clicks,
    }
    log(f"final: {rec['final']['loudness']} lag vs export {rec['final']['lag_vs_raw_export_ms']} ms "
        f"(after priming {rec['final']['lag_vs_raw_export_after_priming_ms']} ms); "
        f"duration {rec['final']['duration_final_s']} vs export {rec['final']['duration_export_s']}")


def dry_run_checks(a, tl, pid, stems, plan, rec):
    voice, raw = stems["voice"], stems["raw_voice"]
    words = br.read_transcript(pid, tl["duration_ms"])
    words = [w for w in words if w[1] - w[0] >= 120 and w[3].startswith("microphone")]
    rec["transcript_words"] = len(words)

    def lvl(x, w):
        i, j = int(w[0] * SR / 1000), int(w[1] * SR / 1000)
        return al.rms_db(x[i:j])
    allv = np.array([lvl(voice, w) for w in words])
    allr = np.array([lvl(raw, w) for w in words])
    gap = allv < -50
    rec["transcript_check"] = {
        "words_checked": len(words), "voice_rms_dbfs_median": round(float(np.median(allv)), 1) if len(words) else None,
        "raw_rms_dbfs_median": round(float(np.median(allr)), 1) if len(words) else None,
        "words_with_voice_below_-50dBFS": int(gap.sum()),
        "speech_words_raw_at_or_above_-50dBFS": int(np.sum(allr >= -50)),
        "of_those_voice_below_-40dBFS (real gaps)": int(np.sum((allr >= -50) & (allv < -40))),
        "max_raw_dbfs_of_quiet_words": round(float(allr[gap].max()), 1) if gap.any() else None,
        "examples_below": [{"t_ms": w[0], "word": w[2], "voice_dbfs": round(float(v), 1), "raw_dbfs": round(float(r), 1)}
                           for w, v, r, g in zip(words, allv, allr, gap) if g][:15],
    }
    if words:
        idx = np.linspace(0, len(words) - 1, 10).astype(int)
        rec["transcript_samples"] = [{"t_ms": words[i][0], "end_ms": words[i][1], "word": words[i][2],
                                      "voice_dbfs": round(float(allv[i]), 1), "raw_dbfs": round(float(allr[i]), 1)} for i in idx]
    # mic coverage: every transcript word must sit inside a mic segment
    segs = [(s.t0 * 1000, s.t1 * 1000) for s in plan["mic"]]
    outside = [w for w in words if not any(a0 - 1 <= w[0] and w[1] <= b0 + 1 for a0, b0 in segs)]
    rec["transcript_words_outside_mic_segments"] = len(outside)
    cov = []
    for sc in tl["overview"]["scenes"]:
        a0, b0 = sc["start_ms"] / 1000, sc["end_ms"] / 1000
        covered = sum(max(0.0, min(b0, s_.t1) - max(a0, s_.t0)) for s_ in plan["mic"])
        cov.append({"scene": sc["name"], "id": sc["id"], "start_s": a0, "end_s": b0, "mic_covered_s": round(covered, 3),
                    "mic_fraction": round(covered / (b0 - a0), 4) if b0 > a0 else None,
                    "mic_media": sorted({s_.media[:8] for s_ in plan["mic"] if s_.t0 < b0 and s_.t1 > a0}),
                    "other_audio": sorted({f"{s_.layer}:{s_.media[:8]}" for k in OTHER_STEMS for s_ in plan[k] if s_.t0 < b0 and s_.t1 > a0}),
                    "muted": sorted({f"{s_.layer}:{s_.media[:8]}" for s_ in plan["muted"] if s_.t0 < b0 and s_.t1 > a0})})
    rec["mic_coverage_by_scene"] = cov
    rec["levels"] = {k: al.ebur128(stems[k]) for k in ("voice", "raw_voice", "other")}
    rec["sound_effect_levels"] = [{"t_s": round(s.t0, 3), "other_rms_dbfs_first_400ms": round(al.rms_db(
        stems["other"][int(s.t0 * SR):int((s.t0 + 0.4) * SR)]), 1)} for s in plan["sfx"]]
    if not a.no_preview:
        work = tempfile.mkdtemp(prefix="remix-")
        final, mb = master_bus(stems["voice"] + stems["other"], work)
        p = os.path.join(a.stems_dir, "mix_preview.wav")
        al.write_wav(p, final, codec="pcm_s24le")
        rec["preview_mix"] = {"path": p, "master_bus": mb, "loudness_mono": al.ebur128(p),
                              "loudness_as_dual_mono": al.ebur128(p, dualmono=True)}
        shutil.rmtree(work, ignore_errors=True)
    log(f"transcript check: {rec['transcript_check']['words_with_voice_below_-50dBFS']} of {len(words)} words below -50 dBFS in voice stem")


if __name__ == "__main__":
    main()
