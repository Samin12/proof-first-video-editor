#!/usr/bin/env python3
"""bwlib - shared helpers for the proof-first Borumi TIMELINE WRITER scripts (import it, don't run it).

    import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bwlib as B

Dependency: Samin12/edit-borumi-video (bridge client `bm`, bmlib.py, applylib.py). Found at, first that exists:
  --edit-skill DIR  |  $BORUMI_EDIT_SKILL  |  ~/.borumi-edit/skill        (DIR must contain scripts/applylib.py)
The Borumi bridge (edit-borumi-video scripts/bridge/bridgectl.sh start) must answer on $BORUMI_BRIDGE_PORT (47811).

What is in here:
  context(args)            -> Ctx: WORK dir, project id, merged config, output dir (frames, copies, logs)
  add_common_args(parser)  -> --work/--project/--project-id/--config/--out/--edit-skill/--lock-timeout/--dry
  geometry                 -> tile_px / panel_px / badge boxes / camera column, all derived from the config
  layout builders          -> cam_source, behind_props, sandwich_props, cam_only_props, panel_props, fullscreen_props
  30/60 fps grid           -> grid(), grid_hits(), frame() (render ONE 30 fps grid frame), save_frame(), montage()
  imgcheck(sub, payload)   -> run imgcheck.py (numpy + Pillow) in an interpreter that has them (uv fallback)
  reexec_with_numpy()      -> re-run the current script under `uv run --with numpy --with pillow` when numpy is missing
  probe_video(), exact_copy(), render_frame()  -> exact-length render copies (see mp4_set_duration.py)
  media_file(), src_frames()                   -> source frames of a bundle media at an exact media time

Config (JSON; every key optional). First that exists: --config FILE | $PFVE_WRITER_CONFIG | ./pfve-writer.json |
WORK/pfve-writer.json | ~/.config/proof-first-video-editor/writer.json. See examples/writer.example.json.

Python 3.9+, stdlib only (image analysis runs in imgcheck.py, which needs numpy + Pillow). ffmpeg >= 6 + ffprobe.
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _edit_skill_dir():
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--edit-skill" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--edit-skill="):
            return a.split("=", 1)[1]
    return os.environ.get("BORUMI_EDIT_SKILL") or "~/.borumi-edit/skill"


EDIT_SKILL = os.path.abspath(os.path.expanduser(_edit_skill_dir()))
EDIT_SCRIPTS = os.path.join(EDIT_SKILL, "scripts")
_HELP = any(a in ("-h", "--help") for a in sys.argv[1:])

if not os.path.exists(os.path.join(EDIT_SCRIPTS, "applylib.py")):
    if not _HELP:
        sys.exit("bwlib: edit-borumi-video not found at %s (needs scripts/applylib.py).\n"
                 "Install it (git clone https://github.com/Samin12/edit-borumi-video ~/.borumi-edit/skill) or pass "
                 "--edit-skill DIR / set BORUMI_EDIT_SKILL." % EDIT_SKILL)
    al = bmlib = None
else:
    sys.dont_write_bytecode = True          # never litter the dependency's folder with .pyc files
    if EDIT_SCRIPTS not in sys.path:
        sys.path.insert(1, EDIT_SCRIPTS)    # after this folder: our module names win
    import applylib as al  # noqa: E402
    import bmlib  # noqa: E402

if al is not None:
    from applylib import call, log, die, Lock, Tx, Checks  # noqa: E402,F401
else:   # --help without the dependency: enough to print usage
    def log(*a):
        print(*a, flush=True)

    def die(msg, code=1):
        print("ERROR: " + msg, file=sys.stderr, flush=True)
        sys.exit(code)

    call = Lock = Tx = Checks = None

# ------------------------------------------------------------------------------------------------ defaults
# The "standard look": footage / visuals in the LEFT rounded panel, the camera in the RIGHT tile, a step badge above the
# camera, a dark dot-grid canvas. Ratios are Borumi layout-source fields (fractions of the 1920x1080 frame).
SHADOW = {"blur_ratio": 0.018518518656492233, "color": {"a": 100, "b": 0, "g": 0, "r": 0},
          "x_offset_ratio": 0.0, "y_offset_ratio": 0.018518518656492233}
ZERO_SHADOW = {"blur_ratio": 0.0, "color": {"a": 0, "b": 0, "g": 0, "r": 0}, "x_offset_ratio": 0.0, "y_offset_ratio": 0.0}
STANDARD_CAMERA_RECT = {"x_ratio": 0.7966337703449105, "y_ratio": 0.2126346230506897, "width_ratio": 0.1843080371618271,
                        "height_ratio": 0.5747307538986206, "border_radius_ratio": 0.01777777634561062,
                        "corner_shape": "regular", "lock_aspect_ratio": False, "overflow": False, "shadow": SHADOW}
STANDARD_PANEL_RECT = {"x_ratio": 0.009999999776482582, "y_ratio": 0.01777777634561062, "width_ratio": 0.7675755023956299,
                       "height_ratio": 0.9644443988800049, "border_radius_ratio": 0.01777777634561062,
                       "corner_shape": "regular", "lock_aspect_ratio": True, "overflow": False, "shadow": SHADOW}
STANDARD_BADGE_PROPS = {"border_radius_ratio": 0.0, "entrance_transition": {"kind": "fade"},
                        "exit_transition": {"kind": "instant"}, "lock_aspect_ratio": False,
                        "position": {"height_ratio": 0.138889, "kind": "custom", "width_ratio": 0.184375,
                                     "x_ratio": 0.7966, "y_ratio": 0.045},
                        "shadow": {"blur_ratio": 0.018518518518518517, "color": {"a": 100, "b": 0, "g": 0, "r": 0},
                                   "x_offset_ratio": 0.0, "y_offset_ratio": 0.018518518518518517}}
STILL_OVERLAY_PROPS = {"entrance_transition": {"kind": "instant"}, "exit_transition": {"kind": "instant"},
                       "lock_aspect_ratio": False,
                       "position": {"kind": "custom", "x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 1.0, "height_ratio": 1.0},
                       "shadow": ZERO_SHADOW}
DEFAULTS = {
    "project_id": None,        # else WORK/project.json "id" (written by edit-borumi-video find_project.py --open)
    "out_dir": None,           # default WORK/writer
    "medias_dir": None,        # default WORK/project.json "bundle_medias" (<bundle>/medias)
    "frame_w": 1920, "frame_h": 1080,
    "render_fps": 30,          # inspect_timeline renders the 30 fps grid only; renders are authored at 30 fps
    "check_fps": [30, 60],     # exports run at 60 fps: a gap or sliver is harmless only if NO 30 or 60 fps frame lands in it
    "camera_layer": "camera_1",
    "camera_rect": STANDARD_CAMERA_RECT,    # dict, or a path to a JSON file holding one
    "panel_rect": STANDARD_PANEL_RECT,
    "badge_props": STANDARD_BADGE_PROPS,
    "card_ms": 1601,           # a 1.6 s chapter card occupies 1601 ms (the take starts 1 ms after T)
    "canvas_png": None,        # default <out>/canvas/dot-grid-dark-4k.png (canvas.py png makes it)
    "click_wav": None,         # default: applylib.click_wav() (a private chapter-transitions repo)
    "strict_footage_media": [],  # media ids of camera-like footage (stricter source-frame floor 26 dB, span 0.13 s)
    "tile_bottom_min_frac": 0.677,  # a dark shirt on a dark background reads short: accept a tile bottom from here (~650 px)
}
CONFIG_CANDIDATES = (
    lambda work: os.environ.get("PFVE_WRITER_CONFIG"),
    lambda work: os.path.join(os.getcwd(), "pfve-writer.json"),
    lambda work: os.path.join(work, "pfve-writer.json") if work else None,
    lambda work: os.path.expanduser("~/.config/proof-first-video-editor/writer.json"),
)


def _x(p):
    return os.path.abspath(os.path.expanduser(p)) if p else p


# ------------------------------------------------------------------------------------------------ args + context
def add_common_args(p, dry=True):
    g = p.add_argument_group("project / config")
    g.add_argument("--work", help="WORK dir (default $BORUMI_WORK; edit-borumi-video convention "
                                  "'<bundle folder>/<name> exports/_work'). The write lock WORK/borumi.lock lives here.")
    g.add_argument("--project", help="open Borumi project NAME, only used to derive the default WORK dir")
    g.add_argument("--project-id", help="Borumi project id (default: config project_id, else WORK/project.json id)")
    g.add_argument("--config", help="writer config JSON (default: $PFVE_WRITER_CONFIG, ./pfve-writer.json, "
                                    "WORK/pfve-writer.json, ~/.config/proof-first-video-editor/writer.json)")
    g.add_argument("--out", help="output dir for frames, exact copies, snapshots, commits.log (default WORK/writer)")
    g.add_argument("--edit-skill", help="edit-borumi-video skill dir (default $BORUMI_EDIT_SKILL or ~/.borumi-edit/skill)")
    g.add_argument("--lock-timeout", type=float, default=900, help="seconds to wait for WORK/borumi.lock (default 900)")
    if dry:
        p.add_argument("--dry", action="store_true", help="stage + verify, then ABORT the transaction (nothing changes)")
    return p


def load_json(path):
    with open(_x(path)) as fh:
        return json.load(fh)


def _resolve(v):
    """A config value that may be a path to a JSON file."""
    if isinstance(v, str) and v.lower().endswith(".json"):
        return load_json(v)
    return v


class Ctx:
    def __init__(self, work, pid, cfg, out, project):
        self.work, self.pid, self.cfg, self.out, self.project = work, pid, cfg, out, project
        self.commits_log = os.path.join(out, "commits.log")

    @property
    def key(self):
        return {"project_id": self.pid}

    def path(self, *parts):
        """a file path under the output dir (its folder is created)."""
        p = os.path.join(self.out, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def dir(self, *parts):
        p = os.path.join(self.out, *parts)
        os.makedirs(p, exist_ok=True)
        return p

    @property
    def medias_dir(self):
        return _x(self.cfg.get("medias_dir") or self.project.get("bundle_medias") or "") or None


def context(args, need_project=True):
    work = bmlib.resolve_work(args)
    cfg = dict(DEFAULTS)
    cpath = getattr(args, "config", None)
    if not cpath:
        for f in CONFIG_CANDIDATES:
            c = f(work)
            if c and os.path.exists(c):
                cpath = c
                break
    if cpath:
        user = load_json(cpath)
        # "_comment" keys and "<placeholder>" strings (as in examples/writer.example.json) are ignored
        cfg.update({k: v for k, v in user.items() if not k.startswith("_") and not (isinstance(v, str) and v.startswith("<"))})
        log("config: %s" % _x(cpath))
    for k in ("camera_rect", "panel_rect", "badge_props"):
        cfg[k] = _resolve(cfg[k])
    proj = {}
    pj_ = os.path.join(work, "project.json")
    if os.path.exists(pj_):
        proj = load_json(pj_)
    pid = getattr(args, "project_id", None) or cfg.get("project_id") or proj.get("id") or proj.get("project_id")
    if need_project and not pid:
        die("no project id: pass --project-id, set project_id in the config, or write WORK/project.json "
            "(edit-borumi-video find_project.py --open)")
    out = _x(getattr(args, "out", None) or cfg.get("out_dir") or os.path.join(work, "writer"))
    os.makedirs(out, exist_ok=True)
    return Ctx(work, pid, cfg, out, proj)


# ------------------------------------------------------------------------------------------------ geometry
def rect_px(r, cfg):
    W, H = cfg["frame_w"], cfg["frame_h"]
    return [r["x_ratio"] * W, r["y_ratio"] * H, (r["x_ratio"] + r["width_ratio"]) * W, (r["y_ratio"] + r["height_ratio"]) * H]


def tile_px(cfg):
    """camera tile [x0, y0, x1, y1] in frame px (float), e.g. [1529.5, 229.6, 1883.4, 850.3]."""
    return rect_px(cfg["camera_rect"], cfg)


def panel_px(cfg):
    """left panel [x0, y0, x1, y1] in frame px (float), e.g. [19.2, 19.2, 1493.0, 1060.8]."""
    return rect_px(cfg["panel_rect"], cfg)


def badge_rect_px(cfg):
    return rect_px(cfg["badge_props"]["position"], cfg)


def badge_outer_px(cfg):
    """badge box + shadow margin, e.g. (1520, 40, 1895, 225): 'did the badge area change'."""
    x0, y0, x1, y1 = badge_rect_px(cfg)
    return (int(round(x0 - 9.5)), int(round(y0 - 9)), int(round(x1 + 11.5)), int(round(y1 + 26)))


def badge_inner_px(cfg):
    """badge box inset ~5 px, e.g. (1535, 55, 1880, 195): 'is the badge drawn'."""
    x0, y0, x1, y1 = badge_rect_px(cfg)
    return (int(round(x0 + 6)), int(round(y0 + 6)), int(round(x1 - 3.5)), int(round(y1 - 4)))


def tile_int(cfg):
    """the tile in whole px, e.g. [1530, 230, 1884, 850]."""
    x0, y0, x1, y1 = tile_px(cfg)
    return [int(round(x0 + 0.5)), int(round(y0)), int(round(x1 + 0.5)), int(round(y1))]


def tile_zone(cfg):
    """the tile expanded by ~10 px (its shadow, face-tracking crop wobble), e.g. [1520, 220, 1895, 860]."""
    x0, y0, x1, y1 = tile_px(cfg)
    return [int(round(x0 - 9.5)), int(round(y0 - 10)), int(round(x1 + 12)), int(round(y1 + 10))]


def tile_inner(cfg):
    """inside the tile, clear of rounded corners and the lower body, e.g. [1550, 250, 1863, 790]."""
    x0, y0, x1, y1 = tile_px(cfg)
    return [int(round(x0 + 20.5)), int(round(y0 + 20)), int(round(x1 - 20)), int(round(y1 - 60))]


def cam_column_x(cfg):
    """x where the camera / badge column starts; frames are compared LEFT of it, e.g. 1500."""
    return int(round(tile_px(cfg)[0] - 29.5))


def panel_int(cfg):
    """the left panel in whole px, e.g. [19, 19, 1493, 1061]."""
    x0, y0, x1, y1 = panel_px(cfg)
    return [int(round(x0)), int(round(y0)), int(round(x1)), int(math.ceil(y1))]


def tile_ok(box, cfg, tol=8):
    """measured tile box vs the configured tile: left / top / right +-tol px; the bottom only bracketed (a dark shirt on a
    dark background reads short)."""
    if not box:
        return False
    T = tile_px(cfg)
    lo = T[1] + cfg["tile_bottom_min_frac"] * (T[3] - T[1])
    return all(abs(box[i] - T[i]) <= tol for i in (0, 1, 2)) and lo <= box[3] <= T[3] + tol


def geometry_payload(cfg, canvas=None):
    """every box imgcheck.py needs, derived from the config (the defaults reproduce the proven numbers of the first
    production run: tile [1530, 230, 1884, 850], camera column x 1500, panel [19, 19, 1493, 1061], badge (1520, 40, 1895,
    225)). `canvas` = the dot-grid PNG (needed by measure / coverage)."""
    T = tile_px(cfg)
    B_ = badge_rect_px(cfg)
    return {"W": cfg["frame_w"], "H": cfg["frame_h"], "tile": [round(v, 1) for v in T], "tile_int": tile_int(cfg),
            "tile_zone": tile_zone(cfg), "tile_inner": tile_inner(cfg), "col_x": cam_column_x(cfg),
            "panel": panel_int(cfg), "badge_outer": list(badge_outer_px(cfg)), "badge_inner": list(badge_inner_px(cfg)),
            "badge_bottom": int(round(B_[3])),
            # rows above this are ignored when the tile box is measured from a canvas diff (the step badge sits there)
            "tile_y_lo": int(round(B_[3] + 16)) if B_[3] + 16 < T[1] else 0,
            "canvas": canvas or canvas_png(cfg, create=False)}


# ------------------------------------------------------------------------------------------------ layout builders
def cam_source(cfg):
    return dict(cfg["camera_rect"], layer_id=cfg["camera_layer"])


def full_source(layer):
    return {"layer_id": layer, "x_ratio": 0.0, "y_ratio": 0.0, "width_ratio": 1.0, "height_ratio": 1.0,
            "border_radius_ratio": 0.0, "lock_aspect_ratio": False, "overflow": False}


def behind_props(layer, cfg):
    """BEHIND: the render full frame (bottom, draws its own panel + canvas) + the camera tile on top."""
    return {"kind": "custom", "transition": {"kind": "instant"}, "sources": [full_source(layer), cam_source(cfg)]}


def sandwich_props(old_props, render_layer, footage_layer, cfg):
    """FRONT: old piece props -> [footage panel (old geometry, named by the footage's ACTUAL layer: adding the render take
    makes Borumi re-derive the old panel name), render full frame, camera tile LAST (later sources draw in front)]."""
    p = json.loads(old_props) if isinstance(old_props, str) else json.loads(json.dumps(old_props))
    panels = [dict(x) for x in p.get("sources", []) if x.get("layer_id") != cfg["camera_layer"] and x.get("width_ratio", 1) < 0.99]
    srcs = []
    if panels and footage_layer:
        panels[0]["layer_id"] = footage_layer
        srcs = [panels[0]]
    return {"kind": "custom", "transition": p.get("transition") or {"kind": "instant"},
            "sources": srcs + [full_source(render_layer), cam_source(cfg)]}


def cam_only_props(cfg):
    """camera tile only; the left panel area stays empty canvas (visuals are placed there later)."""
    return {"kind": "custom", "transition": {"kind": "instant"}, "sources": [cam_source(cfg)]}


def panel_props(layer, cfg, lock=True):
    """a screen layer in the left rounded panel (+ the camera tile). lock=False cover-crops instead of letterboxing."""
    return {"kind": "custom", "transition": {"kind": "instant"},
            "sources": [dict(cfg["panel_rect"], layer_id=layer, lock_aspect_ratio=lock), cam_source(cfg)]}


def fullscreen_props(layer):
    return {"kind": "fullscreen", "layer_id": layer, "fit": "cover", "transition": {"kind": "instant"}}


def norm_numbers(x, nd=9):
    """round every number so stored-vs-requested props compare (Borumi stores float32-ish values)."""
    if isinstance(x, dict):
        return {k_: norm_numbers(v_, nd) for k_, v_ in x.items()}
    if isinstance(x, list):
        return [norm_numbers(v_, nd) for v_ in x]
    return round(float(x), nd) if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def drop_screen_names(props_json, top=False):
    """props JSON with every screen_N SOURCE name removed (Borumi re-derives those names; compare the rest).
    top=True also drops a fullscreen layout's own screen layer_id."""
    p = json.loads(props_json) if isinstance(props_json, str) else json.loads(json.dumps(props_json))
    if not p:
        return None
    for s in p.get("sources", []):
        if str(s.get("layer_id", "")).startswith("screen"):
            s.pop("layer_id", None)
    if top and str(p.get("layer_id", "")).startswith("screen"):
        p.pop("layer_id", None)
    return json.dumps(p, sort_keys=True)


