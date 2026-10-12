#!/usr/bin/env python3
"""mp4_set_duration.py - shorten the LAST video sample of a single-track MP4 / MOV so the track lasts EXACTLY target ms.

usage: mp4_set_duration.py IN.mp4 OUT.mp4 TARGET_MS

Why: Borumi reads a take's length from the file's duration (not from the frame count). A render placed into a slot of
L ms must last exactly L ms, but L is rarely a whole number of 30 fps frames. Encode the first ceil(L*30/1000) frames,
then cut the last sample's duration so the track ends at L (the last frame is just shown for less time).

The input must be what bwlib.exact_copy() encodes:
  ffmpeg ... -frames:v N [-c:v libx264 -bf 0 | -c:v prores_ks -profile:v 4444] -an -use_editlist 0 \\
             -video_track_timescale 30000 IN.mp4
  - exactly one track (no audio), moov AFTER mdat (ffmpeg's default without +faststart),
  - no ctts box (H.264 with -bf 0; ProRes has none), no edts (-use_editlist 0),
  - a media timescale in which TARGET_MS is representable (30000: every whole ms is).
It rewrites stts (last delta), mdhd / mvhd / tkhd durations and grows the parent box sizes. Stdlib only.
"""
import struct
import sys


def boxes(buf, start, end):
    i = start
    while i < end:
        size, typ = struct.unpack(">I4s", buf[i:i + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
            hdr = 16
        yield i, size, typ.decode("latin1"), hdr
        i += size


def find(buf, start, end, path):
    for off, size, typ, hdr in boxes(buf, start, end):
        if typ == path[0]:
            if len(path) == 1:
                return off, size, hdr
            return find(buf, off + hdr, off + size, path[1:])
    return None


def set_duration(src, dst, target_ms):
    data = bytearray(open(src, "rb").read())
    top = list(boxes(data, 0, len(data)))
    order = [t for _, _, t, _ in top]
    assert "mdat" in order and "moov" in order, "not an MP4/MOV with mdat + moov"
    assert order.index("mdat") < order.index("moov"), "moov must come after mdat (do not use +faststart)"
    moov = [b for b in top if b[2] == "moov"][0]
    m_off, m_size, m_hdr = moov[0], moov[1], moov[3]
    mvhd = find(data, m_off + m_hdr, m_off + m_size, ["mvhd"])
    traks = [b for b in boxes(data, m_off + m_hdr, m_off + m_size) if b[2] == "trak"]
    assert len(traks) == 1, "expected one track (encode with -an)"
    t_off, t_size, _, t_hdr = traks[0]
    mdhd = find(data, t_off + t_hdr, t_off + t_size, ["mdia", "mdhd"])
    tkhd = find(data, t_off + t_hdr, t_off + t_size, ["tkhd"])
    stts = find(data, t_off + t_hdr, t_off + t_size, ["mdia", "minf", "stbl", "stts"])
    assert find(data, t_off + t_hdr, t_off + t_size, ["mdia", "minf", "stbl", "ctts"]) is None, "ctts present (encode with -bf 0)"
    assert find(data, t_off + t_hdr, t_off + t_size, ["edts"]) is None, "edts present (encode with -use_editlist 0)"

    def ver(off, hdr):
        return data[off + hdr]
    # media timescale
    o, s, h = mdhd
    ts_off = o + h + 4 + (16 if ver(o, h) == 1 else 8)
    media_ts = struct.unpack(">I", data[ts_off:ts_off + 4])[0]
    o, s, h = mvhd
    mts_off = o + h + 4 + (16 if ver(o, h) == 1 else 8)
    movie_ts = struct.unpack(">I", data[mts_off:mts_off + 4])[0]
    target_media = target_ms * media_ts
    assert target_media % 1000 == 0, "target not representable in media timescale %d (use -video_track_timescale 30000)" % media_ts
    target_media //= 1000
    target_movie = target_ms * movie_ts // 1000
    # stts entries -> per-sample deltas, shorten the last one
    o, s, h = stts
    n = struct.unpack(">I", data[o + h + 4:o + h + 8])[0]
    ents = [struct.unpack(">II", data[o + h + 8 + 8 * j:o + h + 16 + 8 * j]) for j in range(n)]
    deltas = [d for c, d in ents for _ in range(c)]
    cur = sum(deltas)
    cut = cur - target_media
    assert cut != 0 and cut < deltas[-1], "need cut (%d) != 0 and < last delta (%d): wrong frame count for the target" % (cut, deltas[-1])
    deltas[-1] -= cut
    new = []
    for d in deltas:
        if new and new[-1][1] == d:
            new[-1][0] += 1
        else:
            new.append([1, d])
    payload = struct.pack(">I", 0) + struct.pack(">I", len(new)) + b"".join(struct.pack(">II", c, d) for c, d in new)
    new_stts = struct.pack(">I4s", 8 + len(payload), b"stts") + payload
    grow = len(new_stts) - s

    def set_dur(box, value, fixed_off_v0, fixed_off_v1):
        o_, s_, h_ = box
        v = ver(o_, h_)
        p = o_ + h_ + 4 + (fixed_off_v1 if v == 1 else fixed_off_v0)
        if v == 1:
            data[p:p + 8] = struct.pack(">Q", value)
        else:
            data[p:p + 4] = struct.pack(">I", value)
    # durations first (these boxes sit before stts, so their offsets stay valid after the splice)
    set_dur(mdhd, target_media, 12, 20)   # creation, modification, timescale, duration
    set_dur(mvhd, target_movie, 12, 20)
    set_dur(tkhd, target_movie, 16, 24)   # creation, modification, track_ID, reserved, duration
    o, s, h = stts
    data[o:o + s] = new_stts
    stbl = find(data, t_off + t_hdr, t_off + t_size + grow, ["mdia", "minf", "stbl"])
    minf = find(data, t_off + t_hdr, t_off + t_size + grow, ["mdia", "minf"])
    mdia = find(data, t_off + t_hdr, t_off + t_size + grow, ["mdia"])
    # the parents were located with the OLD sizes; they start before the splice point, so their offsets are unchanged
    for po in (stbl[0], minf[0], mdia[0], t_off, m_off):
        size = struct.unpack(">I", data[po:po + 4])[0]
        data[po:po + 4] = struct.pack(">I", size + grow)
    open(dst, "wb").write(data)
    return "frames %d, %d -> %d media ticks (ts %d), last delta %d; movie dur %d (ts %d)" % (
        len(deltas), cur, target_media, media_ts, deltas[-1], target_movie, movie_ts)


def main(argv):
    if len(argv) != 3 or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv and argv[0] in ("-h", "--help") else 64
    print(set_duration(argv[0], argv[1], int(argv[2])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
