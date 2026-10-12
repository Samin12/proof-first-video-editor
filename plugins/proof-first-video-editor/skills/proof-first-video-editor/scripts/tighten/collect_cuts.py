#!/usr/bin/env python3
"""Collect the tighten workflow's final per-section cut lists into one CUTS.json for snap_cuts.py (+ a pins draft).

usage: collect_cuts.py RESULT.json CUTS.json [--config tighten.json] [--skip S4:329,S5:56 ...] [--drop-priority could]
                       [--pins-draft PINS.draft.json] [--results-out final_results.json]

RESULT.json: what workflows/tighten.js returned ({"globalPlan", "proposals", "results"}), or the Workflow task output
file that wraps it ({"result": {...}}), or the bare "results" list. Each result is {key, scene, final, verdict, revised}.
CUTS.json: {"cuts": [... each with "section" and "scene" added ...], "pause_safe": [...], "pause_unsafe": [...]}

Prints, per section: revised?, skeptic ok?, blocking/minor issue counts, cut count by priority, estimated seconds;
then every blocking issue, every flag that mentions pins/overrides/nudges/snapping, and every "could" cut with its
reason and join: "could" cuts are the risky ones (mid-phrase, setup a payoff may need, a visual that drops under 1 s):
LISTEN to each join in a preview build before keeping it (the reference video kept 9 of 14). Drop the rejected ones with
--skip SECTION:FROM_WORD (repeatable, or comma-separated) or all of them with --drop-priority could.
--pins-draft writes the cuts' optional pin_start_ms/pin_end_ms (set by judges/revisers when the snapped edge would show
a flash of a removed visual or crush a pause) as an apply_pins.py file, snapped to the frame grid. REVIEW IT: check one
frame on each side of every pinned edge with frame.sh before using it, and add by-value nudges found later.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tlib  # noqa: E402

PIN_WORDS = re.compile(r"PIN|OVERRIDE|NUDGE|SNAP|out-point|in-point|flash", re.I)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="tighten.js return value (or the task output file)")
    ap.add_argument("cuts", help="CUTS.json to write")
    ap.add_argument("--config", help="tighten.json (maps section keys to scenes when a result lacks 'scene'; fps)")
    ap.add_argument("--skip", action="append", default=[], help="SECTION:FROM_WORD to drop (repeatable / comma list)")
    ap.add_argument("--drop-priority", action="append", default=[], help="drop every cut with this priority (e.g. could)")
    ap.add_argument("--pins-draft", help="write the cuts' pin_start_ms/pin_end_ms as an apply_pins.py file")
    ap.add_argument("--results-out", help="also save the raw per-section results here")
    ap.add_argument("--fps", type=int, help="grid for the pins draft (default: config fps or 60)")
    a = ap.parse_args()
    cfg = tlib.load_config(a.config) if a.config else None
    fps = a.fps or (cfg["fps"] if cfg else 60)

    with open(a.result, encoding="utf-8") as fh:
        d = json.load(fh)
    if isinstance(d, dict) and "result" in d:
        d = d["result"]
    res = d["results"] if isinstance(d, dict) else d
    skip = set()
    for s in a.skip:
        for item in s.split(","):
            if item.strip():
                sec, w = item.strip().split(":")
                skip.add((sec, int(w)))

    cuts, safe, unsafe, flags, blocks, coulds, pins = [], [], [], [], [], [], []
    used_skips = set()
    for r in res:
        if not r:
            continue
        key = r.get("key")
        scene = r.get("scene") or (tlib.section_scene(cfg, key) if cfg else None)
        if not scene:
            tlib.die(f"result {key} has no scene; pass --config")
        v = r.get("verdict") or {}
        f = r.get("final") or {}
        blk = [i for i in (v.get("issues") or []) if i.get("severity") == "blocking"]
        mn = [i for i in (v.get("issues") or []) if i.get("severity") != "blocking"]
        pr = {}
        for c in f.get("cuts", []):
            pr[c.get("priority")] = pr.get(c.get("priority"), 0) + 1
        print(f"== {key} revised={r.get('revised')} skeptic_ok={v.get('ok')} blocking={len(blk)} minor={len(mn)} "
              f"cuts={len(f.get('cuts', []))} {pr} est={f.get('est_removed_s')}")
        blocks += [(key, i) for i in blk]
        for c in f.get("cuts", []):
            k = (key, c["from_word"])
            if k in skip:
                used_skips.add(k); continue
            if c.get("priority") in a.drop_priority:
                continue
            cuts.append(dict(c, section=key, scene=scene))
            if c.get("priority") == "could":
                coulds.append(cuts[-1])
            if c.get("pin_start_ms") is not None or c.get("pin_end_ms") is not None:
                ov = {"section": key, "from_word": c["from_word"], "why": f"from the cut list: {str(c.get('reason', ''))[:300]}"}
                if c.get("pin_start_ms") is not None: ov["start_ms"] = tlib.grid_round(float(c["pin_start_ms"]), fps)
                if c.get("pin_end_ms") is not None: ov["end_ms"] = tlib.grid_round(float(c["pin_end_ms"]), fps)
                pins.append(ov)
        safe += f.get("pause_tighten_safe") or []
        unsafe += f.get("pause_tighten_unsafe") or []
        flags += [(key, x) for x in (f.get("flags") or [])]
    with open(a.cuts, "w", encoding="utf-8") as fh:
        json.dump({"cuts": cuts, "pause_safe": safe, "pause_unsafe": unsafe}, fh, indent=1)
    if a.results_out:
        with open(a.results_out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1)
    for k in skip - used_skips:
        print(f"WARN --skip {k[0]}:{k[1]} matched no cut")
    print(f"\n{len(cuts)} cuts -> {a.cuts}; {len(safe)} pause-safe ranges")
    if blocks:
        print("\nBLOCKING issues (the reviser should have fixed these; check the final list handles each):")
        for key, i in blocks:
            print(f"  [{key}] {i.get('problem', '')[:400]}\n      FIX: {i.get('fix', '')[:300]}")
    pf = [(k, x) for k, x in flags if PIN_WORDS.search(x)]
    if pf:
        print("\nFLAGS about edges / pins (turn these into PINS.json entries after checking frames):")
        for k, x in pf:
            print(f"  [{k}] {x[:600]}")
    if coulds:
        print("\n'could' cuts (listen to each join before keeping it; --skip SECTION:FROM_WORD to drop):")
        for c in coulds:
            print(f"  {c['section']}:{c['from_word']} w{c['from_word']}-w{c['to_word']} [{c.get('kind')}] '{str(c.get('removed_text', ''))[:90]}'\n"
                  f"      reason: {str(c.get('reason', ''))[:230]}\n      join: {str(c.get('join_reads', ''))[:160]}")
    if a.pins_draft:
        with open(a.pins_draft, "w", encoding="utf-8") as fh:
            json.dump({"edge_overrides": pins, "end_by_value": [], "start_by_value": []}, fh, indent=1)
        print(f"\npins draft: {len(pins)} edge override(s) -> {a.pins_draft} (review before apply_pins.py)")


if __name__ == "__main__":
    main()
