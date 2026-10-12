#!/usr/bin/env python3
"""Shared helpers for the tighter-cut (v-next) scripts: project config, word lists, frame grid, ffmpeg/ffprobe.

Not a CLI. Imported by make_sections.py, snap_cuts.py, apply_pins.py, build_cut.py, build_captions.py, qa_cut.py,
collect_cuts.py and words_from_whisper.py (all in this folder).

THE CONFIG FILE (tighten.json; see tighten.example.json next to this file and references/tighten.md)
Relative paths are resolved against the folder that holds the config file; "~" is expanded.

  {
    "master": "<finished master mp4 (CFR, integer fps)>",
    "audio_wav": "<mastered mono WAV, sample-aligned with the master (same start, same length)>",
    "master_label": "v2",                 # how prompts/section files name the master ("Section range in the v2 video")
    "fps": 60, "sample_rate": 48000,
    "words_dir": "words",                 # <scene>.json = [[start_ms, end_ms, word], ...] in MASTER ms
                                          # <scene>_sentences.json = [[start_ms, end_ms, text], ...] (optional)
    "sections_dir": "sections",           # make_sections.py output
    "srt": "<captions of the master (optional; build_captions.py input)>",
    "intro_title": "Intro",
    "sections": [{"key": "S1", "scene": "<scene id>", "title": "<what happens here>", "range": [start_ms, end_ms]}],
    "cards": [{"start_ms": 0, "end_ms": 0, "title": "<card text>", "chapter": "<YouTube chapter title or null>",
               "chapter_at_ms": null}],
    "sub_chapters": [{"at_ms": 0, "title": "<sub-chapter title>"}]
  }

Requirements: python3 >= 3.9; numpy only for decode_audio() (snap_cuts, build_cut, qa_cut); ffmpeg/ffprobe >= 6 on PATH.
"""
import json
import math
import os
import re
import subprocess
import sys

PATH_KEYS = ("master", "audio_wav", "words_dir", "sections_dir", "srt", "whisper_model")


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve(base, p):
    if p is None:
        return None
    p = os.path.expanduser(str(p))
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))


def load_config(path):
    """Load and validate tighten.json. Returns a dict with resolved paths and defaults filled in."""
    if not path:
        die("--config is required (see scripts/tighten/tighten.example.json)")
    if not os.path.exists(path):
        die(f"config not found: {path}")
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    base = os.path.dirname(os.path.abspath(path))
    for k in PATH_KEYS:
        if cfg.get(k):
            cfg[k] = resolve(base, cfg[k])
    cfg.setdefault("fps", 60)
    cfg.setdefault("sample_rate", 48000)
    cfg.setdefault("master_label", "master")
    cfg.setdefault("intro_title", "Intro")
    cfg.setdefault("sections", [])
    cfg.setdefault("cards", [])
    cfg.setdefault("sub_chapters", [])
    if int(cfg["fps"]) != cfg["fps"]:
        die(f"fps must be an integer (got {cfg['fps']}); the cut grid needs a CFR integer-fps master")
    cfg["fps"] = int(cfg["fps"])
    for s in cfg["sections"]:
        for k in ("key", "scene", "title", "range"):
            if k not in s:
                die(f"section {s} is missing '{k}'")
        if len(s["range"]) != 2 or s["range"][0] >= s["range"][1]:
            die(f"section {s['key']}: bad range {s['range']}")
    for c in cfg["cards"]:
        if not (c.get("start_ms") is not None and c.get("end_ms") is not None and c["start_ms"] < c["end_ms"]):
            die(f"card {c} needs start_ms < end_ms")
    cfg["_path"] = os.path.abspath(path)
    cfg["_dir"] = base
    return cfg


def scene_order(cfg):
    """Unique scene ids in section order."""
    seen, out = set(), []
    for s in cfg["sections"]:
        if s["scene"] not in seen:
            seen.add(s["scene"])
            out.append(s["scene"])
    return out


def scene_ranges(cfg):
    """{scene: (start_ms, end_ms)}; a scene split over several sections gets the union of their ranges."""
    out = {}
    for s in cfg["sections"]:
        a, b = s["range"]
        if s["scene"] in out:
            a0, b0 = out[s["scene"]]
            out[s["scene"]] = (min(a0, a), max(b0, b))
        else:
            out[s["scene"]] = (a, b)
    return out


def section_scene(cfg, key):
    for s in cfg["sections"]:
        if s["key"] == key:
            return s["scene"]
    return None