# ------------------------------------------------------------------------------------------------ frame grid
def grid(t_ms, fps=30):
    """the nearest fps grid time at or after t_ms (ms, float); tolerates float noise like 617566.667."""
    n = math.ceil(t_ms * fps / 1000.0 - 1e-4)
    return n * 1000.0 / fps


def grid_hits(a, b, fps_list=(30, 60)):
    """30 / 60 fps frame times in [a - 0.5, b + 0.5) (Borumi may round frame times to integer ms)."""
    return sorted({round(n * 1000.0 / f, 2) for f in fps_list
                   for n in range(math.floor(a * f / 1000.0) - 1, math.ceil(b * f / 1000.0) + 2)
                   if a - 0.5 <= n * 1000.0 / f < b + 0.5})


def grid_frames_in(a, b, fps=30):
    return [n * 1000.0 / fps for n in range(math.ceil(a * fps / 1000.0) - 1, math.ceil(b * fps / 1000.0) + 1)
            if a <= n * 1000.0 / fps < b]


def frame(k, g, quality="high"):
    """Render the 30 fps grid frame g (ms, float; must be a grid time). Returns (time_ms, path).
    inspect_timeline returns the first 30 fps grid frame STRICTLY after start_ms, so ask for [ceil(g) - 1, ceil(g)],
    max_frames 1. Only 30 fps grid times can be rendered (60 fps-only times cannot)."""
    c = int(round(g))
    r = call("inspect_timeline", dict(k, range={"start_ms": c - 1, "end_ms": c}, presentation="frames", quality=quality,
                                      save=True, include_images=False, sampling={"type": "uniform", "max_frames": 1}))
    f = r["frames"][0]
    if f["time_ms"] != c:
        log("WARNING: asked grid %.3f, rendered %s" % (g, f["time_ms"]))
    return f["time_ms"], f["path"]


