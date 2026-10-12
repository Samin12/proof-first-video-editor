#!/usr/bin/env python3
"""align_ctc.py - CTC forced alignment of a KNOWN word list on a 48 kHz mono float32 audio file
(torchaudio WAV2VEC2_ASR_BASE_960H), giving word start / end times far tighter than whisper's.

usage: align_ctc.py IN.json OUT.json
  IN  = {"f32": "/path/audio.f32", "words": ["word", ...]}        raw little-endian float32, mono, 48 kHz
  OUT = [[start_ms, end_ms, word, score], ...]                    FILE time (ms from the start of the f32 file)
run it with its pinned deps (proven combination):
  uv run --no-project --python 3.12 --with torch==2.5.1 --with torchaudio==2.5.1 --with numpy python align_ctc.py IN.json OUT.json
(scene_words.py builds IN.json from a scene's mic pieces and calls this.)

Details that matter:
  - numbers / money / percents are spoken out before alignment ("$1,200" -> "one thousand two hundred dollars", "40%" ->
    "forty percent", "3.5" -> "three point five", "10k" -> "ten k"); "&" -> "and"; "-" and "/" split words.
    A word with no alignable letters becomes "A" so every input word gets a time.
  - the emission runs in 20 s windows with 1 s of context on both sides (long files do not fit at once), then the
    whole token sequence is force-aligned in one pass.
  - score = mean token probability; < 0.3 means "check this word" (the script prints how many).
"""
import json
import re
import sys

try:
    import numpy as np
    import torch
    import torchaudio
except ImportError:      # --help still works
    np = torch = torchaudio = None

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def n2w(n):
    n = int(n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + n2w(n % 100))
    if n < 1000000:
        return n2w(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + n2w(n % 1000))
    if n < 10 ** 9:
        return n2w(n // 10 ** 6) + " million" + ("" if n % 10 ** 6 == 0 else " " + n2w(n % 10 ** 6))
    return n2w(n // 10 ** 9) + " billion" + ("" if n % 10 ** 9 == 0 else " " + n2w(n % 10 ** 9))


def spoken(tok):
    t = tok.strip(".,?!;:\"()")
    m = re.fullmatch(r"(\$?)([\d,]+(?:\.\d+)?)(%|k|K|B|M)?", t)
    if m:
        num = m.group(2).replace(",", "")
        s = n2w(num.split(".")[0]) + ((" point " + " ".join(ONES[int(c)] for c in num.split(".")[1])) if "." in num else "")
        suf = {"%": " percent", "k": " k", "K": " k", "B": " billion", "M": " million"}.get(m.group(3) or "", "")
        return s + suf + (" dollars" if m.group(1) else "")
    return t.replace("&", " and ")


def chars(s):
    s = s.upper().replace("-", " ").replace("/", " ")
    s = re.sub(r"[^A-Z' ]", "", s)
    return [w for w in s.split() if w]


def align(f32_path, words):
    y = np.fromfile(f32_path, dtype=np.float32)
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    labels = bundle.get_labels()
    l2i = {c: i for i, c in enumerate(labels)}
    units = [chars(spoken(w)) or ["A"] for w in words]
    seq, owner = [], []
    for wi, subs in enumerate(units):
        for sw in subs:
            if seq:
                seq.append(l2i["|"])
                owner.append(-1)
            for ch in sw:
                seq.append(l2i[ch])
                owner.append(wi)
    model = bundle.get_model().eval()
    wav = torchaudio.functional.resample(torch.from_numpy(y.copy()).unsqueeze(0), 48000, bundle.sample_rate)
    SR = bundle.sample_rate
    win, ctx = 20 * SR, 1 * SR
    ems = []
    with torch.inference_mode():
        pos, N = 0, wav.shape[1]
        while pos < N:
            a = max(0, pos - ctx)
            b = min(N, pos + win + ctx)
            e, _ = model(wav[:, a:b])
            e = torch.log_softmax(e, dim=-1)[0]
            fpm = e.shape[0] / ((b - a) / SR)
            f0 = int(round((pos - a) / SR * fpm))
            f1 = f0 + int(round(min(win, N - pos) / SR * fpm))
            ems.append(e[f0:f1])
            pos += win
    em = torch.cat(ems)
    fps = em.shape[0] / (N / SR)
    ali, scores = torchaudio.functional.forced_align(em.unsqueeze(0), torch.tensor([seq], dtype=torch.int32), blank=0)
    spans = torchaudio.functional.merge_tokens(ali[0], scores[0].exp())
    assert len(spans) == len(seq), (len(spans), len(seq))
    ws, we, sc = {}, {}, {}
    for sp, own in zip(spans, owner):
        if own < 0:
            continue
        ws[own] = min(ws.get(own, 1e9), sp.start)
        we[own] = max(we.get(own, -1), sp.end)
        sc.setdefault(own, []).append(float(sp.score))
    out = [[ws[i] / fps * 1000.0, we[i] / fps * 1000.0, w, float(np.mean(sc[i]))] for i, w in enumerate(words)]
    return out, fps


def main(argv):
    if len(argv) != 2 or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv and argv[0] in ("-h", "--help") else 64
    if torch is None:
        sys.exit("align_ctc.py needs torch + torchaudio + numpy: uv run --no-project --python 3.12 --with torch==2.5.1 "
                 "--with torchaudio==2.5.1 --with numpy python align_ctc.py IN.json OUT.json")
    D = json.load(open(argv[0]))
    out, fps = align(D["f32"], D["words"])
    json.dump(out, open(argv[1], "w"))
    print("aligned %d words, emission %.2f fps; low-confidence (<0.3): %d" % (len(out), fps, sum(1 for o in out if o[3] < 0.3)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
