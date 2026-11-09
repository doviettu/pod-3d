"""Exact fills that need no AI: borrow from a twin panel, and continue a 2D repeating pattern.

Many all-over designs print the same art on Front and Back, and the mockup shows the two from
different sides, so one panel's hidden edge is often seen on the other. And true all-over
patterns (florals, polka dots, plaids) repeat on a 2D lattice, so any hidden pixel is a copy of
a seen pixel one or more lattice steps away. Both are checked on the seen area first and only
used when they reproduce it.
"""
import cv2
import numpy as np

TWIN_DE = 12.0     # median Lab distance on the overlap that makes two panels the same art
TWIN_OVERLAP = 0.15
LATTICE_EDGE = 384  # work-size images are shrunk to this for the lattice search
LATTICE_RMS = 0.18  # shift error / pattern contrast that counts as a true repeat
MIN_STEP = 12


def _lab(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)


def borrow(img, known, twin_img, twin_known):
    """Copy pixels seen on the twin panel (as is, or mirrored) into this panel's hidden area.
    Returns (image, known, how) where how is 'same', 'mirror' or None."""
    if twin_img is None or twin_img.shape != img.shape:
        return img, known, None
    lab = _lab(img)
    best = None
    for how, other, other_known in (('same', twin_img, twin_known),
                                    ('mirror', twin_img[:, ::-1], twin_known[:, ::-1])):
        both = known & other_known
        if both.mean() < TWIN_OVERLAP:
            continue
        # The two views are shaded differently; match the twin's brightness to ours on the overlap.
        gain = (img[both].astype(np.float32).mean(0) + 1) / (other[both].astype(np.float32).mean(0) + 1)
        matched = np.clip(other.astype(np.float32) * gain, 0, 255).astype(np.uint8)
        de = float(np.median(np.linalg.norm(lab[both] - _lab(matched)[both], axis=-1)))
        if de < TWIN_DE and (best is None or de < best[0]):
            best = (de, how, matched, other_known)
    if best is None:
        return img, known, None
    _, how, matched, other_known = best
    take = ~known & other_known
    out = img.copy()
    out[take] = matched[take]
    return out, known | take, how


def _masked_shift_error(lab, known):
    """Mean squared Lab difference between the seen pattern and itself shifted by every (dy, dx),
    over the pixels seen in both, via FFT. Returns (error, overlap) indexed [dy % h, dx % w]."""
    h, w = known.shape
    k = known.astype(np.float64)
    shape = (2 * h, 2 * w)
    fk = np.fft.rfft2(k, shape)

    def corr(fa, fb):
        return np.fft.irfft2(np.conj(fa) * fb, shape)

    overlap = corr(fk, fk)
    err = np.zeros(shape)
    for c in range(3):
        x = lab[..., c].astype(np.float64) * k
        fx, fx2 = np.fft.rfft2(x, shape), np.fft.rfft2(x * x, shape)   # k is 0/1, so (k a)^2 = k a^2
        err += corr(fx2, fk) + corr(fk, fx2) - 2 * corr(fx, fx)
    return err / np.maximum(overlap, 1e-9), overlap


def lattice(img, known):
    """Two repeat vectors of the seen pattern at work size, or None when it is not a 2D repeat."""
    h, w = known.shape
    scale = min(1.0, LATTICE_EDGE / max(h, w))
    small = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    k = cv2.resize(known.astype(np.uint8), small.shape[1::-1], interpolation=cv2.INTER_NEAREST) > 0
    if k.mean() < 0.2:
        return None
    lab = _lab(small)
    contrast = float(lab[k].var(0).sum())
    if contrast < 60:
        return None   # flat colour: the background fills handle it
    err, overlap = _masked_shift_error(lab, k)
    sh, sw = k.shape
    dy = np.arange(2 * sh)
    dx = np.arange(2 * sw)
    dy = np.where(dy < sh, dy, dy - 2 * sh)[:, None]
    dx = np.where(dx < sw, dx, dx - 2 * sw)[None, :]
    step = MIN_STEP * scale
    ok = (overlap > 0.25 * k.sum()) & (dy * dy + dx * dx >= step * step) & (dy >= 0)
    ok &= ~((dy == 0) & (dx < 0))   # each shift once
    score = np.where(ok, err / (2 * contrast), np.inf)
    def at(v):
        y, x = int(round(v[0])) % (2 * sh), int(round(v[1])) % (2 * sw)
        if v[0] < 0 or (v[0] == 0 and v[1] < 0):
            y, x = int(round(-v[0])) % (2 * sh), int(round(-v[1])) % (2 * sw)
        return err[y, x] / (2 * contrast) if overlap[y, x] > 0.25 * k.sum() else np.inf

    def pick(candidates):
        """Best shift, reduced to the shortest one that still repeats; a smooth gradient also
        'repeats' for short shifts, but there half a step is a quarter as wrong, not far worse."""
        idx = np.unravel_index(np.argmin(candidates), candidates.shape)
        if candidates[idx] > LATTICE_RMS:
            return None
        v = np.array([dy[idx[0], 0], dx[0, idx[1]]], np.float64)
        for n in (4, 3, 2):
            if np.hypot(*(v / n)) >= step and at(v / n) <= LATTICE_RMS:
                v = v / n
                break
        if at(v / 2) < 4 * at(v) + 0.05:
            return None
        return v

    v1 = pick(score)
    if v1 is None:
        return None
    cross = np.abs(v1[0] * dx - v1[1] * dy) / (np.hypot(dy, dx) * np.hypot(*v1) + 1e-9)
    v2 = pick(np.where(cross > 0.3, score, np.inf))
    if v2 is None:
        return None   # repeats in one direction only: a band, handled by fill.repeat_bands
    return v1 / scale, v2 / scale


def lattice_fill(img, known, reach=12):
    """Fill hidden pixels with the seen pixel the nearest number of lattice steps away.
    Returns (image, still-hidden mask, vectors or None)."""
    vectors = lattice(img, known)
    if vectors is None:
        return img, ~known, None
    v1, v2 = vectors
    h, w = known.shape
    steps = sorted(((i, j) for i in range(-reach, reach + 1) for j in range(-reach, reach + 1) if i or j),
                   key=lambda s: np.hypot(*(s[0] * v1 + s[1] * v2)))
    out = img.copy()
    left = ~known
    ys, xs = np.nonzero(left)
    for i, j in steps:
        if not len(ys):
            break
        oy, ox = i * v1 + j * v2
        sy, sx = np.rint(ys + oy).astype(int), np.rint(xs + ox).astype(int)
        inside = (sy >= 0) & (sy < h) & (sx >= 0) & (sx < w)
        hit = np.zeros(len(ys), bool)
        hit[inside] = known[sy[inside], sx[inside]]
        out[ys[hit], xs[hit]] = img[sy[hit], sx[hit]]
        left[ys[hit], xs[hit]] = False
        ys, xs = ys[~hit], xs[~hit]
    return out, left, vectors