def save_frame(k, g, dst, quality="max"):
    """render grid frame g and copy it to dst (Borumi's own frame files get recycled). Returns dst."""
    _, p = frame(k, g, quality)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(p, dst)
    return dst


def montage(paths, out, cols, w=640, h=360):
    """tile frames into one JPG/PNG with ffmpeg xstack (no drawtext needed)."""
    if not paths:
        return None
    args = []
    for p in paths:
        args += ["-i", p]
    n = len(paths)
    f = "".join("[%d:v]scale=%d:%d[v%d];" % (i, w, h, i) for i in range(n))
    if n == 1:
        f += "[v0]null[o]"
    else:
        f += "".join("[v%d]" % i for i in range(n)) + "xstack=inputs=%d:layout=%s[o]" % (
            n, "|".join("%d_%d" % ((i % cols) * w, (i // cols) * h) for i in range(n)))
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error"] + args + ["-filter_complex", f, "-map", "[o]", out], check=True)
    return out


# ------------------------------------------------------------------------------------------------ numpy side
_NP = None


def has_numpy():
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def np_python():
    """command prefix for a Python that has numpy + Pillow: this interpreter if it can, else uv."""
    global _NP
    if _NP is None:
        if has_numpy():
            _NP = [sys.executable]
        elif shutil.which("uv"):
            _NP = ["uv", "run", "--no-project", "--with", "numpy", "--with", "pillow", "python"]
        else:
            die("numpy + Pillow are needed (pip install numpy pillow, or install uv: https://docs.astral.sh/uv/)")
    return list(_NP)


def reexec_with_numpy():
    """re-run this script under uv with numpy + Pillow when the current interpreter lacks them."""
    if has_numpy():
        return
    if os.environ.get("PFVE_REEXEC") == "1" or not shutil.which("uv"):
        die("numpy + Pillow are needed (pip install numpy pillow, or install uv)")
    env = dict(os.environ, PFVE_REEXEC="1")
    cmd = ["uv", "run", "--no-project", "--with", "numpy", "--with", "pillow", "python", os.path.abspath(sys.argv[0])] + sys.argv[1:]
    os.execvpe("uv", cmd, env)


def imgcheck(sub, payload):
    """run `imgcheck.py SUB @payload.json` and return its JSON output."""
    fd, tmp = tempfile.mkstemp(prefix="imgcheck_", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
    try:
        r = subprocess.run(np_python() + [os.path.join(HERE, "imgcheck.py"), sub, "@" + tmp], capture_output=True, text=True)
    finally:
        os.remove(tmp)
    if r.returncode != 0:
        raise RuntimeError("imgcheck %s failed: %s" % (sub, (r.stderr or r.stdout)[-1500:]))
    return json.loads(r.stdout)


# ------------------------------------------------------------------------------------------------ media helpers
def probe_video(p, count=True):
    """frames (decoded count), fps, size, codec, pix_fmt, B-frames, duration ms, stream count."""
    d = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0"] + (["-count_frames"] if count else []) +
        ["-show_entries", "stream=nb_read_frames,nb_frames,r_frame_rate,width,height,has_b_frames,codec_name,pix_fmt:"
                          "format=duration,nb_streams", "-of", "json", p], capture_output=True, text=True, check=True).stdout)
    st = d["streams"][0]
    nf = st.get("nb_read_frames") if count else st.get("nb_frames")
    return {"frames": int(nf) if nf not in (None, "N/A") else None, "fps": st["r_frame_rate"], "w": st["width"],
            "h": st["height"], "codec": st["codec_name"], "pix_fmt": st.get("pix_fmt"), "bframes": st.get("has_b_frames"),
            "dur_ms": float(d["format"]["duration"]) * 1000, "streams": int(d["format"]["nb_streams"])}


def has_alpha(pix_fmt):
    return bool(pix_fmt) and "a" in pix_fmt.replace("yuv", "").replace("p", "")


def exact_copy(render, out, length_ms, alpha=False, fps=30, size=(1920, 1080)):
    """Copy of `render` that lasts EXACTLY length_ms on a Borumi timeline: the first ceil(L*fps/1000) frames, no audio,
    no edit list, video timescale 30000, last sample shortened (mp4_set_duration.py) so duration == L. Borumi reads the
    take length from the file; a longer take stretched the scene in a probe.
      behind (alpha=False): H.264 crf 12, -bf 0 (no ctts), yuv420p, .mp4
      front  (alpha=True):  ProRes 4444 yuva444p10le (alpha kept), .mov
    Rebuilt only when missing or older than the render. Returns (out, probe)."""
    L = int(length_ms)
    src = probe_video(render)
    if src["fps"] != "%d/1" % fps or (src["w"], src["h"]) != tuple(size):
        raise RuntimeError("%s: need %dx%d %d fps, got %s" % (render, size[0], size[1], fps, src))
    nfr = math.ceil(L * fps / 1000.0)
    if src["frames"] < nfr:
        raise RuntimeError("%s has %d frames, the slot (%d ms) needs %d" % (render, src["frames"], L, nfr))
    if alpha and not has_alpha(src["pix_fmt"]):
        raise RuntimeError("%s: a FRONT render needs an alpha pix_fmt (ProRes 4444 / qtrle / png), got %s" % (render, src["pix_fmt"]))
    ext = os.path.splitext(out)[1]
    enc = out[:-len(ext)] + "_enc" + ext
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(render):
        if alpha:
            codec = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0"]
        else:
            codec = ["-c:v", "libx264", "-preset", "slow", "-crf", "12", "-bf", "0", "-pix_fmt", "yuv420p"]
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", render, "-map", "0:v:0", "-an", "-frames:v", str(nfr)]
                       + codec + ["-use_editlist", "0", "-video_track_timescale", "30000", enc], check=True)
        if L * fps % 1000 == 0:
            os.replace(enc, out)
        else:
            subprocess.run([sys.executable, os.path.join(HERE, "mp4_set_duration.py"), enc, out, str(L)], check=True)
            os.remove(enc)
    cp = probe_video(out)
    if not (cp["frames"] == nfr and abs(cp["dur_ms"] - L) < 0.5 and cp["streams"] == 1):
        raise RuntimeError("exact copy %s is %s (want %d frames, %d ms, 1 stream)" % (out, cp, nfr, L))
    return out, dict(cp, nfr=nfr, L=L, src_frames=src["frames"])


def render_frame(copy, n, out_dir, nfr=None, rgba=False, tag=None):
    """frame n (0-based, clamped) of an exact copy as PNG (RGBA for alpha renders). Cached by name."""
    if nfr is not None:
        n = min(max(n, 0), nfr - 1)
    n = max(n, 0)
    base = tag or os.path.splitext(os.path.basename(copy))[0]
    out = os.path.join(out_dir, "%s_f%d%s.png" % (base, n, "_rgba" if rgba else ""))
    if not os.path.exists(out):
        os.makedirs(out_dir, exist_ok=True)
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", copy, "-vf", "select=eq(n\\,%d)" % n, "-frames:v", "1",
                        "-fps_mode", "passthrough"] + (["-pix_fmt", "rgba"] if rgba else []) + [out], check=True)
    return out


