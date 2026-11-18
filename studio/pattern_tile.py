"""Seamless pattern tile from an all-over print: the local, free version of "Pattern 3D".

Input is a flat view of the pattern: a panel the 3D pipeline unwrapped (Front.png ...), a crop of
a fabric photo taken straight on, or any repeating design. Steps:

1. repeat.lattice (the same FFT search the 3D fill uses) finds the two repeat vectors v1, v2.
   A pattern that repeats one way only (hem, cuff, sleeve stripe band) falls back to a band
   tile: one period wide, full height.
2. The smallest near-rectangular cell made of whole lattice steps is chosen, so the tile repeats
   with plain side-by-side tiling (a half-drop or brick pattern gives a cell two repeats wide).
3. Every copy of that cell inside the image is sampled (sub-pixel, along the exact lattice basis)
   and the per-pixel median is taken. Noise, JPEG blocks, small folds and the odd hidden pixel
   are voted out; with one copy the tile is just that copy.
4. The tile is resampled to the requested edge and checked: the seam error compares the jump
   across the tile's wrap-around edges with the ordinary pixel-to-pixel change inside it.

No model, no network, a fraction of a second on CPU.
"""
from pathlib import Path
import sys

import cv2
import numpy as np

try:
    from pod3d import repeat
except ImportError:   # studio started without the engine on sys.path (tests, CLI)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pod3d import repeat

MAX_STEPS = 8          # cells up to this many lattice steps on a side
AXIS_SLANT = 0.03      # a cell side may lean this much (|off-axis| / length) and still count
MAX_COPIES = 48
SEAM_OK = 1.6          # seam jump / interior change at or below this looks seamless


REFINE_EDGE = 640      # work size for checking candidate repeat vectors


