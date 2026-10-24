"""Fill the parts of a panel that the mockup does not show (sides, under the arms).

Ugly-sweater and all-over designs are mostly horizontal bands that repeat, so each band of
rows is continued sideways with its own repeat period. What has no period is mirrored.
"""
import cv2
import numpy as np

BLOCK = 16  # rows per band block at work size


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


def fill(img, cov, thresh=0.5, log=print):
    """Return the panel with uncovered pixels filled in. img: work-size BGR, cov: 0..1.
    Repeating bands are continued exactly; everything else goes to LaMa inpainting."""
    out, left = repeat_bands(img, cov >= thresh)
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
