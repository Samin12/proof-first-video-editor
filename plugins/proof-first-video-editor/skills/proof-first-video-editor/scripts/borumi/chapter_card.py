#!/usr/bin/env python3
"""chapter_card.py - insert ONE chapter card (a silent 1.6 s render; 1601 ms on the timeline incl. the 1 ms lead-in) plus
its click sound at native level and a fullscreen layout [T, T+1601] (no badge, never a whoosh), in one verified tx.

usage:
  chapter_card.py --name 03-my-section --before SCENE_ID [--dry]     card = WORK/cards/<name>/card-silent.mp4,
  chapter_card.py --name 03-my-section --mid T [--dry]               title from WORK/cards/rows.json
  chapter_card.py --card card-silent.mp4 --title "My Section" (--before SCENE_ID | --mid T) [--click click.wav] [--dry]

  --before SCENE : T = the scene's start. BUILD THE CARD SCENE AT THE PROJECT END (create_scenes destination end, take at
                   E+1 -> a 1601 ms scene), then move_scenes before SCENE: an exact +1601 shift from T. Then add the click
                   (AFTER the move: added at the project end it belongs to the last scene and falls off) and the layout.
                   (The older create_scenes-before flow pads an empty scene to 3000 ms, and filling it shifted LATER card
                   scenes by +1 ms.)
  --mid T        : an INTEGRAL T inside a scene, in a sentence gap (no word may span T): split content / overlays /
                   layouts at T, move the scene's right block to T+1601 (move_timeline_segments at_time), move sound
                   effects that did not ride along (+1601), re-place later card layouts that came back re-anchored +1 ms,
                   check the gap is empty on every layer, then add the card take at T+1 and the click at T.
Insert cards from the LATEST to the EARLIEST so earlier times stay valid. Layouts spanning T are split at T first
(pieces < 10 ms cannot be split: the card layout absorbs them).

Checks: strict project-wide diff (exactly +1601 from T on; only the T splits, the card, its click and its layout are
new; +-1 ms display rounding tolerated only when every layer of a group moved together), no new displayed camera gap
holding a 30/60 fps frame, the layout picture map = before shifted (a dropped screen_N name where no screen take exists
is accepted), duration +1601, scenes, no layer repacking, transcript unchanged, one click at T at native level, nothing
else inside the card span (no badge), no transition sound anywhere, no new layout gap holding a frame, frames before
(identical) / after (same shot, or the panel = the expected source frame) / on the card (white: mean luma > 200).
Then run card_lead_in.py when a 30/60 fps frame lands in the 1 ms lead-in [T, T+1) (it renders the bare canvas).
Exit: 0 ok, 2 checks failed, 3 error, 4 collision (all aborted). Receipt: WORK/cards/<name>/applied.json.
"""
import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwlib as B  # noqa: E402
import snapshot as S  # noqa: E402
import txrun  # noqa: E402

al = B.al
log = B.log


def slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:40] or "card"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--name", help="card row name in WORK/cards/rows.json (card file WORK/cards/<name>/card-silent.mp4)")
    ap.add_argument("--card", help="silent card render (overrides the --name file)")
    ap.add_argument("--title", help="card title (default: from rows.json); the card scene is named 'Chapter: <title>'")
    ap.add_argument("--click", help="click sound (default: config click_wav, else the private chapter-transition click)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--before", help="scene id: insert the card as its own scene right before this scene")
    g.add_argument("--mid", type=int, help="integral project ms inside a scene (a sentence gap)")
    B.add_common_args(ap)
    args = ap.parse_args()
    ctx = B.context(args)
    cfg = ctx.cfg
    G = B.geometry_payload(cfg)
    C = int(cfg["card_ms"])
    title = args.title
    if args.name and not title:
        rows = B.load_json(os.path.join(ctx.work, "cards", "rows.json"))["rows"]
        hit = [r for r in rows if r["name"] == args.name]
        if not hit:
            B.die("card %s not in WORK/cards/rows.json" % args.name)
        title = hit[0]["title"]
    if not title:
        ap.error("--title is required without --name")
    name = args.name or slug(title)
    card = os.path.abspath(os.path.expanduser(args.card)) if args.card else os.path.join(ctx.work, "cards", name, "card-silent.mp4")
    if not os.path.exists(card):
        B.die("card render not found: %s" % card)
    click = os.path.abspath(os.path.expanduser(args.click)) if args.click else B.click_wav(cfg)
    SCENE_NAME = "Chapter: " + title
    tag = "_dry" if args.dry else ""
    FD = ctx.dir("frames", "card_%s%s" % (name, tag))
    strict_media = set(cfg.get("strict_footage_media") or [])

    def fp(n):
        return os.path.join(FD, n)

    def body(tx, k, pre, checks):
        checks.add("no scene named %r yet" % SCENE_NAME, all(s[1] != SCENE_NAME for s in pre["scenes"]))
        if args.before:
            tgt = [s for s in pre["scenes"] if s[0] == args.before]
            checks.add("target scene %s exists" % args.before, len(tgt) == 1)
            if not tgt:
                return None, {"T": None, "end": None}
            T = tgt[0][2]
            host = None
        else:
            T = args.mid
            hs = [s for s in pre["scenes"] if s[2] <= T < s[3]]
            host = hs[0] if hs else None
            checks.add("T=%d inside scene %s, >= 500 ms from its edges" % (T, host[0] if host else None),
                       bool(host) and T - host[2] > 500 and host[3] - T > 500, str(host))
            if not host:
                return None, {"T": T, "end": T + C}
        mics = al.mic_layers(k)
        ws = al.words_in(k, max(0, T - 4000), T + 4000, mics)
        prev = [w for w in ws if w[1] <= T]
        nxt = [w for w in ws if w[0] >= T]
        across = [w for w in ws if w[0] < T < w[1]]
        log("T=%d: words before %s | after %s | across %s" % (T, [w[2] for w in prev[-6:]], [w[2] for w in nxt[:6]], across))
        checks.add("no word spans T", not across, str(across))
        words0 = [w[2] for w in S.words_all(k)]
        span_ct = [(i, v) for i, v in pre["content"].items() if v[1] < T < v[2]]
        span_ov = [(i, v) for i, v in pre["overlays"].items() if v[1] < T < v[2]]
        span_ly = [(i, v) for i, v in pre["layouts"].items() if v[0] < T < v[1]]
        log("spanning T: content %s overlays %s layouts %s" % ([i for i, _ in span_ct], [i for i, _ in span_ov], [(i, v[:2]) for i, v in span_ly]))
        if args.before:
            checks.add("no content spans the scene boundary", not span_ct)
        pre_g = {"before": B.grid(T - 300), "after": B.grid(T + 200), "after2": B.grid(T + 1500)}
        pre_fr = {nm: B.save_frame(k, gt, fp("pre_%s_%d.png" % (nm, round(gt)))) for nm, gt in pre_g.items()}
        card_id = al.import_media(tx, card)
        click_id = al.import_media(tx, click)
        tiny = [(i, v) for i, v in span_ly if v[1] - T < 10 or T - v[0] < 10]
        if tiny:
            log("layouts with a < 10 ms piece past / before T (cannot be split; the card layout absorbs it): %s" % [(i, v[:2]) for i, v in tiny])
        span_ly = [(i, v) for i, v in span_ly if (i, v) not in tiny]
        for i, v in span_ly + span_ov:
            B.call("split_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "splits": [{"segment_id": i, "times_ms": [T]}]})
        if args.before:
            D0 = pre["duration"]
            B.call("create_scenes", {"tx_id": tx.id, "timeline_hash": tx.hash(), "destination": {"type": "end"}, "scenes": [{"name": SCENE_NAME}]})
            new = al.scenes(k)[-1]
            checks.add("card scene created at the end", new["name"] == SCENE_NAME and new["start_ms"] == D0, str(new))
            E = new["start_ms"]
            B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
                {"type": "take", "placement": {"at_ms": E + 1}, "content": {"media_id": card_id, "video_kind": "screen"}}]})
            new = al.scene_by_id(k, new["id"])
            checks.add("card scene is %d ms at the end" % C, new["end_ms"] - new["start_ms"] == C, str(new))
            B.call("move_scenes", {"tx_id": tx.id, "timeline_hash": tx.hash(), "scene_ids": [new["id"]],
                                   "destination": {"type": "before_scene", "scene_id": args.before}})
            new = al.scene_by_id(k, new["id"])
            checks.add("card scene now at [%d, %d]" % (T, T + C), (new["start_ms"], new["end_ms"]) == (T, T + C), str(new))
            new_scene = new["id"]
            # the click goes in AFTER the move: added at the project end it was attached to the previous (last) scene,
            # stayed behind on move_scenes and fell off the end
            B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
                {"type": "sound_effect", "placement": {"at_ms": T}, "content": {"media_id": click_id}}]})
        else:
            done = set()
            for i, v in span_ct:
                if v[6] in done:
                    continue
                B.call("split_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "splits": [{"segment_id": i, "times_ms": [T]}]})
                done.add(v[6])
            s1 = S.snapshot(k)
            checks.add("no content spans T after the split", not [i for i, v in s1["content"].items() if v[1] < T < v[2]])
            right = [(i, v) for i, v in s1["content"].items() if v[1] >= T and v[2] <= host[3]]
            checks.add("the right-side block starts exactly at T", bool(right) and min(v[1] for i, v in right) == T)
            B.call("move_timeline_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "segment_ids": [i for i, v in right],
                                              "destination": {"type": "at_time", "start_ms": T + C}})
            s2 = S.snapshot(k)
            # sound effects at/after T are not carried by the block move: move them +C
            fixed_ov = []
            for i, v in pre["overlays"].items():
                if v[1] >= T and i in s2["overlays"] and s2["overlays"][i][1] != v[1] + C:
                    B.call("move_timeline_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "segment_ids": [i],
                                                      "destination": {"type": "at_time", "start_ms": v[1] + C}})
                    fixed_ov.append((i, s2["overlays"][i][1], v[1] + C))
            # later card layouts (fullscreen) come back re-anchored to the card take start (+1 ms): replace them, same
            # props, exact +C
            s2 = S.snapshot(k)
            fixed_ly = []
            for i, v in pre["layouts"].items():
                p_ = json.loads(v[2])
                if v[0] >= T and p_.get("kind") == "fullscreen" and i in s2["layouts"] and s2["layouts"][i][:2] != (v[0] + C, v[1] + C):
                    B.call("delete_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "segment_ids": [i], "ripple": False})
                    B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
                        {"type": "layout", "placement": {"range": [v[0] + C, v[1] + C]}, "properties": p_}]})
                    fixed_ly.append((i, s2["layouts"][i][:2], (v[0] + C, v[1] + C)))
            log("after the block move: sound effects moved %s ; card layouts re-placed %s" % (fixed_ov, fixed_ly))
            s2 = S.snapshot(k)
            gap = [(c, i, v) for c in ("content", "overlays") for i, v in s2[c].items() if v[1] < T + C and v[2] > T] + \
                  [("layouts", i, v) for i, v in s2["layouts"].items() if v[0] < T + C and v[1] > T]
            checks.add("the gap [%d, %d] is empty on every layer" % (T, T + C), not gap, str(gap)[:300])
            B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
                {"type": "take", "placement": {"at_ms": T + 1}, "content": {"media_id": card_id, "video_kind": "screen"}},
                {"type": "sound_effect", "placement": {"at_ms": T}, "content": {"media_id": click_id}}]})
            new_scene = None
        s3 = S.snapshot(k)
        take = [(i, v) for i, v in s3["content"].items() if v[3] == card_id]
        checks.add("card take at [%d, %d]" % (T + 1, T + C), len(take) == 1 and take[0][1][1:3] == (T + 1, T + C), str(take))
        layer = take[0][1][0] if take else "screen_1"
        props = B.fullscreen_props(layer)
        B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
            {"type": "layout", "placement": {"range": [T, T + C]}, "properties": props}]})
        post = S.snapshot(k)
        # ---- verification
        sh = lambda t, e=False: t + C if (t > T if e else t >= T) else t  # noqa: E731
        exp_c = {i: post["content"].get(i) for i, _ in span_ct}   # checked below
        exp_o = {i: post["overlays"].get(i) for i, _ in span_ov}
        exp_l = {i: post["layouts"].get(i) for i in pre["layouts"]}  # by the layout map below
        pool_c = [(v[0], T + C, v[2] + C, v[3]) for i, v in span_ct]

        def new_ok(cat, i, v):
            if cat == "content":
                if v[3] == card_id and v[1:3] == (T + 1, T + C):
                    return True
                hit = [p for p in pool_c if (v[0], v[1], v[2], v[3]) == p]
                if hit:
                    pool_c.remove(hit[0])
                    return True
                return False
            if cat == "overlays":
                return (v[4] == click_id and v[1] == T) or any(v[0] == w[0] and v[1:3] == (T + C, w[2] + C) and v[4] == w[4] for _, w in span_ov)
            return cat == "layouts"
        # fractional underlying times (an app edit at a sub-ms time shifted everything after it by ~x.5 ms): a +1601 shift
        # displays as +1601 / +1602. Tolerate +-1 ms on content from T on, but every layer of a group must move together.
        TOLC = {i for i, v in pre["content"].items() if v[1] >= T}
        bad, notes = S.strict_diff(pre, post, exp_c, exp_l, exp_o, new_ok, sh, tol_ids=TOLC)
        gshift = {}
        for i in TOLC:
            w = post["content"].get(i)
            if w:
                gshift.setdefault(pre["content"][i][6], set()).add((w[1] - pre["content"][i][1], w[2] - pre["content"][i][2]))
        mixed = {g_: v_ for g_, v_ in gshift.items() if len(v_) > 1}
        n_odd = sum(1 for v_ in gshift.values() if v_ != {(C, C)})
        checks.add("content from T on: every group's layers moved together (%d of %d groups show the +-1 ms display rounding)" % (
            n_odd, len(gshift)), not mixed, str(list(mixed.items())[:4]))

        def cam_gaps(sn, lo, hi):
            segs_ = sorted((v[1], v[2]) for v in sn["content"].values() if v[0] == cfg["camera_layer"] and v[2] > lo and v[1] < hi)
            return [(a[1], b[0]) for a, b in zip(segs_, segs_[1:]) if b[0] > a[1]]
        pre_g_ = {(sh(a), sh(b, True)) for a, b in cam_gaps(pre, 0, pre["duration"])}
        newg = [(a, b) for a, b in cam_gaps(post, 0, post["duration"]) if not any(abs(a - x) <= 1 and abs(b - y) <= 1 for x, y in pre_g_)
                and not (a < T + C + 10 and b > T - 10)]
        framed_g = [(a, b, [round(n * 1000.0 / f_, 2) for f_ in (30, 60) for n in range(math.ceil(a * f_ / 1000.0 - 0.5), math.ceil(b * f_ / 1000.0 + 0.5))
                            if a - 0.5 <= n * 1000.0 / f_ < b + 0.5]) for a, b in newg]
        framed_g = [x for x in framed_g if x[2]]
        log("displayed camera-layer gaps that are new: %s ; holding a frame (+-0.5 ms): %s" % (newg[:8], framed_g[:4]))
        checks.add("no NEW displayed camera gap holding a 30/60 fps frame (rounding gaps are frame-free)", not framed_g, str(framed_g)[:300])
        for n_ in notes:
            log("  note: " + n_)
        for b_ in bad:
            log("  DIFF " + b_[:300])
        checks.add("project-wide diff: exact +%d from T on; only the T splits, the card, its click and layout are new" % C, not bad,
                   "%d problems" % len(bad))
        okc = all(post["content"].get(i) and post["content"][i][1:3] == (v[1], T) for i, v in span_ct) and not pool_c
        checks.add("content that spanned T is now [start, T] + [T+%d, end+%d]" % (C, C), okc or not span_ct, str(pool_c))
        pre_l = dict(pre["layouts"])
        for i, v in tiny:
            pre_l[i] = (v[0], T, v[2]) if v[1] - T < 10 else (T + C, v[1], v[2])   # absorbed by the card
        for i, v in pre["layouts"].items():
            if T < v[0] < T + 10:   # a layout starting < 10 ms after T (a 1 ms offset left by an app edit) now starts at the card end
                pre_l[i] = (T, v[1], v[2])   # sh(T) = T + C
        for i, v in span_ly:   # split at T (the right piece moves +C with everything after T)
            pre_l[i] = (v[0], T, v[2])
            pre_l[i + "_R"] = (T, v[1], v[2])
        want_map = S.layout_map(pre_l, 0, post["duration"], sh)
        want_map = [x for x in want_map if not (x[0] >= T and x[1] <= T + C)]
        got_map = S.layout_map(post["layouts"], 0, post["duration"])
        card_piece = [x for x in got_map if x[0] == T and x[1] == T + C]
        got_map = [x for x in got_map if not (x[0] == T and x[1] == T + C)]

        def norm(M):
            return [(a, b, (json.dumps(json.loads(p), sort_keys=True) if p else None)) for a, b, p in M]
        ok_map = len(want_map) == len(got_map)
        renamed = []
        for (a1, b1, p1), (a2, b2, p2) in zip(norm(want_map), norm(got_map)):
            if (a1, b1, p1) == (a2, b2, p2):
                continue
            no_screen = not any(v[0].startswith("screen") and v[1] < b2 and v[2] > a2 for v in post["content"].values())
            if (a1, b1) == (a2, b2) and B.drop_screen_names(p1) == B.drop_screen_names(p2) and no_screen:
                renamed.append((a2, b2))   # Borumi dropped the panel's screen layer name where no screen take exists: same render
            else:
                ok_map = False
        if renamed:
            log("layout pieces whose panel screen name Borumi dropped (no screen content there, renders the same): %s" % renamed)
        checks.add("layout picture map = before, shifted +%d from T; the card layout exactly [T, T+%d]" % (C, C),
                   ok_map and len(card_piece) == 1 and json.loads(card_piece[0][2]) == props,
                   str([(a[:2], b[:2]) for a, b in zip(norm(want_map), norm(got_map)) if a != b][:3]))
        checks.add("duration +%d" % C, post["duration"] == pre["duration"] + C, "%d -> %d" % (pre["duration"], post["duration"]))
        nm_ = lambda n: n if not n.startswith("Scene ") else "Scene #"   # noqa: E731  (unnamed scenes are renumbered by position)
        exp_sc = [(x[0], nm_(x[1]), sh(x[2]), sh(x[3], True)) for x in pre["scenes"]]
        got_sc = [(y[0], nm_(y[1]), y[2], y[3]) for y in post["scenes"] if y[0] != new_scene]
        checks.add("scenes: card inserted%s, all others exact" % (" before %s" % args.before if args.before else " inside %s" % host[0]),
                   exp_sc == got_sc, str([(a, b) for a, b in zip(exp_sc, got_sc) if a != b][:3]) + (" len %d vs %d" % (len(exp_sc), len(got_sc))))
        layer_of = {i: v[0] for i, v in pre["content"].items()}
        swaps = [i for i, v in post["content"].items() if i in layer_of and layer_of[i] != v[0]]
        checks.add("no layer repacking", not swaps, str(swaps[:6]))
        words1 = [w[2] for w in S.words_all(k)]
        checks.add("transcript unchanged (%d words)" % len(words0), words0 == words1, "%d -> %d" % (len(words0), len(words1)))
        clicks = [i for i, v in post["overlays"].items() if i not in pre["overlays"] and v[4] == click_id]
        checks.add("one click at T, native level", len(clicks) == 1 and post["overlays"][clicks[0]][1] == T
                   and json.loads(post["overlays"][clicks[0]][3]).get("volume_filter") in (None, 0))
        in_card = [(c, i, v[:3]) for c in ("content", "overlays") for i, v in post[c].items() if v[1] < T + C and v[2] > T
                   and not (c == "content" and v[3] == card_id) and not (c == "overlays" and v[4] == click_id)] + \
                  [("layouts", i, v[:2]) for i, v in post["layouts"].items() if v[0] < T + C and v[1] > T and (v[0], v[1]) != (T, T + C)]
        checks.add("the card span holds only the card, its click and its layout (no badge)", not in_card, str(in_card)[:300])
        whoosh = [i for i, v in post["layouts"].items() if json.loads(v[2]).get("transition_sound")]
        checks.add("no layout with a transition sound (never a whoosh)", not whoosh)
        gall = S.gaps(post, 0, post["duration"])["layout"]
        gpre = S.gaps(pre, 0, pre["duration"])["layout"]
        new_framed = [(a_, b_, fr[:3]) for a_, b_, fr in gall if fr and not any(sh(a0) == a_ and sh(b0, True) == b_ for a0, b0, f0 in gpre)]
        checks.add("no NEW layout gap holding a 30/60 fps frame anywhere in the project", not new_framed, str(new_framed)[:300])
        # frames: before (unchanged), card middle, after (= pre 'after' frames shifted)
        rows = [("before", pre_g["before"], pre_g["before"]), ("after", pre_g["after"], pre_g["after"] + C),
                ("after2", pre_g["after2"], pre_g["after2"] + C)]
        outp = [(nm, pre_fr[nm], B.save_frame(k, g1, fp("post_%s_%d.png" % (nm, round(g1))))) for nm, g0, g1 in rows]
        cards = [B.save_frame(k, B.grid(t), fp("card_%d.png" % round(B.grid(t)))) for t in (T + 200, T + 800, T + 1500)]
        dif = B.imgcheck("meandiff", {"pairs": [[x, y, n != "before"] for n, x, y in outp]})
        lum = B.imgcheck("luma", {"paths": cards, "mode": "RGB"})
        log("frames vs pre (mean abs diff): %s ; card frames mean luma %s" % (list(zip([o[0] for o in outp], dif)), lum))
        # panel frames after the card: a moving source (a handheld device) defeats a pixel diff across the 30 fps grid
        # offset, so check the panel against the source frame at the expected media time instead
        src_ok = {}
        for (nm, g0, g1), (nm2, p0, p1) in zip(rows, outp):
            if nm == "before":
                continue
            gpost = B.grid(g1)
            L_ = [v for v in post["layouts"].values() if v[0] <= gpost < v[1]]
            srcs = [x.get("layer_id") for x in json.loads(L_[0][2]).get("sources", [])] if L_ else []
            scr = [v for v in post["content"].values() if v[0].startswith("screen") and v[0] in srcs and v[1] <= gpost < v[2]]
            if not scr:
                continue
            v = scr[0]
            mf = B.media_file(ctx.medias_dir, v[3], (".mov", ".mp4"))
            if not mf:
                log("frame %s: no media file for %s under %s (set medias_dir): pixel check only" % (nm, v[3], ctx.medias_dir))
                continue
            mt = (v[4] + (gpost - v[1])) / 1000.0
            strict = v[3] in strict_media
            cands = B.src_frames(mf, mt, fp("src_%s" % nm), span=0.13 if strict else 0.25)
            exp = max([c for c in cands if c[0] <= mt + 1e-6], key=lambda c: c[0], default=None)
            mode = "cover" if not json.loads(L_[0][2])["sources"][0].get("lock_aspect_ratio") else "fit"
            ps = B.imgcheck("panelcmp", {"rows": [{"borumi": p1, "cands": [c[1] for c in cands], "mode": mode}], "panel": G["panel"]})[0]
            e_ = ps[[c[0] for c in cands].index(exp[0])] if exp else None
            floor = 26 if strict else 22   # recordings: Borumi draws the cursor separately, PSNR ~24-30
            src_ok[nm] = bool(e_ is not None and e_ >= floor and e_ >= max(ps) - 0.5)
            log("frame %s %.2f: panel = %s %s media %.3f s: expected source frame %.1f dB (best %.1f, mode %s)" % (
                nm, gpost, v[0], v[3], mt, e_ or -1, max(ps) if ps else -1, mode))
        for j, nm in enumerate([o[0] for o in outp]):
            if nm in src_ok:
                dif[j] = 0.0 if src_ok[nm] else 99.0
        checks.add("frames before / after the card match the pre-insert render (before: identical; after: same shot, 96x54 mean "
                   "diff < 8, or the panel shows the expected source frame)", dif[0] < 1.0 and all(d_ < 8.0 for d_ in dif[1:]), str(dif))
        checks.add("card frames are the white card (mean luma > 200)", all(l_ > 200 for l_ in lum), str(lum))
        mont = ctx.path("frames", "card_%s%s.jpg" % (name, tag))
        B.montage([outp[0][2], cards[0], cards[1], cards[2], outp[1][2], outp[2][2]], mont, 3, 480, 270)
        log("montage: %s" % mont)
        lead_hits = B.grid_hits(T, T + 1)
        if lead_hits:
            log("NOTE: a 30/60 fps frame lands in the 1 ms lead-in [%d, %d): %s -> run card_lead_in.py after the commit" % (T, T + 1, lead_hits))
        post_lay = dict(post["layouts"])

        def post_check(pre_proj, post_c):
            pool_c.extend([(v[0], T + C, v[2] + C, v[3]) for i, v in span_ct])
            b2, n2 = S.strict_diff(pre_proj, post_c, {i: post["content"].get(i) for i, _ in span_ct},
                                   {i: post_lay.get(i) for i in set(pre_proj["layouts"]) | set(post_c["layouts"])},
                                   {i: post["overlays"].get(i) for i, _ in span_ov}, new_ok, sh, tol_ids=TOLC)
            if post_c["layouts"] != post_lay:
                b2.append("layouts differ from the verified tx state")
            return b2, n2
        return post_check, {"name": name, "title": title, "T": T, "end": T + C, "scene_id": new_scene, "card_media_id": card_id,
                            "click_media_id": click_id, "layer": layer, "montage": mont, "lead_in_frames": lead_hits}

    info = txrun.run(ctx, "card_" + name, body, args.dry, lambda i: "Insert the '%s' chapter card (1.6 s, click sound) at %s%s." % (
        title, B.fmt_ts(i["T"]) if i.get("T") is not None else "?", " (new scene)" if args.before else " (mid-scene)"), args.lock_timeout)
    if info.get("commit_id"):
        B.write_json(os.path.join(ctx.work, "cards", name, "applied.json"), {
            "name": name, "title": title, "mode": "before" if args.before else "mid", "where": args.before or args.mid,
            "start_ms": info["T"], "end_ms": info["end"], "scene_id": info["scene_id"], "card_media_id": info["card_media_id"],
            "click_media_id": info["click_media_id"], "tx": info["commit_id"],
            "method": "chapter_card.py (%s)" % ("build at end + move_scenes" if args.before else "split + block move")})
    print(json.dumps(info, default=str))
    return 0 if info.get("checks_ok") else 2


if __name__ == "__main__":
    sys.exit(main())
