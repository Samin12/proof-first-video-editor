#!/usr/bin/env python3
"""Apply hand pins to snapped ranges: per-cut edge overrides plus by-value start/end nudges, then re-check the grid.

usage: apply_pins.py SNAPPED.json PINS.json OUT.json [--fps 60] [--total-ms MS] [--snap-to-grid] [--allow-missing]

The snapper only knows audio: it picks the quietest grid point. It cannot honour notes like "the out-point must be at or
after 570480 ms, the removed card is still fading until then". Those come from looking at frames (frame.sh) and are
applied here, AFTER snap_cuts.py and BEFORE build_cut.py.

PINS.json:
{
  "edge_overrides": [   # keyed by the cut that STARTS the snapped range (section + from_word)
    {"section": "S4", "from_word": 110, "start_ms": 553733.333, "end_ms": 570483.333, "why": "removed card still fading"},
    {"section": "S2", "from_word": 584, "end_ms": 305183.333, "why": "README page still fully visible at the snapped out-point"}
  ],
  "end_by_value":   [{"from_ms": 877116.667, "to_ms": 877250.0, "why": "..."}],   # any range ending at from_ms (+-0.5)
  "start_by_value": [{"from_ms": 977750.0, "to_ms": 977116.667, "why": "..."}]    # any range starting at from_ms (+-0.5)
}
(The values above are the REA examples; yours come from your own frame checks.)
Rules kept from production:
  * For a MERGED range (several cuts snapped into one), a start pin applies only if its cut is the first one and an end
    pin only if its cut is the last one; anything else is a warning, never silently applied.
  * Overrides are applied first, by-value nudges second (so a nudge can refine a pinned edge).
  * Every edge must end up on the 1000/fps grid (pins like 305183.3 fail; give 305183.333, or pass --snap-to-grid to
    round pins to the nearest frame with a warning). build_cut.py rounds ms to frames with round(), so an off-grid value
    can land one frame early and show a full-brightness flash frame of the removed visual.
  * Every pin must match something: unmatched pins are an error unless --allow-missing.
  * After pinning, ranges must stay non-empty and non-overlapping.
Pin both edges of a cut together when one moves: moving only the out-point can crush the pause at the join (a 176 ms
join pause sounded rushed in REA; ~250-300 ms reads naturally).
OUT.json has the same shape as SNAPPED.json plus "pins_applied" and recomputed removed_ms/new_duration_ms.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tlib  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapped", help="snap_cuts.py output")
    ap.add_argument("pins", help="PINS.json")
    ap.add_argument("out", help="pinned ranges JSON")
    ap.add_argument("--fps", type=int, help="default: 'fps' in SNAPPED.json, else 60")
    ap.add_argument("--total-ms", type=float, help="default: 'total_ms' in SNAPPED.json")
    ap.add_argument("--snap-to-grid", action="store_true", help="round off-grid pin values to the nearest frame (warns)")
    ap.add_argument("--allow-missing", action="store_true", help="do not fail when a pin matches no range")
    a = ap.parse_args()
    with open(a.snapped, encoding="utf-8") as fh:
        d = json.load(fh)
    with open(a.pins, encoding="utf-8") as fh:
        P = json.load(fh)
    FPS = a.fps or d.get("fps") or 60
    TOTAL = a.total_ms or d.get("total_ms")
    if TOTAL is None:
        tlib.die("no total_ms in the snapped file; pass --total-ms")

    def pinval(v, what):
        if v is None:
            return None
        if not tlib.on_grid(v, FPS):
            if a.snap_to_grid:
                g = tlib.grid_round(v, FPS)
                print(f"WARN {what}: {v} is off the {FPS} fps grid -> {g}")
                return g
            tlib.die(f"{what}: {v} ms is not on the {FPS} fps grid (k*1000/{FPS}); fix the value or pass --snap-to-grid")
        return v

    OV = {}
    for o in P.get("edge_overrides", []):
        k = (o["section"], o["from_word"])
        OV[k] = (pinval(o.get("start_ms"), f"override {k} start"), pinval(o.get("end_ms"), f"override {k} end"))
    END_BY_VALUE = [(x["from_ms"], pinval(x["to_ms"], f"end_by_value {x['from_ms']}")) for x in P.get("end_by_value", [])]
    START_BY_VALUE = [(x["from_ms"], pinval(x["to_ms"], f"start_by_value {x['from_ms']}")) for x in P.get("start_by_value", [])]

    applied, warns = [], []
    used = set()
    for r in d["ranges"]:
        keys = [(r.get("section"), r.get("from_word"))] + [(r.get("section"), m[0]) for m in r.get("merged", [])]
        for k in keys:
            if k in OV:
                used.add(k)
                s, e = OV[k]
                # for merged ranges, a start pin applies only to the first cut, an end pin only to the last
                if s is not None and k == keys[0]: applied.append((k, "start", r["start_ms"], s)); r["start_ms"] = s
                if e is not None and k == keys[-1]: applied.append((k, "end", r["end_ms"], e)); r["end_ms"] = e
                if e is not None and k != keys[-1]: warns.append(f"end pin on non-last merged cut {k}: NOT applied")
                if s is not None and k != keys[0]: warns.append(f"start pin on non-first merged cut {k}: NOT applied")
        for v, nv in END_BY_VALUE:
            if abs(r["end_ms"] - v) < 0.5:
                applied.append(((r.get("section"), r.get("from_word")), "end", r["end_ms"], nv)); r["end_ms"] = nv
                used.add(("end", v))
        for v, nv in START_BY_VALUE:
            if abs(r["start_ms"] - v) < 0.5:
                applied.append(((r.get("section"), r.get("from_word")), "start", r["start_ms"], nv)); r["start_ms"] = nv
                used.add(("start", v))
    for a_ in applied: print("pin", a_)
    expected = sum((s is not None) + (e is not None) for s, e in OV.values()) + len(END_BY_VALUE) + len(START_BY_VALUE)
    print(f"pins applied: {len(applied)} of {expected}")
    missing = [f"override {k}" for k in OV if k not in used] + \
              [f"end_by_value {v}" for v, _ in END_BY_VALUE if ("end", v) not in used] + \
              [f"start_by_value {v}" for v, _ in START_BY_VALUE if ("start", v) not in used]
    for m in missing: warns.append(f"pin matched no range: {m}")
    for w in warns: print("WARN", w)
    if missing and not a.allow_missing:
        tlib.die(f"{len(missing)} pin(s) matched nothing (re-check section/from_word or the by-value numbers); "
                 "--allow-missing to continue anyway")

    d["ranges"].sort(key=lambda r: r["start_ms"])
    for r in d["ranges"]:
        for x in (r["start_ms"], r["end_ms"]):
            assert tlib.on_grid(x, FPS), f"edge {x} ms is off the {FPS} fps grid"
        if r["end_ms"] - r["start_ms"] < 1000 / FPS:
            tlib.die(f"range {r.get('section')} w{r.get('from_word')} became empty after pinning: {r['start_ms']}-{r['end_ms']}")
    for p, q in zip(d["ranges"], d["ranges"][1:]):
        if q["start_ms"] < p["end_ms"]:
            tlib.die(f"pinned ranges overlap: {p['start_ms']}-{p['end_ms']} and {q['start_ms']}-{q['end_ms']}")
    rem = sum(r["end_ms"] - r["start_ms"] for r in d["ranges"])
    d["removed_ms"] = rem; d["new_duration_ms"] = TOTAL - rem
    d["total_ms"] = TOTAL; d["fps"] = FPS
    d["pins_applied"] = [{"cut": list(k) if isinstance(k, tuple) else k, "edge": e, "from": f, "to": t} for k, e, f, t in applied]
    d.setdefault("warnings", []).extend(f"pins: {w}" for w in warns)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    print(f"ranges {len(d['ranges'])}, removed {rem/1000:.1f} s -> {(TOTAL-rem)/1000:.1f} s ({(TOTAL-rem)/60000:.2f} min)")


if __name__ == "__main__":
    main()
