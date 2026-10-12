#!/usr/bin/env python3
"""imgcheck.py - the numpy + Pillow frame analyses behind every writer check (read-only; JSON in, JSON out).

usage:  imgcheck.py SUBCOMMAND '<json payload>'     or     imgcheck.py SUBCOMMAND @payload.json
needs:  numpy + Pillow (pip install numpy pillow, or: uv run --no-project --with numpy --with pillow python imgcheck.py ...)
        ffmpeg (coverage only). bwlib.imgcheck() picks a suitable interpreter and builds the geometry from the config.

All frames are compared at 1920x1080 (or half of it for coverage). Boxes are [x0, y0, x1, y1] in frame px; the defaults
in brackets are the standard look (camera tile right, panel left, badge above the tile) - see bwlib.geometry_payload().

subcommands (payload keys -> output):
  render-psnr   rows [{render_frames: [png...], after: png, before: png}], col_x [1500]
                -> per row {psnr_after, best, all, psnr_before}: PSNR LEFT of the camera column (the tile + badge differ by design)
  tilebox       pairs [[borumi_png, render_png]], x_lo [1300], row_band [300, 800], col_band [1560, 1850], y_lo [215]
                -> camera tile bbox per pair = where the Borumi frame differs from the render's own frame
  alpha-audit   pngs [rgba png...], col_x, panel -> per png [opaque share in the camera column, outside the panel, inside it]
  behind-outside rows [[t, pre_png, post_png, in_slot]], badge_outer, tile_zone -> per row [badge diff, diff outside the
                tile zone, tile zone diff]
  composite     rows [{pre, post, rgba: [png...]}], tile_int, badge_outer -> per row [best PSNR of post vs pre composited
                with the render's RGBA frame (tile + badge excluded), tile diff, badge diff, whole-frame diff]
  swapcmp       rows [{name, post, pre, refs: [png...]}], col_x, tile_zone, tile_inner, badge_inner -> per row dict
  measure       paths, canvas (dot-grid png) + geometry -> per frame {tile_diff, panel_diff, tile_x/y, panel_x/y, left_frac_diff}
                (tile / panel found where the canvas DOTS are hidden: robust to a dark shirt on the dark canvas)
  panelcmp      rows [{borumi, cands: [png...], mode: cover|fit}], panel, margin [30] -> per row PSNR of the panel vs
                each candidate source frame scaled into the panel the same way
  classify      paths + geometry -> per frame verdict STANDARD / OVERSIZE/FULL / BLACK-BG / CANVAS (+BLACK)
  luma          paths -> mean luma per frame
  meandiff      pairs [[a, b, small?]] -> mean |a - b| (small = compare 96x54 thumbnails: same shot, moving content)
  coverage      rows [{t, frame, kind, render?, n?, mode?}], canvas + geometry -> per frame {problems: [...], ...}
"""
import json
import math
import subprocess
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError:      # --help still works; every subcommand needs them
    np = Image = None


def load_payload(arg):
    if arg.startswith("@"):
        with open(arg[1:]) as fh:
            return json.load(fh)
    return json.loads(arg)


def L(p, W=1920, H=1080):
    return np.asarray(Image.open(p).convert("RGB").resize((W, H)), dtype=float)


def LB(p, W=1920, H=1080):
    return np.asarray(Image.open(p).convert("RGB").resize((W, H), Image.BILINEAR), dtype=float)


def psnr_mse(m):
    return 99.0 if m == 0 else 10 * math.log10(255 ** 2 / m)


# ------------------------------------------------------------------------------------------------ placement checks
def render_psnr(P):
    col = int(P.get("col_x", 1500))

    def ps(a, b):
        m = ((a - b) ** 2)[:, :col].mean()   # mask the camera tile + badge column
        return round(psnr_mse(m), 1)
    out = []
    for r in P["rows"]:
        refs = [L(p) for p in r["render_frames"]]
        a = L(r["after"])
        b = L(r["before"])
        vals = [ps(a, x) for x in refs]
        best = max(range(len(vals)), key=lambda i: vals[i])
        out.append({"psnr_after": vals[best], "best": best, "all": vals, "psnr_before": max(ps(b, x) for x in refs)})
    return out


