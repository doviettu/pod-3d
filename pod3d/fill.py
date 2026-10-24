"""Fill the parts of a panel that the mockup does not show (sides, under the arms).

Ugly-sweater and all-over designs are mostly horizontal bands that repeat, so, in order:
each band of rows is continued sideways with its own repeat period, plain background is tiled
from a clean square of itself, and only what is left goes to LaMa.
"""
import cv2
import numpy as np

BLOCK = 16  # rows per band block at work size
REPEAT_ERR = 9.0  # mean Lab difference allowed between a band and itself shifted by one period


def _period(strip, valid):
    """Horizontal repeat period of a block of rows, from the autocorrelation of its columns."""
    cols = np.where(valid)[0]
    if len(cols) < 48:
        return None
    x0, x1 = cols.min(), cols.max() + 1
    sig = strip[:, x0:x1].astype(np.float32).mean(0)
    sig = sig - sig.mean(0)
    n = len(sig)
    if np.abs(sig).max() < 4:
        return None  # flat color, nothing to repeat
    f = np.fft.rfft(sig, n=2 * n, axis=0)
    ac = np.fft.irfft((f * np.conj(f)).sum(-1) if f.ndim > 1 else f * np.conj(f), axis=0)[:n]
    ac = ac / (ac[0] + 1e-6)
    lo, hi = 24, n - 12
    if hi <= lo:
        return None
    # unbiased: divide by the overlap length so long lags are not penalized
    acu = ac[lo:hi] * n / (n - np.arange(lo, hi))
    p = lo + int(np.argmax(acu))
    return p if acu[p - lo] > 0.6 else None


def solid_mask(cov, thresh=0.5):
    """Trusted area without pinholes: a gap enclosed by trusted map is trusted too (the map is
    smooth there; the gap is only where calibration found nothing to compare)."""
    m = (cov >= thresh).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    flood = m.copy()
    h, w = m.shape
    ff = np.zeros((h + 2, w + 2), np.uint8)
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if flood[y, x] == 0:
            cv2.floodFill(flood, ff, (x, y), 2)
    return (m > 0) | (flood == 0)


CORE = 0.6  # middle part of a sleeve strip used for tiling; its edges are stretched by the fold


def tile(img, known):
    """Sleeves: continue each band of the seen strip across the whole width, by its own repeat
    period when it has one, else by mirroring the strip back and forth (no seams either way)."""
    out = img.copy()
    left = ~known
    h, w = known.shape
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    for y0 in range(0, h, BLOCK):
        y1 = min(h, y0 + BLOCK)
        cols = np.where(known[y0:y1].mean(0) > 0.9)[0]
        if len(cols) < 16:
            continue
        a, b = cols.min(), cols.max()
        m = int((b - a + 1) * (1 - CORE) / 2)
        a, b = a + m, b - m
        wy0, wy1 = max(0, y0 - BLOCK), min(h, y1 + BLOCK)
        valid = np.zeros(w, bool)
        valid[a:b + 1] = True
        p = _period(lab[wy0:wy1], valid & (known[wy0:wy1].mean(0) > 0.9))
        x = np.arange(w)
        if p and p <= b - a + 1:
            src = a + (x - a) % p
        else:
            src = a + _pingpong(x - a, b - a + 1)
        hole = left[y0:y1].copy()
        hole[:, a:b + 1] = False
        out[y0:y1][hole] = out[y0:y1][:, src][hole]
        left[y0:y1][hole] = False
    return out, left


