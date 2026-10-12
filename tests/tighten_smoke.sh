#!/usr/bin/env bash
# tighten_smoke.sh - synthetic end-to-end test of the tighter-cut (v-next) scripts. No real media, no network.
#
# usage: bash tests/tighten_smoke.sh
#        PYTHON=/path/to/python-with-numpy bash tests/tighten_smoke.sh
#        KEEP=1 bash tests/tighten_smoke.sh        (keep the temp folder for inspection)
# needs: ffmpeg + ffprobe >= 6 (libx264), python3 >= 3.9 with numpy (falls back to `uv run --with numpy` when uv exists).
#
# It generates a 30 s 640x360 @ 60 fps testsrc2 "master" whose mono 48 kHz audio has one tone burst per word (whisper-
# style contiguous word times, 450 ms silences between sentences, one 1.5 s pause), a chapter card range, word +
# sentence files, an SRT and a tighten.json, then runs:
#   make_sections -> snap_cuts --tighten-pauses -> apply_pins -> build_cut (tail render + keep-rect mask + panel
#   patch) -> build_cut with 120 removals (select grouping beyond the 100-term parser limit) -> build_captions ->
#   qa_cut (--skip-whisper, PSNR mapping, flash scan) -> collect_cuts on a fake workflow result
# and checks: every edge on the frame grid, frame counts (build report and ffprobe), audio length, red tail-render
# pixels where the mask lets it through and master pixels where it doesn't, the patched panel frames (first one
# included: the half-frame lead), the frame after the patch untouched, captions and chapters mapped through the cut.
case "${1:-}" in -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0;; esac
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
T="$REPO/plugins/proof-first-video-editor/skills/proof-first-video-editor/scripts/tighten"
W="$(mktemp -d "${TMPDIR:-/tmp}/tighten_smoke.XXXXXX")"
cleanup() { if [ "${KEEP:-0}" = "1" ]; then echo "kept: $W"; else rm -rf "$W"; fi; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

command -v ffmpeg >/dev/null || fail "ffmpeg not on PATH"
command -v ffprobe >/dev/null || fail "ffprobe not on PATH"

# ---- pick a python with numpy
PY=()
for cand in "${PYTHON:-}" python3; do
  [ -n "$cand" ] || continue
  if "$cand" -c "import numpy" >/dev/null 2>&1; then PY=("$cand"); break; fi
done
if [ ${#PY[@]} -eq 0 ] && command -v uv >/dev/null; then
  if uv run --quiet --no-project --with numpy python3 -c "import numpy" >/dev/null 2>&1; then
    PY=(uv run --quiet --no-project --with numpy python3)
  fi
fi
[ ${#PY[@]} -gt 0 ] || fail "no python with numpy (set PYTHON=..., pip install numpy, or install uv)"
echo "python: ${PY[*]}"
echo "work:   $W"

step "compile + --help"
for f in "$T"/*.py; do python3 -c "import sys; compile(open(sys.argv[1]).read(), sys.argv[1], 'exec')" "$f" || fail "compile $f"; done
bash -n "$T/frame.sh" || fail "bash -n frame.sh"
for s in make_sections snap_cuts apply_pins build_cut build_captions qa_cut collect_cuts words_from_whisper; do
  "${PY[@]}" "$T/$s.py" --help >/dev/null || fail "$s.py --help"
done

step "generate synthetic master, words, SRT, config"
cd "$W"
"${PY[@]}" - <<'EOF'
import json, os, wave
import numpy as np
SR, DUR = 48000, 30.0
scenes = {"A": (0, 14000), "B": (15600, 30000)}
WORD, GAP, NW = 260, 450, 5
os.makedirs("words", exist_ok=True)
y = np.zeros(int(SR * DUR), np.float32)
srt, k = [], 0
for sid, (a, b) in scenes.items():
    t, words, sents, n = a + 300, [], [], 0
    while t + NW * WORD < b - 400:
        s0 = t
        for i in range(NW):
            w = f"w{k}"; k += 1
            w = w.capitalize() if i == 0 else w
            if i == NW - 1: w += "."
            words.append([t, t + WORD, w])
            i0, i1 = int((t + 15) * SR / 1000), int((t + WORD - 15) * SR / 1000)
            tt = np.arange(i1 - i0) / SR
            env = np.minimum(1, np.minimum(tt, tt[::-1]) / 0.005)
            y[i0:i1] = 0.3 * np.sin(2 * np.pi * 220 * tt) * env
            t += WORD
        sents.append([s0, t, " ".join(x[2] for x in words[-NW:])])
        n += 1
        t += 1500 if (sid == "B" and n == 2) else GAP   # one long pause in B for --tighten-pauses
    json.dump(words, open(f"words/{sid}.json", "w"))
    json.dump(sents, open(f"words/{sid}_sentences.json", "w"))
    srt += sents
pcm = (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()
with wave.open("master.wav", "wb") as f:
    f.setnchannels(1); f.setsampwidth(2); f.setframerate(SR); f.writeframes(pcm)
def ts(ms):
    ms = int(ms); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
with open("master.srt", "w") as f:
    for i, (a, b, t) in enumerate(srt, 1): f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n\n")
cfg = {"master": "master.mp4", "audio_wav": "master.wav", "master_label": "v1", "fps": 60, "sample_rate": SR,
       "words_dir": "words", "sections_dir": "sections", "intro_title": "Intro",
       "sections": [{"key": "S1", "scene": "A", "title": "Part A", "range": [0, 14000]},
                    {"key": "S2", "scene": "B", "title": "Part B", "range": [15600, 30000]}],
       "cards": [{"start_ms": 14000, "end_ms": 15600, "title": "Card B", "chapter": "Level 1: Part B"}],
       "sub_chapters": [{"at_ms": 22000, "title": "Later In B"}]}
json.dump(cfg, open("tighten.json", "w"), indent=1)
print("words:", {s: len(json.load(open(f"words/{s}.json"))) for s in scenes})
EOF
ffmpeg -v error -y -f lavfi -i "testsrc2=size=640x360:rate=60" -i master.wav -t 30 -map 0:v -map 1:a \
  -c:v libx264 -preset ultrafast -g 60 -pix_fmt yuv420p -c:a aac -ac 2 -b:a 128k master.mp4
ffmpeg -v error -y -f lavfi -i "color=c=red:size=640x360:rate=30" -t 4 -c:v libx264 -preset ultrafast -pix_fmt yuv420p tail.mp4
NF=$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 master.mp4)
[ "$NF" = "1800" ] || fail "master has $NF frames, expected 1800"

step "make_sections"
"${PY[@]}" "$T/make_sections.py" --config tighten.json
grep -q "^## Words (index · start_ms · end_ms · gap_to_next_ms · word)" sections/S1_A.md || fail "word table missing"
grep -q "^- \[w5–w9\]" sections/S1_A.md || fail "sentence w5-w9 missing"
grep -q "Card B \[14000–15600\]" sections/S2_B.md || fail "card line missing"
"${PY[@]}" -c "import json; d=json.load(open('sections/index.json')); assert len(d['sections_list'])==2" || fail "index.json"

step "snap_cuts (+ pause tightening)"
NA=$("${PY[@]}" -c "import json; print(len(json.load(open('words/A.json'))))")
cat > cuts.json <<EOF
{"cuts": [
  {"section": "S1", "from_word": 5, "to_word": 9, "first_word": "W5", "last_word": "w9.", "priority": "must", "kind": "redundancy"},
  {"section": "S1", "from_word": $((NA - 5)), "to_word": $((NA - 1)), "first_word": "W$((NA - 5))", "last_word": "w$((NA - 1)).", "priority": "should", "kind": "meta"},
  {"section": "S2", "scene": "B", "from_word": 0, "to_word": 4, "first_word": "W$NA", "last_word": "w$((NA + 4)).", "priority": "should", "kind": "throat-clearing"}
 ],
 "pause_safe": [{"from_ms": 15600, "to_ms": 25000, "note": "static (the trailing silence after 25 s stays)"}]}
EOF
"${PY[@]}" "$T/snap_cuts.py" --config tighten.json cuts.json snapped.json --tighten-pauses
"${PY[@]}" - <<'EOF' || fail "snapped ranges"
import json
d = json.load(open("snapped.json"))
r = d["ranges"]
assert d["n_word_cuts"] == 3 and d["n_pause_cuts"] == 1, (d["n_word_cuts"], d["n_pause_cuts"])
assert not d["warnings"], d["warnings"]
for x in r:
    for e in (x["start_ms"], x["end_ms"]):
        assert abs(e * 0.06 - round(e * 0.06)) < 1e-3, e
assert any(abs(x["end_ms"] - 14000.0) < 1e-6 for x in r), "scene-end cut must end on the grid point at the scene end"
assert any(abs(x["start_ms"] - 15600.0) < 1e-6 for x in r), "scene-start cut must start on the grid point at/before the scene start"
for x in r:
    if x.get("section") != "pause":
        assert x["rms_in_db"] < -60 and x["rms_out_db"] < -60, x
print("ranges ok:", [(x["start_ms"], x["end_ms"]) for x in r])
EOF

step "apply_pins"
"${PY[@]}" - <<'EOF'
import json
d = json.load(open("snapped.json"))
c1 = [x for x in d["ranges"] if x.get("section") == "S1" and x["from_word"] == 5][0]
pins = {"edge_overrides": [{"section": "S1", "from_word": 5, "end_ms": round(c1["end_ms"] + 1000 / 60, 3), "why": "test"}],
        "start_by_value": [{"from_ms": 15600.0, "to_ms": 15616.667, "why": "test"}]}
json.dump(pins, open("pins.json", "w"))
json.dump({"edge_overrides": [{"section": "S1", "from_word": 5, "end_ms": 1234.5}]}, open("pins_bad.json", "w"))
EOF
"${PY[@]}" "$T/apply_pins.py" snapped.json pins.json pinned.json
if "${PY[@]}" "$T/apply_pins.py" snapped.json pins_bad.json pinned_bad.json >/dev/null 2>&1; then fail "off-grid pin accepted"; fi
"${PY[@]}" - <<'EOF' || fail "pinned ranges"
import json
a, b = json.load(open("snapped.json")), json.load(open("pinned.json"))
assert len(b["pins_applied"]) == 2, b["pins_applied"]
assert any(abs(x["start_ms"] - 15616.667) < 1e-6 for x in b["ranges"])
assert abs(a["removed_ms"] - b["removed_ms"]) < 0.1, (a["removed_ms"], b["removed_ms"])  # +1 frame at an end, -1 at a start
print("pins ok")
EOF

step "build_cut (tail render + mask + panel patch)"
echo '[{"from": 300, "to": 330, "src": 400, "why": "test patch"}]' > patches.json
"${PY[@]}" "$T/build_cut.py" pinned.json cut.mp4 --config tighten.json --tail-render tail.mp4 --tail-start-s 24.0 \
  --tail-from-frame 30 --tail-fps 30 --tail-keep-rect 480,0,160,360 --panel-patches patches.json --panel-crop 480:360:0:0 \
  --preset ultrafast --crf 23 || fail "build_cut"
"${PY[@]}" - <<'EOF' || fail "build checks"
import json, subprocess
import numpy as np
edl = json.load(open("cut.mp4.work/edl.json")); rep = json.load(open("cut.mp4.work/build_report.json"))
keep = edl["keep_frames"]; exp = sum(e - s for s, e in keep)
n = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", "cut.mp4"], capture_output=True, text=True).stdout)
assert n == exp == rep["frames"], (n, exp, rep["frames"])
assert abs(rep["audio_s"] - exp / 60) < 0.05, rep
pieces, o = [], 0
for s, e in keep: pieces.append((s, e, o)); o += e - s
m2o = lambda m: next(oo + (m - s) for s, e, oo in pieces if s <= m < e)
def fr(path, k):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{(k - 0.3) / 60:.6f}", "-i", path, "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(360, 640, 3).astype(np.float32)
mad = lambda p, q: float(np.abs(p - q).mean())
# tail render: master frame 1560 (26.0 s) is inside the overlay window (from 24 + 30/30 = 25 s)
o_t, m_t = fr("cut.mp4", m2o(1560)), fr("master.mp4", 1560)
left = o_t[:, :470].mean(axis=(0, 1))
assert left[0] > 180 and left[1] < 70 and left[2] < 70, ("tail render not visible", left)
assert mad(o_t[:, 490:], m_t[:, 490:]) < 12, ("keep-rect must show the master", mad(o_t[:, 490:], m_t[:, 490:]))
o_b, m_b = fr("cut.mp4", m2o(1440)), fr("master.mp4", 1440)   # 24.0 s: before the overlay starts
assert mad(o_b, m_b) < 12, ("no overlay before tail-from-frame", mad(o_b, m_b))
# panel patch: master frames [300, 330) show master frame 400 in the 480x360 panel, frame 330 is untouched
src = fr("master.mp4", 400)
for m in (300, 315, 329):
    o_p = fr("cut.mp4", m2o(m))
    assert mad(o_p[:, :480], src[:, :480]) < 12, (m, "patch missing", mad(o_p[:, :480], src[:, :480]))
    assert mad(o_p[:, 480:], fr("master.mp4", m)[:, 480:]) < 12, (m, "outside the panel must stay")
o_after = fr("cut.mp4", m2o(330))
assert mad(o_after[:, :480], fr("master.mp4", 330)[:, :480]) < 12, "frame after the patch must be the master"
assert mad(o_after[:, :480], src[:, :480]) > mad(o_after[:, :480], fr("master.mp4", 330)[:, :480])
pm = json.load(open("cut.mp4.work/patch_out_frames.json"))[0]
assert pm["out_first"] == m2o(300) and pm["out_last"] == m2o(329), pm
print("build ok:", n, "frames; tail, mask and patch verified")
EOF

step "build_cut with > 100 keep pieces (grouped select) in --limit-s mode"
"${PY[@]}" -c "
import json
json.dump({'ranges': [{'start_ms': round((2 * k + 1) * 1000 / 60, 3), 'end_ms': round((2 * k + 2) * 1000 / 60, 3)} for k in range(120)]},
          open('many.json', 'w'))"
"${PY[@]}" "$T/build_cut.py" many.json many.mp4 --config tighten.json --limit-s 6 --preset ultrafast --crf 35 >/dev/null || fail "grouped select build"
grep -q "(between" many.mp4.work/graph.txt || fail "select terms were not grouped"
N2=$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 many.mp4)
[ "$N2" = "240" ] || fail "grouped build has $N2 frames, expected 240"
echo "grouped select ok: 121 pieces -> $N2 frames"

step "build_captions"
"${PY[@]}" "$T/build_captions.py" --config tighten.json cut.mp4.work/edl.json master.srt cut.srt --chapters-out chapters.txt
"${PY[@]}" - <<'EOF' || fail "captions/chapters"
import json, math
cues = [b for b in open("cut.srt").read().strip().split("\n\n") if "-->" in b]
A = json.load(open("words/A_sentences.json")); B = json.load(open("words/B_sentences.json"))
removed_sents = [A[1][2], A[-1][2], B[0][2]]
kept = len(A) + len(B) - 3
assert len(cues) == kept, (len(cues), kept)
txt = open("cut.srt").read()
for s in removed_sents: assert s not in txt, s
ch = open("chapters.txt").read().splitlines()
assert ch[0] == "Chapters" and ch[1] == "0:00 Intro", ch
assert any(l.endswith(" Level 1: Part B") for l in ch) and any(l.endswith(" Later In B") for l in ch), ch
edl = json.load(open("cut.mp4.work/edl.json"))
pieces, o = [], 0.0
for s, e in edl["keep_frames"]: pieces.append((s * 1000 / 60, e * 1000 / 60, o)); o += (e - s) * 1000 / 60
def mapt(t):
    for a, b, oo in pieces:
        if a <= t < b: return oo + (t - a)
        if t < a: return oo
    return o
want = math.ceil(mapt(14001) / 1000)
assert f"{want // 60}:{want % 60:02d} Level 1: Part B" in ch, (want, ch)
print("captions ok:", len(cues), "cues;", ch)
EOF

step "qa_cut (--skip-whisper, PSNR mapping, flash scan)"
"${PY[@]}" "$T/qa_cut.py" cut.mp4 cut.mp4.work/edl.json qa --config tighten.json --skip-whisper --psnr-joins 3 \
  --flash-scan --panel-crop 480:360:0:0 >/dev/null || fail "qa_cut"
"${PY[@]}" - <<'EOF' || fail "qa report"
import json
r = json.load(open("qa/qa_report.json"))
assert r["frames_in_file"] == r["frames_expected"], r
assert r["joins"] == 4 and len(r["join_sheets"]) == 1, (r["joins"], r["join_sheets"])
assert not r["click_suspects"], r["click_suspects"]
assert not r["loud_joins"], r["loud_joins"]
assert all(p["psnr_before_db"] > 28 and p["psnr_after_db"] > 28 for p in r["psnr_mapping"]), r["psnr_mapping"]
assert len(r["flash_scan"]) == 4
print("qa ok:", {k: r[k] for k in ("joins", "duration_s", "loudness")})
EOF

step "collect_cuts on a fake workflow result"
"${PY[@]}" - <<'EOF'
import json
c = json.load(open("cuts.json"))["cuts"]
fin = lambda cs: {"cuts": [dict(x, removed_text="", reason="r", join_reads="j") for x in cs], "flags": ["PIN the out-point"],
                  "pause_tighten_safe": [{"from_ms": 15600, "to_ms": 30000}]}
res = {"result": {"results": [
    {"key": "S1", "scene": "A", "final": fin([dict(c[0], pin_end_ms=3816.6), c[1]]), "verdict": {"ok": True, "issues": []}, "revised": False},
    {"key": "S2", "scene": "B", "final": fin([dict(c[2], priority="could")]), "verdict": {"ok": False, "issues": [{"severity": "blocking", "problem": "p", "fix": "f"}]}, "revised": True}]}}
json.dump(res, open("wf_result.json", "w"))
EOF
"${PY[@]}" "$T/collect_cuts.py" wf_result.json collected.json --config tighten.json --skip S1:5 --pins-draft pins_draft.json >/dev/null
"${PY[@]}" - <<'EOF' || fail "collect_cuts"
import json
d = json.load(open("collected.json")); p = json.load(open("pins_draft.json"))
assert [(x["section"], x["from_word"]) for x in d["cuts"]][0][0] == "S1" and len(d["cuts"]) == 2, d["cuts"]
assert all("scene" in x for x in d["cuts"])
assert p["edge_overrides"] == [], p   # the pinned cut was skipped
print("collect ok")
EOF

echo
echo "TIGHTEN SMOKE TEST: ALL PASS"