def _run(prof, lo, gap=3, thr=10):
    """longest contiguous run (gaps <= gap px bridged) of mean diff > thr at index >= lo."""
    idx = np.where(prof > thr)[0]
    idx = idx[idx >= lo]
    if len(idx) == 0:
        return None
    runs, s, e = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - e > gap:
            runs.append((s, e))
            s = i
        e = i
    runs.append((s, e))
    s, e = max(runs, key=lambda r: r[1] - r[0])
    return int(s), int(e) + 1


def tilebox(P):
    """the camera tile = where the Borumi frame differs from the render's own frame (the render draws the canvas there).
    Column profile over the row band, row profile over the column band (below the badge: y >= y_lo); runs of mean diff > 10
    (a dark shirt vs the dark canvas still differs by > 10)."""
    x_lo = int(P.get("x_lo", 1300))
    r0, r1 = P.get("row_band", [300, 800])
    c0, c1 = P.get("col_band", [1560, 1850])
    y_lo = int(P.get("y_lo", 215))
    out = []
    for a, b in P["pairs"]:
        d = np.abs(L(a) - L(b)).mean(axis=2)
        xr = _run(d[r0:r1, :].mean(axis=0), x_lo)
        yr = _run(d[:, c0:c1].mean(axis=1), y_lo, gap=int(P.get("gap_rows", 60)))
        out.append([xr[0], yr[0], xr[1], yr[1]] if xr and yr else None)
    return out


def alpha_audit(P):
    col = int(P.get("col_x", 1500))
    x0, y0, x1, y1 = P.get("panel", [19, 19, 1493, 1061])
    r = []
    for p in P["pngs"]:
        a = np.asarray(Image.open(p).convert("RGBA"))[:, :, 3]
        m = np.ones(a.shape, bool)
        m[y0:y1, x0:x1] = False
        r.append([round(float((a[:, col:] > 8).mean()), 5), round(float((a[m] > 8).mean()), 5),
                  round(float((a[y0:y1, x0:x1] > 8).mean()), 4)])
    return r


def behind_outside(P):
    x0, y0, x1, y1 = P.get("badge_outer", [1520, 40, 1895, 225])
    tx0, ty0, tx1, ty1 = P.get("tile_zone", [1520, 220, 1895, 860])
    r = []
    for g, a, b, ins in P["rows"]:
        A = L(a)
        B_ = L(b)
        d = np.abs(A - B_).mean(axis=2)
        m = np.ones(d.shape, bool)
        m[ty0:ty1, tx0:tx1] = False
        r.append([round(float(d[y0:y1, x0:x1].mean()), 2), round(float(d[m].mean()), 3), round(float(d[ty0:ty1, tx0:tx1].mean()), 2)])
    return r


def composite(P):
    """FRONT: the post frame must equal the pre frame composited with the render's own RGBA frame (alpha respected,
    render above the footage) everywhere except the camera tile + badge, which must be unchanged (drawn ABOVE the render)."""
    x0, y0, x1, y1 = P.get("badge_outer", [1520, 40, 1895, 225])
    tx0, ty0, tx1, ty1 = P.get("tile_int", [1530, 230, 1884, 850])
    out = []
    for r in P["rows"]:
        Pp = L(r["pre"])
        Q = L(r["post"])
        best = -1
        m_ = np.ones((1080, 1920), bool)
        m_[ty0:ty1, tx0:tx1] = False
        m_[y0:y1, x0:x1] = False
        for f in r.get("rgba", []):
            F = np.asarray(Image.open(f).convert("RGBA"), dtype=float)
            a = F[:, :, 3:] / 255.0
            C = Pp * (1 - a) + F[:, :, :3] * a
            m = ((C - Q) ** 2)[m_].mean()
            best = max(best, psnr_mse(m))
        d = np.abs(Pp - Q).mean(axis=2)
        out.append([round(best, 1), round(float(d[ty0:ty1, tx0:tx1].mean()), 2), round(float(d[y0:y1, x0:x1].mean()), 2),
                    round(float(d.mean()), 3)])
    return out