def media_file(medias_dir, mid, exts=(".wav", ".mov", ".mp4")):
    """the bundle media whose file name starts with the media id (a media id = first hex chars of its UUID file name)."""
    if not medias_dir or not os.path.isdir(medias_dir):
        return None
    c = sorted(f for f in os.listdir(medias_dir) if f.startswith(mid) and f.lower().endswith(tuple(exts)) and not f.startswith("."))
    return os.path.join(medias_dir, c[0]) if c else None


def src_frames(path, t_s, out_dir, span=0.13, stream="0:v:0"):
    """decode source frames with pts in [t_s - span, t_s + span] -> [(pts_s, png)] (exact pts via -copyts)."""
    import re
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, f))
    p = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "info", "-copyts", "-ss", "%.3f" % max(0, t_s - span - 0.5), "-i", path,
                        "-map", stream, "-vf", "select='between(t,%.4f,%.4f)',showinfo" % (max(0, t_s - span), t_s + span),
                        "-fps_mode", "passthrough", "-frames:v", "60", out_dir + "/f_%03d.png"], capture_output=True, text=True)
    pts = [float(x) for x in re.findall(r"pts_time:\s*([\d.]+)", p.stderr)]
    files = sorted(os.listdir(out_dir))
    return [(t, os.path.join(out_dir, f)) for t, f in zip(pts, files)]


def canvas_png(cfg, create=True, out=None):
    """the canvas dot-grid PNG used by the frame checks (config canvas_png, else <out>/canvas/dot-grid-dark-4k.png)."""
    p = _x(cfg.get("canvas_png")) if cfg.get("canvas_png") else None
    if p:
        return p
    if out is None:
        return None
    p = os.path.join(out, "canvas", "dot-grid-dark-4k.png")
    if create and not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        subprocess.run(np_python() + [os.path.join(HERE, "canvas.py"), "png", p], check=True)
    return p


def click_wav(cfg):
    if cfg.get("click_wav"):
        return _x(cfg["click_wav"])
    return al.click_wav()


def fmt_ts(ms):
    return al.fmt_ts(ms) if al else "%d ms" % ms


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1, default=str)
    os.replace(tmp, path)


if __name__ == "__main__":  # a library, not a CLI: running it prints this description
    print(__doc__)
