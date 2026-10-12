#!/usr/bin/env python3
"""Master every microphone SOURCE file used on the current Borumi timeline through Auphonic (your preset,
e.g. one named "Broadcast Voice"), one production per file, all concurrently, with a verified receipt per file.

usage:
  master_sources.py [project options] [--dry] [--only UUID_PREFIX ...] [--media PREFIX ...] [--reverify] [--force]

  project options: --bundle PATH | --project ALIAS [--config PATH] [--audio-dir DIR] [--project-id ID]
                   [--preset UUID] [--preset-name NAME]       (see audio_config.py)

Without --media the sources are the microphone media used on the CURRENT timeline (read-only MCP reads);
--media masters exactly the given bundle media id prefixes (no Borumi connection needed beyond the bundle).

Output: <audio_dir>/mastered/<media-uuid>.wav (the preset's wav-24bit output, 48 kHz, same length as the
source) + <audio_dir>/mastered/receipts.json (one record per media id; format in references/audio.md).
Per file it verifies duration (|out - src| <= 20 ms) and lag (GCC-PHAT + plain xcorr on 4 x 20 s speech
windows, must be 0 ms); a file that fails is recorded with ok=false and the run exits 1.

Why per-source (not only the finished mix): remix.py then rebuilds the timeline audio from these files at
exact timeline positions, which removes Borumi's variable export lag and its voice AGC from the voice.

Requirements: ffmpeg >= 6, numpy (+ scipy recommended), an Auphonic account with enough credits, and a preset
whose outputs include exactly one "WAV 24-bit" file (not a mono mixdown). The API key comes from
$AUPHONIC_API_KEY or the macOS Keychain (auphonic_api.py) and is never printed or written anywhere.
"""
import argparse, json, os, subprocess, sys, threading, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio_lib as al  # noqa: E402  (re-execs under a python with numpy if needed)
import audio_config  # noqa: E402
import auphonic_api as aph  # noqa: E402
import borumi_read as br  # noqa: E402

OUT = None
WORK = None
P = {}


class Fail(Exception):
    pass


def safe(fn, *a, **k):
    """Turn Auphonic API errors into Fail so one bad production does not kill the other threads."""
    try:
        return fn(*a, **k)
    except aph.AuphonicError as e:
        raise Fail(str(e))


def explicit_sources(prefixes):
    con = br.db_copy()
    full = [r[0] for r in con.execute("select id from medias")]
    out = {}
    for pre in prefixes:
        c = [f for f in full if f.startswith(pre)]
        if len(c) != 1:
            raise SystemExit(f"media prefix {pre!r} matches {len(c)} medias")
        out[c[0]] = {"short_id": pre, "path": br.media_file(c[0])}
    return out


def used_mic_sources():
    pid = br.project_id()
    tl = br.read_timeline(pid, fields=("media",))
    con = br.db_copy()
    full = [r[0] for r in con.execute("select id from medias")]
    shorts = set()
    for lid, L in tl["layers"].items():
        if L["kind"] == "microphone":
            for s in L["segments"].values():
                shorts.add(s["media"]["media_id"])
    res = br.resolver(full, shorts)
    out = {}
    for sh in sorted(shorts):
        f = res(sh)
        out[f] = {"short_id": sh, "path": br.media_file(f)}
    return out, tl


def master_one(key, media_id, src, log):
    t0 = time.time()
    os.makedirs(WORK, exist_ok=True)
    inp = os.path.join(WORK, f"{media_id}_in.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-c:a", "pcm_s24le", inp], check=True)
    body = {"title": f"{P['name']} mic {media_id[:8]}", "preset": P["preset"]}
    prod = safe(aph.create_production, key, body)
    log(f"{media_id[:8]} production {prod} uploading")
    safe(aph.upload, key, prod, inp)
    safe(aph.start, key, prod)
    d = safe(aph.wait, key, prod, 20)
    wavs = [f for f in d["output_files"] if f.get("download_url") and f.get("ending") == "wav"
            and f.get("format") == "wav-24bit" and not f.get("mono_mixdown")]
    if len(wavs) != 1:
        raise Fail("expected exactly one wav-24bit (non-mixdown) output in the preset, got "
                   f"{[(f.get('format'), f.get('suffix')) for f in d['output_files']]}")
    dl = os.path.join(WORK, f"{media_id}_auphonic.wav")
    safe(aph.download, key, wavs[0]["download_url"], dl)
    dst = os.path.join(OUT, f"{media_id}.wav")
    os.replace(dl, dst)
    log(f"{media_id[:8]} downloaded in {time.time() - t0:.0f}s, verifying")
    v = al.verify_pair(src, dst)
    a_in, a_out = aph.level_stats(d)
    rec = {
        "media_id": media_id, "source": src, "mastered": dst,
        "production": prod, "preset": P["preset"], "preset_name": P["preset_name"],
        "used_credits": d.get("used_credits"),
        "auphonic_input": a_in,
        "auphonic_output": a_out,
        "warning": d.get("warning_message") or None,
        **v,
        "seconds": round(time.time() - t0, 1),
    }
    os.remove(inp)
    return rec


