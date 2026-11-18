"""Closed-form alpha matting (Levin, Lischinski, Weiss, CVPR 2006) on the uncertain band only.

Why: the linear un-mix in chroma_key / art2d assumes one flat background colour. Real inputs are
not flat: AI "green screens" carry gradients and noise, photos have uneven light, and hair,
smoke or fur mix several colours inside one pixel. Closed-form matting assumes only that, inside
every 3 x 3 window, foreground and background colours each lie on a line in RGB. Then alpha is
locally an affine function of colour, alpha ~ a.I + b, and the best alpha for the whole image
solves one sparse linear system with the matting Laplacian L:

    minimise  alpha' L alpha   with alpha fixed to 0 / 1 where the trimap is sure.

Only the unsure pixels are unknowns, and the image is cut into overlapping tiles so memory stays
small (a few hundred MB at most for a 4000 px design). scipy only, CPU, no model.
"""
import cv2
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve

EPS = 1e-7       # regularisation: larger = smoother alpha (1e-7 measured best on soft edges)
TILE = 160       # tile side (px) for the solve
OVERLAP = 12     # tiles overlap so their seams can be blended
MAX_EDGE = 1600  # larger images are matted at this size and the band alpha scaled back up


def trimap_from_alpha(alpha, band=10, lo=0.02, hi=0.98):
    """Trimap (0 background, 1 foreground, 0.5 unknown) around a rough alpha: every pixel that
    is not clearly 0 or 1, grown by band px, is unknown."""
    sure_bg = alpha <= lo
    sure_fg = alpha >= hi
    unsure = ~(sure_bg | sure_fg)
    if band > 0:
        k = np.ones((2 * band + 1, 2 * band + 1), np.uint8)
        unsure = cv2.dilate(unsure.astype(np.uint8), k) > 0
    tri = np.where(sure_fg, 1.0, 0.0).astype(np.float32)
    tri[unsure] = 0.5
    return tri


def _laplacian(img, eps=EPS, r=1):
    """Matting Laplacian of an (h, w, 3) float image as a sparse (h*w, h*w) matrix."""
    h, w = img.shape[:2]
    win = (2 * r + 1) ** 2
    idx = np.arange(h * w).reshape(h, w)
    # All windows fully inside the tile: (n, win) pixel indices and (n, win, 3) colours.
    ys, xs = np.mgrid[r:h - r, r:w - r]
    ys, xs = ys.ravel(), xs.ravel()
    off = np.array([(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1)])
    py = ys[:, None] + off[None, :, 0]
    px = xs[:, None] + off[None, :, 1]
    widx = idx[py, px]
    colours = img[py, px]                                  # (n, win, 3)
    mu = colours.mean(1, keepdims=True)
    d = colours - mu
    cov = np.einsum('nki,nkj->nij', d, d) / win + (eps / win) * np.eye(3)[None]
    inv = np.linalg.inv(cov)
    vals = (1 + np.einsum('nki,nij,nlj->nkl', d, inv, d)) / win   # (n, win, win)
    rows = np.repeat(widx, win, axis=1).ravel()
    cols = np.tile(widx, (1, win)).ravel()
    lap = coo_matrix((-vals.ravel(), (rows, cols)), shape=(h * w, h * w)).tocsr()
    # Diagonal: number of windows each pixel is in (the delta term), added once per window.
    counts = np.bincount(widx.ravel(), minlength=h * w).astype(np.float64)
    lap.setdiag(lap.diagonal() + counts)
    return lap


def _solve_tile(img, tri):
    unknown = (tri == 0.5).ravel()
    alpha = tri.astype(np.float64).ravel().copy()
    if not unknown.any():
        return alpha.reshape(tri.shape)
    lap = _laplacian(img)
    known = ~unknown
    l_uu = lap[unknown][:, unknown]
    rhs = -lap[unknown][:, known] @ alpha[known]
    try:
        sol = spsolve(l_uu.tocsc(), rhs)
    except Exception:  # noqa: BLE001 - singular tile (no known pixels nearby): keep the guess
        sol = None
    if sol is None or not np.all(np.isfinite(sol)):
        return None
    alpha[unknown] = sol
    return np.clip(alpha, 0, 1).reshape(tri.shape)


def closed_form(rgb, trimap, guess=None, tile=TILE, overlap=OVERLAP):
    """Alpha for an (h, w, 3) uint8 or 0..1 float image and a trimap (0 / 0.5 / 1).
    guess: alpha to keep where a tile cannot be solved (e.g. no sure pixel in it).
    Returns float32 alpha in 0..1."""
    img = rgb.astype(np.float64)
    if img.max() > 1.5:
        img /= 255.0
    h, w = trimap.shape
    out = np.where(trimap == 1, 1.0, 0.0)
    if guess is not None:
        out = np.where(trimap == 0.5, guess, out)
    unknown = trimap == 0.5
    if not unknown.any():
        return out.astype(np.float32)
    acc = np.zeros((h, w))
    wsum = np.zeros((h, w))
    step = tile - 2 * overlap
    ramp = np.minimum(np.arange(tile) + 1, np.arange(tile)[::-1] + 1).astype(np.float64)
    ramp = np.minimum(ramp, overlap + 1) / (overlap + 1)
    for y0 in range(0, max(1, h - 2 * overlap), step):
        for x0 in range(0, max(1, w - 2 * overlap), step):
            y1, x1 = min(h, y0 + tile), min(w, x0 + tile)
            if not unknown[y0:y1, x0:x1].any():
                continue
            t = trimap[y0:y1, x0:x1]
            if not ((t == 0).any() and (t == 1).any()):
                continue   # needs both a sure background and a sure foreground pixel, or alpha drifts
            a = _solve_tile(img[y0:y1, x0:x1], t)
            if a is None:
                continue
            wt = np.outer(ramp[:y1 - y0], ramp[:x1 - x0])
            acc[y0:y1, x0:x1] += a * wt
            wsum[y0:y1, x0:x1] += wt
    solved = unknown & (wsum > 0)
    out[solved] = acc[solved] / wsum[solved]
    return np.clip(out, 0, 1).astype(np.float32)


def refine_alpha(rgb, alpha, band=10, max_edge=MAX_EDGE):
    """Closed-form alpha on the uncertain band around a rough alpha (both full size).
    Big images are solved at max_edge and only the band is taken from the upscaled result."""
    h, w = alpha.shape
    s = min(1.0, max_edge / max(h, w))
    if s < 1:
        size = (max(2, round(w * s)), max(2, round(h * s)))
        small_rgb = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
        small_a = cv2.resize(alpha.astype(np.float32), size, interpolation=cv2.INTER_AREA)
        tri = trimap_from_alpha(small_a, max(2, round(band * s)))
        solved = closed_form(small_rgb, tri, guess=small_a)
        up = cv2.resize(solved, (w, h), interpolation=cv2.INTER_LINEAR)
        band_full = cv2.resize((tri == 0.5).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        return np.where(band_full, up, alpha).astype(np.float32)
    tri = trimap_from_alpha(alpha, band)
    return closed_form(rgb, tri, guess=alpha)
