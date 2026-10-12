#!/usr/bin/env python3
"""make_card.py - render a silent 1.6 s white chapter card (light grid, bold black title revealed in 3 groups) without
the private card template, in the exact layout chapter_card.py expects. Optionally make a quiet click WAV too.

usage:
  make_card.py --title "Why This Matters" [--name 01-why-this-matters] [--work WORK | --out-dir DIR]
               [--groups "Why|This Matters"] [--font PATH] [--fps 30] [--seconds 1.6] [--no-rows]
  make_card.py --make-click OUT.wav [--click-src built-in-sound-click.wav] [--click-peak-db -18]
  (both can be combined in one call)

Card output (the same files edit-borumi-video's render_cards.py writes):
  <out>/<name>/card-silent.mp4   1920x1080 H.264 yuv420p, --fps (30), exactly round(seconds * fps) frames, NO audio
  <out>/<name>/hold.png          the frame at 1.2 s;   <out>/<name>/last_frame.png   the last frame
  <out> = --out-dir, else WORK/cards (WORK = --work or $BORUMI_WORK). With WORK, the row {name, title, title_groups} is
  added to (or updated in) WORK/cards/rows.json unless --no-rows, so `chapter_card.py --name <name>` finds the title.
  name defaults to "NN-<slug>" with NN = 1 + the number of rows already in rows.json.
The look: white background, a light grey 60 px grid, the title in black bold (Inter if installed, else a system bold
font; --font to choose), centred, auto-sized to fit 1600 px; the '|'-separated groups (default: up to 3 groups of
words) rise in at 0.2 / 0.43 / 0.63 s; held to the end. Titles: 2-5 words, Title Case, never "Level N"; the first card
after the intro is "Why This Matters" (references/creator-style.md).
Checks (exit 2 when one fails): duration within 1.55-1.65 s, 1920x1080, no audio stream, hold.png mean luma > 200 (what
chapter_card.py and coverage.py test on the timeline).

Click: --make-click writes a 48 kHz stereo 24-bit WAV from Borumi's built-in click (default
$BORUMI_BUILTIN_SOUNDS or /Applications/Borumi.app/Contents/Resources/built-in-sound-click.wav), gained so its sample peak
lands at --click-peak-db (default -18 dBFS, the level of the quiet chapter click used on the REA video; the built-in
click itself peaks at 0 dBFS). Pass it as `chapter_card.py --click OUT.wav` or set click_wav in the writer config.
Never a whoosh.

Needs Pillow (re-runs itself under `uv run --with pillow` when missing) and ffmpeg/ffprobe >= 6 (no drawtext needed).
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys

W, H = 1920, 1080
BOLD_CANDIDATES = ["~/Library/Fonts/Inter-Black.ttf", "/Library/Fonts/Inter-Black.ttf", "~/Library/Fonts/Inter-ExtraBold.ttf",
                   "~/Library/Fonts/Inter-Bold.ttf", "/Library/Fonts/Inter-Bold.ttf",
                   "/System/Library/Fonts/Supplemental/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                   "/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
REVEAL_S = [0.2, 0.433, 0.633]


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageStat
        return Image, ImageDraw, ImageFont, ImageStat
    except ImportError:
        if os.environ.get("PFVE_REEXEC") == "1" or not shutil.which("uv"):
            sys.exit("make_card.py needs Pillow (pip install pillow, or install uv)")
        env = dict(os.environ, PFVE_REEXEC="1")
        os.execvpe("uv", ["uv", "run", "--no-project", "--with", "pillow", "python", os.path.abspath(__file__)] + sys.argv[1:], env)


def slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:40] or "card"


def font_at(ImageFont, path, size):
    for p in ([path] if path else []) + BOLD_CANDIDATES:
        p = os.path.expanduser(p)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def default_groups(title):
    words = title.split()
    if len(words) <= 3:
        return words
    k = math.ceil(len(words) / 3)
    return [" ".join(words[i:i + k]) for i in range(0, len(words), k)]


def ease_out(t):
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def render_frames(title, groups, font_path, fps, n_frames, mods):
    Image, ImageDraw, ImageFont, _ = mods
    base = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(base)
    for x in range(0, W + 1, 60):
        d.line([(x, 0), (x, H)], fill=(236, 238, 241), width=2)
    for y in range(0, H + 1, 60):
        d.line([(0, y), (W, y)], fill=(236, 238, 241), width=2)
    size = 150
    while True:
        font = font_at(ImageFont, font_path, size)
        tw = d.textlength(title, font=font)
        if tw <= 1600 or size <= 60:
            break
        size -= 6
    space = d.textlength(" ", font=font)
    widths = [d.textlength(g, font=font) for g in groups]
    total = sum(widths) + space * (len(groups) - 1)
    asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (size, 0)
    x0 = (W - total) / 2
    y0 = (H - (asc + desc)) / 2
    xs = []
    x = x0
    for w in widths:
        xs.append(x)
        x += w + space
    reveal = [REVEAL_S[min(i, len(REVEAL_S) - 1)] for i in range(len(groups))]
    frames = []
    for f in range(n_frames):
        t = f / float(fps)
        img = base.copy()
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for g, gx, r in zip(groups, xs, reveal):
            p = ease_out((t - r) / 0.2)
            if p <= 0:
                continue
            ld.text((gx, y0 + (1 - p) * 0.25 * size), g, font=font, fill=(17, 17, 17, int(round(255 * p))))
        img.paste(layer, (0, 0), layer)
        frames.append(img)
    return frames


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "json", path], capture_output=True, text=True).stdout
    return json.loads(out or "{}")


def make_card(a, mods):
    Image, _, _, ImageStat = mods
    work = a.work or os.environ.get("BORUMI_WORK")
    out = a.out_dir or (os.path.join(work, "cards") if work else None)
    if not out:
        sys.exit("make_card.py: pass --work WORK (or set $BORUMI_WORK) or --out-dir DIR")
    out = os.path.abspath(os.path.expanduser(out))
    rows_path = os.path.join(out, "rows.json") if (work and not a.out_dir and not a.no_rows) else None
    rows = {"rows": []}
    if rows_path and os.path.exists(rows_path):
        with open(rows_path) as fh:
            rows = json.load(fh)
    name = a.name or "%02d-%s" % (len(rows.get("rows", [])) + 1, slug(a.title))
    groups = [g.strip() for g in a.groups.split("|")] if a.groups else default_groups(a.title)
    groups = [g for g in groups if g]
    n = int(round(a.seconds * a.fps))
    folder = os.path.join(out, name)
    os.makedirs(folder, exist_ok=True)
    frames = render_frames(a.title, groups, a.font, a.fps, n, mods)
    mp4 = os.path.join(folder, "card-silent.mp4")
    tmp = mp4 + ".part.mp4"
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                          "-r", str(a.fps), "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
                          "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp], stdin=subprocess.PIPE)
    for im in frames:
        p.stdin.write(im.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        sys.exit("make_card.py: ffmpeg failed to encode %s" % mp4)
    os.replace(tmp, mp4)
    hold = frames[min(len(frames) - 1, int(round(1.2 * a.fps)))]
    hold.save(os.path.join(folder, "hold.png"))
    frames[-1].save(os.path.join(folder, "last_frame.png"))

    info = probe(mp4)
    streams = info.get("streams", [])
    v = [s for s in streams if s.get("codec_type") == "video"]
    dur = float((info.get("format") or {}).get("duration") or 0)
    luma = ImageStat.Stat(Image.open(os.path.join(folder, "hold.png")).convert("L")).mean[0]
    checks = [("duration 1.55-1.65 s", 1.55 <= dur <= 1.65, "%.3f s" % dur),
              ("1920x1080", bool(v) and v[0].get("width") == W and v[0].get("height") == H, ""),
              ("silent (no audio stream)", not any(s.get("codec_type") == "audio" for s in streams), ""),
              ("white card (hold.png mean luma > 200)", luma > 200, "%.1f" % luma)]
    for c, ok, det in checks:
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", c, (" - " + det) if det else ""))
    if rows_path:
        rs = [r for r in rows.get("rows", []) if r.get("name") != name]
        rs.append({"name": name, "title": a.title, "title_groups": "|".join(groups)})
        rs.sort(key=lambda r: r["name"])
        rows["rows"] = rs
        with open(rows_path + ".tmp", "w") as fh:
            json.dump(rows, fh, indent=1)
        os.replace(rows_path + ".tmp", rows_path)
        print("rows.json: %s" % rows_path)
    print(mp4)
    return all(ok for _, ok, _ in checks)


def make_click(a):
    src = a.click_src or os.path.join(os.environ.get("BORUMI_BUILTIN_SOUNDS") or "/Applications/Borumi.app/Contents/Resources",
                                      "built-in-sound-click.wav")
    if not os.path.exists(src):
        sys.exit("make_card.py: click source not found: %s (pass --click-src)" % src)
    r = subprocess.run(["ffmpeg", "-nostats", "-v", "info", "-i", src, "-af", "aresample=48000,volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    peak = float(m.group(1)) if m else 0.0
    gain = a.click_peak_db - peak
    out = os.path.abspath(os.path.expanduser(a.make_click))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    ch = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of", "csv=p=0",
                         src], capture_output=True, text=True).stdout.strip()
    # a mono source is duplicated into L and R at unity (pan), so each channel keeps the target peak; ffmpeg's -ac 2
    # upmix would put -3 dB on each channel (references/audio.md lesson 4)
    af = "volume=%.2fdB" % gain + (",pan=stereo|c0=c0|c1=c0" if ch == "1" else "")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af", af, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", out],
                   check=True)
    print("click: %s (source peak %.1f dBFS, gain %+.1f dB -> peak %.1f dBFS)" % (out, peak, gain, a.click_peak_db))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--title", help="card title (2-5 words, Title Case)")
    ap.add_argument("--name", help="folder name under WORK/cards (default NN-<slug>)")
    ap.add_argument("--work", help="WORK folder (default $BORUMI_WORK); the card goes to WORK/cards/<name>/")
    ap.add_argument("--out-dir", help="write <out-dir>/<name>/ instead (rows.json is not touched)")
    ap.add_argument("--groups", help="'|'-separated reveal groups (default: up to 3 groups of words)")
    ap.add_argument("--font", help="bold font file (default: Inter Black/Bold if installed, else a system bold font)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=1.6)
    ap.add_argument("--no-rows", action="store_true", help="do not add the row to WORK/cards/rows.json")
    ap.add_argument("--make-click", metavar="OUT.wav", help="also write a quiet click WAV here")
    ap.add_argument("--click-src", help="click source (default: Borumi's built-in-sound-click.wav)")
    ap.add_argument("--click-peak-db", type=float, default=-18.0)
    a = ap.parse_args()
    if not a.title and not a.make_click:
        ap.error("pass --title (a card) and/or --make-click OUT.wav")
    ok = True
    if a.title:
        ok = make_card(a, _pil()) and ok
    if a.make_click:
        ok = make_click(a) and ok
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
