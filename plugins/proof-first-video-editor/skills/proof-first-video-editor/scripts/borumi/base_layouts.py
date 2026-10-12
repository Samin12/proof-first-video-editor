#!/usr/bin/env python3
"""base_layouts.py - lay down the STANDARD LOOK on ranges / scenes that have no layout yet, in one verified tx:
  cam     camera in the right tile only; the left panel area stays empty canvas (proof visuals / renders go there later)
  screen  a screen recording in the left rounded panel (letterboxed, lock_aspect_ratio true) + the camera tile.
          The panel check expects the recording to FILL the panel box (panel_rect matched to the recording's aspect);
          a recording of another aspect letterboxes and fails it: use cover, or adjust panel_rect in the config.
  cover   like screen but cover-cropped (lock_aspect_ratio false: crops, never stretches) - e.g. device / phone footage

usage: base_layouts.py --scene SCENE_ID:cam --scene SCENE_ID:screen[:screen_1] --range A:B:cover[:screen_2] ... [--dry]
       base_layouts.py --plan layouts.json [--dry]       [{"scene": id | "range": [a, b], "kind": "cam|screen|cover",
                                                          "layer": "screen_1"}, ...]  (examples/base_layouts.example.json)
Run it EARLY (right after the canvas, before cuts): ripple trims carry layouts along, and adding layouts later over
existing ones splits them. Geometry comes from the config (camera_rect, panel_rect); defaults = the standard look.

Checks: no layouts yet in the ranges; camera content covers every range (no gap holding a 30/60 fps frame); screen
ranges have that screen layer covering them; exactly the planned layouts with the stored props == requested (numeric
compare); strict project-wide diff (only the new layouts); per range 3 frames: the camera tile at the configured box
(left / top / right +-8 px; the bottom bracketed by the hidden canvas dot rows, robust to a dark shirt on the dark
canvas), the left side = empty canvas on cam ranges / the panel box +-8 px on screen ranges.
Exit: 0 ok, 2 checks failed, 3 error, 4 collision (all aborted).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwlib as B  # noqa: E402
import snapshot as S  # noqa: E402
import txrun  # noqa: E402

al = B.al
log = B.log


def covered(snap, layer_pred, a, b):
    """True when content on layers matching layer_pred covers [a, b) except frame-free slivers."""
    segs = sorted((v[1], v[2]) for v in snap["content"].values() if layer_pred(v[0]) and v[2] > a and v[1] < b)
    cur = a
    for x, y in segs:
        if x > cur and B.grid_hits(cur, x - 0.5):
            return False
        cur = max(cur, y)
    return cur >= b or not B.grid_hits(cur, b - 0.5)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--scene", action="append", default=[], help="SCENE_ID:cam|screen|cover[:LAYER]")
    ap.add_argument("--range", action="append", default=[], help="A:B:cam|screen|cover[:LAYER]")
    ap.add_argument("--plan", help="JSON list of {scene|range, kind, layer}")
    B.add_common_args(ap)
    args = ap.parse_args()
    ctx = B.context(args)
    cfg = ctx.cfg
    G = B.geometry_payload(cfg, canvas=B.canvas_png(cfg, create=True, out=ctx.out))
    cam = cfg["camera_layer"]
    items = []
    for x in args.scene:
        p = x.split(":")
        items.append({"scene": p[0], "kind": p[1], "layer": p[2] if len(p) > 2 else "screen_1"})
    for x in args.range:
        p = x.split(":")
        items.append({"range": [int(p[0]), int(p[1])], "kind": p[2], "layer": p[3] if len(p) > 3 else "screen_1"})
    if args.plan:
        items += B.load_json(args.plan)
    if not items:
        ap.error("nothing to do: give --scene / --range / --plan")
    pre0 = S.snapshot(ctx.key)
    scm = S.scene_map(pre0)
    PLAN = []
    for it in items:
        if it.get("scene"):
            if it["scene"] not in scm:
                B.die("scene %s not found" % it["scene"])
            a, b = scm[it["scene"]][2], scm[it["scene"]][3]
        else:
            a, b = it["range"]
        kind = it.get("kind", "cam")
        if kind not in ("cam", "screen", "cover"):
            B.die("kind must be cam / screen / cover, got %s" % kind)
        layer = it.get("layer") or "screen_1"
        props = B.cam_only_props(cfg) if kind == "cam" else B.panel_props(layer, cfg, lock=(kind == "screen"))
        PLAN.append((it.get("scene") or "%d-%d" % (a, b), a, b, kind, layer, props))
    PLAN.sort(key=lambda x: x[1])
    T = G["tile"]
    PANEL_PX = B.panel_px(cfg)
    tag = "_dry" if args.dry else ""
    FD = ctx.dir("frames", "base_layouts%s" % tag)

    def tile_good(mm):
        """left / top / right edges from the canvas diff (+-8 px); the bottom edge from the hidden dot rows:
        last covered dot row <= bottom <= first visible dot row below + 8."""
        t, ty = mm.get("tile_diff"), mm.get("tile_y")
        if not t or not ty or ty[5] is None:
            return False
        return all(abs(t[i] - T[i]) <= 8 for i in (0, 1, 2)) and ty[4] <= T[3] <= ty[5] + 8

    def body(tx, k, pre, checks):
        clash = {i: v[:2] for i, v in pre["layouts"].items() if any(v[0] < b and v[1] > a for _, a, b, _, _, _ in PLAN)}
        checks.add("no layouts yet in the planned ranges", not clash, str(clash))
        for x, y in zip(PLAN, PLAN[1:]):
            checks.add("ranges %s / %s do not overlap" % (x[0], y[0]), x[2] <= y[1])
        for nm, a, b, kind, layer, _ in PLAN:
            checks.add("%s [%d, %d]: %s content covers it" % (nm, a, b, cam), covered(pre, lambda l: l == cam, a, b))
            if kind != "cam":
                checks.add("%s [%d, %d]: %s content covers it" % (nm, a, b, layer), covered(pre, lambda l: l == layer, a, b))
        B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
            {"type": "layout", "placement": {"range": [a, b]}, "properties": props} for _, a, b, _, _, props in PLAN]})
        post = S.snapshot(k)
        nl = {i: v for i, v in post["layouts"].items() if i not in pre["layouts"]}
        got = sorted((v[0], v[1], v[2]) for v in nl.values())
        want = sorted((a, b, S.pj(props)) for _, a, b, _, _, props in PLAN)
        log("new layouts: %s" % [(i, v[:2]) for i, v in sorted(nl.items(), key=lambda x: x[1][0])])
        checks.add("exactly the %d planned layouts (ranges)" % len(PLAN), [g_[:2] for g_ in got] == [w[:2] for w in want], str([g_[:2] for g_ in got]))
        checks.add("stored props == requested (numeric compare)", len(got) == len(want) and all(
            B.norm_numbers(json.loads(g_[2])) == B.norm_numbers(json.loads(w[2])) for g_, w in zip(got, want)),
            "; ".join(g_[2][:300] for g_, w in zip(got, want) if B.norm_numbers(json.loads(g_[2])) != B.norm_numbers(json.loads(w[2]))))
        bad, notes = S.strict_diff(pre, post, {}, {}, {}, lambda cat, i, v: cat == "layouts" and i in nl, S.ident)
        for b_ in bad:
            log("  DIFF " + b_[:300])
        checks.add("project-wide diff: only the new layouts", not bad and post["duration"] == pre["duration"] and post["scenes"] == pre["scenes"],
                   "%d problems" % len(bad))
        rows = []
        for nm, a, b, kind, layer, _ in PLAN:
            for g_ in (B.grid(a + 100), B.grid((a + b) / 2.0), B.grid(b - 200)):
                rows.append((nm, kind, round(g_, 2), B.save_frame(k, g_, os.path.join(FD, "%s_%d.png" % (nm, round(g_))), "high")))
        m = B.imgcheck("measure", dict(G, paths=[r[3] for r in rows]))
        tile_ok, panel_ok = [], []
        for r, mm in zip(rows, m):
            log("frame %s %s %.2f: tile(diff) %s bottom dot rows %s panel(diff) %s left diff %.3f" % (
                r[0], r[1], r[2], mm["tile_diff"], mm["tile_y"][4:6] if mm["tile_y"] else None, mm["panel_diff"], mm["left_frac_diff"]))
            tile_ok.append(tile_good(mm))
            if r[1] == "cam":
                panel_ok.append(mm["left_frac_diff"] < 0.01)
            else:
                panel_ok.append(bool(mm["panel_diff"]) and all(abs(x - y) <= 8 for x, y in zip(mm["panel_diff"], PANEL_PX)))
        checks.add("camera tile at %s in every frame (edges +-8 px; bottom bracketed by hidden dot rows)" % [round(v) for v in T], all(tile_ok), str(tile_ok))
        checks.add("left side: empty canvas on cam ranges, the panel at %s on screen ranges" % [round(v) for v in PANEL_PX], all(panel_ok), str(panel_ok))
        mont = ctx.path("frames", "base_layouts%s.jpg" % tag)
        B.montage([r[3] for r in rows], mont, 3, 480, 270)
        log("montage: %s" % mont)

        def post_check(pre_proj, post_c):
            n2 = {i: v for i, v in post_c["layouts"].items() if i not in pre_proj["layouts"]}
            b2, nn = S.strict_diff(pre_proj, post_c, {}, {}, {}, lambda cat, i, v: cat == "layouts" and i in n2, S.ident)
            if sorted(v[:2] for v in n2.values()) != sorted((a, b) for _, a, b, _, _, _ in PLAN):
                b2.append("new layouts %s" % sorted(v[:2] for v in n2.values()))
            return b2, nn
        return post_check, {"layouts": {i: v[:2] for i, v in nl.items()}, "plan": [(nm, a, b, kind, layer) for nm, a, b, kind, layer, _ in PLAN],
                            "montage": mont}

    info = txrun.run(ctx, "base_layouts", body, args.dry,
                     lambda i: "Standard look: camera in the right tile (empty left panel for later visuals) and screen recordings in the "
                               "left rounded panel with the camera tile (%d ranges)." % len(PLAN), args.lock_timeout)
    print(json.dumps(info, default=str))
    return 0 if info.get("checks_ok") else 2


if __name__ == "__main__":
    sys.exit(main())