def card_list(cfg):
    """[(start_ms, end_ms, title)] sorted by start: the ranges no cut may touch."""
    return sorted((c["start_ms"], c["end_ms"], c.get("title", "")) for c in cfg["cards"])


def words_path(cfg, scene, words_dir=None):
    d = words_dir or cfg.get("words_dir")
    if not d:
        die("words_dir is not set (config 'words_dir' or --words-dir)")
    return os.path.join(d, f"{scene}.json")


def load_words(cfg, scene, words_dir=None):
    """[[start_ms, end_ms, word], ...] for one scene, in MASTER ms. Validates the shape."""
    p = words_path(cfg, scene, words_dir)
    if not os.path.exists(p):
        die(f"word file not found: {p}")
    with open(p, encoding="utf-8") as fh:
        w = json.load(fh)
    if isinstance(w, dict):
        w = w.get("words", [])
    for k, x in enumerate(w):
        if not (isinstance(x, (list, tuple)) and len(x) >= 3):
            die(f"{p}: word {k} is not [start_ms, end_ms, word]: {x!r}")
    return [list(x[:3]) for x in w]


def load_sentences(cfg, scene, words_dir=None):
    """[[start_ms, end_ms, text], ...] or None when <scene>_sentences.json is absent."""
    d = words_dir or cfg.get("words_dir")
    p = os.path.join(d, f"{scene}_sentences.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        s = json.load(fh)
    out = []
    for x in s:
        if isinstance(x, dict):
            out.append([x.get("start_ms"), x.get("end_ms"), x.get("text", "")])
        else:
            out.append(list(x[:3]))
    return out


def sentences_from_words(w):
    """Fallback sentence split on . ? ! when no sentence file exists."""
    out, cur = [], []
    for x in w:
        cur.append(x)
        if str(x[2]).rstrip("\"')]").endswith((".", "?", "!")):
            out.append([cur[0][0], cur[-1][1], " ".join(str(y[2]) for y in cur)])
            cur = []
    if cur:
        out.append([cur[0][0], cur[-1][1], " ".join(str(y[2]) for y in cur)])
    return out


def all_words(cfg, words_dir=None):
    """Every word of every configured scene as sorted (start_ms, end_ms, word) tuples."""
    words = []
    for sid in scene_order(cfg):
        words += [tuple(w) for w in load_words(cfg, sid, words_dir)]
    words.sort()
    return words


def on_grid(ms, fps, tol=1e-3):
    x = ms * fps / 1000
    return abs(x - round(x)) < tol


def grid_round(ms, fps):
    return round(round(ms * fps / 1000) * 1000 / fps, 3)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def nframes(path):
    """Exact video frame count (counts packets; fast, no decode)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=nb_read_packets", "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    if not out:
        die(f"ffprobe could not count frames of {path}")
    return int(out.splitlines()[0])


def stream_duration(path, sel):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", sel, "-show_entries", "stream=duration", "-of",
                          "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    return float(out.splitlines()[0]) if out else float("nan")


def video_size(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of",
               "csv=p=0:s=x", path]).stdout.strip().splitlines()[0]
    w, h = out.split("x")
    return int(w), int(h)


def audio_channels(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of",
                          "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    return int(out.splitlines()[0]) if out else 0


def ffmpeg_major():
    """Major version of ffmpeg on PATH (git builds without a number count as new)."""
    try:
        first = run(["ffmpeg", "-hide_banner", "-version"]).stdout.splitlines()[0]
    except Exception:
        return 99
    m = re.search(r"ffmpeg version n?(\d+)\.", first)
    return int(m.group(1)) if m else 99


def decode_audio(path, sr, channels=1):
    """Decode the first audio stream to float32 at sr; returns shape (n,) for mono, (n, channels) otherwise."""
    import numpy as np  # lazy: only the scripts that touch samples need numpy
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", str(channels), "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    return x if channels == 1 else x.reshape(-1, channels)


def fmt_ms(ms):
    """m:ss.mmm (section file style)."""
    ms = int(round(ms))
    return f"{ms // 60000}:{(ms % 60000) / 1000:06.3f}"


def require_numpy():
    try:
        import numpy  # noqa: F401
    except ImportError:
        die("numpy is required (pip install numpy, or run with: uv run --with numpy python3 <script>)")


if __name__ == "__main__":  # a library, not a CLI: running it prints this description
    print(__doc__)
