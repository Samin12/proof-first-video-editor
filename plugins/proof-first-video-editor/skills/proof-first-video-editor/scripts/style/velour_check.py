#!/usr/bin/env python3
"""velour_check.py - acceptance test of an animation style against the canvas contract the skill relies on
(PANEL_MODE panel + scaling, the drifting dot grid, transparent FRONT renders), WITHOUT touching your workspace.

usage:
  velour_check.py [--kit DIR] [--package DIR] [--style velour] [--canvas VelourCanvas] [--out-dir DIR] [--keep]

  --kit DIR       the animation workspace (default: $BORUMI_AGENT_HOME/animation-kit, else ~/.borumi-agent/animation-kit);
                  it must have node_modules (opencutagent-borumi: `doctor.sh --install` / `kit.mjs ensure`)
  --package DIR   the style package to test (default: <kit>/styles/<style>). Test the shipped package BEFORE installing
                  it with: --package <skill>/templates/velour-style
  --style ID      the style id = the folder name scenes import from (default velour)
  --canvas NAME   the canvas component the package exports (default VelourCanvas); it must accept the prop
                  `transparent`, and the package must export a palette `C` with `accent` (the test jobs are not in
                  JOB_START_MS, so their drift starts at phase 0)
  --out-dir DIR   where the three stills are copied (default: a temp folder, printed)
  --keep          keep the temporary overlay workspace (printed) for inspection

How: builds a throwaway overlay of the kit in a temp folder (kit configs and src/ copied, node_modules entries
symlinked one by one so Remotion's webpack cache lands in the overlay, an empty public/, ONLY the tested style under
styles/, and two throwaway jobs: a BEHIND scene drawing a 12 px accent frame around the 1920x1080 stage, and a
transparent FRONT scene with one 200x200 accent square at (100, 100)). Runs the kit's TypeScript compiler (the same
check as `job.mjs typecheck`), renders stills at frames 0, 240 and 480 of the BEHIND job and frame 0 of the FRONT job
with the kit's Remotion CLI, then measures pixels (ffmpeg decodes the PNGs; stdlib only):
  * the canvas right of the panel (x 1510-1910) is #121212 with grey dots (dot pixels 0.3-15 %);
  * the panel band above the scaled stage is the panel fill (median ~#181716), the canvas left of the panel is not;
  * the accent frame lands exactly where PANEL + PANEL_SCALE put the stage: x 19-28 / 1484-1493, y 125-135 / 945-955,
    and nothing of it shows outside (the stage is scaled x0.7676 and centred vertically in the panel);
  * the dots drift: frame 240 (8 s = 20 px) differs from frame 0, frame 480 (16 s = one full 40 px pitch) matches it;
  * FRONT: fully transparent outside the square (x >= 1500 included), the square unscaled at final-frame pixels.
Exit 0 = every check passed; 2 = a check (or the type check) failed; 3 = setup error. Nothing in --kit is written.

Requires: node >= 18 (the kit's), ffmpeg >= 6 on PATH, Python 3.9+.
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

W, H = 1920, 1080
PANEL = dict(x=19.2, y=19.2, w=1473.75, h=1041.6)
SCALE = PANEL["w"] / 1920.0
STAGE_TOP = PANEL["y"] + (PANEL["h"] - 1080 * SCALE) / 2.0          # ~125.5
STAGE_BOTTOM = STAGE_TOP + 1080 * SCALE                               # ~954.5
ACCENT = (226, 119, 78)

BEHIND = r'''import React from "react";
import { %(canvas)s, C } from "../../../styles/%(style)s/src";

// pfve acceptance job (BEHIND): a 12 px accent frame around the whole 1920x1080 stage.
export default function Scene() {
  return (
    <%(canvas)s>
      <div style={{ position: "absolute", left: 0, top: 0, width: 1920, height: 1080, boxSizing: "border-box",
                    border: `12px solid ${C.accent}` }} />
    </%(canvas)s>
  );
}
'''
FRONT = r'''import React from "react";
import { %(canvas)s, C } from "../../../styles/%(style)s/src";

// pfve acceptance job (FRONT): transparent, one 200x200 accent square at final-frame pixels (100, 100).
export default function Scene() {
  return (
    <%(canvas)s transparent>
      <div style={{ position: "absolute", left: 100, top: 100, width: 200, height: 200, background: C.accent }} />
    </%(canvas)s>
  );
}
'''
MANIFEST = '''import type { JobEntry } from "./types";
import Behind from "./pfve-check-behind/Scene";
import Front from "./pfve-check-front/Scene";

export const jobs: JobEntry[] = [
  { id: "pfve-check-behind", component: Behind, fps: 30, width: 1920, height: 1080, durationInFrames: 600 },
  { id: "pfve-check-front", component: Front, fps: 30, width: 1920, height: 1080, durationInFrames: 30 },
];
'''


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail})
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, (" - %s" % detail) if detail else ""))

    @property
    def ok(self):
        return all(r["ok"] for r in self.rows)


def die(msg, code=3):
    print("velour_check: " + msg, file=sys.stderr)
    sys.exit(code)


def pkg_bin(kit, pkg, key, fallback):
    p = os.path.join(kit, "node_modules", pkg, "package.json")
    try:
        with open(p) as fh:
            b = json.load(fh).get("bin")
        rel = b if isinstance(b, str) else (b or {}).get(key) or fallback
    except (OSError, ValueError):
        rel = fallback
    return os.path.join(kit, "node_modules", pkg, rel)


def build_overlay(kit, package, style, canvas):
    T = tempfile.mkdtemp(prefix="pfve_velour_check_")
    for f in ("package.json", "tsconfig.json", "remotion.config.ts"):
        if os.path.exists(os.path.join(kit, f)):
            shutil.copy2(os.path.join(kit, f), os.path.join(T, f))
    nm = os.path.join(T, "node_modules")
    os.makedirs(nm)
    for e in os.listdir(os.path.join(kit, "node_modules")):
        if e == ".cache":
            continue  # Remotion's webpack cache: keep it inside the overlay
        os.symlink(os.path.join(kit, "node_modules", e), os.path.join(nm, e))
    os.makedirs(os.path.join(T, "public"))
    shutil.copytree(os.path.join(kit, "src"), os.path.join(T, "src"),
                    ignore=lambda d, names: ["jobs"] if os.path.abspath(d) == os.path.abspath(os.path.join(kit, "src")) else [])
    jobs = os.path.join(T, "src", "jobs")
    os.makedirs(jobs)
    shutil.copy2(os.path.join(kit, "src", "jobs", "types.ts"), os.path.join(jobs, "types.ts"))
    for name, tpl in (("pfve-check-behind", BEHIND), ("pfve-check-front", FRONT)):
        os.makedirs(os.path.join(jobs, name))
        with open(os.path.join(jobs, name, "Scene.tsx"), "w") as fh:
            fh.write(tpl % {"canvas": canvas, "style": style})
    with open(os.path.join(jobs, "manifest.ts"), "w") as fh:
        fh.write(MANIFEST)
    os.makedirs(os.path.join(T, "styles"))
    shutil.copytree(package, os.path.join(T, "styles", style), ignore=shutil.ignore_patterns("node_modules", ".DS_Store"))
    return T


def run(cmd, cwd, label, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)[-4000:]


def rgba(png):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", png, "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    if len(raw) != W * H * 4:
        raise ValueError("%s: expected a %dx%d image, got %d bytes" % (png, W, H, len(raw)))
    return raw


def px(img, x, y):
    i = (int(y) * W + int(x)) * 4
    return tuple(img[i:i + 4])


def region(img, x0, y0, x1, y1, step=1):
    return [px(img, x, y) for y in range(int(y0), int(y1), step) for x in range(int(x0), int(x1), step)]


def luma(p):
    return 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]


def near(p, rgb, tol=40):
    return all(abs(p[i] - rgb[i]) <= tol for i in range(3))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    home = os.environ.get("BORUMI_AGENT_HOME") or os.path.expanduser("~/.borumi-agent")
    ap.add_argument("--kit", default=os.path.join(home, "animation-kit"))
    ap.add_argument("--package")
    ap.add_argument("--style", default="velour")
    ap.add_argument("--canvas", default="VelourCanvas")
    ap.add_argument("--out-dir")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    kit = os.path.abspath(os.path.expanduser(a.kit))
    package = os.path.abspath(os.path.expanduser(a.package or os.path.join(kit, "styles", a.style)))
    for need in (os.path.join(kit, "src", "Root.tsx"), os.path.join(kit, "src", "jobs", "types.ts"),
                 os.path.join(kit, "node_modules", "typescript"), os.path.join(kit, "node_modules", "@remotion", "cli")):
        if not os.path.exists(need):
            die("not an installed animation workspace (missing %s). Install opencutagent-borumi and run its "
                "doctor.sh --install, or pass --kit." % need)
    if not os.path.isfile(os.path.join(package, "src", "index.ts")):
        die("style package not found: %s (needs src/index.ts)" % package)
    for tool in ("node", "ffmpeg"):
        if not shutil.which(tool):
            die("%s not on PATH" % tool)

    T = build_overlay(kit, package, a.style, a.canvas)
    out = os.path.abspath(os.path.expanduser(a.out_dir)) if a.out_dir else tempfile.mkdtemp(prefix="pfve_velour_stills_")
    os.makedirs(out, exist_ok=True)
    print("overlay workspace: %s\nstills: %s" % (T, out))
    c = Checks()
    try:
        code, log = run(["node", pkg_bin(kit, "typescript", "tsc", "bin/tsc"), "--noEmit"], T, "typecheck")
        c.add("typecheck (tsc --noEmit, as job.mjs typecheck)", code == 0, "" if code == 0 else log[-1500:])
        if code != 0:
            raise SystemExit(2)
        cli = pkg_bin(kit, os.path.join("@remotion", "cli"), "remotion", "remotion-cli.js")
        stills = {}
        for comp, frame in (("pfve-check-behind", 0), ("pfve-check-behind", 240), ("pfve-check-behind", 480),
                            ("pfve-check-front", 0)):
            f = os.path.join(out, "%s_f%d.png" % (comp, frame))
            code, log = run(["node", cli, "still", comp, f, "--frame=%d" % frame], T, "still")
            c.add("still %s frame %d" % (comp, frame), code == 0 and os.path.exists(f), "" if code == 0 else log[-1500:])
            if code != 0 or not os.path.exists(f):
                raise SystemExit(2)
            stills[(comp, frame)] = rgba(f)

        b0 = stills[("pfve-check-behind", 0)]
        right = region(b0, 1510, 0, 1910, H, 2)
        dots = sum(1 for p in right if luma(p) > 45) / len(right)
        dark = sum(1 for p in right if luma(p) < 30) / len(right)
        med = tuple(int(statistics.median(p[i] for p in right)) for i in range(3))
        c.add("canvas right of the panel: #121212 with grey dots", near(med, (18, 18, 18), 5) and 0.003 <= dots <= 0.15
              and dark > 0.7, "median rgb %s, dot pixels %.1f %%, dark %.0f %%" % (med, 100 * dots, 100 * dark))
        c.add("no accent in the camera / badge column (x >= 1500)", not any(near(p, ACCENT, 30) for p in right))
        band = region(b0, 300, 40, 1200, int(STAGE_TOP) - 8, 3)
        band_med = tuple(int(statistics.median(p[i] for p in band)) for i in range(3))
        left = region(b0, 0, 200, 15, 900, 1)
        left_med = tuple(int(statistics.median(p[i] for p in left)) for i in range(3))
        c.add("panel band above the stage = the panel fill (~#181716)", near(band_med, (24, 23, 22), 4),
              "median rgb %s" % (band_med,))
        c.add("left of the panel = canvas (~#121212)", near(left_med, (18, 18, 18), 4), "median rgb %s" % (left_med,))
        mid_x, mid_y = 756, 540
        frame_pts = {"left edge": (23, mid_y), "right edge": (1489, mid_y), "top edge": (mid_x, 130),
                     "bottom edge": (mid_x, 950)}
        for nm, (x, y) in frame_pts.items():
            p = px(b0, x, y)
            c.add("stage %s at (%d, %d) is the accent frame" % (nm, x, y), near(p, ACCENT), "rgb %s" % (p[:3],))
        for nm, (x, y) in {"above the stage": (mid_x, 112), "below the stage": (mid_x, 968), "inside the frame": (mid_x, mid_y),
                           "left of the panel": (9, mid_y)}.items():
            p = px(b0, x, y)
            c.add("no accent %s at (%d, %d)" % (nm, x, y), not near(p, ACCENT, 30), "rgb %s" % (p[:3],))
        r240 = region(stills[("pfve-check-behind", 240)], 1510, 0, 1910, H, 2)
        r480 = region(stills[("pfve-check-behind", 480)], 1510, 0, 1910, H, 2)
        d240 = sum(abs(luma(p) - luma(q)) for p, q in zip(right, r240)) / len(right)
        d480 = sum(abs(luma(p) - luma(q)) for p, q in zip(right, r480)) / len(right)
        c.add("the dots drift (frame 240 differs from frame 0)", d240 > 1.0, "mean |diff| %.2f" % d240)
        c.add("drift period 16 s (frame 480 matches frame 0)", d480 < 0.5, "mean |diff| %.2f" % d480)

        f0 = stills[("pfve-check-front", 0)]
        col = region(f0, 1500, 0, W, H, 4)
        c.add("FRONT: x >= 1500 fully transparent", all(p[3] == 0 for p in col))
        c.add("FRONT: empty areas transparent", px(f0, 1000, 900)[3] == 0 and px(f0, 95, 95)[3] == 0 and px(f0, 305, 305)[3] == 0)
        ins = [px(f0, 105, 105), px(f0, 200, 200), px(f0, 295, 295)]
        c.add("FRONT: the square is opaque accent at final-frame pixels (not scaled)",
              all(p[3] == 255 and near(p, ACCENT, 12) for p in ins), str([p for p in ins]))
    except SystemExit as e:
        if e.code not in (None, 0, 2):
            raise
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        c.add("ran without error", False, str(e)[:500])
    finally:
        if a.keep:
            print("kept overlay: %s" % T)
        else:
            shutil.rmtree(T, ignore_errors=True)
    with open(os.path.join(out, "velour_check.json"), "w") as fh:
        json.dump({"kit": kit, "package": package, "style": a.style, "ok": c.ok, "checks": c.rows}, fh, indent=1)
    print("%s: %d/%d checks passed (report %s)" % ("OK" if c.ok else "FAILED", sum(r["ok"] for r in c.rows), len(c.rows),
                                                   os.path.join(out, "velour_check.json")))
    return 0 if c.ok else 2


if __name__ == "__main__":
    sys.exit(main())
