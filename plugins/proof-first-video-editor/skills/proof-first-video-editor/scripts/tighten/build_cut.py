#!/usr/bin/env python3
"""Build the tighter cut from the finished master in ONE encode: frame-exact cuts, crossfaded mastered audio,
optional full-frame tail-render overlay (with alpha mask) and optional left-panel patches.

usage: build_cut.py RANGES.json OUT.mp4 [--config tighten.json] [--master M.mp4] [--wav AUDIO.wav]
                    [--tail-render R.mp4 --tail-start-s T0 [--tail-from-frame F] [--tail-fps 30]
                     (--tail-mask MASK.png | --tail-keep-rect X,Y,W,H ...)]
                    [--panel-patches PATCHES.json [--panel-crop W:H:X:Y]]
                    [--crf 16] [--preset medium] [--limit-s N] [--dry-run]

RANGES.json: snap_cuts.py / apply_pins.py output ({"ranges": [{"start_ms","end_ms"}...]}); every edge on the fps grid.
What it does (all of it proven on a 20:02 -> 14:02 cut with 56 removals):
  * VIDEO: removal ms -> frames with round(); keep pieces -> select='between(n,a,b)+...',setpts=N/FPS/TB, one libx264
    encode at constant fps (-fps_mode cfr). The filtergraph is written to WORK/graph.txt and passed with ffmpeg's
    "-/filter_complex FILE" syntax (ffmpeg >= 7; ffmpeg 9 removed -filter_complex_script, used here only on ffmpeg 6).
    The select parser fails above 100 '+' terms ("Error while parsing expression"), so with > 100 keep pieces the
    terms are grouped in parentheses of 50.
  * AUDIO: the mastered WAV (not the master's AAC) is cut on the same frame grid in samples (sr/fps per frame) with an
    8 ms equal-power crossfade centred on every join, then muxed as AAC stereo (mono -> stereo is -3 dB per channel,
    which keeps the integrated loudness). Without --wav/audio_wav the master's own audio track is used (one more AAC
    generation; channel count preserved so the level does not drop).
  * TAIL RENDER (optional): a re-rendered full-frame visual for the end of the video (e.g. a privacy fix) that was not
    placed back into the editor. It is shifted to timeline second --tail-start-s, alpha-masked (MASK white = render,
    black = keep master: e.g. the camera tile + step badge) and overlaid on the master from render frame
    --tail-from-frame on, BEFORE the cut, so it is cut together with everything else.
  * PANEL PATCHES (optional): PATCHES.json = [{"from": f0, "to": f1, "src": fs, "why": "..."}] in MASTER frames:
    the panel crop of master frames [f0, f1) is replaced with a still of master frame fs. Use it for a removed visual's
    fade-out that still shows after a join (src = first clean frame after it) or a near-empty panel (src = an earlier
    frame to hold). Each still is a BOUNDED looped input (-loop 1 -t ... -itsoffset ...): an unbounded -loop 1 input
    never reaches EOF and the encode never ends. It starts half a frame early so the first patched frame always has a
    patch frame (timestamp rounding), and the overlay uses eof_action=pass.
  * CHECKS: output frame count == kept frames, video duration == kept/fps (+-20 ms), audio == video (+-50 ms);
    prints BUILD OK or BUILD CHECK FAILED (exit 1). WORK = OUT.mp4.work/ holds edl.json (keep_frames, remove_frames,
    fps: the input of build_captions.py and qa_cut.py), graph.txt, the audio f32, panel stills, patch_out_frames.json,
    build_report.json.
--limit-s N: test mode on the first N seconds of the master (fast check of the cut logic).
--dry-run: write edl.json + graph.txt (+ audio unless --no-audio) and print the ffmpeg command, no encode.
Presets: a quick full-length preview with --preset veryfast --crf 20 (about 9x real time for 1080p60 on Apple silicon),
the final with --preset medium --crf 16.
Requires numpy, ffmpeg/ffprobe >= 6 (7+ recommended).
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tlib  # noqa: E402


def select_expr(keep):
    terms = [f"between(n\\,{s}\\,{e - 1})" for s, e in keep]
    if len(terms) <= 100:  # proven flat form
        return "+".join(terms)
    while len(terms) > 100:  # the expression parser rejects > 100 '+' terms: group them
        terms = ["(" + "+".join(terms[i:i + 50]) + ")" for i in range(0, len(terms), 50)]
    return "+".join(terms)


def make_mask(path, size, rects):
    """Gray PNG: 255 everywhere (render shows), 0 inside each keep-rect (master shows)."""
    import numpy as np
    w, h = size
    m = np.full((h, w), 255, np.uint8)
    for x, y, rw, rh in rects:
        m[max(0, y):min(h, y + rh), max(0, x):min(w, x + rw)] = 0
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-i", "-", path],
                   input=m.tobytes(), check=True)


def parse_rect(s):
    p = [int(round(float(v))) for v in s.replace(":", ",").split(",")]
    if len(p) != 4:
        raise argparse.ArgumentTypeError("rect must be X,Y,W,H")
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ranges"); ap.add_argument("out")
    ap.add_argument("--config", help="tighten.json (master, audio_wav, fps, sample_rate)")
    ap.add_argument("--master", help="finished master mp4 (default: config master)")
    ap.add_argument("--wav", help="mastered audio aligned with the master (default: config audio_wav, else the master's audio)")
    ap.add_argument("--fps", type=int, help="default: config fps or 60")
    ap.add_argument("--sr", type=int, help="default: config sample_rate or 48000")
    ap.add_argument("--xfade-ms", type=float, default=8.0, help="equal-power crossfade at each join (8)")
    ap.add_argument("--tail-render", help="full-frame render to overlay at the end (formerly the W3 patch)")
    ap.add_argument("--tail-start-s", type=float, help="timeline second where the render's frame 0 sits")
    ap.add_argument("--tail-from-frame", type=int, default=0, help="first render frame that replaces the master (0)")
    ap.add_argument("--tail-fps", type=float, default=30.0, help="render frame rate (30)")
    ap.add_argument("--tail-mask", help="gray PNG at master size: white = render, black = keep master")
    ap.add_argument("--tail-keep-rect", type=parse_rect, action="append", default=[],
                    help="X,Y,W,H region where the master stays (repeatable); builds the mask when --tail-mask is not given")
    ap.add_argument("--panel-patches", help='JSON [{"from": f0, "to": f1, "src": fs}] in master frames')
    ap.add_argument("--panel-crop", default="1500:1080:0:0",
                    help="W:H:X:Y of the patched panel (1500:1080:0:0 = left panel of the 1920x1080 proof-first layout)")
    ap.add_argument("--crf", type=int, default=16); ap.add_argument("--preset", default="medium")
    ap.add_argument("--audio-bitrate", default="320k")
    ap.add_argument("--limit-s", type=float, help="test mode: only the first N seconds of the master")
    ap.add_argument("--dry-run", action="store_true", help="write edl/graph (+audio) and print the command; no encode")
    ap.add_argument("--no-audio", action="store_true", help="with --dry-run: skip the audio render")
    a = ap.parse_args()
    tlib.require_numpy()
    import numpy as np

    cfg = tlib.load_config(a.config) if a.config else {}
    MASTER = a.master or cfg.get("master") or tlib.die("no --master and no master in the config")
    WAV = a.wav or cfg.get("audio_wav")
    FPS = a.fps or cfg.get("fps") or 60
    SR = a.sr or cfg.get("sample_rate") or 48000
    if SR % FPS:
        tlib.die(f"sample rate {SR} is not a multiple of fps {FPS}; the sample grid must match the frame grid")
    spf = SR // FPS  # samples per frame (800 at 48 kHz / 60 fps)
    XF = int(round(a.xfade_ms * SR / 1000))  # 384 samples at 48 kHz
    XF -= XF % 2
    if XF // 2 >= spf:
        tlib.die("--xfade-ms must be shorter than two frames")
    if a.tail_render and a.tail_start_s is None:
        tlib.die("--tail-render needs --tail-start-s")
    if a.tail_render and not (a.tail_mask or a.tail_keep_rect):
        tlib.die("--tail-render needs --tail-mask or at least one --tail-keep-rect (what of the master stays visible)")
    work = a.out + ".work"; os.makedirs(work, exist_ok=True)

    N_FULL = tlib.nframes(MASTER)
    N = min(N_FULL, int(a.limit_s * FPS)) if a.limit_s else N_FULL
    with open(a.ranges, encoding="utf-8") as fh:
        rdoc = json.load(fh)
    off = [r for r in rdoc["ranges"] for x in (r["start_ms"], r["end_ms"]) if not tlib.on_grid(x, FPS)]
    if off:
        print(f"WARN {len(off)} range edge(s) off the {FPS} fps grid; round() decides the frame (check for flash frames)")
    rem = [(round(r["start_ms"] * FPS / 1000), round(r["end_ms"] * FPS / 1000)) for r in rdoc["ranges"]]
    rem = sorted((max(0, s), min(N, e)) for s, e in rem if e > s)
    keep, cur = [], 0
    for s, e in rem:
        if s > cur: keep.append((cur, s))
        cur = max(cur, e)
    if cur < N: keep.append((cur, N))
    kept_frames = sum(e - s for s, e in keep)
    print(f"master {N} frames; {len(rem)} removals; keep {len(keep)} pieces = {kept_frames} frames = {kept_frames / FPS:.3f} s", flush=True)
    with open(os.path.join(work, "edl.json"), "w") as fh:
        json.dump({"keep_frames": keep, "remove_frames": rem, "fps": FPS, "master": os.path.abspath(MASTER)}, fh)

    # ---- audio: concatenate kept sample ranges, equal-power crossfade centred on every join
    wav = os.path.join(work, "audio_cut.f32")
    if WAV:
        CH = 1 if tlib.audio_channels(WAV) <= 1 else tlib.audio_channels(WAV)
        src_audio = WAV
    else:
        CH = max(1, tlib.audio_channels(MASTER))
        src_audio = MASTER
        print("NOTE no --wav/audio_wav: cutting the master's own audio track (one more AAC generation)", flush=True)
    if not (a.dry_run and a.no_audio):
        x = tlib.decode_audio(src_audio, SR, CH)
        if x.ndim == 1: x = x[:, None]
        if abs(len(x) / SR - N_FULL / FPS) > 0.1:
            print(f"WARN audio is {len(x) / SR:.3f} s but the master is {N_FULL / FPS:.3f} s: "
                  "check that the WAV is sample-aligned with the master", flush=True)
        need = N * spf
        if len(x) < need: x = np.concatenate([x, np.zeros((need - len(x), CH), np.float32)])
        h = XF // 2
        tt = np.linspace(0, np.pi / 2, XF, dtype=np.float32)
        fin, fout = np.sin(tt)[:, None], np.cos(tt)[:, None]
        parts = []
        for k, (s, e) in enumerate(keep):
            A, B = s * spf, e * spf
            a0 = A + (h if k > 0 else 0)
            b0 = B - (h if k < len(keep) - 1 else 0)
            parts.append(x[a0:b0])
            if k < len(keep) - 1:
                A2 = keep[k + 1][0] * spf
                parts.append(x[B - h:B + h] * fout + x[A2 - h:A2 + h] * fin)
        y = np.concatenate(parts)
        assert len(y) == kept_frames * spf, (len(y), kept_frames * spf)
        y.astype(np.float32).tofile(wav)
        print(f"audio {len(y) / SR:.3f} s ({CH} ch)", flush=True)

    # ---- video filtergraph
    sel = select_expr(keep)
    tail = f"select='{sel}',setpts=N/{FPS}/TB,format=yuv420p[v]"
    inputs = (["-t", f"{N / FPS:.6f}"] if a.limit_s else []) + ["-i", MASTER]
    head = None
    if a.tail_render:
        T0 = a.tail_start_s
        tp = T0 + a.tail_from_frame / a.tail_fps
        mask = a.tail_mask
        if not mask:
            mask = os.path.join(work, "tail_alpha_mask.png")
            make_mask(mask, tlib.video_size(MASTER), a.tail_keep_rect)
        # bounded like every looped still (a couple of spare frames so alphamerge never runs short)
        mask_t = (tlib.nframes(a.tail_render) + 2) / a.tail_fps
        inputs += ["-i", a.tail_render, "-loop", "1", "-framerate", f"{a.tail_fps:g}", "-t", f"{mask_t:.6f}", "-i", mask]
        head = (f"[1:v]setpts=PTS-STARTPTS,format=yuva420p[r];[2:v]setpts=PTS-STARTPTS,format=gray[m];"
                f"[r][m]alphamerge,setpts=PTS+{T0}/TB[ra];"
                f"[0:v][ra]overlay=x=0:y=0:enable='gte(t\\,{tp:.6f})':eof_action=pass:format=auto")
    n_in = 3 if a.tail_render else 1
    if not a.panel_patches:
        g = (head + "," + tail) if head else ("[0:v]" + tail)
    else:
        with open(a.panel_patches, encoding="utf-8") as fh:
            patches = json.load(fh)
        cw, ch_, cx, cy = [int(v) for v in a.panel_crop.split(":")]
        chain, cur = [], "pp0"
        chain.append(head + f"[{cur}]" if head else f"[0:v]null[{cur}]")
        pieces, o = [], 0
        for s, e in keep:
            pieces.append((s, e, o)); o += e - s
        m2o = lambda m: next((oo + (m - s) for s, e, oo in pieces if s <= m < e), None)
        pmap = []
        for k, pt in enumerate(patches):
            if not (pt["from"] < pt["to"]):
                tlib.die(f"panel patch {k}: from must be < to ({pt})")
            still = os.path.join(work, f"panel_{k}.png")
            if not a.dry_run:
                # -ss (n - 0.3)/fps before -i lands exactly on frame n
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{(pt['src'] - 0.3) / FPS:.6f}", "-i", MASTER, "-frames:v", "1",
                                "-vf", f"crop={cw}:{ch_}:{cx}:{cy}", still], check=True)
            # bounded input: only frames inside the patch window, shifted to the window start (no infinite loop -> EOF works)
            # starts half a frame early so the first patched frame always has a patch frame (timestamp rounding)
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{(pt['to'] - pt['from'] + 1) / FPS:.6f}",
                       "-itsoffset", f"{(pt['from'] - 0.5) / FPS:.6f}", "-i", still]
            idx = n_in + k
            nxt = f"pp{k + 1}"
            chain.append(f"[{cur}][{idx}:v]overlay=x={cx}:y={cy}:eof_action=pass:enable='between(t\\,{(pt['from'] - 0.4) / FPS:.6f}\\,{(pt['to'] - 0.5) / FPS:.6f})'[{nxt}]")
            cur = nxt
            of, ol = m2o(pt["from"]), m2o(pt["to"] - 1)
            if of is None and ol is None:
                print(f"WARN panel patch {k} ({pt['from']}-{pt['to']}) lies entirely inside removed ranges", flush=True)
            pmap.append({**pt, "out_first": of, "out_last": ol})
        chain.append(f"[{cur}]{tail}")
        g = ";".join(chain)
        n_in += len(patches)
        with open(os.path.join(work, "patch_out_frames.json"), "w") as fh:
            json.dump(pmap, fh, indent=1)
    ai = n_in
    gpath = os.path.join(work, "graph.txt")
    with open(gpath, "w") as fh:
        fh.write(g)
    graph_opt = ["-/filter_complex", gpath] if tlib.ffmpeg_major() >= 7 else ["-filter_complex_script", gpath]
    cmd = ["ffmpeg", "-v", "error", "-stats", "-y", *inputs, "-f", "f32le", "-ar", str(SR), "-ac", str(CH), "-i", wav,
           *graph_opt, "-map", "[v]", "-map", f"{ai}:a",
           "-c:v", "libx264", "-preset", a.preset, "-crf", str(a.crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-r", str(FPS), "-g", str(2 * FPS), "-fps_mode", "cfr",
           "-ac", "2", "-c:a", "aac", "-b:a", a.audio_bitrate, "-ar", str(SR), "-movflags", "+faststart", a.out]
    if a.dry_run:
        print("DRY RUN:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
        print(f"graph -> {gpath}; edl -> {os.path.join(work, 'edl.json')}")
        return
    print(" ".join(cmd[:6]), "...", flush=True)
    subprocess.run(cmd, check=True)

    vd = tlib.stream_duration(a.out, "v:0")
    ad = tlib.stream_duration(a.out, "a:0")
    nf = tlib.nframes(a.out)
    rep = {"out": a.out, "frames": nf, "expected_frames": kept_frames, "video_s": vd, "audio_s": ad,
           "expected_s": kept_frames / FPS, "keep_pieces": len(keep), "removals": len(rem),
           "tail_render": a.tail_render, "panel_patches": a.panel_patches}
    with open(os.path.join(work, "build_report.json"), "w") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps(rep, indent=1))
    ok = nf == kept_frames and abs(vd - kept_frames / FPS) < 0.02 and abs(ad - vd) < 0.05
    print("BUILD OK" if ok else "BUILD CHECK FAILED"); sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
