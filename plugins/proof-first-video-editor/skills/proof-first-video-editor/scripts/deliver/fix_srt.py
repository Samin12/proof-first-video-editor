#!/usr/bin/env python3
"""Fix brand and name spellings in an exported SRT, driven by a JSON name map.

Borumi's transcript (and whisper) mishears product and people names: "Claude Code" comes out as
"cloud code", "Ghidra" as "Gidra"/"Hydra", the creator's own name as a common lookalike. This
rewrites ONLY caption text lines; cue numbers and timings are never touched.

usage: fix_srt.py --names NAMES.json [--names MORE.json ...] IN.srt [OUT.srt]
                  [--dry-run] [--report REPORT.json] [--fail-on-review]

  OUT defaults to IN (rewritten in place). In-place runs first copy IN to IN.orig; an existing .orig
  is NEVER overwritten, so a second run cannot destroy the pristine export.
  --names       name-map JSON (repeatable; applied in the order given: e.g. a shared brand map,
                then a per-video map). See examples/names.example.json.
  --dry-run     print what would change, write nothing.
  --report      write a JSON report of every change (line, before, after, rules) and review hits.
  --fail-on-review  exit 3 when a "review" pattern still matches after fixing.

Name-map format (all keys optional except "fixes"):
  {
    "fixes": [
      {"to": "Ghidra", "from": ["Gidra", "Hydra", "Gedra"]},          literal, whole word
      {"to": "Claude Code", "from": ["cloud code", "Claud Code"], "ignore_case": true},
      {"pattern": "^Cloud right now\\b", "replace": "Claude right now"},  regex (re.sub syntax)
      {"pattern": "\\bchat GPT\\b", "replace": "ChatGPT", "flags": "i", "note": "why this rule exists"}
    ],
    "review": ["\\bcloud\\b", "\\bclaw\\b"]       regexes a human must check after fixing
  }
  * Entries run top to bottom; each sees the output of the previous ones (put long phrases before
    their sub-words: a misheard full name BEFORE the rule for the first name alone).
  * "from"/"to": each phrase is matched literally; a \\b boundary is added on a side only when the
    phrase starts/ends with a word character. Case-sensitive unless "ignore_case": true.
  * "pattern"/"replace": a Python regex applied to each caption line on its own, so ^ and $ mean
    the start/end of a caption line. "flags" may contain i (ignore case).
  * "review": ambiguous words that must NOT be blindly replaced (a creator who says "Claude" a lot
    still says "cloud" sometimes). Every caption line still matching one is printed as REVIEW.

Lessons baked in (from real deliveries):
  * Never blanket-replace an ambiguous word ("cloud" -> "Claude"): anchor it with context
    ("cloud code", "your claw", "^Cloud right now") and list the bare word under "review".
  * Transcripts glue words ("ChatGPTKenCloud") and stutter ("recreate Recreate"): one regex each.
  * Keep one map per video in the project's receipts folder; promote rules that recur to a shared map.
  * Check the creator's own name first: it is the most common and most embarrassing miss.

Requires: Python 3.8+ (stdlib only).
"""
import argparse
import json
import os
import re
import shutil
import sys


def _literal_regex(phrase):
    body = re.escape(phrase)
    left = r"\b" if re.match(r"\w", phrase[:1]) else ""
    right = r"\b" if re.match(r"\w", phrase[-1:]) else ""
    return left + body + right


def load_rules(paths):
    """-> (rules, review) where rules = [(label, compiled_regex, replacement)], review = [(src, compiled)]."""
    rules, review = [], []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as e:
            sys.exit("cannot read name map %s: %s" % (path, e))
        if isinstance(d, list):
            d = {"fixes": d}
        fixes = d.get("fixes")
        if not isinstance(fixes, list):
            sys.exit("%s: expected {\"fixes\": [...]} (see examples/names.example.json)" % path)
        for i, fx in enumerate(fixes):
            where = "%s fixes[%d]" % (os.path.basename(path), i)
            if not isinstance(fx, dict):
                sys.exit("%s: each fix must be an object" % where)
            try:
                if "pattern" in fx:
                    if "replace" not in fx:
                        sys.exit("%s: a \"pattern\" entry needs \"replace\"" % where)
                    flags = re.I if "i" in str(fx.get("flags", "")).lower() else 0
                    rx = re.compile(fx["pattern"], flags)
                    rules.append(("%s %s" % (where, fx["pattern"]), rx, fx["replace"]))
                elif "to" in fx and "from" in fx:
                    src = fx["from"] if isinstance(fx["from"], list) else [fx["from"]]
                    src = [s for s in src if s]
                    if not src:
                        sys.exit("%s: empty \"from\"" % where)
                    # longest first so "Hicks field" wins over "Hicks"
                    alt = "|".join(_literal_regex(s) for s in sorted(src, key=len, reverse=True))
                    rx = re.compile("(?:%s)" % alt, re.I if fx.get("ignore_case") else 0)
                    # a literal replacement: escape backslashes so re.sub does not read group refs
                    rules.append(("%s -> %s" % (where, fx["to"]), rx, fx["to"].replace("\\", "\\\\")))
                else:
                    sys.exit("%s: needs either \"pattern\"+\"replace\" or \"from\"+\"to\"" % where)
            except re.error as e:
                sys.exit("%s: bad regex: %s" % (where, e))
        for j, pat in enumerate(d.get("review") or []):
            try:
                review.append((pat, re.compile(pat)))
            except re.error as e:
                sys.exit("%s review[%d]: bad regex: %s" % (os.path.basename(path), j, e))
    return rules, review