def swapcmp(P):
    col = int(P.get("col_x", 1500))
    zy0, zy1 = P.get("tile_zone", [1520, 220, 1895, 860])[1], P.get("tile_zone", [1520, 220, 1895, 860])[3]
    ix0, iy0, ix1, iy1 = P.get("tile_inner", [1550, 250, 1863, 790])
    bx0, by0, bx1, by1 = P.get("badge_inner", [1535, 55, 1880, 195])

    def ps(a, b):
        return round(psnr_mse(((a - b) ** 2).mean()), 1)
    out = []
    for r in P["rows"]:
        Q = L(r["post"])
        Pp = L(r["pre"])
        o = {"name": r.get("name")}
        o["vs_pre_left"] = ps(Q[:, :col], Pp[:, :col])
        o["change_outside_tile"] = round(float(np.abs(Q - Pp)[np.r_[0:zy0, zy1:1080]].mean()), 3)
        if r.get("refs"):
            R = [L(x) for x in r["refs"]]
            s = [ps(Q[:, :col], x[:, :col]) for x in R]
            b = R[int(np.argmax(s))]
            o["vs_render"] = max(s)
            d = np.abs(Q - b).mean(axis=2)
            o["tile_diff"] = round(float(d[iy0:iy1, ix0:ix1].mean()), 1)
            o["badge_diff"] = round(float(d[by0:by1, bx0:bx1].mean()), 1)
        out.append(o)
    return out


# ------------------------------------------------------------------------------------------------ layout measurement
def _covered_runs(match, dot, axis, lo, hi, band, bridge=45):
    """fraction of canvas-dot pixels still visible per column (axis=0) or row (axis=1) inside the band of the other axis;
    returns [first, last covered dot line of the longest run, last visible before it, first visible after it,
    overall last covered line, first visible line after that] or None."""
    if axis == 0:
        m = match[band[0]:band[1], :]
        d = dot[band[0]:band[1], :]
        frac = m.sum(axis=0) / np.maximum(d.sum(axis=0), 1)
        has = d.sum(axis=0) > 0
    else:
        m = match[:, band[0]:band[1]]
        d = dot[:, band[0]:band[1]]
        frac = m.sum(axis=1) / np.maximum(d.sum(axis=1), 1)
        has = d.sum(axis=1) > 0
    idx = [i for i in range(lo, hi) if has[i] and frac[i] < 0.5]
    vis = [i for i in range(lo, hi) if has[i] and frac[i] >= 0.5]
    if not idx:
        return None
    runs, s, e = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - e > bridge or any(e < v < i for v in vis):
            runs.append((s, e))
            s = i
        e = i
    runs.append((s, e))
    s, e = max(runs, key=lambda r: r[1] - r[0])
    prev_vis = max([v for v in vis if v < s], default=None)
    next_vis = min([v for v in vis if v > e], default=None)
    return [int(s), int(e), prev_vis, next_vis, int(max(idx)), min([v for v in vis if v > max(idx)], default=None)]


