#!/usr/bin/env python3
"""scene_words.py - final WORD and SENTENCE timings per scene on the CURRENT committed timeline (read-only), precise
enough to time renders, captions and chapter-card gaps to the spoken words.

usage: scene_words.py [--scene ID ...] [--plans plans.json | --plan-dir DIR] [--names words-names.json] [--prompt "Names."]
                      [--secondary-speaker NAME] [--whisper-model ggml-small.bin] [common args]

Pipeline per scene:
  1. rebuild the scene's audio from the committed MIC pieces (bundle medias; gaps such as a mid-scene card are filled
     with silence) -> 48 kHz float32 + a 16 kHz wav
  2. whisper-cli (-ml 1 -sow: one word per segment) with an optional --prompt of names / jargon
  3. if a plan text exists for the scene (the cut plan's "text"), the plan's spelling wins (difflib): names, punctuation,
     casing. Plan-only words are kept only when whisper's neighbouring words leave room for them (>= ~125 ms per word for
     3+ words): words removed later by an app cut must not be re-invented. Whisper-only words stay (real audio).
     "[...]" placeholders in the plan (e.g. "[unclear app names]") take whisper's guesses, or a 2-word filler so the
     alignment reserves their time, and come back as one labelled token. Then --names forced fixes ({"cloud": "Claude"}).
  4. align_ctc.py (wav2vec2 CTC forced alignment, pinned torch/torchaudio via uv)
  5. edges snapped to RMS speech runs (-62 dB, runs merged across <= 35 ms, peak >= -57 dB): starts move back to the
     nearest run start (<= 150 ms earlier), ends extend to the run end or the next word (else +60 ms), never past the
     scene end; overlaps removed
  6. sentences = words up to . ! ?   With --secondary-speaker NAME and plan text tagged "[NAME:] ... [HOST:] ...", a
     quiet second voice on the same mic (a TTS assistant in a demo) is labelled, and the host's next line after it is
     re-timed to the host's own loud onset (the CTC would otherwise start it inside the quiet reply).
Output: <out>/words/<scene>.json [[start_ms, end_ms, word], ...] (PROJECT ms), <scene>_sentences.json
[[start_ms, end_ms, text], ...], _index.json (hash, plan similarity, low-confidence words, dropped plan words).

Needs: whisper-cli (whisper.cpp) + a ggml model (--whisper-model, $WHISPER_MODEL, or ~/.cache/whisper/ggml-small.bin),
ffmpeg, numpy (re-runs itself under uv when missing), uv for the pinned CTC step, the bundle medias dir (config
medias_dir or WORK/project.json bundle_medias).
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bwlib as B  # noqa: E402

if not B._HELP:
    B.reexec_with_numpy()
import snapshot as S  # noqa: E402

try:
    import numpy as np  # noqa: E402
except ImportError:   # --help only
    np = None

SR = 48000
HERE = os.path.dirname(os.path.abspath(__file__))
CTC_CMD = ["uv", "run", "--no-project", "--python", "3.12", "--with", "torch==2.5.1", "--with", "torchaudio==2.5.1", "--with", "numpy",
           "python", os.path.join(HERE, "align_ctc.py")]
MODEL_CANDIDATES = ("~/.cache/whisper/ggml-small.bin", "~/.cache/whisper.cpp/ggml-small.bin", "./models/ggml-small.bin")
_WAV = {}


def core(w):
    return re.sub(r"[^a-z0-9']", "", w.lower().replace("\u2019", "'"))


def norm_words(text):
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = text.lower().replace("\u2019", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return [w for w in text.split() if w]


def compare(plan_text, words):
    a, b = norm_words(plan_text), norm_words(" ".join(words))
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops = [(op, " ".join(a[i1:i2]), " ".join(b[j1:j2])) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    return sm.ratio(), ops, len(a), len(b)


# ------------------------------------------------------------------------------------------------ audio
def wav48(medias, mid, cache_dir):
    """float32 mono 48 kHz samples of a bundle audio media (Borumi media ms == file ms). Non-f32 files are decoded once."""
    if mid in _WAV:
        return _WAV[mid]
    f = B.media_file(medias, mid, (".wav",)) or B.media_file(medias, mid, (".m4a", ".aac", ".caf", ".mov", ".mp4"))
    if not f:
        B.die("no media file for %s under %s" % (mid, medias))
    b = open(f, "rb").read() if f.lower().endswith(".wav") else b""
    i = b.find(b"data")
    if b[:4] == b"RIFF" and i > 0 and b[20:22] == b"\x03\x00" and b[22:24] == b"\x01\x00" and int.from_bytes(b[24:28], "little") == SR:
        n = int.from_bytes(b[i + 4:i + 8], "little")
        y = np.frombuffer(b[i + 8:i + 8 + n], dtype=np.float32)
    else:
        out = os.path.join(cache_dir, "%s_48k.f32" % mid)
        if not os.path.exists(out):
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", f, "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", out], check=True)
        y = np.fromfile(out, dtype=np.float32)
    _WAV[mid] = y
    return y


def rebuild(medias, pieces, out_prefix, cache_dir):
    """pieces = [(proj_s, proj_e, media_id, media_s, media_e)] sorted -> out_prefix.f32 (48 kHz) + out_prefix16.wav.
    Gaps between pieces on the timeline are filled with silence."""
    parts, t = [], pieces[0][0]
    for s, e, mid, ms, me in pieces:
        if s > t:
            parts.append(np.zeros(int(round((s - t) / 1000 * SR)), np.float32))
        x = wav48(medias, mid, cache_dir)
        i0 = int(round(ms / 1000 * SR))
        n = int(round((e - s) / 1000 * SR))
        seg = x[i0:i0 + n]
        if len(seg) < n:
            seg = np.concatenate([seg, np.zeros(n - len(seg), np.float32)])
        parts.append(seg)
        t = e
    y = np.concatenate(parts).astype(np.float32)
    y.tofile(out_prefix + ".f32")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", out_prefix + ".f32",
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out_prefix + "16.wav"], check=True)
    return out_prefix + "16.wav", len(y) / SR


def whisper(model, wav16, prompt=None):
    """-> [(from_ms, to_ms, word)] in file time (one word per segment)."""
    of = wav16[:-4]
    cmd = ["whisper-cli", "-m", model, "-f", wav16, "-l", "en", "-ml", "1", "-sow", "-ojf", "-of", of]
    if prompt:
        cmd += ["--prompt", prompt]
    subprocess.run(cmd, capture_output=True, check=True)
    d = json.load(open(of + ".json"))
    out = []
    for s in d["transcription"]:
        t = s["text"].strip()
        if t:
            out.append((s["offsets"]["from"], s["offsets"]["to"], t))
    return out


def runs_db(y, S0, th=-62.0, merge=30, minpk=-57.0):
    """speech runs (project ms) from a 10 ms / 5 ms-hop RMS envelope: >= th dB, merged across <= merge + 5 ms, peak >= minpk."""
    w, h, HOP = 480, 240, 5
    n = (len(y) - w) // h + 1
    c = np.concatenate([[0], np.cumsum(y.astype(np.float64) ** 2)])
    idx = np.arange(n) * h
    db = 10 * np.log10((c[idx + w] - c[idx]) / w + 1e-12)
    tc = S0 + (idx + w / 2) / SR * 1000.0
    out, cur = [], None
    for t, d in zip(tc, db):
        if d >= th:
            if cur and t - cur[1] <= merge + HOP:
                cur[1] = t
                cur[2] = max(cur[2], d)
            else:
                if cur:
                    out.append(cur)
                cur = [t, t, d]
    if cur:
        out.append(cur)
    return [(s - HOP / 2, e + HOP / 2) for s, e, pk in out if pk >= minpk and e - s >= 20]


# ------------------------------------------------------------------------------------------------ text
SPEAKER_TAG = re.compile(r"\[[A-Za-z][\w .'-]{0,40}:\]")


def fix_names(wh, plan_text, force, wt=None, dropped=None):
    """canonical word sequence = the plan's curated text (names, punctuation), plus whisper's extra words where the audio
    holds speech the plan does not list; then forced name fixes. Returns (words, placeholder labels in order)."""
    pt = SPEAKER_TAG.sub(" ", plan_text)
    labels = re.findall(r"\[[^\]]*\]", pt)
    pt = re.sub(r"\[[^\]]*\]", " UNCLEARX ", pt)          # placeholders such as [unclear app names]
    plan = []
    for w in pt.split():
        if not core(w) and plan:                             # punctuation-only token: attach to the previous word
            plan[-1] += w
        elif w:
            plan.append(w)
    a = [core(w) for w in wh]
    b = [core(w) for w in plan]
    out = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "insert":            # plan-only words: whisper missed them (a quiet second voice) OR the audio no longer has
            # them (a later app cut removed them). Keep them only when the gap between whisper's neighbouring words can
            # hold them (>= half of ~250 ms per word); 1-2 word omissions are whisper slips (offsets abut).
            if wt is not None:
                prev_end = wt[i1 - 1][1] if i1 > 0 else 0
                next_start = wt[i1][0] if i1 < len(wt) else prev_end + 10 ** 7
                if (j2 - j1) >= 3 and next_start - prev_end < 0.5 * 250 * (j2 - j1):
                    if dropped is not None:
                        dropped.append((" ".join(plan[j1:j2]), prev_end, next_start))
                    continue
            out += plan[j1:j2]
        elif op == "delete":          # whisper-only words: real audio the plan does not list
            out += wh[i1:i2]
        elif op == "replace" and any(core(x) == "unclearx" for x in plan[j1:j2]):
            out += ["UNCLEARX:" + w for w in wh[i1:i2]]   # unintelligible in the plan: whisper's guess, merged after alignment
        else:                         # equal / replace: the plan's spelling
            out += plan[j1:j2]
    for i, w in enumerate(out):
        c = core(w)
        if c in force:
            tail = re.search(r"[.,?!;:]+$", w)
            out[i] = force[c] + (tail.group(0) if tail else "")
    return out, labels


def plan_for(sid, plans, plan_dir):
    if plans and sid in plans:
        v = plans[sid]
        if isinstance(v, dict):
            return v
        return B.load_json(v)
    if plan_dir and os.path.isdir(plan_dir):
        for f in sorted(os.listdir(plan_dir)):
            if f.endswith(".json"):
                try:
                    d = B.load_json(os.path.join(plan_dir, f))
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("scene_id") == sid and d.get("text"):
                    return d
    return None


def scene_words(ctx, snap, sid, a, model, force, plans, out_dir, work_dir):
    sc = [s for s in snap["scenes"] if s[0] == sid][0]
    S0, S1 = sc[2], sc[3]
    mic = sorted((v[1], v[2], v[3], v[4], v[5]) for v in snap["content"].values() if v[0].startswith("microphone") and S0 <= v[1] < S1)
    if not mic:
        return {"scene": sid, "skipped": "no microphone content"}
    pref = os.path.join(work_dir, sid)
    if mic[0][0] > S0:
        mic = [(S0, S0, mic[0][2], mic[0][3], mic[0][3])] + mic
    wav16, secs = rebuild(ctx.medias_dir, mic, pref, work_dir)
    y = np.fromfile(pref + ".f32", dtype=np.float32)
    W0 = mic[0][0]
    plan = plan_for(sid, plans, a.plan_dir)
    ws = whisper(model, wav16, prompt=a.prompt)
    dropped = []
    if plan and plan.get("text"):
        words, labels = fix_names([w[2] for w in ws], plan["text"], force, [(w[0], w[1]) for w in ws], dropped)
    else:
        words, labels = [w[2] for w in ws], []
        for i, w in enumerate(words):
            c = core(w)
            if c in force:
                tail = re.search(r"[.,?!;:]+$", w)
                words[i] = force[c] + (tail.group(0) if tail else "")
    for d_ in dropped:
        print("  %s: plan words not in the audio any more (gap %d ms between whisper words at %d-%d): %r" % (
            sid, d_[2] - d_[1], W0 + d_[1], W0 + d_[2], d_[0]), file=sys.stderr)
    unclear = [w.startswith("UNCLEARX:") for w in words]
    words = [w[9:] if w.startswith("UNCLEARX:") else w for w in words]
    # a placeholder whisper had nothing for: a 2-word filler so CTC reserves its time (merged back into one token below)
    w2, u2 = [], []
    for w, u in zip(words, unclear):
        if core(w) == "unclearx":
            tail = re.search(r"[.,?!;:]+$", w)
            w2 += ["unclear", "speech" + (tail.group(0) if tail else "")]
            u2 += [True, True]
        else:
            w2.append(w)
            u2.append(u)
    words, unclear = w2, u2
    if not words:
        return {"scene": sid, "skipped": "no words heard"}
    B.write_json(pref + "_in.json", {"f32": pref + ".f32", "words": words})
    o = subprocess.run(CTC_CMD + [pref + "_in.json", pref + "_ali.json"], capture_output=True, text=True)
    if o.returncode:
        raise RuntimeError(o.stderr[-1500:])
    A = B.load_json(pref + "_ali.json")
    Rn = runs_db(y, W0)

    def run_containing(t, tol=0):
        for r in Rn:
            if r[0] - tol <= t <= r[1] + tol:
                return r
        return None
    wl = [[W0 + x[0], W0 + x[1], x[2]] for x in A]
    for i, wd in enumerate(wl):
        cands = [r for r in Rn if wd[0] - 150 <= r[0] <= wd[0] + 30]
        r = min(cands, key=lambda r: abs(r[0] - wd[0])) if cands else None
        if r and (i == 0 or wl[i - 1][1] <= r[0] + 120):
            wd[0] = max(r[0], wl[i - 1][1] if i else r[0])
    for i, wd in enumerate(wl):
        nxt = wl[i + 1][0] if i + 1 < len(wl) else None
        r = run_containing(wd[1], tol=60) or run_containing(wd[0], tol=60)
        if nxt is not None and r and nxt <= r[1] + 30:
            wd[1] = nxt
        elif r and r[1] >= wd[1] - 30 and r[1] - wd[1] <= 400:
            wd[1] = r[1]
        else:
            wd[1] = wd[1] + 60
        if nxt is not None:
            wd[1] = min(wd[1], nxt)
        wd[1] = min(wd[1], S1)
    out = [[int(round(p)), int(round(q)), t] for p, q, t in wl]
    if any(unclear):
        unclear = unclear[:len(out)]
        merged, i, li = [], 0, 0
        while i < len(out):
            if i < len(unclear) and unclear[i]:
                j = i
                while j + 1 < len(out) and j + 1 < len(unclear) and unclear[j + 1]:
                    j += 1
                tail = re.search(r"[.,?!;:]+$", out[j][2])
                lab = labels[li] if li < len(labels) else "[unclear]"
                li += 1
                merged.append([out[i][0], out[j][1], lab + (tail.group(0) if tail else "")])
                i = j + 1
            else:
                merged.append(out[i])
                i += 1
        out = merged
    for p, q in zip(out, out[1:]):
        if q[0] < p[1]:
            p[1] = q[0]
    for x in out:
        if x[1] <= x[0]:
            x[1] = x[0] + 1
    # a second (quiet) voice on the same mic, tagged in the plan: label its lines, re-time the host's next line to the
    # host's own loud onset
    secondary = []
    tagname = a.secondary_speaker
    if tagname and plan and ("[%s:]" % tagname) in plan.get("text", ""):
        for seg in re.findall(r"\[%s:\]\s*(.*?)(?=\[[A-Za-z][\w .'-]{0,40}:\]|$)" % re.escape(tagname), plan["text"], flags=re.S):
            secondary.append(set(core(w) for w in seg.split() if core(w)))
    extra = set(x.strip() for x in (a.secondary_lines or []))
    openers = tuple(x.lower() for x in a.primary_openers)

    def is_sec(ws_):
        cw = set(core(z[2]) for z in ws_ if core(z[2]))
        txt = " ".join(z[2] for z in ws_)
        return txt in extra or any(len(cw & j) >= max(2, 0.6 * len(cw)) for j in secondary)
    if secondary:
        hh = 480

        def db_at(t):
            i = int((t - W0) / 1000 * SR)
            seg = y[max(0, i):i + hh]
            return 20 * np.log10(np.sqrt((seg.astype(np.float64) ** 2).mean()) + 1e-12) if len(seg) else -99
        tmp, cur2 = [], []
        for x in out:
            cur2.append(x)
            if re.search(r"[.!?]$", x[2]):
                tmp.append(cur2)
                cur2 = []
        if cur2:
            tmp.append(cur2)
        for a_, b_ in zip(tmp, tmp[1:]):
            if is_sec(a_) and not is_sec(b_) and db_at(b_[0][0]) < a.quiet_db:
                t0 = b_[0][0]
                on = next((t for t in range(int(t0), int(t0) + 2500, 5) if db_at(t) >= a.onset_db), None)
                if on is None:
                    continue
                lead = [z for z in b_ if z[0] < on]
                nxt = next((z[0] for z in b_ if z[0] >= on), None)
                end_ = nxt if nxt is not None else lead[-1][1]
                if end_ <= on:
                    continue
                n_ = len(lead)
                step = (end_ - on) / n_
                for q, z in enumerate(lead):
                    z[0] = int(round(on + q * step))
                    z[1] = int(round(on + (q + 1) * step))
                a_[-1][1] = on
                print("  %s: host line after the %s reply re-timed to the host's onset %d: %s" % (sid, tagname, on, " ".join(z[2] for z in lead)),
                      file=sys.stderr)
    sents, cur = [], []
    for x in out:
        cur.append(x)
        if re.search(r"[.!?]$", x[2]):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    SS = []
    for s in sents:
        txt = " ".join(y_[2] for y_ in s)
        if secondary and is_sec(s) and not txt.lower().startswith(openers):
            txt = "[%s] %s" % (tagname, txt)
        SS.append([s[0][0], s[-1][1], txt])
    B.write_json(os.path.join(out_dir, "%s.json" % sid), out)
    B.write_json(os.path.join(out_dir, "%s_sentences.json" % sid), SS)
    res = {"scene": sid, "range": [S0, S1], "words": len(out), "sentences": len(SS), "first": out[0], "last": out[-1],
           "audio_s": round(secs, 3), "dropped_plan_words": dropped}
    if plan and plan.get("text"):
        ratio, ops, na, nb = compare(plan["text"], [x[2] for x in out])
        res["plan_similarity"] = round(ratio, 3)
    low = [(round(x[0] + W0), x[2], round(x[3], 2)) for x in A if x[3] < 0.3]
    res.update({"low_confidence": low[:12], "n_low": len(low)})
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--scene", action="append", default=[], help="scene id (repeatable; default: every scene with mic content)")
    ap.add_argument("--plans", help='JSON {"<scene_id>": "plan.json" | {"text": "..."}}')
    ap.add_argument("--plan-dir", help="folder of plan JSON files that carry {scene_id, text} (edit-borumi-video's "
                    "plan_scene*.json have no text: use --plans from plans_from_kept.py instead)")
    ap.add_argument("--names", help='JSON {"<lowercase whisper spelling>": "Canonical"} forced fixes, e.g. {"cloud": "Claude"}')
    ap.add_argument("--prompt", help="whisper prompt: names and jargon of the video")
    ap.add_argument("--whisper-model", help="ggml model (default $WHISPER_MODEL, ~/.cache/whisper/ggml-small.bin, ...)")
    ap.add_argument("--secondary-speaker", help="plan tag of a quiet second voice on the mic, e.g. 'Assistant' for '[Assistant:] ...'")
    ap.add_argument("--secondary-lines", nargs="*", help="exact sentence texts that belong to the second voice (e.g. a one-word reply)")
    ap.add_argument("--primary-openers", nargs="*", default=["hey man", "okay", "alright", "all right", "can you", "so "],
                    help="sentence starts that are always the host's")
    ap.add_argument("--quiet-db", type=float, default=-42.0, help="the host line starts quiet (below this) -> look for the onset")
    ap.add_argument("--onset-db", type=float, default=-40.0, help="the host's own onset level")
    B.add_common_args(ap, dry=False)
    a = ap.parse_args()
    ctx = B.context(a)
    if not ctx.medias_dir or not os.path.isdir(ctx.medias_dir):
        B.die("bundle medias dir unknown: set medias_dir in the config or write WORK/project.json bundle_medias")
    model = a.whisper_model or os.environ.get("WHISPER_MODEL")
    if not model:
        model = next((os.path.expanduser(c) for c in MODEL_CANDIDATES if os.path.exists(os.path.expanduser(c))), None)
    if not model or not os.path.exists(os.path.expanduser(model)):
        B.die("no whisper model: pass --whisper-model PATH (a whisper.cpp ggml model, e.g. ggml-small.bin) or set WHISPER_MODEL")
    model = os.path.expanduser(model)
    names = B.load_json(a.names) if a.names else {}
    if not isinstance(names, dict) or any(not isinstance(v_, str) for v_ in names.values()):
        B.die("--names must be a FLAT {\"whisper spelling\": \"Canonical\"} map (examples/names.example.json). A map with "
              "\"fixes\" / \"review\" lists is the CAPTION map for scripts/deliver/fix_srt.py, not for scene_words.py.")
    force = {k_.lower(): v_ for k_, v_ in names.items()}
    plans = B.load_json(a.plans) if a.plans else None
    if plans is not None and not isinstance(plans, dict):
        B.die('--plans must be {"<scene id>": {"text": "..."} | "plan.json"} (scripts/borumi/plans_from_kept.py writes it)')
    out_dir = ctx.dir("words")
    work_dir = ctx.dir("words", "_audio")
    snap = S.snapshot(ctx.key)
    if plans:
        unknown = sorted(k_ for k_ in plans if k_ not in {s_[0] for s_ in snap["scenes"]})
        if unknown:
            B.log("WARNING: plans.json scene ids not on the current timeline (their text is ignored): %s" % ", ".join(unknown))
    ids = a.scene or [s[0] for s in snap["scenes"]
                      if any(v[0].startswith("microphone") and s[2] <= v[1] < s[3] for v in snap["content"].values())]
    res = []
    for sid in ids:
        r = scene_words(ctx, snap, sid, a, model, force, plans, out_dir, work_dir)
        print(json.dumps(r, default=str))
        res.append(r)
    B.write_json(os.path.join(out_dir, "_index.json"), {"hash": snap["hash"], "duration": snap["duration"], "scenes": snap["scenes"],
                                                        "results": res})
    return 0


if __name__ == "__main__":
    sys.exit(main())
