#!/usr/bin/env python3
"""Automatic QA of a tighter cut: transcript diff vs the expected kept words, join clicks, join contact sheets,
loudness and length; optional frame-mapping (PSNR) and post-join flash scans; side-by-side join transcriptions.

usage: qa_cut.py CUT.mp4 EDL.json OUTDIR --config tighten.json [--whisper-model ggml-small.bin | --skip-whisper]
                 [--master MASTER.mp4 --psnr-joins 8] [--flash-scan [--panel-crop 1500:1080:0:0]]
                 [--verify-joins auto|none|T1,T2,...]

EDL.json = build_cut.py's OUT.mp4.work/edl.json. Writes OUTDIR/qa_report.json (summary printed), transcript_diffs.json,
join_audio.json, joins_NN.jpg contact sheets, and cut_whisper.json (cached: only re-run when CUT.mp4 is newer).
  1. TRANSCRIPT: whisper-cli on the cut (16 kHz mono) vs the words whose midpoint survives the EDL (normalised a-z0-9),
     difflib opcodes; each diff gets the nearest join and its distance. word_match_ratio ~0.97 is normal with the
     small model; most diffs are spellings (names, "README" -> "read me"). Only deletes/inserts within ~500 ms of a join
     are suspicious, and those are checked by transcribing each side of the join SEPARATELY (step 5), because a
     whisper diff across a join is often whisper merging two phrases, or the speaker's own words in the master.
  2. CLICKS: |diff| spike in +-2 ms of each join vs the 99th percentile of +-200 ms around it (ratio > 3 = suspect);
     joins louder than -35 dB RMS (10 ms) are listed (a cut inside speech).
  3. CONTACT SHEETS: (last frame before, first frame after) every join, 8 pairs per 4x4 sheet, in time order. The
     select filter is fed 10 joins per pass: one expression with ~100+ eq() terms fails to parse.
  4. LOUDNESS (EBU R128 I / LRA / true peak) and duration.
  5. JOIN CHECKS (--verify-joins auto: every delete/insert diff within 500 ms of a join): 3 s before / after the join
     in the cut and 3 s before the cut-in / after the cut-out in the master, each transcribed on its own.
  6. --master: PSNR of the frames on both sides of sampled joins vs the master frames the EDL says they come from
     (40+ dB = right frame; ~5-10 dB = off by a piece). Tail renders and panel patches lower it locally: expected.
  7. --flash-scan: panel-crop frame differences for j-2..j+12 at every join. A hard cut is one big difference at the
     join; ONE more big jump in the next two frames followed by calm is a flash frame (typically 1 frame of the
     previous visual before a chapter card: the cut ended one frame early). It does NOT catch semantic leftovers (a
     removed visual's faint fade-out residue, a visual stranded without its narration, a near-empty panel): those need
     eyes on the contact sheets (workflows/final-qa.js). Suspects are to LOOK at (frame.sh f<N>), not failures.
Requires numpy, ffmpeg; whisper-cli + a ggml model for 1 and 5 (--whisper-model, $WHISPER_MODEL or config whisper_model).
"""
import argparse
import difflib
import json
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tlib  # noqa: E402


def whisper_text(model, wav, lang):
    r = subprocess.run(["whisper-cli", "-m", model, "-f", wav, "-l", lang, "-nt", "-np"], capture_output=True, text=True)
    return " ".join(r.stdout.split())


