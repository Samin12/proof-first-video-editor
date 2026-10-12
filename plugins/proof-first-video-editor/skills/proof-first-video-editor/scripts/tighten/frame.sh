#!/usr/bin/env bash
# frame.sh - grab ONE frame of a video as a jpg so an agent can look at it (Read the jpg).
#
# usage: frame.sh <ms | fN> <out.jpg> [video] [width]
#   <ms>     time in milliseconds (e.g. 553733.333): the frame shown at that time
#   fN       exact frame index N (e.g. f34229), using $TIGHTEN_FPS (default 60): seeks to (N - 0.3)/fps, which lands
#            exactly on frame N (seeking to N/fps can round to the next frame)
#   video    default: $TIGHTEN_MASTER (the finished master being cut)
#   width    output width in px (default 960; 0 = full size)
# examples:
#   TIGHTEN_MASTER="/path/My Video - final edit v2.mp4" frame.sh 305183.333 /tmp/work/frames/s2_305183.jpg
#   frame.sh f23334 /tmp/work/r2chk/n23334.jpg "/path/My Video v3 (build r2).mp4" 480
# Notes: no drawtext (many ffmpeg builds lack it: label frames by file name instead). Under zsh, loops that clean a
# folder with `rm -f *.png` fail with "no matches found" when it is empty: run such loops with bash.
set -euo pipefail
case "${1:-}" in -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0;; esac
if [ $# -lt 2 ]; then
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi
T="$1"; OUT="$2"; VIDEO="${3:-${TIGHTEN_MASTER:-}}"; WIDTH="${4:-960}"; FPS="${TIGHTEN_FPS:-60}"
if [ -z "$VIDEO" ]; then echo "frame.sh: no video (3rd arg or \$TIGHTEN_MASTER)" >&2; exit 2; fi
if [ ! -f "$VIDEO" ]; then echo "frame.sh: video not found: $VIDEO" >&2; exit 2; fi
case "$T" in
  f*) SS=$(awk -v n="${T#f}" -v r="$FPS" 'BEGIN { s = (n - 0.3) / r; if (s < 0) s = 0; printf "%.6f", s }') ;;
  *)  SS=$(awk -v ms="$T" 'BEGIN { printf "%.6f", ms / 1000 }') ;;
esac
mkdir -p "$(dirname "$OUT")"
if [ "$WIDTH" = "0" ]; then VF="null"; else VF="scale=${WIDTH}:-2"; fi
ffmpeg -v error -y -ss "$SS" -i "$VIDEO" -frames:v 1 -vf "$VF" -q:v 3 "$OUT"
