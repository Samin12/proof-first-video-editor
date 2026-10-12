#!/usr/bin/env python3
"""Deliver a finished master: versioned names, checks, 720p phone copy, fixed SRT, chapters, superseded/, sha256 receipt.

usage: deliver.py MASTER.mp4 --out-dir DIR [--name NAME] [--version N]
                  [--audio MASTERED.wav | --stereo] [--allow-mono] [--move]
                  [--srt FILE | --reuse-previous-srt] [--names NAMES.json ...] [--chapters youtube-chapters.txt]
                  [--expect-size 1920x1080] [--expect-duration S] [--expect-fps 60]
                  [--loudness-target -16] [--loudness-tol 1.0] [--max-true-peak -1.0] [--no-loudness]
                  [--no-phone] [--receipts-dir DIR] [--dry-run] [--force]
       deliver.py --phone IN.mp4 OUT.mp4                       only the 720p phone copy (+ checks)
       deliver.py --verify delivered_vN.sha256 [--out-dir DIR] re-hash the delivered files against a receipt

What it does (nothing is ever deleted; older versions move aside only when EVERY check of the new one passes):
  1. Version: "<NAME> - final edit vN". N = 1 + the highest vK found in DIR and DIR/superseded (or --version).
     NAME defaults to the part of the master's file name before " - final edit vK", else --name is required.
     If MASTER already is DIR/"<NAME> - final edit vN.mp4" (e.g. written by edit-borumi-video's finalize_export.py),
     it is delivered in place under its own N.
  2. Final: copy (or --move) MASTER to "<NAME> - final edit vN.mp4". With --audio WAV the video stream is copied and
     WAV becomes the only audio track (AAC 320k 48 kHz stereo; a mono WAV is upmixed with ffmpeg -ac 2: -3.01 dB per
     channel, so the integrated loudness is preserved; never a unity duplicate, which is +3 dB, see
     references/audio.md lesson 4): use
     this to mux the Auphonic WAV directly instead of re-encoding an AAC track twice. --stereo upmixes the
     master's own mono track the same way (one extra AAC generation).
     Checks: size (1920x1080), audio present, 48 kHz, stereo (unless --allow-mono), A/V lengths within 0.1 s,
     duration (--expect-duration +-0.25 s), fps (--expect-fps), integrated loudness within --loudness-tol of
     --loudness-target (default -16 LUFS +-1) and true peak <= --max-true-peak (default -1.0 dBFS).
  3. Captions: --srt FILE is copied to "<NAME> - final edit vN.srt" and, with --names, fixed by fix_srt.py
     (lines still matching a "review" pattern are listed). --reuse-previous-srt copies the previous version's SRT
     (right for an audio-only remaster; WRONG for a shorter cut: map the captions through the cut list instead).
     Check: the last cue ends inside the video (catches a long master's SRT shipped with a shorter cut).
  4. Chapters: --chapters FILE (chapters.py output) becomes DIR/youtube-chapters.txt; the previous one moves to
     superseded/"youtube-chapters (vK).txt". Checks: first at 0:00, >= 3 chapters, >= 10 s apart, last one >= 10 s
     before the end.
  5. Phone copy: "<NAME> - final edit vN (720p phone).mp4" (scale 1280:720, x264 crf 23 medium, yuv420p, audio
     copied, faststart) for reviewing on a phone. Checks: 1280x720, audio, duration == final +-0.1 s.
  6. If all checks pass: every "<NAME> - final edit vK*" with K < N in DIR moves to DIR/superseded/ (never
     overwritten: a clash gets a timestamp suffix). Receipts in --receipts-dir (default DIR/_work/export):
       delivered_vN.sha256   "<sha256>  <file name>" lines; verify with: cd DIR && shasum -a 256 -c <receipt>
       delivered_vN.json     paths, sizes, hashes, probes, loudness, caption fixes, chapters, moves, checks
     If a check fails: exit 2, nothing is moved, the vN files stay for inspection, the JSON receipt says ok=false.
     A version number is consumed only by a successful delivery: the next run (without --version) retries the
     failed vN under the same N. Files of the vN being retried or replaced (--force) are first moved to
     superseded/replaced-vN-<time>/, never overwritten in place. The previous youtube-chapters.txt is labelled with
     the version whose receipt hashes match it ("youtube-chapters (v2).txt"), else "(before vN)".

Requires: Python 3.8+ (stdlib only), ffmpeg + ffprobe >= 6 on PATH. fix_srt.py next to this file (for --names).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TS_LINE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})\s+(\S.*)$")
SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def die(msg, code=1):
    log("deliver.py: " + msg)
    sys.exit(code)


class Checks:
    def __init__(self):
        self.items = []

    def add(self, name, ok, detail=""):
        self.items.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        return ok

    @property
    def ok(self):
        return all(c["ok"] for c in self.items)

    def show(self):
        log("checks:")
        for c in self.items:
            log("  [%s] %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"], (" - " + c["detail"]) if c["detail"] else ""))
        log("  => %s" % ("ALL PASS" if self.ok else "%d FAILED" % sum(1 for c in self.items if not c["ok"])))


# ------------------------------------------------------------------ media helpers

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die("command failed (%s): %s" % (" ".join(cmd[:4]) + " ...", r.stderr[-800:]), 2)
    return r


def ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die("ffprobe failed on %s: %s" % (path, r.stderr[-400:]), 2)
    d = json.loads(r.stdout)
    fmt_dur = float(d.get("format", {}).get("duration") or 0)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)

    def sdur(s):
        return float(s.get("duration") or fmt_dur) if s else 0.0

    fps = None
    if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        n, _, dd = v["avg_frame_rate"].partition("/")
        fps = float(n) / float(dd or 1)
    return {"duration": fmt_dur, "video": v, "audio": a, "video_s": sdur(v), "audio_s": sdur(a), "fps": fps,
            "summary": {"width": (v or {}).get("width"), "height": (v or {}).get("height"),
                        "vcodec": (v or {}).get("codec_name"), "fps": round(fps, 3) if fps else None,
                        "acodec": (a or {}).get("codec_name"), "sample_rate": (a or {}).get("sample_rate"),
                        "channels": (a or {}).get("channels"), "video_s": round(sdur(v), 3),
                        "audio_s": round(sdur(a), 3)}}


def loudness(path):
    """EBU R128 of the first audio track: integrated LUFS, LRA, true peak (dBFS)."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0",
                        "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"], capture_output=True, text=True)
    err = r.stderr
    s = err[err.rfind("Summary:"):] if "Summary:" in err else err

    def g(pat):
        m = re.findall(pat, s)
        if not m:
            return None
        return float("-inf") if m[-1] == "-inf" else float(m[-1])

    return {"integrated_lufs": g(r"I:\s+(-?[\d.]+|-inf)\s+LUFS"), "lra_lu": g(r"LRA:\s+(-?[\d.]+)\s+LU"),
            "true_peak_dbfs": g(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS")}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def phone_copy(src, out, size="1280:720"):
    tmp = out + ".part.mp4"
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src, "-vf", "scale=%s" % size, "-c:v", "libx264", "-crf", "23",
         "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", tmp])
    os.replace(tmp, out)
    return out


