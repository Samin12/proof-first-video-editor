#!/usr/bin/env python3
"""canvas.py - the dark dot-grid canvas: make the still PNG, make DRIFTING clips phase-locked to project time, and set
the Borumi canvas background (one verified tx; nothing on the timeline may change).

usage:
  canvas.py png OUT.png [--width 3840 --height 2160] [--bg 18 --dot 82]
  canvas.py clip OUT.mp4 --start-ms S --end-ms E [--fps 30] [--width 1920 --height 1080]
  canvas.py set [PNG] [--dry] [common args]          (default PNG: config canvas_png, else <out>/canvas/dot-grid-dark-4k.png)

The look: near-black #121212 (18) with grey dots (82) on a 40 px pitch at 1080p (80 px at 4K), dot centres on
multiples of the pitch (0, 0 is a dot), radius ~2-2.5 px at 1080p with a 1 px anti-aliased edge (4K: 4-5 px).
The drift: 2.5 px/s diagonally (right + down), one full pitch every 16 s, phase = PROJECT time:
    offset_px(t_ms) = (t_ms / 1000 * 2.5) % 40          (1080p px; the same formula as the velour renders' canvas)
so a clip made for [S, E] lines up with renders that paint the same drifting canvas, and cuts never make the grid jump.

How the moving canvas gets onto the timeline (Borumi's canvas background accepts only solid / gradient / image):
  1. renders that paint their own drifting canvas (velour PANEL_MODE) cover the talking-head stretches (place_render.py
     --mode behind), phase-locked by their project start time;
  2. camera-only stretches without a render: `canvas.py clip` for the range, then place_render.py --mode behind with it
     (it is a full-frame render under the camera tile);
  3. the static PNG as the canvas background (`canvas.py set`) shows wherever nothing else draws: around the footage
     panel, behind front renders, in 1 ms lead-ins. A moving background under a screen recording needs a clip on a
     screen layer referenced as a full-frame BOTTOM source in the layout ([clip full frame, footage panel, camera tile]);
     that variant is not automated here - verify it in an aborted tx (see references/borumi-writer.md).

png / clip need numpy + Pillow (re-run under `uv run --with numpy --with pillow` automatically) and ffmpeg (clip).
`set` checks: background == the imported image, canvas format unchanged, strict project-wide diff (timeline untouched),
sample frames rendered. Exit: 0 ok, 2 checks failed, 3 error, 4 collision (all aborted).
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PITCH_1080 = 40
DRIFT_PX_PER_S = 2.5


def _numpy():
    try:
        import numpy as np  # noqa: F401
        from PIL import Image  # noqa: F401
        return np, Image
    except ImportError:
        if os.environ.get("PFVE_REEXEC") == "1" or not shutil.which("uv"):
            sys.exit("canvas.py png/clip need numpy + Pillow (pip install numpy pillow, or install uv)")
        env = dict(os.environ, PFVE_REEXEC="1")
        os.execvpe("uv", ["uv", "run", "--no-project", "--with", "numpy", "--with", "pillow", "python", os.path.abspath(__file__)] + sys.argv[1:], env)


def offset_px(t_ms):
    """drift offset in 1080p px at project time t_ms."""
    return (t_ms / 1000.0 * DRIFT_PX_PER_S) % PITCH_1080


def frame(np, W, H, off1080=0.0, bg=18, dot=82):
    """one canvas frame (uint8 gray, H x W) with the grid shifted by off1080 (1080p px) right + down."""
    s = H / 1080.0
    P = int(round(PITCH_1080 * s))      # sizes that are not a multiple of P work too: one cell more is tiled, then cropped
    r0, r1 = 2.0 * s, 2.5 * s
    off = off1080 * s
    xs = np.arange(P, dtype=np.float64)
    dx = ((xs - off) + P / 2.0) % P - P / 2.0
    d = np.sqrt(dx[None, :] ** 2 + dx[:, None] ** 2)      # the same offset on x and y: a diagonal drift
    cov = np.clip((r1 - d) / max(r1 - r0, 1e-6), 0.0, 1.0)
    cell = bg + (dot - bg) * cov
    img = np.tile(cell, (H // P + 1, W // P + 1))[:H, :W]
    return np.floor(img + 1e-6).astype(np.uint8)


def cmd_png(a):
    np, Image = _numpy()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    img = frame(np, a.width, a.height, 0.0, a.bg, a.dot)
    Image.fromarray(np.stack([img] * 3, axis=2)).save(a.out)
    print("wrote %s (%dx%d, pitch %d px, bg %d, dots %d)" % (a.out, a.width, a.height, round(PITCH_1080 * a.height / 1080.0), a.bg, a.dot))
    return 0


def cmd_clip(a):
    np, _ = _numpy()
    if a.end_ms <= a.start_ms:
        sys.exit("end must be after start")
    L = a.end_ms - a.start_ms
    n = int(math.ceil(L * a.fps / 1000.0))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "%dx%d" % (a.width, a.height),
                          "-r", str(a.fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "14", "-bf", "0", "-pix_fmt", "yuv420p",
                          "-movflags", "+faststart", a.out], stdin=subprocess.PIPE)
    cache = {}
    for i in range(n):
        t = a.start_ms + i * 1000.0 / a.fps
        key = round(offset_px(t) * a.height / 1080.0, 3)
        buf = cache.get(key)
        if buf is None:
            buf = frame(np, a.width, a.height, offset_px(t), a.bg, a.dot).tobytes()
            if len(cache) < 2000:
                cache[key] = buf
        p.stdin.write(buf)
    p.stdin.close()
    if p.wait() != 0:
        sys.exit("ffmpeg failed")
    print("wrote %s: %d frames at %d fps for [%d, %d] (%d ms); drift phase %.3f px at the start" % (
        a.out, n, a.fps, a.start_ms, a.end_ms, L, offset_px(a.start_ms)))
    return 0


def cmd_set(a):
    sys.path.insert(0, HERE)
    import bwlib as B
    import snapshot as S
    import txrun
    ctx = B.context(a)
    png = os.path.abspath(os.path.expanduser(a.png)) if a.png else B.canvas_png(ctx.cfg, create=True, out=ctx.out)
    if not os.path.exists(png):
        B.die("canvas PNG not found: %s" % png)
    tag = "_dry" if a.dry else ""
    FD = ctx.dir("frames", "canvas%s" % tag)

    def body(tx, k, pre, checks):
        c0 = B.call("get_project_overview", {"tx_id": tx.id, "slices": ["canvas"]})["canvas"]
        B.log("canvas before: %s" % json.dumps(c0))
        mid = B.al.import_media(tx, png)
        B.call("update_canvas", {"tx_id": tx.id, "background": {"type": "image", "media_id": mid}})
        c1 = B.call("get_project_overview", {"tx_id": tx.id, "slices": ["canvas"]})["canvas"]
        B.log("canvas after: %s" % json.dumps(c1))
        checks.add("background = image %s" % mid, c1["background"] == {"type": "image", "media_id": mid}, json.dumps(c1["background"]))
        checks.add("canvas format unchanged (%sx%s)" % (c0["format"].get("width_px"), c0["format"].get("height_px")),
                   c1["format"] == c0["format"], json.dumps(c1["format"]))
        post = S.snapshot(k)
        bad, notes = S.strict_diff(pre, post, {}, {}, {}, lambda cat, i, v: False, S.ident)
        for b_ in bad:
            B.log("  DIFF " + b_[:300])
        checks.add("project-wide diff: timeline untouched", not bad and post["duration"] == pre["duration"] and post["scenes"] == pre["scenes"],
                   "%d problems" % len(bad))
        fr = [B.save_frame(k, g, os.path.join(FD, "frame_%d.png" % round(g)), "high")
              for g in (B.grid(pre["duration"] * 0.25), B.grid(pre["duration"] * 0.75)) if g < pre["duration"]]
        B.log("frames: %s" % fr)

        def post_check(pre_proj, post_c):
            b2, n2 = S.strict_diff(pre_proj, post_c, {}, {}, {}, lambda cat, i, v: False, S.ident)
            c2 = B.call("get_project_overview", {"project_id": ctx.pid, "slices": ["canvas"]})["canvas"]
            if c2["background"] != {"type": "image", "media_id": mid}:
                b2.append("canvas after commit: %s" % c2["background"])
            return b2, n2
        return post_check, {"media": mid, "png": png, "canvas": c1, "frames": fr}

    info = txrun.run(ctx, "canvas", body, a.dry, lambda i: "Set the canvas background to the dark dot-grid image.", a.lock_timeout)
    print(json.dumps(info, default=str))
    return 0 if info.get("checks_ok") else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("png", help="the still dot-grid PNG")
    p.add_argument("out")
    p.add_argument("--width", type=int, default=3840)
    p.add_argument("--height", type=int, default=2160)
    p.add_argument("--bg", type=int, default=18)
    p.add_argument("--dot", type=int, default=82)
    c = sp.add_parser("clip", help="a drifting clip for a project range (phase = project time)")
    c.add_argument("out")
    c.add_argument("--start-ms", type=int, required=True)
    c.add_argument("--end-ms", type=int, required=True)
    c.add_argument("--fps", type=int, default=30)
    c.add_argument("--width", type=int, default=1920)
    c.add_argument("--height", type=int, default=1080)
    c.add_argument("--bg", type=int, default=18)
    c.add_argument("--dot", type=int, default=82)
    s = sp.add_parser("set", help="set the Borumi canvas background image (verified tx)")
    s.add_argument("png", nargs="?")
    if "set" in sys.argv[1:2]:
        sys.path.insert(0, HERE)
        import bwlib as B
        B.add_common_args(s)
    a = ap.parse_args()
    return {"png": cmd_png, "clip": cmd_clip, "set": cmd_set}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