def caption_line_indexes(lines):
    """Indexes of caption TEXT lines: the lines after a timing line, up to the next blank line.
    Cue numbers and timing lines are never returned (stricter than 'skip digit-only lines',
    so a caption that is just a number, e.g. a year, is still fixable)."""
    out, in_text = [], False
    for i, raw in enumerate(lines):
        s = raw.rstrip("\r")
        if "-->" in s:
            in_text = True
            continue
        if not s.strip():
            in_text = False
            continue
        if in_text:
            out.append(i)
    return out


def fix_lines(lines, rules):
    """Apply rules in place to caption text lines. -> [(line_no, before, after, [rule labels])]."""
    changes = []
    for i in caption_line_indexes(lines):
        raw = lines[i]
        eol = "\r" if raw.endswith("\r") else ""
        old = raw[:-1] if eol else raw
        new, fired = old, []
        for label, rx, rep in rules:
            nxt = rx.sub(rep, new)
            if nxt != new:
                fired.append(label)
                new = nxt
        if new != old:
            lines[i] = new + eol
            changes.append((i + 1, old, new, fired))
    return changes


def review_hits(lines, review):
    hits = []
    for i in caption_line_indexes(lines):
        s = lines[i].rstrip("\r")
        for pat, rx in review:
            if rx.search(s):
                hits.append((i + 1, pat, s))
    return hits


def run(src, dst=None, names=(), dry_run=False, report=None, quiet=False):
    """Library entry point (deliver.py uses it). -> (changes, review_hits)."""
    dst = dst or src
    rules, review = load_rules(names)
    try:
        with open(src, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        sys.exit("cannot read SRT %s: %s" % (src, e))
    bom = text.startswith("﻿")
    if bom:
        text = text[1:]
    lines = text.split("\n")
    if not any("-->" in ln for ln in lines):
        sys.exit("%s does not look like an SRT (no '-->' timing lines)" % src)
    changes = fix_lines(lines, rules)
    hits = review_hits(lines, review)
    if not quiet:
        for n, old, new, _ in changes:
            print("%d: %r -> %r" % (n, old, new))
        for n, pat, s in hits:
            print("REVIEW %d [%s]: %s" % (n, pat, s))
    if not dry_run and (changes or dst != src):
        if dst == src:
            orig = src + ".orig"
            if os.path.exists(orig):
                if not quiet:
                    print("keeping existing %s (never overwritten)" % orig)
            else:
                shutil.copy2(src, orig)
        tmp = dst + ".part"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(("﻿" if bom else "") + "\n".join(lines))
        os.replace(tmp, dst)
    if report:
        per_rule = {}
        for _, _, _, fired in changes:
            for f in fired:
                per_rule[f] = per_rule.get(f, 0) + 1
        with open(report, "w", encoding="utf-8") as fh:
            json.dump({"src": src, "dst": dst, "dry_run": dry_run, "names": list(names),
                       "lines_changed": len(changes), "per_rule": per_rule,
                       "changes": [{"line": n, "before": o, "after": w, "rules": f} for n, o, w, f in changes],
                       "review": [{"line": n, "pattern": p, "text": s} for n, p, s in hits]}, fh, indent=1)
    if not quiet:
        print("%d lines %s -> %s%s" % (len(changes), "would change" if dry_run else "changed", dst,
                                       "; %d line(s) to REVIEW" % len(hits) if hits else ""))
    return changes, hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", metavar="IN.srt")
    ap.add_argument("dst", metavar="OUT.srt", nargs="?")
    ap.add_argument("--names", action="append", required=True, help="name-map JSON (repeatable, applied in order)")
    ap.add_argument("--dry-run", action="store_true", help="print the changes, write nothing")
    ap.add_argument("--report", help="write a JSON report of the changes and review hits")
    ap.add_argument("--fail-on-review", action="store_true", help="exit 3 when a review pattern still matches")
    a = ap.parse_args()
    _, hits = run(a.src, a.dst, a.names, a.dry_run, a.report)
    sys.exit(3 if (hits and a.fail_on_review) else 0)


if __name__ == "__main__":
    main()