def mux_audio(video_src, out, audio_src=None):
    """Copy the video stream; the audio (audio_src, else the source's own track) -> AAC 320k 48 kHz stereo."""
    tmp = out + ".part.mp4"
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", video_src]
    if audio_src:
        cmd += ["-i", audio_src, "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    cmd += ["-c:v", "copy", "-ac", "2", "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart", tmp]
    run(cmd)
    os.replace(tmp, out)
    return out


def check_video(path, checks, label, want_size=None, want_dur=None, dur_tol=0.25, av_tol=0.1, want_fps=None,
                need_48k=False, need_stereo=False):
    p = ffprobe(path)
    v, a = p["video"] or {}, p["audio"]
    if want_size:
        checks.add("%s is %dx%d" % (label, want_size[0], want_size[1]),
                   v.get("width") == want_size[0] and v.get("height") == want_size[1], "%sx%s" % (v.get("width"), v.get("height")))
    checks.add("%s has audio" % label, a is not None,
               "" if not a else "%s %s Hz %s ch" % (a.get("codec_name"), a.get("sample_rate"), a.get("channels")))
    if a is not None and need_48k:
        checks.add("%s audio is 48 kHz" % label, str(a.get("sample_rate")) == "48000", a.get("sample_rate"))
    if a is not None and need_stereo:
        checks.add("%s audio is stereo" % label, a.get("channels") == 2,
                   "%s ch%s" % (a.get("channels"), "" if a.get("channels") == 2 else
                                " (mux the mastered WAV with --audio, or --stereo, or pass --allow-mono)"))
    if want_fps:
        checks.add("%s is %g fps" % (label, want_fps), p["fps"] is not None and abs(p["fps"] - want_fps) < 0.01, p["fps"])
    if want_dur is not None:
        checks.add("%s duration == %.2f s (+-%.2f)" % (label, want_dur, dur_tol), abs(p["video_s"] - want_dur) <= dur_tol,
                   "video %.3f s" % p["video_s"])
    if a is not None:
        checks.add("%s audio/video lengths match (+-%.2f s)" % (label, av_tol), abs(p["video_s"] - p["audio_s"]) <= av_tol,
                   "video %.3f s, audio %.3f s" % (p["video_s"], p["audio_s"]))
    return p


# ------------------------------------------------------------------ captions + chapters

def srt_cues(path):
    """-> [(start_s, end_s)] for every timing line."""
    out = []
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            m = SRT_TIME.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                out.append((g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0))
    return out


def check_srt(path, checks, video_s):
    cues = srt_cues(path)
    checks.add("srt has cues", len(cues) > 0, "%d cues" % len(cues))
    if cues:
        bad = [i + 1 for i, (s, e) in enumerate(cues) if e < s]
        checks.add("srt cue times are well formed (end >= start)", not bad, "bad cues %s" % bad[:5] if bad else "")
        last = max(e for _, e in cues)
        checks.add("srt ends inside the video", last <= video_s + 0.5,
                   "last cue ends %.2f s, video %.2f s%s" % (last, video_s, "" if last <= video_s + 0.5 else
                                                             " (a longer cut's SRT? map it through the cut list)"))
    return cues


def parse_chapters(path):
    """-> (header_lines, [(sec, title)])"""
    header, ch = [], []
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            m = TS_LINE.match(line)
            if m:
                h = int(m.group(1) or 0)
                ch.append((h * 3600 + int(m.group(2)) * 60 + int(m.group(3)), m.group(4).strip()))
            elif not ch:
                header.append(line)
            else:
                ch.append((None, line))
    return header, ch


def check_chapters(path, checks, video_s):
    header, ch = parse_chapters(path)
    junk = [t for s, t in ch if s is None]
    ch = [(s, t) for s, t in ch if s is not None]
    checks.add("chapters: every line after the header is 'M:SS Title'", not junk, "; ".join(junk[:3]))
    checks.add("chapters: first at 0:00", bool(ch) and ch[0][0] == 0, ch[0] if ch else "none")
    checks.add("chapters: at least 3", len(ch) >= 3, len(ch))
    short = ["%s (%d s)" % (a[1], b[0] - a[0]) for a, b in zip(ch, ch[1:]) if b[0] - a[0] < 10]
    if ch and video_s:
        if video_s - ch[-1][0] < 10:
            short.append("%s (%.1f s to the end)" % (ch[-1][1], video_s - ch[-1][0]))
    checks.add("chapters: each >= 10 s (YouTube ignores ALL chapters otherwise)", not short, "; ".join(short))
    if header and header[0].rstrip() != "Chapters":
        log("note: chapters header is %r (house style: 'Chapters')" % header[0])
    return header, ch


# ------------------------------------------------------------------ versions + supersede

def version_re(name):
    return re.compile(r"^%s - final edit v(\d+)\b" % re.escape(name))


def scan_versions(out_dir, name):
    rx, found = version_re(name), {}
    for d in (out_dir, os.path.join(out_dir, "superseded")):
        if os.path.isdir(d):
            for f in os.listdir(d):
                m = rx.match(f)
                if m:
                    found.setdefault(int(m.group(1)), []).append(os.path.join(d, f))
    return found


def unique_dest(dest):
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(dest)
    return "%s (moved %s)%s" % (stem, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), ext)


