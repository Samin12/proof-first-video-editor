#!/usr/bin/env python3
"""card_lead_in.py - cover a chapter card's 1 ms lead-in [T, T+1) when a 30/60 fps frame lands in it.

usage: card_lead_in.py --name CARD_NAME T [--dry]          card = WORK/cards/<name>/card-silent.mp4
       card_lead_in.py --card card-silent.mp4 T [--dry]

Why: a card's take starts 1 ms after the card layout (T+1). When a 30 or 60 fps frame time falls inside [T, T+1)
(e.g. T = 516633 with the 30 fps frame at 516633.33), that one exported frame shows the BARE CANVAS: a one-frame dark
flash before the white card. Fix: a full-frame still of the card's own frame 0 as a media_overlay [T, T+10] (instant
in / out, ZERO shadow: media overlays get a default drop shadow otherwise). It is identical to the card's first 10 ms.

Checks: the card layout + its (card_ms - 1) ms take are at T (+1); a frame really lands in the lead-in; only the new
overlay appears project-wide (strict diff); the lead-in frame now shows the card (luma > 200, close to the card's frame
100 ms later); the card's later frames unchanged. Exit: 0 ok, 2 checks failed, 3 error, 4 collision (all aborted).
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwlib as B  # noqa: E402
import snapshot as S  # noqa: E402
import txrun  # noqa: E402

al = B.al
log = B.log


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("t", type=int, help="the card start T (its fullscreen layout start)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", help="card name (WORK/cards/<name>/card-silent.mp4)")
    g.add_argument("--card", help="the silent card render")
    B.add_common_args(ap)
    args = ap.parse_args()
    ctx = B.context(args)
    T = args.t
    C = int(ctx.cfg["card_ms"])
    name = args.name or os.path.splitext(os.path.basename(args.card))[0]
    card = os.path.abspath(os.path.expanduser(args.card)) if args.card else os.path.join(ctx.work, "cards", args.name, "card-silent.mp4")
    png = ctx.path("place", "%s_frame0.png" % name)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", card, "-frames:v", "1", png], check=True)
    tag = "_dry" if args.dry else ""
    FD = ctx.dir("frames", "cardlead_%s%s" % (name, tag))

    def body(tx, k, pre, checks):
        card_l = [v for v in pre["layouts"].values() if json.loads(v[2]).get("kind") == "fullscreen" and abs(v[0] - T) <= 1]
        take = [v for v in pre["content"].values() if v[0].startswith("screen") and abs(v[1] - (T + 1)) <= 1 and v[2] - v[1] == C - 1]
        checks.add("card layout + %d ms card take at %d (+1)" % (C - 1, T), len(card_l) == 1 and len(take) == 1, "%s %s" % (card_l, take))
        hits = B.grid_hits(T, T + 1)
        hits = [h for h in hits if T <= h < T + 1.5]
        checks.add("a 30/60 fps frame lands in the lead-in [%d, %d): %s" % (T, T + 1, hits), bool(hits))
        g0, g1, g2 = B.grid(T), B.grid(T + 100), B.grid(T + 1500)
        pre_fr = {g_: B.save_frame(k, g_, os.path.join(FD, "pre_%d.png" % round(g_))) for g_ in (g0, g1, g2)}
        mid = al.import_media(tx, png)
        B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": [
            {"type": "media_overlay", "placement": {"range": [T, T + 10]}, "content": {"media_id": mid}, "properties": B.STILL_OVERLAY_PROPS}]})
        post = S.snapshot(k)
        new = {i: v for i, v in post["overlays"].items() if i not in pre["overlays"]}
        checks.add("one new full-frame still overlay [%d, %d]" % (T, T + 10), len(new) == 1 and list(new.values())[0][1:3] == (T, T + 10), str(new))
        bad, notes = S.strict_diff(pre, post, {}, {}, {}, lambda cat, i, v: cat == "overlays" and i in new, S.ident)
        for b_ in bad:
            log("  DIFF " + b_[:300])
        checks.add("project-wide diff: only the still overlay", not bad and post["duration"] == pre["duration"] and post["scenes"] == pre["scenes"],
                   "%d problems" % len(bad))
        post_fr = {g_: B.save_frame(k, g_, os.path.join(FD, "post_%d.png" % round(g_))) for g_ in (g0, g1, g2)}
        l0, l1 = B.imgcheck("luma", {"paths": [pre_fr[g0], post_fr[g0]]})
        d = B.imgcheck("meandiff", {"pairs": [[post_fr[g0], post_fr[g1]], [pre_fr[g1], post_fr[g1]], [pre_fr[g2], post_fr[g2]]]})
        log("lead-in frame %.2f: luma %.1f -> %.1f; vs the card's frame at +100 ms %.3f; frames at +100 / +1500 ms changed %.3f / %.3f" % (
            g0, l0, l1, d[0], d[1], d[2]))
        checks.add("the lead-in frame now shows the card (white, like the card's next frames)", l1 > 200 and d[0] < 6, "luma %.1f diff %.2f" % (l1, d[0]))
        checks.add("the card's later frames unchanged", d[1] < 0.5 and d[2] < 0.5, str(d[1:]))

        def post_check(pre_proj, post_c):
            n2 = {i: v for i, v in post_c["overlays"].items() if i not in pre_proj["overlays"]}
            return S.strict_diff(pre_proj, post_c, {}, {}, {}, lambda cat, i, v: cat == "overlays" and i in n2, S.ident)
        return post_check, {"card": name, "T": T, "media": mid, "lead_in_luma": [l0, l1]}

    info = txrun.run(ctx, "cardlead_%s" % name, body, args.dry,
                     lambda i: "Cover the 1 ms lead-in of the '%s' card with its own first frame (removes a one-frame flash of the "
                               "empty canvas)." % name, args.lock_timeout)
    print(json.dumps(info, default=str))
    return 0 if info.get("checks_ok") else 2


if __name__ == "__main__":
    sys.exit(main())