def main():
    global OUT, WORK, P
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="list sources and check credits only")
    ap.add_argument("--only", nargs="*", help="restrict to these full media id prefixes")
    ap.add_argument("--reverify", action="store_true", help="re-run the local checks on existing mastered files")
    ap.add_argument("--force", action="store_true", help="re-master sources that already have a verified mastered file")
    ap.add_argument("--media", nargs="*", help="master exactly these bundle media id prefixes")
    ap.add_argument("--credit-margin", type=float, default=1.15,
                    help="require credits >= hours x margin + 0.05 h per file (default %(default)s)")
    audio_config.add_args(ap)
    a = ap.parse_args()
    P = br.set_project(audio_config.resolve(a))
    OUT = P["mastered"]
    WORK = os.path.join(OUT, "work")
    os.makedirs(OUT, exist_ok=True)
    rpath = os.path.join(OUT, "receipts.json")
    if a.reverify:
        receipts = json.load(open(rpath))
        for f, r in receipts.items():
            if r.get("error"):
                continue
            v = al.verify_pair(r["source"], r["mastered"])
            r.update(v)
            print(f"{f[:8]} ok={v['ok']} dur_diff {v['duration_diff_ms']} ms lag {v['lag_ms']} ms "
                  f"xcorr {[w['xcorr_lag_ms'] for w in v['lag_windows']]} in {v['input_lufs']} -> {v['output_lufs']} LUFS / {v['output_true_peak_dbtp']} dBTP")
        json.dump(receipts, open(rpath, "w"), indent=1)
        sys.exit(0 if all(r.get("ok") for r in receipts.values()) else 1)
    audio_config.require_preset(P)
    if a.media:
        srcs = explicit_sources(a.media)
        tl = {"timeline_hash": "(explicit --media)"}
    else:
        srcs, tl = used_mic_sources()
    if a.only:
        srcs = {k: v for k, v in srcs.items() if any(k.startswith(o) for o in a.only)}
    done = json.load(open(rpath)) if os.path.exists(rpath) else {}
    if not a.force:
        skip = [k for k in srcs if done.get(k, {}).get("ok") and os.path.exists(os.path.join(OUT, k + ".wav"))]
        if skip:
            print(f"{len(skip)} sources already mastered and verified (use --force to redo)")
        srcs = {k: v for k, v in srcs.items() if k not in skip}
        if not srcs:
            print("nothing to master")
            return
    total = 0.0
    for f, v in srcs.items():
        v["duration_s"] = al.duration(v["path"])
        total += v["duration_s"]
        print(f"{v['short_id']:6s} {f}  {v['duration_s']:9.3f} s")
    print(f"{len(srcs)} mic sources, total {total:.1f} s = {total / 3600:.3f} h (timeline hash {tl['timeline_hash']})")
    try:
        key = aph.api_key()
        cr = aph.credits(key)
    except aph.AuphonicError as e:
        sys.exit(str(e))
    print(f"Auphonic credits: {cr if cr is None else round(cr, 3)} h")
    if cr is None or cr < total / 3600 * a.credit_margin + 0.05 * len(srcs):
        sys.exit("not enough Auphonic credits")
    if a.dry:
        return
    receipts = json.load(open(rpath)) if os.path.exists(rpath) else {}
    lock = threading.Lock()

    def log(m):
        with lock:
            print(time.strftime("%H:%M:%S"), m, flush=True)

    def save():
        tmp = rpath + ".tmp"
        json.dump(receipts, open(tmp, "w"), indent=1)
        os.replace(tmp, rpath)

    def run(f, v):
        try:
            rec = master_one(key, f, v["path"], log)
            with lock:
                receipts[f] = rec
                save()
            log(f"{f[:8]} {'OK' if rec['ok'] else 'CHECK FAILED'} dur_diff {rec['duration_diff_ms']:.2f} ms lag {rec['lag_ms']} ms "
                f"in {rec['input_lufs']} LUFS -> out {rec['output_lufs']} LUFS / {rec['output_true_peak_dbtp']} dBTP")
        except Exception as e:
            with lock:
                receipts[f] = {"media_id": f, "error": str(e)[:500]}
                save()
            log(f"{f[:8]} FAILED: {e}\n{traceback.format_exc()[-800:]}")

    th = [threading.Thread(target=run, args=(f, v)) for f, v in srcs.items()]
    for t in th:
        t.start()
        time.sleep(1)  # stagger the uploads a little
    for t in th:
        t.join()
    bad = [k for k, r in receipts.items() if k in srcs and (r.get("error") or not r.get("ok"))]
    print("DONE", len(srcs) - len(bad), "ok,", len(bad), "bad", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
