#!/usr/bin/env python3
"""step_badges.py - add the persistent step badges (one PNG media_overlay per chapter, top right above the camera tile,
fade in, instant out), each from the end of one chapter card (or 0) to the start of the next card (or the project end):
never on a card. One verified transaction for the whole set.

usage:
  step_badges.py --auto badge-0.png badge-1.png ... [--dry]     ranges derived from the CURRENT cards (fullscreen layouts):
                                                                [0, card1], [card1 end, card2], ..., [cardN end, duration]
  step_badges.py --table badges.json [--dry]                    [{"png": path, "start": ms, "end": ms}, ...] (see
                                                                examples/badges.example.json); every range is checked
                                                                against the cards: start = a card end (or 0), end = the next
                                                                card start (or up to 2 ms before it, or the project end)
Make the PNGs with make_badges.py. Run AFTER every chapter card is in (and after cuts): badges are overlays and must be
re-derived whenever cards move. The badge position / size / shadow / transitions come from the config badge_props.

Checks: N + 1 badges for N cards; the table sits between the cards; no badge overlay exists yet in those ranges; exactly
the new overlays at the requested ranges with the requested PNGs and props, all on one overlay layer, none over a card;
strict project-wide diff (only the new overlays); frames mid-range, 1.5 s in and 0.2 s before the next card show the
badge (badge box changed > 8) and nothing else changed (< 0.5); every card frame unchanged (no badge on a card).
Exit: 0 ok, 2 checks failed, 3 error, 4 collision (all aborted).
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
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--auto", nargs="+", metavar="PNG", help="one badge PNG per chapter, in order (intro first)")
    g.add_argument("--table", help="JSON [{png, start, end}, ...]")
    B.add_common_args(ap)
    args = ap.parse_args()
    ctx = B.context(args)
    cfg = ctx.cfg
    G = B.geometry_payload(cfg)
    PROPS = cfg["badge_props"]
    BOX = tuple(G["badge_outer"])
    tag = "_dry" if args.dry else ""
    FD = ctx.dir("frames", "badges%s" % tag)
    pre0 = S.snapshot(ctx.key)
    cards0 = [(a, b) for a, b, _ in S.fullscreen_layouts(pre0)]
    if args.table:
        TABLE = [(os.path.abspath(os.path.expanduser(r["png"])), int(r["start"]), int(r["end"])) for r in B.load_json(args.table)]
    else:
        edges = [0] + [x for c in cards0 for x in c] + [pre0["duration"]]
        der = [(edges[2 * i], edges[2 * i + 1]) for i in range(len(cards0) + 1)]
        if len(args.auto) != len(der):
            B.die("%d PNGs for %d chapters (%d cards + the intro): %s" % (len(args.auto), len(der), len(cards0), der))
        TABLE = [(os.path.abspath(os.path.expanduser(p)), a, b) for p, (a, b) in zip(args.auto, der)]
    for p, a, b in TABLE:
        if not os.path.exists(p):
            B.die("badge PNG not found: %s" % p)
    pos = PROPS.get("position")

    def is_badge(v):
        try:
            return v[0].startswith("media_overlay") and B.norm_numbers(json.loads(v[3]).get("position"), 4) == B.norm_numbers(pos, 4)
        except ValueError:
            return False

    def body(tx, k, pre, checks):
        cards = [(a, b) for a, b, _ in S.fullscreen_layouts(pre)]
        checks.add("%d chapter cards (fullscreen layouts), %d badge ranges" % (len(cards), len(TABLE)), len(TABLE) == len(cards) + 1, str(cards))
        edges = [0] + [x for c in cards for x in c] + [pre["duration"]]
        derived = [(edges[2 * i], edges[2 * i + 1]) for i in range(len(cards) + 1)]
        # an end may sit up to 2 ms before the next card (a frame-free remainder between a scene end and the card)
        checks.add("table ranges sit between the current cards (start = card end / 0; end = next card start, or <= 2 ms before it)",
                   len(derived) == len(TABLE) and all(a == da and db - 2 <= b <= db for (_, a, b), (da, db) in zip(TABLE, derived)), str(derived))
        old = [(i, v[1:3]) for i, v in pre["overlays"].items() if is_badge(v) and any(v[1] < b and v[2] > a for _, a, b in TABLE)]
        checks.add("no badge overlay in those ranges yet", not old, str(old))
        rows = []
        for f, a, b in TABLE:
            for nm, t in (("mid", (a + b) / 2.0), ("late", b - 200), ("early", a + 1500)):
                if a < t < b:
                    rows.append([f, nm, B.grid(t)])
        for ca, cb in cards:
            rows.append(["card", "card", B.grid((ca + cb) / 2.0)])
        for r in rows:
            r.append(B.save_frame(k, r[2], os.path.join(FD, "pre_%d.png" % round(r[2]))))
        mids = {}
        adds = []
        for f, a, b in TABLE:
            mid = al.import_media(tx, f)
            mids[f] = mid
            adds.append({"type": "media_overlay", "placement": {"range": [a, b]}, "content": {"media_id": mid}, "properties": PROPS})
        B.call("add_segments", {"tx_id": tx.id, "timeline_hash": tx.hash(), "additions": adds})
        post = S.snapshot(k)
        new = {i: v for i, v in post["overlays"].items() if i not in pre["overlays"]}
        got = sorted((v[1], v[2], v[4]) for v in new.values())
        checks.add("%d new badge overlays at exactly the table ranges with the right PNGs" % len(TABLE),
                   got == sorted((a, b, mids[f]) for f, a, b in TABLE), str(got))
        checks.add("badge props as configured (position, size, shadow, fade in, instant out)",
                   all(B.norm_numbers(json.loads(v[3])) == B.norm_numbers(PROPS) for v in new.values()), str([v[3] for v in new.values()][:1]))
        layers = sorted({v[0] for v in new.values()})
        checks.add("all badges on one overlay layer %s" % layers, len(layers) == 1)
        over_card = [(v[1], v[2]) for v in new.values() for ca, cb in cards if v[1] < cb and v[2] > ca]
        checks.add("no badge overlaps a card", not over_card, str(over_card))
        bad, notes = S.strict_diff(pre, post, {}, {}, {}, lambda cat, i, v: cat == "overlays" and i in new, S.ident)
        for b_ in bad:
            log("  DIFF " + b_[:300])
        checks.add("project-wide diff: only the new badge overlays", not bad and post["duration"] == pre["duration"] and post["scenes"] == pre["scenes"],
                   "%d problems" % len(bad))
        for r in rows:
            r.append(B.save_frame(k, r[2], os.path.join(FD, "post_%d.png" % round(r[2]))))
        out = B.imgcheck("behind-outside", {"rows": [[r[2], r[3], r[4], False] for r in rows], "badge_outer": list(BOX),
                                            "tile_zone": [BOX[0], BOX[1], BOX[2], BOX[3]]})
        okb, okc = [], []
        for r, (inb, outb, _) in zip(rows, out):
            log("%-28s %-5s %.2f: badge box change %.2f, rest %.3f" % (os.path.basename(r[0]), r[1], r[2], inb, outb))
            if r[0] == "card":
                okc.append(inb < 0.5 and outb < 0.5)
            else:
                okb.append(inb > 8 and outb < 0.5)
        checks.add("badge shown mid-range, 1.5 s in and 0.2 s before the next card; nothing else changes", all(okb), str(okb))
        checks.add("no badge on any card (card frames unchanged)", all(okc), str(okc))
        crops = []
        for f, a, b in TABLE:
            r = [x for x in rows if x[0] == f and x[1] == "mid"]
            if not r:
                continue
            c = os.path.join(FD, "crop_%s.png" % os.path.splitext(os.path.basename(f))[0])
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", r[0][4], "-vf", "crop=420:300:%d:20" % (BOX[0] - 20), c], check=True)
            crops.append(c)
        mont = ctx.path("frames", "badges%s.jpg" % tag)
        B.montage(crops, mont, 4, 420, 300)
        log("badge crops montage: %s" % mont)

        def post_check(pre_proj, post_c):
            n2 = {i: v for i, v in post_c["overlays"].items() if i not in pre_proj["overlays"]}
            b2, nn = S.strict_diff(pre_proj, post_c, {}, {}, {}, lambda cat, i, v: cat == "overlays" and i in n2, S.ident)
            if sorted((v[1], v[2]) for v in n2.values()) != sorted((a, b) for _, a, b in TABLE):
                b2.append("new overlays %s" % sorted((v[1], v[2]) for v in n2.values()))
            return b2, nn
        return post_check, {"badges": {os.path.basename(f): [a, b, mids[f]] for f, a, b in TABLE}, "layer": layers, "montage": mont}

    info = txrun.run(ctx, "badges", body, args.dry,
                     lambda i: "Add the %d step badges (top right above the camera, fade in, gone on every chapter card)." % len(TABLE),
                     args.lock_timeout)
    print(json.dumps(info, default=str))
    return 0 if info.get("checks_ok") else 2


if __name__ == "__main__":
    sys.exit(main())
