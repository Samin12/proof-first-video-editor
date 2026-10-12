#!/usr/bin/env python3
"""make_badges.py - render the step-badge PNGs (a dark rounded card: accent dot + letter-spaced eyebrow "SECTION · 2 / 6",
a bold title, a progress bar of one segment per step) for step_badges.py.

usage:
  make_badges.py --titles "Intro" "Why This Matters" "Setting It Up" ... --out-dir DIR [--eyebrows ...]
  make_badges.py --spec badges.spec.json --out-dir DIR        [{"title", "eyebrow"?, "step"?, "total"?, "file"?}, ...]
options: --width 708 --height 300 (2x the badge slot of the standard look: 0.184375 x 0.138889 of 1920x1080)
         --font-bold PATH --font-black PATH (default: Inter if installed, else system bold fonts, else Pillow's default)
         --accent 226,119,78  --intro-name Intro

Step numbering: the first title is the intro (step 0: eyebrow "INTRO", no progress segment lit); title i (1..N) gets
"<EYEBROW> · i / N" and segments 1..i lit. The eyebrow defaults to the title in capitals. Files: badge-<i>-<slug>.png.
Keep titles short (2-4 words): the badge sits above the camera tile and must read at 354x150 px.
Needs Pillow (re-runs itself under `uv run --with pillow` when missing).
"""
import argparse
import json
import os
import re
import shutil
import sys


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        return Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        if os.environ.get("PFVE_REEXEC") == "1" or not shutil.which("uv"):
            sys.exit("make_badges.py needs Pillow (pip install pillow, or install uv)")
        env = dict(os.environ, PFVE_REEXEC="1")
        os.execvpe("uv", ["uv", "run", "--no-project", "--with", "pillow", "python", os.path.abspath(__file__)] + sys.argv[1:], env)


BOLD_CANDIDATES = ["~/Library/Fonts/Inter-Bold.ttf", "/Library/Fonts/Inter-Bold.ttf", "~/Library/Fonts/Inter-SemiBold.ttf",
                   "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
BLACK_CANDIDATES = ["~/Library/Fonts/Inter-Black.ttf", "/Library/Fonts/Inter-Black.ttf", "~/Library/Fonts/Inter-ExtraBold.ttf",
                    "/System/Library/Fonts/Supplemental/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def find_font(ImageFont, path, cands, size):
    for p in ([path] if path else []) + cands:
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


def slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:32] or "step"


def spaced_text(draw, xy, text, font, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor="lm")
        x += draw.textlength(ch, font=font) + spacing
    return x


def render(spec, a, mods):
    Image, ImageDraw, ImageFilter, ImageFont = mods
    W, H = a.width, a.height
    s = H / 300.0
    acc = tuple(int(v) for v in a.accent.split(","))
    fill, border, idle, ink = (38, 36, 33, 240), (75, 73, 71, 242), (79, 77, 75, 242), (245, 240, 232, 255)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    m = int(12 * s)
    r = int(44 * s)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([m, m + int(6 * s), W - m, H - m + int(6 * s)], r, fill=(0, 0, 0, 90))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(6 * s)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([m, m, W - m - 1, H - m - 1], r, fill=fill, outline=border, width=max(2, int(3 * s)))
    x0 = int(56 * s)
    ey = int(68 * s)
    d.ellipse([x0, ey - int(10 * s), x0 + int(20 * s), ey + int(10 * s)], fill=acc + (255,))
    f_eye = find_font(ImageFont, a.font_bold, BOLD_CANDIDATES, int(30 * s))
    eyebrow = spec["eyebrow"].upper()
    if spec["step"] > 0:
        eyebrow += " · %d / %d" % (spec["step"], spec["total"])
    spaced_text(d, (x0 + int(40 * s), ey), eyebrow, f_eye, acc + (255,), int(4 * s))
    size = int(58 * s)
    maxw = W - 2 * x0
    while size > int(30 * s):
        f_t = find_font(ImageFont, a.font_black, BLACK_CANDIDATES, size)
        if d.textlength(spec["title"], font=f_t) <= maxw:
            break
        size -= 2
    d.text((x0, int(165 * s)), spec["title"], font=f_t, fill=ink, anchor="lm")
    n = max(1, spec["total"])
    gap = int(12 * s)
    bw = (W - 2 * x0 - gap * (n - 1)) / n
    by0, by1 = int(238 * s), int(252 * s)
    for i in range(n):
        bx = x0 + i * (bw + gap)
        d.rounded_rectangle([bx, by0, bx + bw, by1], int(7 * s), fill=(acc + (255,)) if i < spec["step"] else idle)
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--titles", nargs="+", help="intro first, then one title per chapter")
    g.add_argument("--spec", help="JSON list of {title, eyebrow?, step?, total?, file?}")
    ap.add_argument("--eyebrows", nargs="*", help="eyebrow per title (default: the title in capitals; the intro: --intro-name)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--width", type=int, default=708)
    ap.add_argument("--height", type=int, default=300)
    ap.add_argument("--font-bold")
    ap.add_argument("--font-black")
    ap.add_argument("--accent", default="226,119,78")
    ap.add_argument("--intro-name", default="Intro")
    a = ap.parse_args()
    mods = _pil()
    if a.spec:
        with open(a.spec) as fh:
            specs = json.load(fh)
        total = max([x.get("step", 0) for x in specs] + [len(specs) - 1])
        for i, x in enumerate(specs):
            x.setdefault("step", i)
            x.setdefault("total", total)
            x.setdefault("eyebrow", a.intro_name if x["step"] == 0 else x["title"])
    else:
        N = len(a.titles) - 1
        eb = a.eyebrows or []
        specs = [{"title": t, "step": i, "total": N, "eyebrow": (eb[i] if i < len(eb) else (a.intro_name if i == 0 else t))}
                 for i, t in enumerate(a.titles)]
    os.makedirs(a.out_dir, exist_ok=True)
    for x in specs:
        f = os.path.join(a.out_dir, x.get("file") or "badge-%d-%s.png" % (x["step"], slug(x["title"])))
        render(x, a, mods).save(f)
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