def move_aside(src, sup_dir, new_name=None):
    os.makedirs(sup_dir, exist_ok=True)
    dest = unique_dest(os.path.join(sup_dir, new_name or os.path.basename(src)))
    shutil.move(src, dest)
    return dest


def chapters_owner(chap, rec_dir):
    """The version whose successful delivery receipt lists this exact chapters file (by sha256), else None."""
    if not os.path.isdir(rec_dir):
        return None
    h, best = sha256(chap), None
    for f in os.listdir(rec_dir):
        mm = re.match(r"^delivered_v(\d+)\.json$", f)
        if not mm:
            continue
        try:
            with open(os.path.join(rec_dir, f), encoding="utf-8") as fh:
                d = json.load(fh)
        except ValueError:
            continue
        if d.get("ok") and ((d.get("files") or {}).get("chapters") or {}).get("sha256") == h:
            best = max(best or 0, int(mm.group(1)))
    return best


def copy_atomic(src, dst):
    tmp = dst + ".part"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


# ------------------------------------------------------------------ modes

def verify(receipt, out_dir=None):
    lines = [ln.rstrip("\n") for ln in open(receipt, encoding="utf-8") if ln.strip()]
    entries = []
    for ln in lines:
        m = re.match(r"^([0-9a-f]{64}) [ *](.+)$", ln)
        if not m:
            die("not a sha256 receipt line: %r" % ln)
        entries.append((m.group(1), m.group(2)))
    rdir = os.path.dirname(os.path.abspath(receipt))
    cands = [out_dir] if out_dir else [rdir, os.path.dirname(os.path.dirname(rdir))]
    base = next((c for c in cands if c and all(os.path.exists(os.path.join(c, n)) for _, n in entries)), None)
    if not base:
        die("cannot find the delivered files next to the receipt; pass --out-dir", 2)
    bad = 0
    for h, n in entries:
        ok = sha256(os.path.join(base, n)) == h
        bad += 0 if ok else 1
        log("%s: %s" % (n, "OK" if ok else "FAILED (file changed since delivery)"))
    sys.exit(0 if not bad else 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("master", nargs="?", help="the finished, mastered video")
    ap.add_argument("--out-dir", help="deliverables folder (e.g. '<project> exports')")
    ap.add_argument("--name", help="video name used in '<NAME> - final edit vN' (default: from the master's file name)")
    ap.add_argument("--version", type=int, help="version N (default: next free, counting superseded/ too)")
    ap.add_argument("--audio", help="mux this mastered WAV as the only audio track (video stream copied)")
    ap.add_argument("--stereo", action="store_true", help="upmix the master's own mono audio to stereo (re-encodes AAC)")
    ap.add_argument("--allow-mono", action="store_true", help="do not require a stereo audio track")
    ap.add_argument("--move", action="store_true", help="move the master instead of copying it (plain copy case only)")
    ap.add_argument("--srt", help="captions for this version")
    ap.add_argument("--reuse-previous-srt", action="store_true",
                    help="copy the previous version's SRT (only valid when the timeline did not change)")
    ap.add_argument("--names", action="append", help="fix_srt.py name map(s) to apply to the delivered SRT")
    ap.add_argument("--chapters", help="chapters.py output for this version")
    ap.add_argument("--expect-size", default="1920x1080", help="final frame size (default 1920x1080)")
    ap.add_argument("--expect-duration", type=float, help="expected duration in seconds (+-0.25)")
    ap.add_argument("--expect-fps", type=float, help="expected frame rate, e.g. 60")
    ap.add_argument("--loudness-target", type=float, default=-16.0, help="integrated LUFS target (default -16)")
    ap.add_argument("--loudness-tol", type=float, default=1.0, help="allowed deviation in LU (default 1.0)")
    ap.add_argument("--max-true-peak", type=float, default=-1.0, help="true-peak ceiling in dBFS (default -1.0)")
    ap.add_argument("--no-loudness", action="store_true", help="skip the loudness measurement")
    ap.add_argument("--no-phone", action="store_true", help="skip the 720p phone copy")
    ap.add_argument("--receipts-dir", help="where the receipts go (default OUT_DIR/_work/export)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    ap.add_argument("--force", action="store_true", help="allow replacing an existing vN (never older versions)")
    ap.add_argument("--phone", nargs=2, metavar=("IN", "OUT"), help="only make a 720p phone copy")
    ap.add_argument("--verify", metavar="RECEIPT", help="re-hash delivered files against a .sha256 receipt")
    a = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool) and not a.verify:
            die("%s not found on PATH (ffmpeg >= 6 required)" % tool)
    if a.verify:
        verify(a.verify, a.out_dir)
    if a.phone:
        src, out = a.phone
        ref = ffprobe(src)
        phone_copy(src, out)
        c = Checks()
        check_video(out, c, os.path.basename(out), (1280, 720), ref["video_s"], dur_tol=0.1)
        c.show()
        sys.exit(0 if c.ok else 2)
    if not a.master or not a.out_dir:
        ap.error("MASTER and --out-dir are required (or use --phone / --verify)")

    master = os.path.abspath(os.path.expanduser(a.master))
    out_dir = os.path.abspath(os.path.expanduser(a.out_dir))
    if not os.path.isfile(master):
        die("master not found: %s" % master)
    if os.path.dirname(master) == os.path.join(out_dir, "superseded"):
        die("the master is in superseded/: deliver a current file")
    m = re.match(r"^(.*) - final edit v(\d+)\b", os.path.basename(master))
    name = a.name or (m.group(1) if m else None)
    if not name:
        die("cannot derive the video name from %r: pass --name" % os.path.basename(master))
    try:
        want_size = tuple(int(x) for x in a.expect_size.lower().split("x"))
        assert len(want_size) == 2
    except (ValueError, AssertionError):
        die("--expect-size must look like 1920x1080")
    if a.audio and a.stereo:
        die("use either --audio or --stereo")
    if a.audio and not os.path.isfile(a.audio):
        die("--audio file not found: %s" % a.audio)
    if a.srt and not os.path.isfile(a.srt):
        die("--srt file not found: %s" % a.srt)
    if a.chapters and not os.path.isfile(a.chapters):
        die("--chapters file not found: %s" % a.chapters)
    if a.names:
        if not (a.srt or a.reuse_previous_srt):
            die("--names needs captions (--srt or --reuse-previous-srt)")
        sys.path.insert(0, HERE)
        import fix_srt  # noqa: E402
        fix_srt.load_rules(a.names)  # fail on a broken name map now, not after the copy and the encodes

    found = scan_versions(out_dir, name)
    sup = os.path.join(out_dir, "superseded")
    rec_dir = os.path.abspath(os.path.expanduser(a.receipts_dir)) if a.receipts_dir else os.path.join(out_dir, "_work", "export")
    in_place = bool(m and os.path.dirname(master) == out_dir and m.group(1) == name
                    and os.path.basename(master) == "%s - final edit v%s.mp4" % (name, m.group(2)))
    retry_failed = False
    if in_place:
        n = int(m.group(2))
        if a.version and a.version != n:
            die("the master is already v%d in place; --version %d conflicts" % (n, a.version))
    elif a.version:
        n = a.version
    elif found:
        n = max(found) + 1
        # a version number is consumed only by a successful delivery: retry a failed vN under the same N
        top_rec = os.path.join(rec_dir, "delivered_v%d.json" % max(found))
        if os.path.exists(top_rec):
            try:
                with open(top_rec, encoding="utf-8") as fh:
                    failed = json.load(fh).get("ok") is False
            except ValueError:
                failed = False
            if failed and all(os.path.dirname(p) == out_dir for p in found[max(found)]):
                n, retry_failed = max(found), True
    else:
        n = 1
    stem = "%s - final edit v%d" % (name, n)
    final = os.path.join(out_dir, stem + ".mp4")
    phone = os.path.join(out_dir, stem + " (720p phone).mp4")
    srt = os.path.join(out_dir, stem + ".srt")
    chap = os.path.join(out_dir, "youtube-chapters.txt")
    if not in_place and not retry_failed and os.path.exists(final) and not a.force:
        die("%s already exists; pick another --version or pass --force to replace vN" % final)
    if not in_place and found and n < max(found):
        die("v%d is older than the existing v%d: versions only go up" % (n, max(found)))
    if os.path.exists(os.path.join(sup, stem + ".mp4")):
        die("v%d is already in superseded/: versions only go up" % n)
    # files of THIS version from a failed run / a --force replacement: moved aside first (never mixed, never deleted)
    replace_now = [] if in_place else [p for p in found.get(n, []) if os.path.dirname(p) == out_dir and p != master]
    stale_parts = [os.path.join(out_dir, f) for f in (os.listdir(out_dir) if os.path.isdir(out_dir) else [])
                   if re.match(r"^youtube-chapters\.txt\.v\d+\.part$", f)]
    older = sorted(k for k in found if k < n)
    prev = older[-1] if older else None
    prev_srt = os.path.join(out_dir, "%s - final edit v%d.srt" % (name, prev)) if prev else None
    if a.reuse_previous_srt:
        if a.srt:
            die("use either --srt or --reuse-previous-srt")
        cands = [p for p in found.get(prev, []) if p.endswith(".srt")] if prev else []
        if not cands:
            die("--reuse-previous-srt: no SRT for v%s found" % prev)
        prev_srt = cands[0]
    plan_moves = [p for k in older for p in found[k] if os.path.dirname(p) == out_dir]
    log("video %r -> v%d%s in %s" % (name, n, " (in place)" if in_place else
                                      " (retrying the failed v%d)" % n if retry_failed else "", out_dir))
    if replace_now or stale_parts:
        log("  first move aside (superseded/replaced-v%d-*/): %s" % (
            n, ", ".join(os.path.basename(p) for p in replace_now + stale_parts)))
    log("  final  %s%s" % (os.path.basename(final), " <- " + master if not in_place else ""))
    if a.audio or a.stereo:
        log("  audio  %s" % (a.audio or "master's own track, upmixed to stereo"))
    log("  phone  %s" % ("(skipped)" if a.no_phone else os.path.basename(phone)))
    log("  srt    %s" % (os.path.basename(srt) + " <- " + (a.srt or prev_srt) if (a.srt or a.reuse_previous_srt) else "(none)"))
    log("  chapters %s" % ("youtube-chapters.txt <- " + a.chapters if a.chapters else "(none)"))
    log("  after all checks pass, move to superseded/: %s" % (", ".join(os.path.basename(p) for p in plan_moves) or "nothing"))
    if a.dry_run:
        return
    if not a.chapters and os.path.exists(chap):
        log("NOTE: keeping the existing youtube-chapters.txt: it matches v%d only if the timeline did not change "
            "(audio-only remaster). For a different cut pass --chapters." % n)
    if not a.srt and not a.reuse_previous_srt:
        log("NOTE: no captions delivered with v%d (--srt or --reuse-previous-srt)" % n)

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(rec_dir, exist_ok=True)
    if replace_now or stale_parts:
        aside = os.path.join(sup, "replaced-v%d-%s" % (n, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        for p in replace_now + stale_parts:
            move_aside(p, aside)
        log("moved aside: %s" % aside)
    checks = Checks()

    # 2. final
    if a.audio or a.stereo:
        log("muxing %s ..." % ("the mastered WAV" if a.audio else "a stereo upmix"))
        mux_audio(master, final, os.path.abspath(a.audio) if a.audio else None)
    elif not in_place:
        if a.move:
            shutil.move(master, final)  # a rename on the same volume, copy+delete across volumes
        else:
            log("copying the master ...")
            copy_atomic(master, final)
    pf = check_video(final, checks, "final", want_size, a.expect_duration, 0.25, 0.1, a.expect_fps, True, not a.allow_mono)
    loud = None
    if not a.no_loudness and pf["audio"] is not None:
        log("measuring loudness (EBU R128) ...")
        loud = loudness(final)
        il, tp = loud["integrated_lufs"], loud["true_peak_dbfs"]
        checks.add("final integrated loudness %.0f LUFS +-%.1f" % (a.loudness_target, a.loudness_tol),
                   il is not None and abs(il - a.loudness_target) <= a.loudness_tol, "%s LUFS (LRA %s LU)" % (il, loud["lra_lu"]))
        checks.add("final true peak <= %.1f dBFS" % a.max_true_peak, tp is not None and tp <= a.max_true_peak, "%s dBFS" % tp)

    # 3. captions
    srt_fix = None
    if a.srt or a.reuse_previous_srt:
        copy_atomic(os.path.abspath(a.srt) if a.srt else prev_srt, srt)
        if a.names:
            import fix_srt  # noqa: E402  (path added during argument checks)
            changes, hits = fix_srt.run(srt, srt, a.names, report=os.path.join(rec_dir, "fix_srt_v%d.json" % n))
            # the in-place run leaves <srt>.orig next to the deliverable: keep it with the receipts instead
            if os.path.exists(srt + ".orig"):
                os.replace(srt + ".orig", unique_dest(os.path.join(rec_dir, os.path.basename(srt) + ".orig")))
            sys.stdout.flush()
            srt_fix = {"lines_changed": len(changes), "review": [{"line": ln, "pattern": p, "text": t} for ln, p, t in hits]}
            if hits:
                log("REVIEW: %d caption line(s) still match a review pattern (see above); fix by hand if needed" % len(hits))
        check_srt(srt, checks, pf["video_s"])

    # 4. chapters (written next to the old file until the checks pass)
    chap_new = None
    if a.chapters:
        chap_new = chap + ".v%d.part" % n
        copy_atomic(os.path.abspath(a.chapters), chap_new)
        check_chapters(chap_new, checks, pf["video_s"])

    # 5. phone copy
    if not a.no_phone:
        log("making the 720p phone copy ...")
        phone_copy(final, phone)
        check_video(phone, checks, "phone copy", (1280, 720), pf["video_s"], dur_tol=0.1)

    checks.show()
    moved = []
    if checks.ok:
        for p in plan_moves:
            if os.path.exists(p):
                moved.append(os.path.basename(move_aside(p, sup)))
        if chap_new:
            if os.path.exists(chap):
                owner = chapters_owner(chap, rec_dir)
                label = "v%d" % owner if owner else "before v%d" % n
                moved.append(os.path.basename(move_aside(chap, sup, "youtube-chapters (%s).txt" % label)))
            os.replace(chap_new, chap)
        if moved:
            log("moved to superseded/: %s" % ", ".join(moved))
    delivered = [("final", final)]
    if not a.no_phone:
        delivered.append(("phone", phone))
    if a.srt or a.reuse_previous_srt:
        delivered.append(("srt", srt))
    if chap_new:
        delivered.append(("chapters", chap if checks.ok else chap_new))
    files = {}
    for role, p in delivered:
        log("hashing %s ..." % os.path.basename(p))
        files[role] = {"path": p, "bytes": os.path.getsize(p), "sha256": sha256(p)}
    if checks.ok:
        with open(os.path.join(rec_dir, "delivered_v%d.sha256" % n), "w", encoding="utf-8") as fh:
            for role, p in delivered:
                fh.write("%s  %s\n" % (files[role]["sha256"], os.path.basename(p)))
    ffv = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[:1]
    receipt = {"ok": checks.ok, "version": n, "name": name, "out_dir": out_dir, "master": master,
               "audio_source": os.path.abspath(a.audio) if a.audio else ("stereo upmix" if a.stereo else "master"),
               "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
               "files": files, "final_probe": pf["summary"], "loudness": loud, "srt_fixes": srt_fix,
               "chapters": [] if not chap_new else [ln.rstrip("\n") for ln in open(chap if checks.ok else chap_new, encoding="utf-8")],
               "moved_to_superseded": moved, "checks": checks.items, "ffmpeg": ffv[0] if ffv else None}
    with open(os.path.join(rec_dir, "delivered_v%d.json" % n), "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=1)
    log("\nDELIVERABLES v%d (%s):" % (n, "all checks pass" if checks.ok else "CHECK FAILURES ABOVE - nothing was moved"))
    for role, p in delivered:
        log("  %-8s %s" % (role, p))
    log("  receipts %s" % rec_dir)
    if checks.ok and not a.no_phone:
        log("Send the phone copy for review; upload the full-size final.")
    sys.exit(0 if checks.ok else 2)


if __name__ == "__main__":
    main()