def fill(img, cov, thresh=0.5, log=print, mode="bands"):
    """Return the panel with uncovered pixels filled in. img: work-size BGR, cov: 0..1.
    mode "tile" is for sleeves, seen only as a narrow strip whose every row is a band.
    Repeating bands are continued exactly, plain background is tiled, the rest goes to LaMa."""
    known = solid_mask(cov, thresh)
    out, left = tile(img, known) if mode == "tile" else repeat_bands(img, known)
    out, left = extend(out, ~left)
    t, tl = extend(out.transpose(1, 0, 2).copy(), ~left.T)  # rows above/below the seen area
    out, left = t.transpose(1, 0, 2).copy(), tl.T
    if left.any():
        from . import inpaint
        if inpaint.available():
            log(f"    LaMa fills {100 * left.mean():.0f}% of the panel")
            out = inpaint.inpaint(out, left)
        else:
            log("    LaMa not installed, filling with the edge color")
            out = cv2.inpaint(out, left.astype(np.uint8), 5, cv2.INPAINT_TELEA)
    return out


def repeat_bands(img, known):
    """Continue each band of rows sideways by whole repeats. Returns (image, still-missing mask)."""
    out = img.copy()
    left = ~known
    h, w = known.shape
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    for y0 in range(0, h, BLOCK):
        y1 = min(h, y0 + BLOCK)
        k = known[y0:y1]
        valid = k.mean(0) > 0.9
        if valid.all():
            continue
        if not valid.any():
            continue
        cols = np.where(valid)[0]
        a, b = cols.min(), cols.max()
        # Judge the repeat on a taller window: a motif (a figure, a tree) spans many blocks and
        # every block of it must be shifted by the same period or it tears into stripes.
        wy0, wy1 = max(0, y0 - 2 * BLOCK), min(h, y1 + 2 * BLOCK)
        wvalid = known[wy0:wy1].mean(0) > 0.9
        p = _period(lab[wy0:wy1], wvalid & valid) if wvalid[valid].mean() > 0.9 else None
        if not p or p > (b - a + 1) / 3:
            continue  # not a repeating band: leave it to inpainting
        # A text line or a drawing can show a weak period by chance; shifting the known part by
        # one period must reproduce it, or the band is not really repeating.
        seg = lab[wy0:wy1, a:b + 1].astype(np.float32)
        if np.abs(seg[:, p:] - seg[:, :-p]).mean() > REPEAT_ERR:
            continue
        for x in range(w):
            if valid[x]:
                continue
            if x < a:
                src = x + p * int(np.ceil((a - x) / p))
            else:
                src = x - p * int(np.ceil((x - b) / p))
            if a <= src <= b:
                out[y0:y1, x] = out[y0:y1, src]
                left[y0:y1, x] = False
    return out, left


OBJ_DIST = 25.0  # Lab distance from the background color that makes a pixel part of a motif
OBJ_AREA = 150  # motif pieces bigger than this (letters, ornaments, a face) are not repeated
MAX_STRIP = 160  # widest background strip mirrored outwards, px at work size
MIN_STRIP = 48  # narrower strips streak when mirrored; a background square is tiled instead
PATCH = 64  # side of that square


def _pingpong(i, n):
    m = np.asarray(i) % (2 * n)
    return np.where(m < n, m, 2 * n - 1 - m)


def _bg_patch(img, lab, known, dom):
    """The cleanest PATCH x PATCH square of background color dom (dots and snow included), or None."""
    near = ((np.linalg.norm(lab - dom, axis=-1) < OBJ_DIST) & known).astype(np.float32)
    score = cv2.boxFilter(near, -1, (PATCH, PATCH), normalize=True, borderType=cv2.BORDER_CONSTANT)
    full = cv2.boxFilter(known.astype(np.float32), -1, (PATCH, PATCH), normalize=True,
                         borderType=cv2.BORDER_CONSTANT)
    score[full < 0.999] = 0
    y, x = np.unravel_index(score.argmax(), score.shape)
    if score[y, x] < 0.85:
        return None
    y0, x0 = y - PATCH // 2, x - PATCH // 2
    return img[y0:y0 + PATCH, x0:x0 + PATCH]


