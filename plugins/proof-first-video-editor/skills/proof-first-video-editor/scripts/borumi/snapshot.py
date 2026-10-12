#!/usr/bin/env python3
"""snapshot.py - PROJECT-WIDE snapshot + diff of a Borumi timeline (read-only), as a library and a CLI.

Why project-wide: ripple trims, layout deletes / untrims and content deletes can make Borumi rewrite layouts ELSEWHERE in
the project (merges, 1 ms shifts, screen_1/screen_2 swaps). A check limited to the edited range misses that. Every
writer script snapshots the whole project before and after its staged edit and diffs everything.

Library:
  snapshot(k)  k = {"project_id": ID} or {"tx_id": TX} ->
      {"duration", "hash", "scenes": [(id, name, s, e)],
       "layouts":  {id: (s, e, props_json)},
       "overlays": {id: (layer, s, e, props_json, media_id, media_s, media_e)},     media overlays / sfx / text / music
       "content":  {id: (layer, s, e, media_id, media_s, media_e, group_id)},       camera / screen / mic / system / audio
       "controls": {id: (layer, s, e, props_json, media_id)}}                       zooms, blur, captions, cursor, ...
  strict_diff(pre, post, exp_content, exp_layouts, exp_overlays, new_ok, shift, tol_groups=(), tol_ids=())
      -> (problems, notes). Every item not listed in exp_* must equal shift(pre item); new ids must pass new_ok(cat, id, v);
         byte-identical adjacent layouts that Borumi MERGES during a ripple are accepted (visual no-op);
         content in tol_groups / tol_ids may show +-1 ms display rounding (fractional underlying times).
  diff(a, b, expect)        the lighter expect-dict API: {"shift": f, "layouts"|"overlays"|"content": {id: tuple|None},
                            "new_layouts"|...: f(id, v) -> bool}
  tol_exp(pre, post, cat)   ids whose only change is a +-1 ms start/end rounding flip (accept them explicitly)
  take_moved(before, cur)   what moved when a take was added (big moves, rounding noise, mixed groups, scenes ok)
  layout_map(lays, lo, hi, shift)  piecewise "which props show when", adjacent equal pieces merged
  gaps(snap, lo, hi)        screen-content and layout gaps, each with the 30 / 60 fps frame times inside
  shift_fn(A, B)            ripple expectation for a trim of [A, B]
  save(snap, path) / load(path)    JSON round trip (tuples restored)

CLI:
  snapshot.py save FILE.json [--tx TX]                    snapshot the committed project (or a staged tx)
  snapshot.py diff A.json B.json [--shift-from T --by D]   strict diff of two saved snapshots
  snapshot.py gaps [--lo MS --hi MS] [--all]              layout / screen gaps (default: only those holding a frame)
  snapshot.py peek A B [--layers L ...] [--props] [--tx TX]   every segment in [A, B)
  snapshot.py wait-stable [--seconds 180] [--poll 10]     exit when the committed hash has not changed for N s
                                                          (the user edits in the app while agents write)
Common args: --work / --project-id / --config / --edit-skill (see bwlib.py).
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwlib as B  # noqa: E402

al = B.al
CONTENT = ("camera", "screen", "microphone", "system_sound", "audio")
CONTROL_OTHER = ("screen_zoom", "camera_zoom", "screen_blur", "screen_highlight", "captions", "cursor", "background")
OVERLAY_KINDS = ("media_overlay", "sound_effect", "text_overlay", "music")
CATS = ("layouts", "overlays", "content", "controls")


def pj(p):
    return json.dumps(p, sort_keys=True)


def snapshot(k):
    summ = al.summary(k)
    kinds = {L["id"]: L.get("kind") for L in summ["layers"]}
    snap = {"duration": summ["duration_ms"], "hash": summ["timeline_hash"],
            "scenes": [(s["id"], s["name"], s["start_ms"], s["end_ms"]) for s in al.scenes(k)]}
    lay = al.segs(k, [l for l, kd in kinds.items() if kd == "layout"], 0, None, fields=("properties",))
    snap["layouts"] = {s["id"]: (s["start_ms"], s["end_ms"], pj(s.get("properties"))) for s in lay}
    ov = al.segs(k, [l for l, kd in kinds.items() if kd in OVERLAY_KINDS], 0, None, fields=("properties", "media"))
    snap["overlays"] = {s["id"]: (s["layer"], s["start_ms"], s["end_ms"], pj(s.get("properties")),
                                  (s.get("media") or {}).get("media_id"), (s.get("media") or {}).get("start_ms"),
                                  (s.get("media") or {}).get("end_ms")) for s in ov}
    ct = al.segs(k, [l for l, kd in kinds.items() if kd in CONTENT], 0, None, fields=("media", "group_id"))
    snap["content"] = {s["id"]: (s["layer"], s["start_ms"], s["end_ms"], (s.get("media") or {}).get("media_id"),
                                 (s.get("media") or {}).get("start_ms"), (s.get("media") or {}).get("end_ms"),
                                 s.get("group_id")) for s in ct}
    co = al.segs(k, [l for l, kd in kinds.items() if kd in CONTROL_OTHER], 0, None, fields=("properties", "media"))
    snap["controls"] = {s["id"]: (s["layer"], s["start_ms"], s["end_ms"], pj(s.get("properties")),
                                  (s.get("media") or {}).get("media_id")) for s in co}
    return snap


def normalize(snap):
    """JSON-loaded snapshot -> tuples again (so == comparisons work)."""
    out = dict(snap)
    out["scenes"] = [tuple(x) for x in snap.get("scenes", [])]
    for cat in CATS:
        out[cat] = {i: tuple(v) for i, v in (snap.get(cat) or {}).items()}
    return out


def save(snap, path):
    B.write_json(path, snap)
    return path


def load(path):
    return normalize(B.load_json(path))


def scene_map(snap):
    return {s[0]: s for s in snap["scenes"]}


def shifted(cat, v, sh):
    if cat == "layouts":
        return (sh(v[0], False), sh(v[1], True), v[2])
    return (v[0], sh(v[1], False), sh(v[2], True)) + tuple(v[3:])


def ident(t, e=False):
    return t


def shift_fn(A, B_):
    """where a time lands after a ripple trim of [A, B]: before A unchanged, inside -> A, after -> t - (B - A)."""
    d = B_ - A
    return lambda t, e=False: t if t <= A else (A if t <= B_ else t - d)


def diff(a, b, expect=None):
    """lenient diff: expect = {"shift": f(t, is_end), "<cat>": {id: tuple or None (= gone)}, "new_<cat>": f(id, v)}."""
    expect = expect or {}
    sh = expect.get("shift") or ident
    bad = []
    for cat in CATS:
        ex = expect.get(cat, {})
        newok = expect.get("new_" + cat) or (lambda i, v: False)
        A, B_ = a.get(cat, {}), b.get(cat, {})
        for i in set(A) | set(B_):
            if i in ex:
                want = ex[i]
                if want is None:
                    if i in B_:
                        bad.append("%s %s should be gone, is %s" % (cat, i, B_[i][:3]))
                elif B_.get(i) != want:
                    bad.append("%s %s expected %s got %s" % (cat, i, str(want)[:140], str(B_.get(i))[:140]))
                continue
            if i not in A:
                if not newok(i, B_[i]):
                    bad.append("%s NEW %s %s" % (cat, i, str(B_[i])[:160]))
                continue
            want = shifted(cat, A[i], sh)
            if B_.get(i) != want:
                bad.append("%s %s %s -> %s" % (cat, i, str(A[i])[:120], str(B_.get(i))[:160]))
    return bad


def identical_chains(pre):
    """lists of adjacent layouts (end == next start) with byte-identical properties (Borumi merges these on ripples)."""
    L = sorted(pre["layouts"].items(), key=lambda x: x[1][0])
    if not L:
        return []
    chains, cur = [], [L[0]]
    for prev, nxt in zip(L, L[1:]):
        if prev[1][1] == nxt[1][0] and prev[1][2] == nxt[1][2]:
            cur.append(nxt)
        else:
            if len(cur) > 1:
                chains.append(cur)
            cur = [nxt]
    if len(cur) > 1:
        chains.append(cur)
    return chains


def strict_diff(pre, post, exp_content, exp_layouts, exp_overlays, new_ok, shift, tol_groups=(), tol_ids=()):
    """exp_*: {id: tuple or None} overrides; everything else must equal shift(pre). new_ok(cat, id, v) -> bool.
    Identical-layout merges are accepted (one survivor carries the union range, the rest gone)."""
    bad, notes = [], []
    merged_ids = set()
    tol_groups, tol_ids = set(tol_groups or ()), set(tol_ids or ())
    for ch in identical_chains(pre):
        ids = [i for i, _ in ch]
        s0, e0, p = ch[0][1][0], ch[-1][1][1], ch[0][1][2]
        surv = [i for i in ids if i in post["layouts"]]
        gone = [i for i in ids if i not in post["layouts"]]
        want = (shift(s0, False), shift(e0, True), p)
        if len(surv) == 1 and post["layouts"][surv[0]] == want:
            merged_ids |= set(ids)
            notes.append("identical-props merge %s -> %s %s" % (ids, surv[0], want[:2]))
        elif not gone:
            pass  # not merged: normal rules apply
        else:
            bad.append("identical chain %s merged unexpectedly: %s" % (ids, {i: post["layouts"].get(i, (None,))[:2] for i in ids}))
            merged_ids |= set(ids)

    def cmp(cat, i, want, got):
        if want == got:
            return True
        if cat == "content" and want and got and (want[6] in tol_groups or i in tol_ids) and want[0] == got[0] and \
                want[3] == got[3] and all(a is not None and b is not None and abs(a - b) <= 1
                                          for a, b in zip((want[1], want[2], want[4], want[5]), (got[1], got[2], got[4], got[5]))):
            notes.append("fractional cut noise %s %s -> %s" % (i, want[1:6], got[1:6]))
            return True
        return False

    for cat, exp in (("content", exp_content), ("layouts", exp_layouts), ("overlays", exp_overlays), ("controls", {})):
        A_, B_ = pre.get(cat, {}), post.get(cat, {})
        for i in set(A_) | set(B_):
            if cat == "layouts" and i in merged_ids:
                continue
            if i in exp:
                want = exp[i]
                if want is None:
                    if i in B_:
                        bad.append("%s %s should be gone, is %s" % (cat, i, str(B_[i])[:160]))
                elif not cmp(cat, i, want, B_.get(i)):
                    bad.append("%s %s expected %s got %s" % (cat, i, str(want)[:200], str(B_.get(i))[:200]))
                continue
            if i not in A_:
                if not new_ok(cat, i, B_[i]):
                    bad.append("%s NEW %s %s" % (cat, i, str(B_[i])[:200]))
                continue
            want = shifted(cat, A_[i], shift)
            if not cmp(cat, i, want, B_.get(i)):
                bad.append("%s %s %s -> %s" % (cat, i, str(A_[i])[:150], str(B_.get(i))[:200]))
    return bad, notes


def tol_exp(pre, post, cat, skip=()):
    """ids of `cat` (layouts / overlays) whose only change is a +-1 ms start/end display-rounding flip (same props /
    media / layer) -> {id: post value} to accept."""
    out = {}
    for i, v in pre[cat].items():
        if i in skip or i not in post[cat] or post[cat][i] == v:
            continue
        w = post[cat][i]
        if cat == "layouts":
            if w[2] == v[2] and abs(w[0] - v[0]) <= 1 and abs(w[1] - v[1]) <= 1:
                out[i] = w
        elif w[0] == v[0] and w[3:] == v[3:] and abs(w[1] - v[1]) <= 1 and abs(w[2] - v[2]) <= 1:
            out[i] = w
    return out


def take_moved(before, cur):
    """(big moves, rounding-noise count, groups with mixed shifts, scenes/duration ok): +-1 ms display rounding (half-ms
    positions left by an app edit) is tolerated only when all layers of a group show the same shift."""
    big, noise, gs = [], 0, {}
    for i, v in before["content"].items():
        w = cur["content"].get(i)
        if w is None or w[0] != v[0] or w[3] != v[3] or abs((w[4] or 0) - (v[4] or 0)) > 1 or abs((w[5] or 0) - (v[5] or 0)) > 1:
            big.append((i, v[:3], w[:3] if w else None))
            continue
        d0, d1 = w[1] - v[1], w[2] - v[2]
        if (d0, d1) != (0, 0):
            if abs(d0) > 1 or abs(d1) > 1:
                big.append((i, v[:3], w[:3]))
            else:
                noise += 1
                gs.setdefault(v[6], set()).add((d0, d1))
    mixed = [g_ for g_, ds in gs.items() if len(ds) > 1]
    sc_ok = len(cur["scenes"]) == len(before["scenes"]) and all(
        x[0] == y[0] and abs(x[2] - y[2]) <= 1 and abs(x[3] - y[3]) <= 1 for x, y in zip(before["scenes"], cur["scenes"]))
    return big, noise, mixed, sc_ok and abs(cur["duration"] - before["duration"]) <= 1


def scenes_pm1(pre, post):
    """same scenes, every edge within +-1 ms, duration within +-1 ms."""
    return abs(post["duration"] - pre["duration"]) <= 1 and len(post["scenes"]) == len(pre["scenes"]) and all(
        x[0] == y[0] and abs(x[2] - y[2]) <= 1 and abs(x[3] - y[3]) <= 1 for x, y in zip(pre["scenes"], post["scenes"]))


def layout_map(lays, lo, hi, shift=None):
    """piecewise props over [lo, hi): [(a, b, props_json or None)] with adjacent equal pieces merged (times shifted if given)."""
    iv = sorted(((shift(v[0], False) if shift else v[0]), (shift(v[1], True) if shift else v[1]), v[2]) for v in lays.values())
    iv = [x for x in iv if x[1] > x[0]]
    pts = sorted({lo, hi} | {t for a, b, _ in iv for t in (a, b) if lo < t < hi})
    out = []
    for a, b in zip(pts, pts[1:]):
        hit = [p for x, y, p in iv if x <= a and b <= y]
        p = hit[0] if hit else None
        if out and out[-1][2] == p and out[-1][1] == a:
            out[-1] = (out[-1][0], b, p)
        else:
            out.append((a, b, p))
    return out


def merge_map(M):
    o = []
    for a, b, p in M:
        if o and o[-1][2] == p and o[-1][1] == a:
            o[-1] = (o[-1][0], b, p)
        else:
            o.append((a, b, p))
    return o


def gaps(snap, lo, hi, fps_list=(30, 60)):
    """coverage gaps in [lo, hi] for screen content and for layouts, each with the 30 / 60 fps frame times inside.
    A gap with no frame inside is invisible (a 1-2 ms rounding gap); one holding a frame flashes the default layout
    (fullscreen camera) or an empty panel."""
    res = {}
    for name, segs in (("screen", [(v[1], v[2]) for v in snap["content"].values() if v[0].startswith("screen")]),
                       ("layout", [(v[0], v[1]) for v in snap["layouts"].values()])):
        segs = sorted(s for s in segs if s[1] > lo and s[0] < hi)
        g, cur = [], lo
        for a, b in segs:
            if a > cur:
                g.append((cur, a))
            cur = max(cur, b)
        if cur < hi:
            g.append((cur, hi))
        out = []
        for a, b in g:
            fr = sorted({round(n * 1000.0 / fps, 2) for fps in fps_list
                         for n in range(math.ceil(a * fps / 1000.0 - 1e-9), math.ceil(b * fps / 1000.0 - 1e-9))
                         if a <= n * 1000.0 / fps < b})
            out.append((a, b, fr))
        res[name] = out
    return res


def words_all(k):
    return al.words_in(k, 0, al.summary(k)["duration_ms"], al.mic_layers(k))


def fullscreen_layouts(snap):
    """chapter cards = fullscreen layouts: sorted [(start, end, id)]."""
    return sorted((v[0], v[1], i) for i, v in snap["layouts"].items() if json.loads(v[2] or "{}").get("kind") == "fullscreen")


# ------------------------------------------------------------------------------------------------ CLI
def peek(k, a, b, layers=None, show_props=False):
    args = dict(k, range={"start_ms": int(a), "end_ms": int(b)}, detail="segments", segment_fields=["media", "group_id", "properties"])
    if layers:
        args["layers"] = layers
    r = B.call("get_timeline", args)
    for L in r.get("layers", []):
        for s in L.get("segments", []) or []:
            m = s.get("media") or {}
            p = s.get("properties") or {}
            line = "%-16s %-6s %10.3f %10.3f (%7.3f) grp=%s media=%s m=[%s,%s]" % (
                L["id"], s["id"], s["start_ms"], s["end_ms"], s["end_ms"] - s["start_ms"], s.get("group_id"),
                m.get("media_id"), m.get("start_ms"), m.get("end_ms"))
            if show_props:
                line += " " + json.dumps(p)[:900]
            else:
                ps = {kk: p[kk] for kk in ("kind", "layer_id", "volume_filter") if kk in p}
                if p.get("kind") == "custom":
                    ps["src"] = [(x.get("layer_id"), round(x.get("x_ratio", 0), 4), round(x.get("y_ratio", 0), 4),
                                  round(x.get("width_ratio", 0), 4), round(x.get("height_ratio", 0), 4)) for x in p.get("sources", [])]
                if "transition" in p:
                    ps["tr"] = (p.get("transition") or {}).get("kind")
                line += " " + json.dumps(ps)[:400]
            print(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="see the module docstring (python3 -c 'import snapshot; help(snapshot)') for the library API")
    B.add_common_args(ap, dry=False)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("save", help="snapshot to JSON")
    s.add_argument("file", help="output JSON file")
    s.add_argument("--tx", help="read a staged transaction instead of the committed project")
    d = sp.add_parser("diff", help="strict diff of two saved snapshots")
    d.add_argument("a")
    d.add_argument("b")
    d.add_argument("--shift-from", type=float, help="expect every time >= T shifted ...")
    d.add_argument("--by", type=float, default=0, help="... by this many ms")
    g = sp.add_parser("gaps", help="layout / screen gaps")
    g.add_argument("--lo", type=int, default=0)
    g.add_argument("--hi", type=int)
    g.add_argument("--all", action="store_true", help="also list frame-free gaps")
    g.add_argument("--from-file", help="use a saved snapshot instead of reading the project")
    p = sp.add_parser("peek", help="every segment in [A, B)")
    p.add_argument("a", type=int)
    p.add_argument("b", type=int)
    p.add_argument("--layers", nargs="*")
    p.add_argument("--props", action="store_true")
    p.add_argument("--tx")
    w = sp.add_parser("wait-stable", help="exit 0 once the committed hash is unchanged for N seconds")
    w.add_argument("--seconds", type=int, default=180)
    w.add_argument("--poll", type=int, default=10)
    a = ap.parse_args()
    if a.cmd == "diff":
        A, Bs = load(a.a), load(a.b)
        sh = ident
        if a.shift_from is not None:
            T, D = a.shift_from, a.by
            sh = lambda t, e=False: (t + D) if (t > T if e else t >= T) else t  # noqa: E731
        bad, notes = strict_diff(A, Bs, {}, {}, {}, lambda cat, i, v: False, sh)
        for n in notes:
            print("note: " + n)
        for x in bad:
            print("DIFF " + x)
        print("duration %d -> %d; scenes %s; %d problems" % (A["duration"], Bs["duration"],
                                                              "same" if A["scenes"] == Bs["scenes"] else "CHANGED", len(bad)))
        return 1 if bad else 0
    if a.cmd == "gaps" and a.from_file:
        sn = load(a.from_file)
    else:
        ctx = B.context(a)
        if a.cmd == "save":
            k = {"tx_id": a.tx} if a.tx else ctx.key
            sn = snapshot(k)
            save(sn, a.file)
            print("saved %s: duration %d, hash %s, %d scenes, %d layouts, %d overlays, %d content" % (
                a.file, sn["duration"], sn["hash"], len(sn["scenes"]), len(sn["layouts"]), len(sn["overlays"]), len(sn["content"])))
            return 0
        if a.cmd == "peek":
            peek({"tx_id": a.tx} if a.tx else ctx.key, a.a, a.b, a.layers, a.props)
            return 0
        if a.cmd == "wait-stable":
            last, since = None, time.time()
            while True:
                try:
                    h = al.summary(ctx.key)["timeline_hash"]
                except Exception as e:  # noqa: BLE001
                    B.log("read failed: %s" % e)
                    h = None
                if h and h != last:
                    B.log("%s hash %s" % (time.strftime("%H:%M:%S"), h))
                    last, since = h, time.time()
                if time.time() - since >= a.seconds:
                    B.log("%s STABLE %s for %ds" % (time.strftime("%H:%M:%S"), last, a.seconds))
                    return 0
                time.sleep(a.poll)
        sn = snapshot(ctx.key)
    hi = a.hi or sn["duration"]
    g_ = gaps(sn, a.lo, hi)
    for name in ("layout", "screen"):
        rows = [(x, y, fr) for x, y, fr in g_[name] if fr or a.all]
        print("%s gaps%s: %d" % (name, "" if a.all else " holding a 30/60 fps frame", len(rows)))
        for x, y, fr in rows:
            print("  [%d, %d) %d ms, frames %s" % (x, y, y - x, fr[:4]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