def clip_wav(src, t0, t1, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t0):.3f}", "-to", f"{max(0.0, t1):.3f}", "-i", src,
                    "-ac", "1", "-ar", "16000", out], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cut"); ap.add_argument("edl"); ap.add_argument("outdir")
    ap.add_argument("--config", required=True, help="tighten.json (words_dir, fps)")
    ap.add_argument("--words-dir")
    ap.add_argument("--whisper-model", help="ggml model path (default: $WHISPER_MODEL or config whisper_model)")
    ap.add_argument("--language", default="en")
    ap.add_argument("--skip-whisper", action="store_true", help="skip transcript diff and join transcriptions")
    ap.add_argument("--near-join-ms", type=float, default=1500)
    ap.add_argument("--click-ratio", type=float, default=3.0)
    ap.add_argument("--loud-join-db", type=float, default=-35)
    ap.add_argument("--joins-per-pass", type=int, default=10, help="joins per select pass (<= 50; the parser dies near 100 terms)")
    ap.add_argument("--sheet-width", type=int, default=640)
    ap.add_argument("--verify-joins", default="auto", help="auto | none | comma-separated cut times in s")
    ap.add_argument("--master", help="master mp4 for the PSNR frame-mapping check (default: edl.json master)")
    ap.add_argument("--psnr-joins", type=int, default=0, help="how many joins (evenly spread) to PSNR-check (0 = off)")
    ap.add_argument("--flash-scan", action="store_true")
    ap.add_argument("--panel-crop", default="1500:1080:0:0", help="W:H:X:Y region for --flash-scan")
    a = ap.parse_args()
    tlib.require_numpy()
    import numpy as np

    cfg = tlib.load_config(a.config)
    V3, OUT = a.cut, a.outdir
    os.makedirs(OUT, exist_ok=True)
    with open(a.edl, encoding="utf-8") as fh:
        edl = json.load(fh)
    FPS = edl.get("fps", cfg["fps"]); SR = cfg["sample_rate"]
    keep = edl["keep_frames"]
    pieces, off = [], 0
    for s, e in keep:
        pieces.append((s, e, off)); off += e - s
    joins = [p[2] for p in pieces[1:]]  # output frame index where each new piece starts
    removed = lambda t_ms: not any(s * 1000 / FPS <= t_ms < e * 1000 / FPS for s, e, _ in pieces)
    rep = {"joins": len(joins)}
    model = a.whisper_model or os.environ.get("WHISPER_MODEL") or cfg.get("whisper_model")
    if not a.skip_whisper and not (model and os.path.exists(model)):
        tlib.die("whisper model not found: pass --whisper-model, set $WHISPER_MODEL, or use --skip-whisper")

    # 1. expected kept words vs whisper on the cut
    words = tlib.all_words(cfg, a.words_dir)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    diffs = []
    if not a.skip_whisper:
        exp = [norm(str(w[2])) for w in words if not removed((w[0] + w[1]) / 2)]
        exp = [w for w in exp if w]
        wav16 = os.path.join(OUT, "cut_16k.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", V3, "-ac", "1", "-ar", "16000", wav16], check=True)
        wjp = os.path.join(OUT, "cut_whisper.json")
        if not (os.path.exists(wjp) and os.path.getmtime(wjp) > os.path.getmtime(V3)):
            subprocess.run(["whisper-cli", "-m", model, "-l", a.language, "-f", wav16, "-oj", "-of", os.path.join(OUT, "cut_whisper"), "-np"],
                           check=True, capture_output=True)
        with open(wjp, encoding="utf-8") as fh:
            wj = json.load(fh)
        segs = [(int(s["offsets"]["from"]), int(s["offsets"]["to"]), s["text"]) for s in wj["transcription"]]
        got, got_t = [], []
        for sa, sb, t in segs:
            toks = [norm(x) for x in t.split()]; toks = [x for x in toks if x]
            for i, x in enumerate(toks):
                got.append(x); got_t.append(sa + (sb - sa) * (i + 0.5) / max(1, len(toks)))
        sm = difflib.SequenceMatcher(a=exp, b=got, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal": continue
            t = got_t[j1] if j1 < len(got_t) else (got_t[-1] if got_t else 0)
            near = min(joins, key=lambda j: abs(j * 1000 / FPS - t)) if joins else None
            dj = abs(near * 1000 / FPS - t) if near is not None else None
            diffs.append({"op": op, "expected": " ".join(exp[i1:i2]), "got": " ".join(got[j1:j2]), "t_s": round(t / 1000, 2),
                          "nearest_join_s": round(near / FPS, 2) if near is not None else None, "dist_ms": round(dj) if dj is not None else None})
        rep["word_match_ratio"] = round(sm.ratio(), 4)
        rep["expected_words"] = len(exp); rep["whisper_words"] = len(got)
        rep["diffs_near_joins"] = [d for d in diffs if d["dist_ms"] is not None and d["dist_ms"] < a.near_join_ms]
        rep["diffs_total"] = len(diffs)
        with open(os.path.join(OUT, "transcript_diffs.json"), "w") as fh:
            json.dump(diffs, fh, indent=1)

    # 2. clicks at joins (sample-difference spike vs local 99th percentile)
    x = tlib.decode_audio(V3, SR, 1)
    d = np.abs(np.diff(x))
    clicks = []
    for j in joins:
        c = j * SR // FPS
        win = d[max(0, c - 96):c + 96]; ref = d[max(0, c - 9600):c + 9600]
        med = float(np.percentile(ref, 99)) + 1e-6
        ratio = float(win.max()) / med if len(win) else 0
        lvl = 20 * math.log10(float(np.sqrt((x[max(0, c - 480):c + 480] ** 2).mean())) + 1e-9)
        clicks.append({"join_s": round(j / FPS, 3), "spike_ratio": round(ratio, 2), "rms_db_10ms": round(lvl, 1)})
    rep["click_suspects"] = [c for c in clicks if c["spike_ratio"] > a.click_ratio]
    rep["loud_joins"] = [c for c in clicks if c["rms_db_10ms"] > a.loud_join_db]
    with open(os.path.join(OUT, "join_audio.json"), "w") as fh:
        json.dump(clicks, fh, indent=1)

    # 3. join contact sheets: frame before / after each join, 8 joins per sheet
    fr = os.path.join(OUT, "jf"); os.makedirs(fr, exist_ok=True)
    for f in os.listdir(fr): os.remove(os.path.join(fr, f))
    step = max(1, min(50, a.joins_per_pass))
    for c0 in range(0, len(joins), step):  # the select parser can't take ~100 terms: a few joins per pass
        chunk = joins[c0:c0 + step]
        sel = "+".join(f"eq(n\\,{j - 1})+eq(n\\,{j})" for j in chunk)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", V3, "-vf", f"select='{sel}',scale={a.sheet_width}:-2", "-fps_mode", "passthrough",
                        "-start_number", str(2 * c0 + 1), os.path.join(fr, "f_%04d.jpg")], check=True)
    n = len(os.listdir(fr)); sheets = []
    if n != 2 * len(joins):
        print(f"WARN contact sheets: {n} frames for {len(joins)} joins (a one-frame piece makes pairs share frames); "
              "pairs after it are shifted", flush=True)
    for k in range(0, n, 16):
        sp = os.path.join(OUT, f"joins_{k // 16 + 1:02d}.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-start_number", str(k + 1), "-i", os.path.join(fr, "f_%04d.jpg"),
                        "-frames:v", "1", "-vf", "tile=4x4", sp], check=True)
        sheets.append(sp)
    rep["join_sheets"] = sheets
    rep["join_times_s"] = [round(j / FPS, 3) for j in joins]
    rep["join_frames"] = joins

    # 4. loudness and lengths
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", V3, "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    g = lambda p: float(re.findall(p, err)[-1])
    try:
        rep["loudness"] = {"I": g(r"I:\s+(-?[\d.]+) LUFS"), "LRA": g(r"LRA:\s+(-?[\d.]+) LU"), "TP": g(r"Peak:\s+(-?[\d.]+) dBFS")}
    except (IndexError, ValueError):
        rep["loudness"] = None
    rep["duration_s"] = off / FPS
    rep["frames_in_file"] = tlib.nframes(V3)
    rep["frames_expected"] = off

    # 5. suspicious diffs: transcribe each side of the join on its own (cut) and the master around the cut point
    master = a.master or edl.get("master")
    if not a.skip_whisper and a.verify_joins != "none":
        if a.verify_joins == "auto":
            tj = sorted({d["nearest_join_s"] for d in diffs if d["op"] in ("delete", "insert") and d["dist_ms"] is not None
                         and d["dist_ms"] < 500})
        else:
            tj = [float(v) for v in a.verify_joins.split(",") if v.strip()]
        checks = []
        jd = os.path.join(OUT, "joincheck"); os.makedirs(jd, exist_ok=True)
        for t in tj:
            jf = min(joins, key=lambda j: abs(j / FPS - t)) if joins else None
            if jf is None: break
            k = joins.index(jf) + 1  # piece index that starts at this join
            cut_in_ms = pieces[k - 1][1] * 1000 / FPS; cut_out_ms = pieces[k][0] * 1000 / FPS
            ts_ = jf / FPS
            res = {"join_s": round(ts_, 3), "master_cut_ms": [round(cut_in_ms, 3), round(cut_out_ms, 3)]}
            for name, src, t0, t1 in [("cut_before", V3, ts_ - 3, ts_), ("cut_after", V3, ts_, ts_ + 3),
                                      ("master_before_cut", master, cut_in_ms / 1000 - 3, cut_in_ms / 1000),
                                      ("master_after_cut", master, cut_out_ms / 1000, cut_out_ms / 1000 + 3)]:
                if not src or not os.path.exists(src): continue
                w16 = os.path.join(jd, f"j{ts_:.2f}_{name}.wav")
                clip_wav(src, t0, t1, w16)
                res[name] = whisper_text(model, w16, a.language)
            checks.append(res)
        rep["join_checks"] = checks

    # 6. PSNR frame-mapping spot check
    if a.psnr_joins and master and os.path.exists(master) and joins:
        W, H = tlib.video_size(V3)

        def frame(path, nidx):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, (nidx - 0.3) / FPS):.6f}", "-i", path, "-frames:v", "1",
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
            return np.frombuffer(raw, np.uint8)[:W * H * 3].reshape(H, W, 3).astype(np.float32) if len(raw) >= W * H * 3 else None

        def psnr(p, q):
            if p is None or q is None or p.shape != q.shape: return None
            m = float(((p - q) ** 2).mean()); return 99.0 if m == 0 else round(10 * math.log10(255 ** 2 / m), 1)
        idxs = sorted({round(i * (len(joins) - 1) / max(1, a.psnr_joins - 1)) for i in range(a.psnr_joins)})
        ps = []
        for i in idxs:
            j = joins[i]
            before = psnr(frame(V3, j - 1), frame(master, pieces[i][1] - 1))
            after = psnr(frame(V3, j), frame(master, pieces[i + 1][0]))
            ps.append({"join_s": round(j / FPS, 3), "psnr_before_db": before, "psnr_after_db": after})
        rep["psnr_mapping"] = ps
        rep["psnr_suspects"] = [p for p in ps if (p["psnr_before_db"] or 0) < 30 or (p["psnr_after_db"] or 0) < 30]

    # 7. post-join flash scan on the panel
    if a.flash_scan and joins:
        cw, ch_, cx, cy = [int(v) for v in a.panel_crop.split(":")]
        SW, SH = 500, max(2, round(500 * ch_ / cw / 2) * 2)

        def grab(first, count):
            out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, (first - 0.3) / FPS):.6f}", "-i", V3, "-frames:v", str(count),
                                  "-vf", f"crop={cw}:{ch_}:{cx}:{cy},scale={SW}:{SH},format=gray", "-f", "rawvideo", "-"],
                                 capture_output=True).stdout
            return np.frombuffer(out, np.uint8)[:len(out) // (SW * SH) * SW * SH].reshape(-1, SH, SW).astype(np.float32)
        scan = []
        for j in joins:
            A = grab(j - 2, 15)
            if len(A) < 8: continue
            dd = [float(np.abs(A[i] - A[i - 1]).mean()) for i in range(1, len(A))]  # dd[1] = (j-1 -> j), the cut
            # a flash = ONE big jump in the first two frames after the join, then calm (calibrated on REA: the real
            # 1-frame flash before a chapter card measured 33.2 then <= 1.0; 30 fps animations inside a 60 fps master
            # alternate 0.03 / 5-8 and must not trigger)
            kmax = 2 + int(np.argmax(dd[2:4]))
            jump = dd[kmax]
            rest = max(dd[kmax + 1:]) if len(dd) > kmax + 1 else 0.0
            scan.append({"join_s": round(j / FPS, 3), "join_frame": j, "cut_diff": round(dd[1], 2),
                         "post_jump": round(jump, 2), "post_jump_frame": j + kmax - 2, "after_jump_max": round(rest, 2),
                         "diffs": [round(v, 2) for v in dd]})
        rep["flash_scan"] = scan
        rep["flash_suspects"] = [{k: v for k, v in s.items() if k != "diffs"} for s in scan
                                 if s["post_jump"] > 10 and s["after_jump_max"] < s["post_jump"] / 4]

    with open(os.path.join(OUT, "qa_report.json"), "w") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k not in ("join_times_s", "join_frames", "flash_scan", "psnr_mapping")},
                     indent=1)[:6000])
    if rep["frames_in_file"] != rep["frames_expected"]:
        print(f"WARN frame count {rep['frames_in_file']} != EDL {rep['frames_expected']}")


if __name__ == "__main__":
    main()