def _dominant(px):
    """Most common color of a set of Lab pixels (a median would mix a motif into the background)."""
    q = (px // 12).astype(np.int32)
    key = q[:, 0] * 10000 + q[:, 1] * 100 + q[:, 2]
    vals, counts = np.unique(key, return_counts=True)
    return px[key == vals[counts.argmax()]].mean(0)


def extend(img, known):
    """Continue the background next to the seen area sideways: the strip of background beside
    the hole (with its dots and snow) is mirrored back and forth until it reaches the panel edge.
    The strip stops before any big motif, so letters and faces are never copied. LaMa smears a
    wide hole at the panel border into gray, this keeps the design's own colors and texture."""
    out = img.copy()
    left = ~known
    h, w = known.shape
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    patches = {}
    for y0 in range(0, h, BLOCK):
        y1 = min(h, y0 + BLOCK)
        if not left[y0:y1].any():
            continue
        valid = known[y0:y1].mean(0) > 0.9
        cols = np.where(valid)[0]
        if len(cols) < 24:
            continue
        a, b = cols.min(), cols.max()
        wy0, wy1 = max(0, y0 - BLOCK), min(h, y1 + BLOCK)
        dmax = min(MAX_STRIP, (b - a + 1) // 2)
        for side in (-1, 1):
            if side < 0:
                holes = np.arange(0, a)
                strip = np.arange(a, a + dmax)
            else:
                holes = np.arange(b + 1, w)
                strip = np.arange(b, b - dmax, -1)
            if not len(holes) or not left[y0:y1, holes].any():
                continue
            win = lab[wy0:wy1][:, strip]
            win_known = known[wy0:wy1][:, strip]
            if win_known.sum() < 32:
                continue
            dom = _dominant(win[win_known])
            obj = ((np.linalg.norm(win - dom, axis=-1) > OBJ_DIST) & win_known).astype(np.uint8)
            n, lbl, stats, _ = cv2.connectedComponentsWithStats(obj, connectivity=8)
            big = np.isin(lbl, 1 + np.where(stats[1:, cv2.CC_STAT_AREA] > OBJ_AREA)[0])
            hit = np.where(big.any(0))[0]
            d = (hit[0] - 4) if len(hit) else dmax
            dist = a - 1 - holes if side < 0 else holes - b - 1  # 0 next to the seen area
            bg = cv2.cvtColor(np.clip(dom, 0, 255).astype(np.uint8)[None, None], cv2.COLOR_LAB2BGR)[0, 0]
            # one patch per background color, so neighbouring blocks tile the very same square
            # The mockup shades the garment towards its edges, so the same background turns up
            # darker there: same hue (a, b) with another lightness is the same background.
            seen = [c for c in patches if np.linalg.norm(np.array(c[1:]) - dom[1:]) < 12
                    and abs(c[0] - dom[0]) < 45]
            if seen:
                patch = patches[seen[0]]
            else:
                patch = patches[tuple(dom)] = _bg_patch(img, lab, known, dom)
            span = win_known[:, :max(d, 16)]
            plain = 1 - obj[:, :max(d, 16)][span].mean() if span.any() else 0
            if d < MIN_STRIP or (plain > 0.8 and patch is not None):
                # Plain background (or too little of it to mirror without streaks): tile a clean
                # square of it. The mockup edge is stretched by the fold, so even a plain strip
                # beside the hole would smear when mirrored.
                for y in range(y0, y1):
                    hx = holes[left[y, holes]]
                    if len(hx):
                        out[y, hx] = bg if patch is None else patch[_pingpong(y, PATCH), _pingpong(hx, PATCH)]
                        left[y, hx] = False
                continue
            src = strip[_pingpong(dist, d)]
            for y in range(y0, y1):
                hx = holes[left[y, holes]]
                if not len(hx):
                    continue
                sx = src[left[y, holes]]
                ok = known[y, sx]
                out[y, hx[ok]] = img[y, sx[ok]]
                out[y, hx[~ok]] = bg
                left[y, hx] = False
    return out, left