def _shift_score(lab, known, contrast, v):
    """Mean squared Lab error of the image against itself moved by v, over contrast (as in
    repeat.lattice). inf when the overlap is too small."""
    h, w = known.shape
    m = np.float32([[1, 0, v[1]], [0, 1, v[0]]])
    moved = cv2.warpAffine(lab, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    ok = cv2.warpAffine(known.astype(np.float32), m, (w, h), flags=cv2.INTER_NEAREST) > 0.5
    ok &= known
    if ok.sum() < 0.2 * known.sum():
        return np.inf
    return float(((lab - moved) ** 2).sum(-1)[ok].mean() / (2 * contrast))


def primitive(bgr, known, v1, v2):
    """repeat.lattice is tuned for filling, where any repeat vector works; a tile needs the
    shortest ones. Divide each vector by the largest n that still repeats, then try the half
    sums (a half-drop / brick lattice is centred) and keep the shorter basis."""
    s, lab, k, contrast = _work(bgr, known, REFINE_EDGE)
    contrast = contrast or 1.0
    limit = repeat.LATTICE_RMS
    min_len = repeat.MIN_STEP

    # Noise raises the error of every true repeat by the same amount, so a candidate counts as a
    # repeat when it is about as good as the vectors repeat.lattice already found.
    ref = max(_shift_score(lab, k, contrast, v1 * s), _shift_score(lab, k, contrast, v2 * s))
    limit = max(limit, ref * 1.5 + 0.03) if np.isfinite(ref) else limit

    def repeats(v):
        return np.hypot(*v) >= min_len and _shift_score(lab, k, contrast, v * s) <= limit

    def reduce(v):
        for n in range(16, 1, -1):
            if repeats(v / n):
                return v / n
        return v

    basis = [reduce(v1), reduce(v2)]
    for _ in range(3):
        a, b = basis
        changed = False
        for c in ((a + b) / 2, (a - b) / 2):
            if not repeats(c):
                continue
            longer = 0 if np.hypot(*a) >= np.hypot(*b) else 1
            other = basis[1 - longer]
            cross = abs(c[0] * other[1] - c[1] * other[0]) / (np.hypot(*c) * np.hypot(*other) + 1e-9)
            if np.hypot(*c) < np.hypot(*basis[longer]) - 0.5 and cross > 0.3:
                basis[longer] = c
                changed = True
                break
        if not changed:
            break
    return basis


def _work(bgr, known, edge):
    h, w = known.shape
    s = min(1.0, edge / max(h, w))
    small = cv2.resize(bgr, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    k = cv2.resize(known.astype(np.uint8), small.shape[1::-1], interpolation=cv2.INTER_NEAREST) > 0
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    return s, lab, k, float(lab[k].var(0).sum()) if k.any() else 0.0


def band_period(bgr, known, axis):
    """Repeat period (full-size px, sub-pixel) of a pattern that repeats along one axis only
    (axis 1 = left-right, the usual hem / sleeve band), or None."""
    s, lab, k, contrast = _work(bgr, known, REFINE_EDGE)
    if contrast < 60:
        return None
    length = lab.shape[1 if axis == 1 else 0]
    vec = (lambda d: (0.0, d)) if axis == 1 else (lambda d: (d, 0.0))
    lo = max(repeat.MIN_STEP * s, 4)
    ds = np.arange(lo, length / 2)   # at least two repeats
    if not len(ds):
        return None
    scores = np.array([_shift_score(lab, k, contrast, vec(d)) for d in ds])
    i = int(np.argmin(scores))
    if not scores[i] <= repeat.LATTICE_RMS:
        return None
    d = float(ds[i])
    for n in range(8, 1, -1):          # shortest repeat, as in primitive()
        if d / n >= lo and _shift_score(lab, k, contrast, vec(d / n)) <= max(repeat.LATTICE_RMS, scores[i] * 1.5 + 0.03):
            d /= n
            break
    if _shift_score(lab, k, contrast, vec(d / 2)) < 4 * _shift_score(lab, k, contrast, vec(d)) + 0.05:
        return None   # a smooth gradient also "repeats" for short shifts
    # Sub-pixel refine at a finer work size.
    s2, lab2, k2, c2 = _work(bgr, known, 2048)
    full = d / s
    fine = np.arange(full - 2 / s, full + 2 / s, 0.25)
    err = [_shift_score(lab2, k2, c2, vec(f * s2)) for f in fine]
    return float(fine[int(np.argmin(err))])


def _median(img, known, ys, xs, corners):
    """Per-pixel median of the image sampled at (ys + o_y, xs + o_x) for each corner o,
    over the known pixels. Returns (tile uint8, seen mask)."""
    imgf = img.astype(np.float32)
    k = known.astype(np.float32)
    stack, weight = [], []
    for o in corners:
        my = (ys + o[0]).astype(np.float32)
        mx = (xs + o[1]).astype(np.float32)
        stack.append(cv2.remap(imgf, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
        weight.append(cv2.remap(k, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE) > 0.5)
    stack = np.stack(stack)
    weight = np.stack(weight)
    with np.errstate(all='ignore'):
        tile = np.nanmedian(np.where(weight[..., None], stack, np.nan), axis=0)
    hole = np.isnan(tile[..., 0])
    if hole.any():
        tile[hole] = np.median(stack, axis=0)[hole]
    return np.clip(np.round(tile), 0, 255).astype(np.uint8), weight.any(0)


def _resize_wrap(tile, edge, wrap_y=True):
    th, tw = tile.shape[:2]
    s = edge / max(tw, th)
    size = (max(2, round(tw * s)), max(2, round(th * s)))
    # Wrap-around padding so the resampled edges blend with the opposite side, not a border.
    pad = 3
    pady = (pad, pad) if wrap_y else (0, 0)
    padded = np.pad(tile, (pady, (pad, pad), (0, 0)), mode='wrap').astype(np.float32)
    big = cv2.resize(padded, (round((tw + 2 * pad) * s), round((th + sum(pady)) * s)),
                     interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    p, py = round(pad * s), round(pady[0] * s)
    return np.clip(big[py:py + size[1], p:p + size[0]], 0, 255).astype(np.uint8)


def extract_band(bgr, known=None, edge=2048, axis=1):
    """Tile of a pattern repeating along one axis (hem, cuff, sleeve stripes): one period wide
    (axis 1) or tall (axis 0), full size along the other axis."""
    h, w = bgr.shape[:2]
    if known is None:
        known = np.ones((h, w), bool)
    period = band_period(bgr, known, axis)
    if period is None:
        return None
    n = int(((w if axis == 1 else h) - 1) // period)
    if n < 2:
        return None
    if axis == 1:
        tw, th = max(2, int(round(period))), h
        gy, gx = np.mgrid[0:th, 0:tw].astype(np.float64)
        gx = gx * period / tw
        corners = [np.array([0.0, i * period]) for i in range(n)]
    else:
        tw, th = w, max(2, int(round(period)))
        gy, gx = np.mgrid[0:th, 0:tw].astype(np.float64)
        gy = gy * period / th
        corners = [np.array([i * period, 0.0]) for i in range(n)]
    tile, seen = _median(bgr, known, gy, gx, corners[:MAX_COPIES])
    measured = [tw, th]
    if edge:
        tile = _resize_wrap(tile if axis == 1 else tile.transpose(1, 0, 2), edge, wrap_y=False)
        if axis == 0:
            tile = tile.transpose(1, 0, 2)
    t = tile.astype(np.float32)
    inner = np.abs(np.diff(t, axis=axis)).mean()
    jump = np.abs(t[:, 0] - t[:, -1]).mean() if axis == 1 else np.abs(t[0] - t[-1]).mean()
    seam = round(float(jump / max(inner, 1e-3)), 2)
    return tile, {'kind': 'band-x' if axis == 1 else 'band-y', 'period_px': measured,
                  'size': [int(tile.shape[1]), int(tile.shape[0])], 'copies': min(n, MAX_COPIES),
                  'half_drop': False, 'coverage': round(float(seen.mean()), 3), 'seam': seam,
                  'seamless': seam <= SEAM_OK, 'vectors': None}


def _cell(v1, v2, h, w):
    """Near-vertical and near-horizontal lattice vectors (dy, dx) that make the smallest cell,
    or None when no such cell fits at least 1.5 times in the image."""
    best_v = best_h = None
    for i in range(-MAX_STEPS, MAX_STEPS + 1):
        for j in range(-MAX_STEPS, MAX_STEPS + 1):
            if not (i or j):
                continue
            v = i * v1 + j * v2
            if v[0] > 0.5 and abs(v[1]) <= AXIS_SLANT * v[0] and v[0] <= h / 1.5:
                if best_v is None or v[0] < best_v[0]:
                    best_v = v
            if v[1] > 0.5 and abs(v[0]) <= AXIS_SLANT * v[1] and v[1] <= w / 1.5:
                if best_h is None or v[1] < best_h[1]:
                    best_h = v
    if best_v is None or best_h is None:
        return None
    return best_v, best_h


def _copies(v1, v2, vert, horiz, h, w):
    """Top-left corners of cell copies fully inside the image, nearest to the image centre first."""
    corners = []
    for m in range(-60, 61):
        for n in range(-60, 61):
            o = m * v1 + n * v2
            ends = [o, o + vert, o + horiz, o + vert + horiz]
            if all(0 <= p[0] <= h - 1 and 0 <= p[1] <= w - 1 for p in ends):
                corners.append(o)
    c = np.array([h, w]) / 2 - (vert + horiz) / 2
    corners.sort(key=lambda o: np.hypot(*(o - c)))
    return corners[:MAX_COPIES]


def seam_error(tile):
    """Jump across the wrap-around edges divided by the typical neighbour change inside."""
    t = tile[..., :3].astype(np.float32)
    inner = (np.abs(np.diff(t, axis=0)).mean() + np.abs(np.diff(t, axis=1)).mean()) / 2
    seam = (np.abs(t[0] - t[-1]).mean() + np.abs(t[:, 0] - t[:, -1]).mean()) / 2
    return float(seam / max(inner, 1e-3))


def extract(bgr, known=None, edge=2048):
    """bgr: uint8 image of the flat pattern. known: optional bool mask of trustworthy pixels.
    edge: longest side of the returned tile (None keeps the measured size).
    Returns (tile bgr uint8, info) or raises ValueError when the image has no 2D repeat."""
    h, w = bgr.shape[:2]
    if known is None:
        known = np.ones((h, w), bool)
    vectors = repeat.lattice(bgr, known)
    cell = None
    if vectors is not None:
        v1, v2 = primitive(bgr, known, *(np.asarray(v, np.float64) for v in vectors))
        cell = _cell(v1, v2, h, w)
    corners = _copies(v1, v2, *cell, h, w) if cell else []
    if not corners:
        # Not a 2D repeat (or no whole cell in view): a band that repeats one way still tiles.
        for axis in (1, 0):
            band = extract_band(bgr, known, edge, axis)
            if band is not None:
                return band
        raise ValueError('Không thấy hoạ tiết lặp trong ảnh. Hãy dùng ảnh phẳng (mảnh đã trải phẳng, '
                         'hoặc ảnh vải chụp thẳng) có ít nhất 2 lần lặp.')
    vert, horiz = cell
    th, tw = max(2, int(round(vert[0]))), max(2, int(round(horiz[1])))

    gy, gx = np.mgrid[0:th, 0:tw].astype(np.float64)
    fy, fx = gy / th, gx / tw          # exact lattice basis: the tile repeats with no drift
    base_y = fy * vert[0] + fx * horiz[0]
    base_x = fy * vert[1] + fx * horiz[1]
    tile, seen = _median(bgr, known, base_y, base_x, corners)

    measured = (tw, th)
    if edge:
        tile = _resize_wrap(tile, edge)
    info = {
        'kind': 'grid',
        'vectors': [[round(float(a), 2) for a in v1], [round(float(a), 2) for a in v2]],
        'period_px': list(measured),          # one repeat in the source image, width x height
        'size': [int(tile.shape[1]), int(tile.shape[0])],
        'copies': len(corners),
        # the rectangle holds more than one repeat: half-drop, brick or another offset lattice
        'half_drop': bool(abs(vert[0] * horiz[1] - vert[1] * horiz[0]) > 1.5 * abs(v1[0] * v2[1] - v1[1] * v2[0])),
        'coverage': round(float(seen.mean()), 3),
        'seam': round(seam_error(tile), 2),
    }
    info['seamless'] = info['seam'] <= SEAM_OK
    return tile, info


def preview(tile, repeats=3, edge=1200, kind='grid'):
    """The tile repeated (3 x 3 for a grid, 3 side by side for a band), shrunk to fit edge."""
    reps = (1, repeats, 1) if kind == 'band-x' else (repeats, 1, 1) if kind == 'band-y' else (repeats, repeats, 1)
    grid = np.tile(tile, reps)
    s = min(1.0, edge / max(grid.shape[:2]))
    if s < 1:
        grid = cv2.resize(grid, (round(grid.shape[1] * s), round(grid.shape[0] * s)), interpolation=cv2.INTER_AREA)
    return grid


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Tách tile hoạ tiết lặp liền mạch từ ảnh phẳng.')
    ap.add_argument('image')
    ap.add_argument('-o', '--out', help='file PNG ra (mặc định <ảnh>_tile.png)')
    ap.add_argument('--edge', type=int, default=2048, help='cạnh dài của tile (0 = giữ kích thước đo được)')
    a = ap.parse_args()
    src = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if src is None:
        sys.exit(f'Không đọc được {a.image}')
    out, meta = extract(src, edge=a.edge or None)
    target = Path(a.out or Path(a.image).with_name(Path(a.image).stem + '_tile.png'))
    cv2.imwrite(str(target), out)
    cv2.imwrite(str(target.with_name(target.stem + '_preview.jpg')), preview(out, kind=meta['kind']))
    print(target, meta)
