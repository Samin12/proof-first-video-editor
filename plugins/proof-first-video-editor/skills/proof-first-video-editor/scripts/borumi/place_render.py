#!/usr/bin/env python3
"""place_render.py - place full-frame 1920x1080 30 fps motion-graphic / proof renders on a Borumi timeline.
ONE locked, verified transaction PER SLOT, each after an aborted PREFLIGHT that picks the take-placement strategy
(Borumi may INSERT-and-ripple a take added exactly where another segment starts; see references/borumi-writer.md).

usage:
  place_render.py SLOTS.json [--dry] [--psnr 30] [common args]
  place_render.py --mode behind --name C1 --render C1.mp4 --start 106564 --end 183420 [--dry]
SLOTS.json = {"mode": "behind" | "front", "slots": [{"name": str, "render": path, "start": ms, "end": ms}, ...]}
  (examples/slots.behind.example.json, examples/slots.front.example.json). Times are CURRENT project ms (integers);
  a slot must lie inside one scene; slots must not overlap.

BEHIND (talking-head stretches; the render draws its own panel + canvas, e.g. a velour PANEL_MODE render):
  0. exact-length copy outside the lock: the first ceil(L*30/1000) frames, H.264 -bf 0, no edit list, timescale 30000,
     last sample cut so the file lasts exactly L ms (mp4_set_duration.py)
  1. add the copy as a SCREEN take at START (the slot must be empty on every screen layer)
  2. layouts: split the scene layouts at the slot edges, set the piece(s) inside to [render full frame (bottom), camera
     tile]; holes are filled with new layout pieces. If a frame shows the wrong source (abutting takes: Borumi resolves
     screen_N from the takes active at the layout START), switch the piece to the other screen layer name and re-check.
  3. checks: strict project-wide diff (only the new take + the slot's layout pieces); layout picture map outside the
     slot unchanged; frames at start / +100 ms / +500 ms / mid / end-500 / end-100 = the render's own frame
     (PSNR >= --psnr left of the camera column and SPECIFIC: >= 2 dB above a frame 15 away, or >= 38 dB, or a static
     hold); camera tile box +-8 px; badge box unchanged; frames outside the slot unchanged; no screen / layout gap
     holding a 30/60 fps frame inside the slot.
FRONT (a render WITH ALPHA over real footage in the panel: ProRes 4444 / any alpha pix_fmt):
  0. exact-length ProRes 4444 copy (yuva444p10le; a longer take stretched the scene in a probe)
  1. add it as a SCREEN take at START (alpha is respected for screen sources), split the layouts at the slot edges, set
     the pieces inside to [footage panel (unchanged geometry), render full frame, camera tile]: later layout sources draw
     in front and badges are media overlays drawn above every layout -> footage < render < camera tile < badge.
     (NOT a media_overlay: overlays draw above the camera tile, get a default drop shadow, and Borumi repacks overlay
     layers on every overlay change, so the render could not be kept below the badge.)
     A layout piece that starts at / before the take start gets split 3 ms into the render when that sliver holds no
     frame (a piece starting before the take names both screen sources after the footage).
  2. checks: no repacking (every existing segment in place); strict diff; frames = the pre frame composited with the
     render's own RGBA frame outside the tile + badge (PSNR >= --psnr); camera tile + badge unchanged; frames just
     outside unchanged; no layout gap holding a frame around the slot.
Preflight strategies (aborted txs, first clean one wins): "exact" (take at START), "plus1-move" (at START+1, then move
to START), "plus1-stay" (at START+1, kept, only when neither 1 ms sliver holds a 30/60 fps frame).

Outputs: <out>/place/ (exact copies, render frames), <out>/frames/place_<label>[_dry]/ + montage JPG, commits.log
entries "place_<name>" (coverage.py reads them). Exit: 0 ok, 2 checks failed, 3 error, 4 collision (all aborted).
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
ARGS = CTX = None
MODE = None
SLOTS = []
FD = WD = None
G = {}
TAG = ""


# ------------------------------------------------------------------------------------------------ media
def exact_copy(s):
    A, B_ = s["start"], s["end"]
    L = B_ - A
    ext = ".mp4" if MODE == "behind" else ".mov"
    out = os.path.join(WD, "%s_%d_%d%s" % (s["name"], A, B_, ext))
    copy, cp = B.exact_copy(s["render"], out, L, alpha=(MODE == "front"), fps=CTX.cfg["render_fps"],
                            size=(CTX.cfg["frame_w"], CTX.cfg["frame_h"]))
    s.update({"copy": copy, "nfr": cp["nfr"], "L": L, "copy_probe": cp,
              "ftag": "%s_%d_%d_%d" % (s["name"], A, B_, int(os.path.getmtime(copy)))})
    log("%s: render %s (%d frames) -> exact copy %s: %d frames, %.3f ms (slot %d ms)" % (
        s["name"], os.path.basename(s["render"]), cp["src_frames"], copy, cp["frames"], cp["dur_ms"], L))


def render_frame(s, n, rgba=False):
    return B.render_frame(s["copy"], n, WD, nfr=s["nfr"], rgba=rgba, tag=s["ftag"])


def alpha_audit(s):
    """fraction of non-transparent pixels in the camera / badge column, outside the panel and inside it, over 5 frames."""
    ns = sorted({0, s["nfr"] // 4, s["nfr"] // 2, 3 * s["nfr"] // 4, s["nfr"] - 1})
    pngs = [render_frame(s, n, True) for n in ns]
    return B.imgcheck("alpha-audit", {"pngs": pngs, "col_x": G["col_x"], "panel": G["panel"]})


def fpath(name):
    return os.path.join(FD, name)


# ------------------------------------------------------------------------------------------------ common
def body(tx, k, pre, checks):
    for s in SLOTS:
        sc = [x for x in pre["scenes"] if x[2] <= s["start"] and s["end"] <= x[3]]
        checks.add("%s [%d, %d] lies inside one scene %s" % (s["name"], s["start"], s["end"], sc[0][:2] if sc else None), len(sc) == 1)
    for x, y in zip(SLOTS, SLOTS[1:]):
        checks.add("slots %s / %s do not overlap" % (x["name"], y["name"]), x["end"] <= y["start"])
    if MODE == "behind":
        return place_behind(tx, k, pre, checks)
    return place_front(tx, k, pre, checks)


def strategies(s, snap):
    """candidate placements: (name, add_at, move_to or None, final [start, end])."""
    A, B_ = s["start"], s["end"]
    scene = [x for x in snap["scenes"] if x[2] <= A and B_ <= x[3]][0]
    c = [("exact", A, None, (A, B_))]
    if B_ < scene[3]:   # START+1 would overhang the scene end (and grow the scene) when the slot ends at it
        c.append(("plus1-move", A + 1, A, (A, B_)))
        if not B.grid_hits(A, A + 1) and not B.grid_hits(B_, B_ + 1):
            c.append(("plus1-stay", A + 1, None, (A + 1, B_ + 1)))
    return c


def add_take(tx, k, s, checks, pre, strat):
    """add the render take with the given strategy; every other segment / scene / the duration must stay (+-1 ms
    display rounding only, group-consistent)."""
    name, at, move_to, final = strat
    mid = s["media"]
    before = S.snapshot(k)
    B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
        {"type": "take", "placement": {"at_ms": at}, "content": {"media_id": mid, "video_kind": "screen"}}]})
    s1 = S.snapshot(k)
    nt = [(i, v) for i, v in s1["content"].items() if i not in before["content"]]
    if len(nt) == 1 and move_to is not None:
        B.call("move_timeline_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "segment_ids": [nt[0][0]],
                                          "destination": {"type": "at_time", "start_ms": move_to}})
        s1 = S.snapshot(k)
    tv = s1["content"].get(nt[0][0]) if len(nt) == 1 else None
    big, noise, mixed, sc_ok = S.take_moved(before, s1)
    ok = not big and not mixed and sc_ok and tv is not None and tv[0].startswith("screen") and tv[1:6] == (final[0], final[1], mid, 0, s["L"])
    if checks is not None:
        checks.add("%s: take placed by '%s' at [%d, %d] media [0, %d] on %s; nothing else moved (%d pieces +-1 ms rounding)" % (
            s["name"], name, final[0], final[1], s["L"], tv[0] if tv else None, noise), ok,
            "moved %s mixed %s scenes ok %s take %s" % (big[:3], mixed[:3], sc_ok, tv))
    s["rounding_noise"] = noise
    return ((nt[0][0], tv) if tv else None), ok


def preflight(s):
    """aborted txs: the first strategy that leaves everything else in place."""
    for strat in strategies(s, S.snapshot(CTX.key)):
        res = {}

        def pbody(tx, k, pre, checks):
            s["media"] = al.import_media(tx, s["copy"])
            _, ok = add_take(tx, k, s, None, pre, strat)
            res["ok"] = ok
            return None, {}
        txrun.preflight(CTX, "%s_%s" % (s["name"], strat[0]), pbody, ARGS.lock_timeout)
        log("%s preflight '%s': %s" % (s["name"], strat[0], "clean" if res.get("ok") else "NOT clean"))
        if res.get("ok"):
            return strat
    raise SystemExit("%s: no clean take placement strategy (see references/borumi-writer.md, 'take insertion')" % s["name"])


def in_any_slot(a, b):
    return any(x["start"] <= a and b <= x["end"] for x in SLOTS)


# ------------------------------------------------------------------------------------------------ behind
def place_behind(tx, k, pre, checks):
    for s in SLOTS:
        clash = [(i, v[:3]) for i, v in pre["content"].items() if v[0].startswith("screen") and v[1] < s["end"] and v[2] > s["start"]]
        checks.add("%s: slot empty on every screen layer" % s["name"], not clash, str(clash))
    pre_fr = {}
    for s in SLOTS:
        for g in (B.grid(s["start"] - 300), B.grid(s["end"] + 300)):
            if 1 <= g < pre["duration"] and not any(x["start"] <= g < x["end"] for x in SLOTS):
                pre_fr[g] = B.save_frame(k, g, fpath("pre_%d.png" % round(g)))
        g = B.grid((s["start"] + s["end"]) / 2.0)
        pre_fr[g] = B.save_frame(k, g, fpath("pre_%d.png" % round(g)))
    takes = {}
    for s in SLOTS:
        s["media"] = al.import_media(tx, s["copy"])
        t_, _ = add_take(tx, k, s, checks, pre, s["strategy"])
        if t_:
            takes[s["name"]] = t_
            s["t0"] = t_[1][1]
    # layouts: split at the slot edges, then set the pieces inside each slot
    for s in SLOTS:
        cur = S.snapshot(k)
        for edge in (s["start"], s["end"]):
            for i in [i for i, v in cur["layouts"].items() if v[0] < edge < v[1]]:
                B.call("split_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "splits": [{"segment_id": i, "times_ms": [edge]}]})
            cur = S.snapshot(k)
        inside = sorted((v[0], v[1], i) for i, v in cur["layouts"].items() if s["start"] <= v[0] and v[1] <= s["end"])
        layer = takes[s["name"]][1][0] if s["name"] in takes else "screen_1"
        if inside:
            B.call("update_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "updates": [
                {"type": "layout", "segment_id": i, "properties": B.behind_props(layer, CTX.cfg)} for a, b, i in inside]})
        holes = []
        t = s["start"]
        for a, b, i in inside:
            if a > t:
                holes.append((t, a))
            t = max(t, b)
        if t < s["end"]:
            holes.append((t, s["end"]))
        if holes:
            B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
                {"type": "layout", "placement": {"range": [a, b]}, "properties": B.behind_props(layer, CTX.cfg)} for a, b in holes]})
        log("%s: take on %s; %d layout piece(s) set to the render layout, holes filled %s" % (s["name"], layer, len(inside), holes))
        s["layer"] = layer
    return verify_behind(tx, k, pre, checks, pre_fr, takes)


def frames_vs_render(k, s, tag):
    A, B_ = s["start"], s["end"]
    beats = [B.grid(A) if A > 0 else B.grid(1), B.grid(A + 100), B.grid(A + 500), B.grid((A + B_) / 2.0), B.grid(B_ - 500),
             B.grid(B_ - 100) if B.grid(B_ - 100) < B_ else B.grid(B_ - 100) - 1000 / 30.0]
    rows = []
    for g in beats:
        n0 = int(round((g - s.get("t0", A)) * 30 / 1000.0))
        far = n0 + 15 if n0 + 15 < s["nfr"] else n0 - 15
        refs = [render_frame(s, n0 + d_) for d_ in (-1, 0, 1)] + [render_frame(s, far)]
        d = B.save_frame(k, g, fpath("%s_%s_%d.png" % (s["name"], tag, round(g))))
        rows.append({"t": round(g, 2), "n": n0, "render_frames": refs, "after": d, "before": d})
    out = B.imgcheck("render-psnr", {"rows": rows, "col_x": G["col_x"]})
    for r, o in zip(rows, out):
        r.update(o)
        allp = o["all"]
        r["psnr_after"] = max(allp[:3])
        r["best"] = max(range(3), key=lambda i: allp[i])
        r["far"] = allp[3]
        # >= 38 dB, or a static hold (the expected frame is at least as close as a frame 15 away) above the floor
        r["specific"] = r["psnr_after"] - allp[3] >= 2.0 or r["psnr_after"] >= 38 or (r["psnr_after"] >= ARGS.psnr and r["psnr_after"] >= allp[3])
    return rows


def verify_behind(tx, k, pre, checks, pre_fr, takes):
    cam = CTX.cfg["camera_layer"]
    results = {}
    for s in SLOTS:
        rows = frames_vs_render(k, s, "a")
        if not all(r["psnr_after"] >= ARGS.psnr and r["specific"] for r in rows):
            other = "screen_2" if s["layer"] == "screen_1" else "screen_1"
            log("%s: frames do not show the render on %s (%s) - trying %s" % (s["name"], s["layer"], [r["psnr_after"] for r in rows], other))
            cur = S.snapshot(k)
            ids = [i for i, v in cur["layouts"].items() if s["start"] <= v[0] and v[1] <= s["end"]]
            try:
                B.call("update_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "updates": [
                    {"type": "layout", "segment_id": i, "properties": B.behind_props(other, CTX.cfg)} for i in ids]})
                rows2 = frames_vs_render(k, s, "b")
                if all(r["psnr_after"] >= ARGS.psnr and r["specific"] for r in rows2):
                    s["layer_fallback"] = other
                    rows = rows2
            except Exception as e:  # noqa: BLE001  (e.g. "screen_2 is not an active visual Layer in the Layout range")
                log("   fallback failed: %s" % e)
        results[s["name"]] = rows
        for r in rows:
            log("%s frame %.2f (render f%d): PSNR %.1f dB (offset %+d; a frame 15 away %.1f dB)" % (
                s["name"], r["t"], r["n"], r["psnr_after"], r["best"] - 1, r["far"]))
        checks.add("%s: every checked frame shows the render's own frame (PSNR >= %.0f left of the camera column, specific)%s" % (
            s["name"], ARGS.psnr, " [layer fallback %s]" % s["layer_fallback"] if s.get("layer_fallback") else ""),
            all(r["psnr_after"] >= ARGS.psnr and r["specific"] for r in rows), str([r["psnr_after"] for r in rows]))
    post = S.snapshot(k)
    nc = {i for i in post["content"] if i not in pre["content"]}
    exp_l = {i: post["layouts"].get(i) for i, v in pre["layouts"].items() if any(v[0] < x["end"] and v[1] > x["start"] for x in SLOTS)}
    tl, to = S.tol_exp(pre, post, "layouts", exp_l), S.tol_exp(pre, post, "overlays")
    exp_l.update(tl)
    log("+-1 ms display rounding accepted on %d layouts / %d overlays: %s" % (len(tl), len(to), sorted(list(tl) + list(to))))
    bad, notes = S.strict_diff(pre, post, {}, exp_l, to, lambda cat, i, v: (cat == "content" and i in nc) or (cat == "layouts"),
                               S.ident, tol_ids=set(pre["content"]))
    for b_ in bad:
        log("  DIFF " + b_[:300])
    checks.add("project-wide diff: only the new render takes and layout pieces around the slots",
               not bad and S.scenes_pm1(pre, post), "%d problems" % len(bad))

    def lmap(lays, cut):
        pts = sorted({0, post["duration"]} | {t for v in lays.values() for t in v[:2]} | cut)
        out = []
        for a, b in zip(pts, pts[1:]):
            hit = [v[2] for v in lays.values() if v[0] <= a and b <= v[1]]
            out.append((a, b, hit[0] if hit else None))
        return out
    cuts = {t for x in SLOTS for t in (x["start"], x["end"])}
    pre_l = dict(pre["layouts"])
    pre_l.update(tl)     # the accepted +-1 ms rounding positions
    pm = [x for x in lmap(pre_l, cuts) if not in_any_slot(x[0], x[1]) and B.grid_hits(x[0], x[1] - 1)]   # frame-free slivers ignored
    qm = [x for x in lmap(post["layouts"], cuts) if not in_any_slot(x[0], x[1]) and B.grid_hits(x[0], x[1] - 1)]
    checks.add("layout picture map outside the slots unchanged", S.merge_map(pm) == S.merge_map(qm),
               str([(a[:2], b[:2]) for a, b in zip(S.merge_map(pm), S.merge_map(qm)) if a != b][:3]))
    for s in SLOTS:
        lays = sorted(v for v in post["layouts"].values() if v[0] < s["end"] and v[1] > s["start"])
        okc = lays and lays[0][0] <= s["start"] and all(
            json.loads(v[2])["sources"][0]["width_ratio"] == 1.0 and json.loads(v[2])["sources"][-1]["layer_id"] == cam
            for v in lays if s["start"] <= v[0])
        checks.add("%s: the slot is covered by render layouts (render full frame + camera tile)" % s["name"], bool(okc), str([(v[:2]) for v in lays]))
    g_ = S.gaps(post, min(x["start"] for x in SLOTS), max(x["end"] for x in SLOTS))
    framed = [(nm, a_, b_, fr[:3]) for nm, gl in g_.items() for a_, b_, fr in gl if fr and in_any_slot(a_, b_)]
    checks.add("no screen / layout gap holding a 30/60 fps frame inside the slots", not framed, str(framed)[:300])
    # camera tile + badge, outside frames
    allrows = [r for rows in results.values() for r in rows]
    tb = B.imgcheck("tilebox", {"pairs": [[r["after"], r["render_frames"][r["best"]]] for r in allrows],
                                "x_lo": int(round(G["tile"][0] - 229.5)), "row_band": [int(round(G["tile"][1] + 70)), int(round(G["tile"][3] - 50))],
                                "col_band": [int(round(G["tile"][0] + 30.5)), int(round(G["tile"][2] - 33))],
                                "y_lo": G["badge_bottom"] + 16})
    tok = [B.tile_ok(b_, CTX.cfg) for b_ in tb]
    log("camera tile boxes (where Borumi differs from the render): %s ; expected %s" % (tb, [round(v) for v in G["tile"]]))
    checks.add("camera tile in place in every checked frame (+-8 px)", all(tok), str(tok))
    outs = []
    for g, p0 in pre_fr.items():
        ins = any(x["start"] <= g < x["end"] for x in SLOTS)
        p1 = B.save_frame(k, g, fpath("post_%s_%d.png" % ("in" if ins else "out", round(g))))
        outs.append((g, p0, p1, ins))
    dif = B.imgcheck("behind-outside", {"rows": outs, "badge_outer": G["badge_outer"], "tile_zone": G["tile_zone"]})
    for (g, _, _, ins), (bx, al_, tl_) in zip(outs, dif):
        log("frame %.2f (%s): badge box diff %.2f, frame outside the camera tile %.3f, camera tile %.2f (the face-tracking "
            "crop restarts at a layout boundary)" % (g, "in slot" if ins else "outside", bx, al_, tl_))
    checks.add("badge unchanged in the slots; frames outside the slots unchanged (camera tile excluded)",
               all(bx < 1.0 for (bx, al_, tl_) in dif) and
               all(al_ < 1.0 for (_, _, _, ins), (bx, al_, tl_) in zip(outs, dif) if not ins), str(dif))
    mont = CTX.path("frames", "place_%s%s.jpg" % (ARGS.label, TAG))
    B.montage([p for r in allrows for p in (r["after"], r["render_frames"][r["best"]])], mont, 4, 480, 270)
    log("montage (Borumi | render): %s" % mont)
    post_lay = dict(post["layouts"])

    def post_check(pre_proj, post_c):
        n2 = {i for i in post_c["content"] if i not in pre_proj["content"]}
        b2, nn = S.strict_diff(pre_proj, post_c, {}, {i: post_lay.get(i) for i in set(pre_proj["layouts"]) | set(post_c["layouts"])},
                               S.tol_exp(pre_proj, post_c, "overlays"),
                               lambda cat, i, v: (cat == "content" and i in n2) or cat == "layouts", S.ident, tol_ids=set(pre_proj["content"]))
        if post_c["layouts"] != post_lay:
            b2.append("layouts differ from the verified tx state")
        return b2, nn
    return post_check, {"mode": MODE, "slots": [{kk: s.get(kk) for kk in ("name", "start", "end", "copy", "media", "layer", "layer_fallback")}
                                                for s in SLOTS], "montage": mont}


# ------------------------------------------------------------------------------------------------ front
def place_front(tx, k, pre, checks):
    for s in SLOTS:
        aud = alpha_audit(s)
        log("%s alpha audit [camera/badge column opaque, outside the panel opaque, panel opaque] per sampled frame: %s "
            "(info: the camera tile and the badge draw above the render; content outside the panel shows over the canvas)" % (s["name"], aud))
        checks.add("%s: render has transparency (alpha < 255 somewhere in every sampled frame)" % s["name"], all(a_[2] < 0.999 for a_ in aud), str(aud))
    pre_fr = {}
    for s in SLOTS:
        A, B_ = s["start"], s["end"]
        for g in (B.grid(A), B.grid(A + 100), B.grid(A + 500), B.grid((A + B_) / 2.0), B.grid(B_ - 500),
                  B.grid(B_ - 100) if B.grid(B_ - 100) < B_ else B.grid(B_ - 100) - 1000 / 30.0):
            pre_fr[(s["name"], g)] = B.save_frame(k, g, fpath("pre_%d.png" % round(g)))
        for g in (B.grid(A - 300), B.grid(B_ + 300)):
            pre_fr[("outside", g)] = B.save_frame(k, g, fpath("pre_%d.png" % round(g)))
    for s in SLOTS:
        A, B_ = s["start"], s["end"]
        s["media"] = mid = al.import_media(tx, s["copy"])
        t_, _ = add_take(tx, k, s, checks, pre, s["strategy"])     # also asserts no repacking: every existing segment unchanged
        s["t0"] = t_[1][1] if t_ else s["start"]
        s["layer"] = t_[1][0] if t_ else "screen_2"
        cur = S.snapshot(k)
        for edge in (A, B_):
            # no split that would leave a piece < 3 ms (Borumi refuses split_too_close_to_boundary; a +-1 ms rounding offset
            # at a card edge is absorbed below)
            for i in [i for i, v in cur["layouts"].items() if v[0] + 3 <= edge <= v[1] - 3]:
                B.call("split_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "splits": [{"segment_id": i, "times_ms": [edge]}]})
            cur = S.snapshot(k)
        inside = sorted((v[0], v[1], i, v[2]) for i, v in cur["layouts"].items() if A - 2 <= v[0] and v[1] <= B_ + 2 and v[1] > A and v[0] < B_
                        and json.loads(v[2]).get("kind") != "fullscreen")
        log("%s: layouts around the slot after the take add + splits: %s" % (s["name"], sorted(
            (v[0], v[1], i, [x.get("layer_id") for x in json.loads(v[2]).get("sources", [])] or json.loads(v[2]).get("layer_id"))
            for i, v in cur["layouts"].items() if v[1] > A - 3000 and v[0] < B_ + 3000)))
        if not inside:
            checks.add("%s: layouts cover the slot" % s["name"], False, "no layout piece inside the slot")
            continue
        checks.add("%s: layouts cover the slot (+-2 ms rounding at the edges)" % s["name"],
                   abs(inside[0][0] - s.get("sandwich_from", A)) <= 2 and abs(inside[-1][1] - B_) <= 2 and
                   all(x[1] == y[0] for x, y in zip(inside, inside[1:])), str([x[:2] for x in inside]))
        # a layout piece that starts BEFORE the render take (scene start at a half ms, take at the next whole ms): Borumi
        # names its sources from the takes active at the piece start (only the footage) -> both screen sources become the
        # footage. Split the piece 3 ms into the render (no frame may fall in the sliver) and sandwich only from there.
        t0 = s["t0"]
        if inside[0][0] <= t0 and not B.grid_hits(inside[0][0], t0 + 3):   # displayed start may hide a half-ms earlier true start
            cut = t0 + 3
            fr_ = B.grid_hits(inside[0][0], cut)
            checks.add("%s: layout starts %d ms before the render take; split at %d leaves a frame-free sliver" % (
                s["name"], t0 - inside[0][0], cut), not fr_, str(fr_))
            B.call("split_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "splits": [{"segment_id": inside[0][2], "times_ms": [cut]}]})
            cur = S.snapshot(k)
            inside = sorted((v[0], v[1], i, v[2]) for i, v in cur["layouts"].items() if cut <= v[0] and v[1] <= B_ + 2 and v[0] < B_
                            and json.loads(v[2]).get("kind") != "fullscreen")
            s["sandwich_from"] = cut
        foot = sorted({v[0] for v in cur["content"].values() if v[0].startswith("screen") and v[3] != mid and v[1] < B_ and v[2] > A})
        checks.add("%s: footage under the slot on one screen layer %s, render on %s" % (s["name"], foot, s["layer"]),
                   len(foot) == 1 and foot[0] != s["layer"])
        s["footage_layer"] = foot[0] if foot else None
        B.call("update_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "updates": [
            {"type": "layout", "segment_id": i, "properties": B.sandwich_props(p_, s["layer"], s["footage_layer"], CTX.cfg)}
            for a, b, i, p_ in inside]})
        log("%s: render take on %s; %d layout piece(s) now [footage panel, render full frame, camera tile]" % (s["name"], s["layer"], len(inside)))
    post = S.snapshot(k)
    nc = {i for i in post["content"] if i not in pre["content"]}
    exp_l = {i: post["layouts"].get(i) for i, v in pre["layouts"].items() if any(v[0] < x["end"] and v[1] > x["start"] for x in SLOTS)}
    tl, to = S.tol_exp(pre, post, "layouts", exp_l), S.tol_exp(pre, post, "overlays")
    exp_l.update(tl)
    log("+-1 ms display rounding accepted on %d layouts / %d overlays: %s" % (len(tl), len(to), sorted(list(tl) + list(to))))
    bad, notes = S.strict_diff(pre, post, {}, exp_l, to, lambda cat, i, v: (cat == "content" and i in nc) or cat == "layouts",
                               S.ident, tol_ids=set(pre["content"]))   # +-1 ms rounding (checked group-consistent in add_take)
    for b_ in bad:
        log("  DIFF " + b_[:300])
    checks.add("project-wide diff: only the render takes + the slot layout pieces", not bad and S.scenes_pm1(pre, post), "%d problems" % len(bad))
    rows = []
    for (nm, g), p0 in pre_fr.items():
        d = B.save_frame(k, g, fpath("%s_%d.png" % (nm, round(g))))
        if nm == "outside":
            rows.append({"name": nm, "t": round(g, 2), "n": -1, "pre": p0, "post": d, "rgba": []})
            continue
        s = [x for x in SLOTS if x["name"] == nm][0]
        n0 = int(round((g - s.get("t0", s["start"])) * 30 / 1000.0))
        rows.append({"name": nm, "t": round(g, 2), "n": n0, "pre": p0, "post": d, "rgba": [render_frame(s, n0 + dd, True) for dd in (-1, 0, 1)]})
    res = B.imgcheck("composite", {"rows": rows, "tile_int": G["tile_int"], "badge_outer": G["badge_outer"]})
    okc, okt, oko = [], [], []
    for r, (ps, tile_d, badge_d, all_d) in zip(rows, res):
        if r["name"] == "outside":
            log("outside frame %.2f: change vs pre %.3f" % (r["t"], all_d))
            oko.append(all_d < 1.0)
            continue
        log("%s frame %.2f (render f%d): frame vs pre+render composite (outside tile + badge) %.1f dB; camera tile change %.2f; "
            "badge box change %.2f" % (r["name"], r["t"], r["n"], ps, tile_d, badge_d))
        okc.append(ps >= ARGS.psnr)
        okt.append(tile_d < 1.5 and badge_d < 1.5)
    checks.add("frames = pre-frame composited with the render's own RGBA frame (alpha respected, render above the footage; "
               "PSNR >= %.0f)" % ARGS.psnr, all(okc), str([x[0] for x in res]))
    checks.add("camera tile and badge unchanged = drawn ABOVE the render", all(okt), str([x[1:3] for x in res]))
    checks.add("frames just outside the slots unchanged", all(oko), str(oko))
    g_ = S.gaps(post, min(x["start"] for x in SLOTS), max(x["end"] for x in SLOTS))
    framed = [(nm, a_, b_, fr[:3]) for nm, gl in g_.items() for a_, b_, fr in gl if fr and nm == "layout"]
    checks.add("no layout gap holding a 30/60 fps frame around the slots", not framed, str(framed)[:300])
    mont = CTX.path("frames", "place_%s%s.jpg" % (ARGS.label, TAG))
    B.montage([p for r in rows if r["name"] != "outside" for p in (r["pre"], r["post"])], mont, 4, 480, 270)
    log("montage (before | after): %s" % mont)
    post_lay = dict(post["layouts"])

    def post_check(pre_proj, post_c):
        n2 = {i for i in post_c["content"] if i not in pre_proj["content"]}
        b2, nn = S.strict_diff(pre_proj, post_c, {}, {i: post_lay.get(i) for i in set(pre_proj["layouts"]) | set(post_c["layouts"])},
                               S.tol_exp(pre_proj, post_c, "overlays"),
                               lambda cat, i, v: (cat == "content" and i in n2) or cat == "layouts", S.ident, tol_ids=set(pre_proj["content"]))
        if post_c["layouts"] != post_lay:
            b2.append("layouts differ from the verified tx state")
        return b2, nn
    return post_check, {"mode": MODE, "slots": [{kk: s.get(kk) for kk in ("name", "start", "end", "t0", "copy", "media", "layer", "footage_layer")}
                                                for s in SLOTS], "montage": mont}


# ------------------------------------------------------------------------------------------------ main
def main():
    global ARGS, CTX, MODE, SLOTS, FD, WD, G, TAG
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("slots", nargs="?", help="SLOTS.json (or use --mode/--name/--render/--start/--end for one slot)")
    ap.add_argument("--mode", choices=("behind", "front"))
    ap.add_argument("--name")
    ap.add_argument("--render")
    ap.add_argument("--start", type=int)
    ap.add_argument("--end", type=int)
    ap.add_argument("--psnr", type=float, default=30.0, help="frame-match floor (30 for real renders; a saturated test-bar dummy needs ~22)")
    B.add_common_args(ap)
    ARGS = ap.parse_args()
    if ARGS.slots:
        cfg = B.load_json(ARGS.slots)
        MODE = cfg["mode"]
        slots = cfg["slots"]
        ARGS.label = os.path.splitext(os.path.basename(ARGS.slots))[0]
    else:
        if not (ARGS.mode and ARGS.name and ARGS.render and ARGS.start is not None and ARGS.end is not None):
            ap.error("give SLOTS.json, or --mode --name --render --start --end")
        MODE = ARGS.mode
        slots = [{"name": ARGS.name, "render": ARGS.render, "start": ARGS.start, "end": ARGS.end}]
        ARGS.label = ARGS.name
    if MODE not in ("behind", "front"):
        ap.error("mode must be behind or front")
    for s in slots:
        if not (isinstance(s["start"], int) and isinstance(s["end"], int) and s["end"] > s["start"]):
            ap.error("slot %s: start / end must be integer ms with end > start" % s.get("name"))
        s["render"] = os.path.abspath(os.path.expanduser(s["render"]))
    CTX = B.context(ARGS)
    G = B.geometry_payload(CTX.cfg)
    TAG = "_dry" if ARGS.dry else ""
    WD = CTX.dir("place")
    FD = CTX.dir("frames", "place_%s%s" % (ARGS.label, TAG))
    slots = sorted(slots, key=lambda s: s["start"])
    for s in slots:
        exact_copy(s)
    results = []
    for s0 in slots:
        SLOTS = [s0]
        s0["strategy"] = preflight(s0)
        log("%s: placement strategy %s" % (s0["name"], s0["strategy"][0]))
        info = txrun.run(CTX, "place_%s" % s0["name"], body, ARGS.dry,
                         lambda i: "Place the %s render %s at %s-%s." % (MODE, s0["name"], B.fmt_ts(s0["start"]), B.fmt_ts(s0["end"])),
                         ARGS.lock_timeout)
        info["strategy"] = s0["strategy"][0]
        results.append(info)
        print(json.dumps(info, default=str))
        if not info.get("checks_ok", True) and not ARGS.dry:
            break
    return 0 if all(r.get("checks_ok", True) for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