def _bbox(mask):
    cols, rows = mask.mean(axis=0), mask.mean(axis=1)
    if cols.max() == 0:
        return None
    xs = np.where(cols > 0.3 * cols.max())[0]
    ys = np.where(rows > 0.3 * rows.max())[0]
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def measure(P):
    """per frame, in 1920x1080 px:
      tile:  camera tile bbox on the right, found where the canvas DOTS are hidden (robust to a black shirt on the dark
             canvas): per column / row, the fraction of canvas dot pixels the frame still shows; covered = fraction < 0.5.
      panel: same method on the left.
      tile_diff / panel_diff: bbox of pixels differing from the bare canvas by > 25 (right / left of the camera column).
      left_frac_diff: share of left-side pixels that differ from the bare canvas by > 25 (empty panel area ~ 0)."""
    C = LB(P["canvas"])
    Cg = C.mean(axis=2)
    DOT = Cg > 60                      # dot centres (dots ~82 on an 18 background, 40 px pitch at 1080p)
    hp = int(P.get("half_pitch", 20))
    T = P.get("tile", [1529.5, 229.6, 1883.4, 850.4])
    pan = P.get("panel", [19, 19, 1493, 1061])
    col = int(P.get("col_x", 1500))
    tx_lo = int(round(T[0] - 79.5))                           # 1450
    ty_band = (int(round(T[1] + 30)), int(round(T[3] - 30)))  # (260, 820)
    trow_band = (int(round(T[0] + 30.5)), int(round(T[2] - 33)))  # (1560, 1850)
    py_band = (int(round(pan[1] + 81)), int(round(pan[3] - 61)))  # (100, 1000)
    prow_band = (int(round(pan[0] + 81)), int(round(pan[2] - 93)))  # (100, 1400)
    left_x = int(round(pan[2] - 13))                          # 1480
    ty_lo = int(P.get("tile_y_lo", 0))                        # 215 with the standard badge
    out = []
    for p in P["paths"]:
        F = LB(p)
        d = np.abs(F - C).mean(axis=2)
        Fg = F.mean(axis=2)
        m1 = np.roll(np.roll(Fg, -hp, axis=0), -hp, axis=1)   # the pixels half a pitch away, both diagonals
        m2 = np.roll(np.roll(Fg, hp, axis=0), hp, axis=1)
        match = DOT & (Fg > 50) & ((m1 < 32) | (m2 < 32))    # dot still visible: bright dot next to dark background
        tx = _covered_runs(match, DOT, 0, tx_lo, 1920, ty_band)
        ty = _covered_runs(match, DOT, 1, 0, 1080, trow_band)
        px = _covered_runs(match, DOT, 0, 0, col, py_band)
        py = _covered_runs(match, DOT, 1, 0, 1080, prow_band)
        md = d > 25
        r_ = np.zeros_like(md)
        r_[ty_lo:, col:] = md[ty_lo:, col:]     # below the step badge (it would merge into the tile box)
        l_ = np.zeros_like(md)
        l_[:, :col] = md[:, :col]
        out.append({"frame": p, "tile_diff": _bbox(r_), "panel_diff": _bbox(l_), "tile_x": tx, "tile_y": ty, "panel_x": px,
                    "panel_y": py, "left_frac_diff": round(float(md[:, :left_x].mean()), 3)})
    return out


def panelcmp(P):
    """PSNR of the Borumi left panel (inner part, rounded corners excluded) against each candidate source frame scaled
    into the panel the same way (cover = scale to fill the panel and centre-crop; fit = the panel is the source aspect)."""
    X0, Y0, X1, Y1 = P.get("panel", [19, 19, 1493, 1061])
    M = int(P.get("margin", 30))
    W, H = X1 - X0, Y1 - Y0

    def src_panel(p):
        im = Image.open(p).convert("RGB")
        sw, sh = im.size
        s = max(W / sw, H / sh)
        nw, nh = round(sw * s), round(sh * s)
        im = im.resize((nw, nh), Image.BILINEAR)
        l, t = (nw - W) // 2, (nh - H) // 2
        return np.asarray(im.crop((l, t, l + W, t + H)), dtype=float)
    out = []
    for r in P["rows"]:
        Bp = LB(r["borumi"])[Y0:Y1, X0:X1]
        vals = []
        for c in r["cands"]:
            Sp = src_panel(c)
            a, b = Bp[M:-M, M:-M], Sp[M:-M, M:-M]
            mse = ((a - b) ** 2).mean()
            vals.append(99.0 if mse == 0 else round(10 * math.log10(255 ** 2 / mse), 2))
        out.append(vals)
    return out


