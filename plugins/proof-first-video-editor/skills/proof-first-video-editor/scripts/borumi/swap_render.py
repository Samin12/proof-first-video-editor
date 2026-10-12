#!/usr/bin/env python3
"""swap_render.py - replace a placed BEHIND render with a new version of the SAME length, in ONE locked, verified tx.

usage: swap_render.py --take TAKE_ID --render NEW.mp4 [--same-before MS] [--changed MS ...] [--name LABEL] [--dry]
       swap_render.py --at START_MS  --render NEW.mp4 ...        (the screen take that starts at START_MS)

What it does (proven on a real project; the v2 -> v3 swap of a closing render):
  1. remove the old take with a TARGETED NON-RIPPLE trim over its exact range. Not delete_segments: content deletes
     rebuild layouts project-wide and merge identical neighbours.
  2. add the exact-length copy of the new render at the same START (an insert would show as a duration / position
     change -> the check fails)
  3. the slot's layout piece (one piece exactly [start, end]: render full frame + camera tile) -> the same props
     naming the new take's layer
Checks: old take gone, nothing else changed project-wide (strict diff), one new take [start, end] media [0, L],
frames in the slot = the new render's own frames (PSNR >= --psnr left of the camera column), camera tile (and the badge,
when a badge overlay covers the slot) present, frames before the slot unchanged; optionally frames before --same-before
look as before (>= 35 dB) and frames at every --changed time look different (< 25 dB): proof that the new version is in.

Only BEHIND renders (opaque, layout [render, camera]). For a FRONT render (alpha over footage) use two steps: remove
the old take with the same targeted non-ripple trim in its own verified tx, then place_render.py --mode front.
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
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--take", help="segment id of the placed render take to replace")
    g.add_argument("--at", type=int, help="start ms of the placed render take to replace")
    ap.add_argument("--render", required=True, help="the new render (1920x1080, 30 fps, at least as long as the slot)")
    ap.add_argument("--same-before", type=int, help="project ms where the new version starts to differ (frames before it must match)")
    ap.add_argument("--changed", type=int, nargs="*", default=[], help="project ms where the new version must look different")
    ap.add_argument("--name", help="label for logs / frames (default swap_<take>)")
    ap.add_argument("--psnr", type=float, default=30.0)
    B.add_common_args(ap)
    args = ap.parse_args()
    ctx = B.context(args)
    cfg = ctx.cfg
    G = B.geometry_payload(cfg)
    cam = cfg["camera_layer"]
    render = os.path.abspath(os.path.expanduser(args.render))
    pre0 = S.snapshot(ctx.key)
    if args.take:
        old_id = args.take
    else:
        hits = [i for i, v in pre0["content"].items() if v[0].startswith("screen") and v[1] == args.at]
        if len(hits) != 1:
            B.die("%d screen takes start at %d: %s" % (len(hits), args.at, hits))
        old_id = hits[0]
    if old_id not in pre0["content"]:
        B.die("no content segment %s" % old_id)
    old0 = pre0["content"][old_id]
    A, B_ = old0[1], old0[2]
    L = B_ - A
    label = args.name or "swap_%s" % old_id
    tag = "_dry" if args.dry else ""
    FD = ctx.dir("frames", label + tag)
    WD = ctx.dir("place")
    copy, cp = B.exact_copy(render, os.path.join(WD, "%s_%d_%d.mp4" % (label, A, B_)), L, alpha=False,
                            fps=cfg["render_fps"], size=(cfg["frame_w"], cfg["frame_h"]))
    NFR = cp["nfr"]
    ftag = "%s_%d_%d_%d" % (label, A, B_, int(os.path.getmtime(copy)))
    log("new render %d frames -> exact copy %s: %d frames, %.3f ms (slot %d ms)" % (cp["src_frames"], copy, cp["frames"], cp["dur_ms"], L))

    def rframe(n):
        return B.render_frame(copy, n, WD, nfr=NFR, tag=ftag)

    def body(tx, k, pre, checks):
        old = pre["content"].get(old_id)
        OLD_MEDIA = old[3] if old else None
        checks.add("old take %s = screen [%d, %d] media [0, %d] (a placed render)" % (old_id, A, B_, L),
                   old is not None and old[0].startswith("screen") and old[1:3] == (A, B_) and old[4] == 0 and old[5] == L, str(old))
        lay = [(i, v) for i, v in pre["layouts"].items() if v[0] < B_ and v[1] > A]
        srcs = [x.get("layer_id") for x in json.loads(lay[0][1][2]).get("sources", [])] if len(lay) == 1 else None
        checks.add("one layout piece exactly [%d, %d] = the render (%s) full frame + %s (a BEHIND slot)" % (A, B_, old[0] if old else "?", cam),
                   len(lay) == 1 and lay[0][1][:2] == (A, B_) and old is not None and srcs == [old[0], cam], str([(i, v[:2]) for i, v in lay])[:200])
        OLD_LAYOUT = lay[0][0] if lay else None
        others = [(i, v[:3]) for i, v in pre["content"].items() if v[0].startswith("screen") and v[1] < B_ and v[2] > A and i != old_id]
        checks.add("no other screen content in the slot", not others, str(others))
        has_badge = any(v[0].startswith("media_overlay") and v[1] <= A + 500 and v[2] >= B_ - 500 for v in pre["overlays"].values())
        beats = {"s+0.5": B.grid(A + 500), "mid": B.grid((A + B_) / 2.0), "e-0.5": B.grid(B_ - 500)}
        if args.same_before and args.same_before - 300 > A + 500:
            beats["same"] = B.grid(args.same_before - 300)
        for j, t in enumerate(args.changed):
            beats["chg%d" % j] = B.grid(t)
        outside = {"before": B.grid(A - 300), "before2": B.grid(A - 2000), "after": B.grid(B_ + 300)}
        outside = {nm: g_ for nm, g_ in outside.items() if 1 <= g_ < pre["duration"]}
        pre_fr = {}
        for nm, g_ in list(beats.items()) + list(outside.items()):
            pre_fr[nm] = B.save_frame(k, g_, os.path.join(FD, "pre_%s_%d.png" % (nm, round(g_))))
        # 1. remove the old take (non-ripple, targeted)
        B.call("trim_timeline", {"tx_id": tx.id, "timeline_hash": tx.hash(), "ranges": [[A, B_]], "ripple": False,
                                 "target": {"type": "segments", "segment_ids": [old_id]}})
        s1 = S.snapshot(k)
        gone = old_id not in s1["content"] and not any(v[3] == OLD_MEDIA for v in s1["content"].values())
        same_c = all(s1["content"].get(i) == v for i, v in pre["content"].items() if i != old_id)
        log("after the removal: layout %s = %s; duration %d; scenes same %s" % (
            OLD_LAYOUT, (s1["layouts"].get(OLD_LAYOUT) or (None,))[:2], s1["duration"], s1["scenes"] == pre["scenes"]))
        checks.add("old take removed; every other content segment, the duration and the scenes unchanged",
                   gone and same_c and s1["duration"] == pre["duration"] and s1["scenes"] == pre["scenes"])
        other_l = {i: v for i, v in pre["layouts"].items() if i != OLD_LAYOUT}
        checks.add("no other layout touched by the removal", all(s1["layouts"].get(i) == v for i, v in other_l.items()),
                   str([(i, v[:2], (s1["layouts"].get(i) or (None,))[:2]) for i, v in other_l.items() if s1["layouts"].get(i) != v][:3]))
        # 2. add the new version
        mid = al.import_media(tx, copy)
        B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
            {"type": "take", "placement": {"at_ms": A}, "content": {"media_id": mid, "video_kind": "screen"}}]})
        s2 = S.snapshot(k)
        nt = [(i, v) for i, v in s2["content"].items() if i not in s1["content"]]
        checks.add("one new take [%d, %d] media [0, %d] (no insert / ripple)" % (A, B_, L),
                   len(nt) == 1 and nt[0][1][0].startswith("screen") and nt[0][1][1:6] == (A, B_, mid, 0, L)
                   and s2["duration"] == pre["duration"] and s2["scenes"] == pre["scenes"], str(nt))
        checks.add("adding it moved nothing else", all(s2["content"].get(i) == v for i, v in s1["content"].items()))
        layer = nt[0][1][0] if nt else "screen_2"
        # 3. layout
        lp = [(i, v) for i, v in s2["layouts"].items() if v[0] < B_ and v[1] > A]
        if len(lp) == 1 and lp[0][1][:2] == (A, B_):
            B.call("update_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "updates": [
                {"type": "layout", "segment_id": lp[0][0], "properties": B.behind_props(layer, cfg)}]})
        else:
            checks.add("slot layout still a single piece [%d, %d]" % (A, B_), False, str([(i, v[:2]) for i, v in lp]))
        post = S.snapshot(k)
        newl = [(i, v) for i, v in post["layouts"].items() if v[0] < B_ and v[1] > A]
        checks.add("slot layout [%d, %d] = render (%s) full frame + %s" % (A, B_, layer, cam),
                   len(newl) == 1 and newl[0][1][:2] == (A, B_) and
                   [x.get("layer_id") for x in json.loads(newl[0][1][2])["sources"]] == [layer, cam], str([(i, v[:2]) for i, v in newl]))
        exp_c = {old_id: None}
        exp_l = {i: post["layouts"].get(i) for i, _ in newl}
        nc = {i for i in post["content"] if i not in pre["content"]}
        bad, notes = S.strict_diff(pre, post, exp_c, exp_l, {}, lambda cat, i, v: cat == "content" and i in nc, S.ident)
        for b_ in bad:
            log("  DIFF " + b_[:300])
        checks.add("project-wide diff: only the take swapped (+ its layout's layer name)",
                   not bad and post["duration"] == pre["duration"] and post["scenes"] == pre["scenes"], "%d problems" % len(bad))
        # frames
        rows = []
        for nm, g_ in beats.items():
            n0 = int(round((g_ - A) * 30 / 1000.0))
            d = B.save_frame(k, g_, os.path.join(FD, "post_%s_%d.png" % (nm, round(g_))))
            rows.append({"name": nm, "t": round(g_, 2), "n": n0, "post": d, "pre": pre_fr[nm], "refs": [rframe(n0 + dd) for dd in (-1, 0, 1)]})
        for nm, g_ in outside.items():
            d = B.save_frame(k, g_, os.path.join(FD, "post_%s_%d.png" % (nm, round(g_))))
            rows.append({"name": nm, "t": round(g_, 2), "n": -1, "post": d, "pre": pre_fr[nm], "refs": []})
        res = B.imgcheck("swapcmp", {"rows": rows, "col_x": G["col_x"], "tile_zone": G["tile_zone"], "tile_inner": G["tile_inner"],
                                     "badge_inner": G["badge_inner"]})
        for o in res:
            log("frame %-8s: %s" % (o["name"], o))
        inr = [o for o in res if "vs_render" in o]
        checks.add("every checked frame in the slot = the new render's own frame (PSNR >= %.0f left of the camera column)" % args.psnr,
                   all(o["vs_render"] >= args.psnr for o in inr), str([o["vs_render"] for o in inr]))
        checks.add("camera tile%s present on every frame in the slot (differs from the render there)" % (" + badge" if has_badge else ""),
                   all(o["tile_diff"] > 15 and (o["badge_diff"] > 8 or not has_badge) for o in inr),
                   str([(o["tile_diff"], o["badge_diff"]) for o in inr]))
        same = [o for o in res if o["name"] in ("s+0.5", "same")] if args.same_before else []
        if same:
            checks.add("frames before the changed beat look as before (old == new there)", all(o["vs_pre_left"] >= 35 for o in same),
                       str([o["vs_pre_left"] for o in same]))
        chg = [o for o in res if o["name"].startswith("chg")]
        if chg:
            checks.add("the changed beats look different (the new content is in)", all(o["vs_pre_left"] < 25 for o in chg),
                       str([o["vs_pre_left"] for o in chg]))
        outs = [o for o in res if o["name"] in outside]
        checks.add("frames outside the slot unchanged", all(o["vs_pre_left"] >= 40 for o in outs), str([o["vs_pre_left"] for o in outs]))
        mont = ctx.path("frames", "%s%s.jpg" % (label, tag))
        B.montage([r["post"] for r in rows if r["refs"]], mont, 3, 640, 360)
        post_lay = dict(post["layouts"])

        def post_check(pre_proj, post_c):
            n2 = {i for i in post_c["content"] if i not in pre_proj["content"]}
            b2, nn = S.strict_diff(pre_proj, post_c, exp_c, {i: post_lay.get(i) for i in set(pre_proj["layouts"]) | set(post_c["layouts"])}, {},
                                   lambda cat, i, v: cat == "content" and i in n2, S.ident)
            if post_c["layouts"] != post_lay:
                b2.append("layouts differ from the verified tx state")
            return b2, nn
        return post_check, {"mode": "behind", "old_take": old_id, "old_media": OLD_MEDIA, "new_take": nt[0][0] if nt else None,
                            "media": mid, "copy": copy, "start": A, "end": B_, "layer": layer,
                            "layout": newl[0][0] if newl else None, "montage": mont}

    info = txrun.run(ctx, label, body, args.dry,
                     lambda i: "Swap the render at %s-%s for a new version of the same length." % (B.fmt_ts(A), B.fmt_ts(B_)),
                     args.lock_timeout)
    print(json.dumps(info, default=str))
    return 0 if info.get("checks_ok") else 2


if __name__ == "__main__":
    sys.exit(main())
