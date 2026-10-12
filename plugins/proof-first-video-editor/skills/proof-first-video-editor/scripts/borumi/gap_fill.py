#!/usr/bin/env python3
"""gap_fill.py - fill LAYOUT GAPS (where Borumi falls back to its default layout: a fullscreen-camera flash) with ADDED
layouts that copy the neighbouring layout's props. No untrim, no delete (both rebuild layouts project-wide).

usage: gap_fill.py [--gap A:B ...] [--min-ms 0] [--dry]
  default: every layout gap in the project that holds a 30/60 fps frame (snapshot.py gaps lists them); --gap picks some.
  gap inside one scene          -> one layout [A, B] with the props of the layout that ENDS at A (its left neighbour)
  gap across a scene boundary M -> two layouts: [A, M] with the left neighbour's props, [M, B] with the props of the
                                   layout that STARTS at B (each scene keeps its own look)
A source without a layer_id (an intentionally empty panel) is dropped from the copied props: add_segments needs every
source to name an active layer. Gaps of 1-2 ms holding no frame are invisible and left alone.

Checks: the gaps are exactly the requested ones; each has the needed neighbour(s); no screen content in a camera-only
fill; only the new layouts appear (strict project-wide diff; an identical-props merge into a neighbour or a screen_N
name Borumi re-derives is accepted); no layout gap holding a frame remains in the filled ranges; mid-gap frames changed
(they were the fallback) and show the camera tile in place; frames just outside unchanged.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--gap", action="append", default=[], help="A:B (project ms) - a specific layout gap to fill")
    ap.add_argument("--min-ms", type=int, default=0, help="ignore auto-found gaps shorter than this")
    B.add_common_args(ap)
    args = ap.parse_args()
    ctx = B.context(args)
    cfg = ctx.cfg
    G = B.geometry_payload(cfg, canvas=B.canvas_png(cfg, create=True, out=ctx.out))
    cam = cfg["camera_layer"]
    pre0 = S.snapshot(ctx.key)
    if args.gap:
        WANT = sorted(tuple(int(x) for x in g_.split(":")) for g_ in args.gap)
    else:
        WANT = [(a, b) for a, b, fr in S.gaps(pre0, 0, pre0["duration"])["layout"] if fr and b - a >= args.min_ms]
    if not WANT:
        log("no layout gap holding a 30/60 fps frame: nothing to do")
        return 0
    log("gaps to fill: %s" % WANT)
    tag = "_dry" if args.dry else ""
    FD = ctx.dir("frames", "gapfill%s" % tag)

    def clean(props_json):
        p = json.loads(props_json)
        if p.get("kind") == "custom":
            # an empty-panel source has no layer (renders nothing) and add_segments needs an active layer: drop it
            p["sources"] = [s_ for s_ in p.get("sources", []) if s_.get("layer_id")]
        return p

    def body(tx, k, pre, checks):
        g0 = [(a, b) for a, b, fr in S.gaps(pre, 0, pre["duration"])["layout"] if (a, b) in WANT]
        checks.add("the layout gaps are exactly %s" % WANT, g0 == WANT, str(g0))
        plan = []
        for a, b in WANT:
            left = [(i, v) for i, v in pre["layouts"].items() if v[1] == a]
            right = [(i, v) for i, v in pre["layouts"].items() if v[0] == b]
            bounds = [s[2] for s in pre["scenes"] if a < s[2] < b]
            if bounds:
                M = bounds[0]
                checks.add("gap [%d, %d] crosses one scene boundary %d; a layout ends at A and one starts at B" % (a, b, M),
                           len(bounds) == 1 and len(left) == 1 and len(right) == 1, "%s %s %s" % (bounds, [i for i, _ in left], [i for i, _ in right]))
                if len(left) == 1 and len(right) == 1:
                    plan += [(a, M, clean(left[0][1][2]), left[0][0]), (M, b, clean(right[0][1][2]), right[0][0])]
            else:
                sc = [s for s in pre["scenes"] if s[2] <= a and b <= s[3]]
                checks.add("gap [%d, %d] lies inside one scene and a layout ends at its start" % (a, b), len(left) == 1 and len(sc) == 1,
                           "%s %s" % ([i for i, _ in left], sc))
                if len(left) == 1:
                    p = clean(left[0][1][2])
                    srcs = [s_.get("layer_id") for s_ in p.get("sources", [])] if p.get("kind") == "custom" else [p.get("layer_id")]
                    if srcs == [cam]:
                        checks.add("no screen content in the camera-only gap [%d, %d]" % (a, b),
                                   not any(v[0].startswith("screen") and v[1] < b and v[2] > a for v in pre["content"].values()))
                    plan.append((a, b, p, left[0][0]))
        mid_fr = {}
        for a, b in WANT:
            for t in (B.grid((a + b) / 2.0), B.grid(a - 300), B.grid(b + 300)):
                if 1 <= t < pre["duration"]:
                    mid_fr[t] = B.save_frame(k, t, os.path.join(FD, "pre_%d.png" % round(t)))
        if not plan:
            return None, {"filled": WANT}
        B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
            {"type": "layout", "placement": {"range": [a, b]}, "properties": p} for a, b, p, _ in plan]})
        post = S.snapshot(k)
        nl = {i: v for i, v in post["layouts"].items() if i not in pre["layouts"]}
        exp_l, merged = {}, []
        for a, b, p, src in plan:     # an identical-props neighbour may absorb the new piece (a visual no-op): accept it
            pv, v = pre["layouts"][src], post["layouts"].get(src)
            want = (pv[0], b, pv[2]) if pv[1] == a else (a, pv[1], pv[2])
            if v == want:
                exp_l[src] = want
                merged.append((a, b))
                log("  %s merged its identical new piece (now %s)" % (src, want[:2]))
        checks.add("new layouts exactly at the planned ranges (or merged into identical neighbours)",
                   sorted([v[:2] for v in nl.values()] + merged) == sorted((a, b) for a, b, _, _ in plan),
                   str(sorted(v[:2] for v in nl.values())))
        checks.add("no layout gap holding a frame left in the filled ranges", not [x for x in S.gaps(post, 0, post["duration"])["layout"]
                                                                                    if x[2] and any(x[0] < b and x[1] > a for a, b in WANT)])

        def same_props(v, p):
            a_, b_ = B.drop_screen_names(v[2]), B.drop_screen_names(json.dumps(p))
            return a_ == b_ or B.norm_numbers(json.loads(a_)) == B.norm_numbers(json.loads(b_))
        okp = all(any(v[:2] == (a, b) and same_props(v, p) for a, b, p, _ in plan) for v in nl.values())
        checks.add("each new layout carries its neighbour's props (screen_N names may be re-derived)", okp,
                   str([(v[:2]) for v in nl.values()]))
        cam_only = {(a, b) for a, b, p, _ in plan if p.get("kind") == "custom" and [s_.get("layer_id") for s_ in p.get("sources", [])] == [cam]}
        bad, notes = S.strict_diff(pre, post, {}, exp_l, {}, lambda cat, i, v: cat == "layouts" and i in nl, S.ident)
        for n_ in notes:
            log("  note: " + n_)
        for b_ in bad:
            log("  DIFF " + b_[:300])
        checks.add("project-wide diff: only the new layouts", not bad and post["duration"] == pre["duration"] and post["scenes"] == pre["scenes"],
                   "%d problems" % len(bad))
        rows = []
        for a, b in WANT:
            tm = B.grid((a + b) / 2.0)
            if tm in mid_fr:
                rows.append(("mid", tm, mid_fr[tm], B.save_frame(k, tm, os.path.join(FD, "post_%d.png" % round(tm)))))
            for t in (B.grid(a - 300), B.grid(b + 300)):
                if t in mid_fr:
                    rows.append(("outside", t, mid_fr[t], B.save_frame(k, t, os.path.join(FD, "post_%d.png" % round(t)))))
        mm = B.imgcheck("measure", dict(G, paths=[r[3] for r in rows]))
        dif = B.imgcheck("meandiff", {"pairs": [[r[2], r[3]] for r in rows]})
        okm, oko = [], []
        for r, m_, d_ in zip(rows, mm, dif):
            t_ = m_["tile_diff"]
            tile = bool(t_) and all(abs(t_[i] - G["tile"][i]) <= 8 for i in (0, 1, 2))
            log("%s %.2f: tile %s left_frac %.3f ; diff vs pre %.3f" % (r[0], r[1], t_, m_["left_frac_diff"], d_))
            if r[0] == "mid":
                empty_ok = m_["left_frac_diff"] < 0.01 if any(a <= r[1] < b for a, b in cam_only) else True
                okm.append(tile and d_ > 5 and empty_ok)
            else:
                oko.append(d_ < 1.0)
        checks.add("mid-gap frames changed (they were the fallback) and show the camera tile in place (+ an empty panel on camera-only fills)", all(okm), str(okm))
        checks.add("frames just outside the gaps unchanged", all(oko), str(dif))
        mont = ctx.path("frames", "gapfill%s.jpg" % tag)
        B.montage([r[2] for r in rows if r[0] == "mid"] + [r[3] for r in rows if r[0] == "mid"], mont, max(1, len(WANT)), 480, 270)

        def post_check(pre_proj, post_c):
            n2 = {i: v for i, v in post_c["layouts"].items() if i not in pre_proj["layouts"]}
            return S.strict_diff(pre_proj, post_c, {}, exp_l, {}, lambda cat, i, v: cat == "layouts" and i in n2, S.ident)
        return post_check, {"filled": WANT, "plan": [(a, b, src) for a, b, _, src in plan], "montage": mont}

    info = txrun.run(ctx, "gapfill", body, args.dry,
                     lambda i: "Fill %d short layout gap(s) with the neighbouring layouts so they no longer flash the default layout." % len(WANT),
                     args.lock_timeout)
    print(json.dumps(info, default=str))
    return 0 if info.get("checks_ok") else 2


if __name__ == "__main__":
    sys.exit(main())