def classify(P):
    """gutterR = mean luma between the panel's right edge and the camera tile; gutterL = left of the panel;
    black = share of near-black pixels inside the panel."""
    col = int(P.get("col_x", 1500))
    T = P.get("tile", [1529.5, 229.6, 1883.4, 850.4])
    pan = P.get("panel", [19, 19, 1493, 1061])
    gx1 = int(round(T[0] - 7.5))                       # 1522
    lx0, lx1 = 2, max(3, int(pan[0]) - 5)              # 2 .. 14
    px0, py0, px1, py1 = int(pan[0]) + 11, int(pan[1]) + 11, int(pan[2]) - 13, int(pan[3]) - 11   # 30, 30, 1480, 1050
    out = []
    for p in P["paths"]:
        im = Image.open(p).convert("L")
        w, h = im.size
        a = np.asarray(im.resize((1920, 1080)), dtype=float)
        gR = a[120:960, col:gx1].mean()
        gL = a[120:960, lx0:lx1].mean()
        panel = a[py0:py1, px0:px1]
        black = float((panel < 6).mean())
        pmean, pstd = panel.mean(), panel.std()
        v = "STANDARD"
        if gR > 45 or gL > 45:
            v = "OVERSIZE/FULL"
        elif gR < 8 and gL < 8:
            v = "BLACK-BG"
        elif pmean < 30 and pstd < 15:
            v = "CANVAS"   # nothing in the panel area (dark canvas only)
        if black > 0.25:
            v += "+BLACK"
        out.append({"path": p, "size": [w, h], "gutterR": round(gR, 1), "gutterL": round(gL, 1), "black": round(black, 3),
                    "panel_mean": round(float(pmean), 1), "verdict": v})
    return out


def luma(P):
    """mean luma ("L", default) or mean of R, G, B ("RGB") per frame."""
    mode = P.get("mode", "L")
    return [round(float(np.asarray(Image.open(p).convert(mode), dtype=float).mean()), 2) for p in P["paths"]]


def meandiff(P):
    def raw(p):
        return np.asarray(Image.open(p).convert("RGB"), dtype=float)

    def small(p):
        return np.asarray(Image.open(p).convert("RGB").resize((96, 54), Image.BILINEAR), dtype=float)
    out = []
    for row in P["pairs"]:
        a, b = row[0], row[1]
        sm = bool(row[2]) if len(row) > 2 else False
        f = small if sm else raw
        out.append(round(float(np.abs(f(a) - f(b)).mean()), 3))
    return out


# ------------------------------------------------------------------------------------------------ coverage
def coverage(P):
    rows = P["rows"]
    SW, SH = 960, 540
    T = P.get("tile", [1529.5, 229.6, 1883.4, 850.4])
    bi = P.get("badge_inner", [1535, 55, 1880, 195])
    pan = P.get("panel", [19, 19, 1493, 1061])
    col_h = int(P.get("col_x", 1500)) // 2
    TILE = (int(round(T[0] / 2)), int(round(T[1] / 2)), int(T[2] / 2), int(T[3] / 2))          # (765, 115, 941, 425)
    BADGE = (int(round(bi[0] / 2)), int(round(bi[1] / 2)), int(bi[2] / 2), int(bi[3] / 2))    # (768, 28, 940, 97)
    PF = ((pan[0] + 11) // 2, (pan[1] + 11) // 2, (pan[2] - 13) // 2, (pan[3] - 11) // 2)      # (15, 15, 740, 525)
    REF = {}   # (render path, n) -> downscaled frame (RGB or RGBA)
    need = {}
    for r in rows:
        if r.get("render"):
            need.setdefault(r["render"], set()).update({max(0, r["n"] + d) for d in (-1, 0, 1)})
    for path, ns in need.items():
        rgba = path.lower().endswith(".mov")
        ch = 4 if rgba else 3
        p = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vf", "scale=%d:%d" % (SW, SH), "-f", "rawvideo",
                              "-pix_fmt", "rgba" if rgba else "rgb24", "-"], stdout=subprocess.PIPE)
        sz = SW * SH * ch
        n = 0
        last = max(ns)
        while n <= last:
            buf = p.stdout.read(sz)
            if len(buf) < sz:
                break
            if n in ns:
                REF[(path, n)] = np.frombuffer(buf, np.uint8).reshape(SH, SW, ch).astype(float)
            n += 1
        p.kill()
    CAN = np.asarray(Image.open(P["canvas"]).convert("RGB").resize((1920, 1080), Image.BILINEAR), dtype=float)
    CAN = np.asarray(Image.fromarray(CAN.astype(np.uint8)).resize((SW, SH), Image.BILINEAR), dtype=float)

    def Lh(p):
        return np.asarray(Image.open(p).convert("RGB").resize((SW, SH), Image.BILINEAR), dtype=float)

    def psnr(a, b):
        m = ((a - b) ** 2).mean()
        return 99.0 if m == 0 else 10 * math.log10(255 ** 2 / m)
    out = []
    for r in rows:
        F = Lh(r["frame"])
        pr = []
        info = {"t": r["t"], "kind": r["kind"], "frame": r["frame"]}
        if r["kind"] == "card":
            lum = float(F.mean())
            info["lum"] = round(lum, 1)
            if lum < float(P.get("card_min_luma", 200)):
                pr.append("card frame not white (%.0f)" % lum)
        else:
            if r["kind"] == "behind":
                refs = [REF[(r["render"], max(0, r["n"] + d))] for d in (-1, 0, 1) if (r["render"], max(0, r["n"] + d)) in REF]
                if not refs:
                    pr.append("no reference render frame (n=%d)" % r["n"])
                    info["problems"] = pr
                    out.append(info)
                    continue
                ps = [psnr(F[:, :col_h], x[:, :col_h]) for x in refs]
                base = refs[max(range(len(refs)), key=lambda i: ps[i])]
                info["panel_psnr"] = round(max(ps), 1)
                if max(ps) < 30:
                    pr.append("panel is not the render's frame (%.1f dB)" % max(ps))
            else:
                base = CAN
                d = np.abs(F - CAN).mean(axis=2)
                fill = float((d[PF[1]:PF[3], PF[0]:PF[2]] > 25).mean())
                info["panel_fill"] = round(fill, 3)
                if fill < 0.5:
                    pr.append("panel looks empty (%.2f of the panel differs from the canvas)" % fill)
                if r["kind"] == "front":
                    best = 0
                    for dd in (-1, 0, 1):
                        A = REF.get((r["render"], max(0, r["n"] + dd)))
                        if A is None:
                            continue
                        m = A[:, :, 3] > 242
                        if m.sum() > 500:
                            best = max(best, psnr(F[m], A[:, :, :3][m]))
                        else:
                            best = 99
                    info["render_opaque_psnr"] = round(best, 1)
                    if best < 20:
                        pr.append("render not drawn over the footage (opaque px %.1f dB)" % best)
            d = np.abs(F - base).mean(axis=2)
            x0, y0, x1, y1 = TILE
            tile_mean = float(d[y0 + 10:y1 - 30, x0 + 10:x1 - 10].mean())
            info["tile_diff"] = round(tile_mean, 1)
            edge_l = float(d[y0 + 20:y1 - 60, x0 - 9:x0 - 3].mean())
            edge_r = float(d[y0 + 20:y1 - 60, x1 + 3:x1 + 9].mean())
            if tile_mean < 15:
                pr.append("camera tile missing (tile region = background, %.1f)" % tile_mean)
            if r["kind"] != "behind" and (edge_l > 12 or edge_r > 12):
                pr.append("something outside the tile edges (%.1f / %.1f)" % (edge_l, edge_r))
            bx0, by0, bx1, by1 = BADGE
            bd = float(d[by0:by1, bx0:bx1].mean())
            info["badge_diff"] = round(bd, 1)
            if r.get("badge", True) and bd < 8:
                pr.append("badge missing (%.1f)" % bd)
        info["problems"] = pr
        out.append(info)
    return out


SUBS = {"render-psnr": render_psnr, "tilebox": tilebox, "alpha-audit": alpha_audit, "behind-outside": behind_outside,
        "composite": composite, "swapcmp": swapcmp, "measure": measure, "panelcmp": panelcmp, "classify": classify,
        "luma": luma, "meandiff": meandiff, "coverage": coverage}


def main(argv):
    if len(argv) < 2 or argv[0] in ("-h", "--help") or argv[0] not in SUBS:
        print(__doc__.strip())
        return 0 if argv and argv[0] in ("-h", "--help") else 64
    if np is None:
        sys.exit("imgcheck.py needs numpy + Pillow (pip install numpy pillow, or run it with: uv run --no-project --with numpy "
                 "--with pillow python imgcheck.py ...)")
    print(json.dumps(SUBS[argv[0]](load_payload(argv[1]))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
