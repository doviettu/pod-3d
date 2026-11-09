"""Sales mockups without AI: put a transparent design onto a photo of a blank product.

Each blank photo is baked once into maps (product mask, relative light, fold displacement,
fabric texture); a render is then a warp of the design into the print zone, a nudge along the
folds, and a multiply by the light, so the design is reproduced exactly (no AI redraw).

Zones (points are fractions of the photo's width/height):
  rect      {'box': [x, y, w, h]}
  quad      {'points': [[x, y] * 4]}  top-left, top-right, bottom-right, bottom-left
  mesh      {'points': [[x, y] * 16]} 4 × 4 grid, row by row (pillows, wrinkled shirts)
  cylinder  {'box': [x0, y0, x1, y1], 'wrap': degrees seen, 'sag': 0..0.3, 'taper': 0.6..1.4}
            mug/tumbler body; sag bends horizontal lines (camera above), taper = bottom/top width
"""
import cv2
import numpy as np

ZONES = ('rect', 'quad', 'mesh', 'cylinder')
DEFAULTS = {'fold': 1.0, 'light': 1.0, 'texture': 0.5, 'fit': 'contain'}


# ---- baking ------------------------------------------------------------------
def auto_mask(bgr):
    """Product vs. a plain studio background: flood from the photo border by colour distance."""
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(bgr, (0, 0), 2), cv2.COLOR_BGR2LAB).astype(np.float32)
    border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    bg = np.median(border, 0)
    spread = np.percentile(np.linalg.norm(border - bg, axis=1), 90)
    near = (np.linalg.norm(lab - bg, axis=-1) < max(12.0, spread * 1.5)).astype(np.uint8)
    flood = np.zeros((h + 2, w + 2), np.uint8)
    seeds = [(x, 0) for x in range(0, w, 16)] + [(x, h - 1) for x in range(0, w, 16)] + \
            [(0, y) for y in range(0, h, 16)] + [(w - 1, y) for y in range(0, h, 16)]
    reach = near.copy()
    for x, y in seeds:
        if reach[y, x] == 1:
            cv2.floodFill(reach, flood, (x, y), 2)
    mask = (reach != 2).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    if count > 1:   # keep the product, drop specks of noise in the background
        keep = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
        mask = (labels == keep).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    return mask * 255


def _masked_blur(values, mask, sigma):
    """Gaussian blur that only averages inside the mask (no background bleeding in at the edges)."""
    m = (mask > 0).astype(np.float32)
    num = cv2.GaussianBlur(values * m, (0, 0), sigma)
    den = cv2.GaussianBlur(m, (0, 0), sigma)
    return np.where(den > 1e-3, num / np.maximum(den, 1e-3), values)


def bake(bgr, mask=None):
    """Maps of a blank photo. light: lightness relative to the product's median (multiply);
    disp: folds, -1..1, from fine lightness detail; texture: fabric grain, lightness units."""
    if mask is None:
        mask = auto_mask(bgr)
    h, w = bgr.shape[:2]
    scale = max(h, w) / 2000
    L = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[..., 0].astype(np.float32)
    inside = mask > 0
    median = float(np.median(L[inside])) if inside.any() else float(L.mean())
    # Relative to the product's own median, but in absolute lightness steps on dark products:
    # a fold on a black tee is a few L units, which as a ratio would black the print out.
    light = np.clip(1 + (_masked_blur(L, mask, 1.5 * scale) - median) / max(median, 110.0), 0.15, 2.5)
    smooth = _masked_blur(L, mask, 3 * scale)
    folds = smooth - _masked_blur(L, mask, 40 * scale)
    spread = float(np.percentile(np.abs(folds[inside]), 98)) if inside.any() else 1.0
    disp = np.clip(folds / max(spread, 1e-3), -1, 1) * inside
    texture = (L - _masked_blur(L, mask, 1.5 * scale)) * inside
    return {'mask': mask.astype(np.uint8), 'light': light.astype(np.float32),
            'disp': disp.astype(np.float32), 'texture': texture.astype(np.float32)}


def diagnostics(maps):
    """Viewable 8-bit images of the baked maps."""
    return {'mask': maps['mask'],
            'light': np.clip(maps['light'] * 128, 0, 255).astype(np.uint8),
            'disp': np.clip(maps['disp'] * 127 + 128, 0, 255).astype(np.uint8),
            'texture': np.clip(maps['texture'] * 8 + 128, 0, 255).astype(np.uint8)}


# ---- zones -------------------------------------------------------------------
def _px(points, w, h):
    return np.array([[x * w, y * h] for x, y in points], np.float32)


def default_zone(mask, kind='rect'):
    """A chest-sized print area inside the product's outline (a starting point to adjust)."""
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    if not len(xs):
        x0, y0, x1, y1 = w * 0.3, h * 0.25, w * 0.7, h * 0.7
    else:
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    bw, bh = x1 - x0, y1 - y0
    if kind == 'cylinder':
        return {'type': 'cylinder', 'box': [(x0 + bw * 0.08) / w, (y0 + bh * 0.15) / h, (x1 - bw * 0.22) / w,
                                             (y1 - bh * 0.1) / h], 'wrap': 150, 'sag': 0.05, 'taper': 1.0}
    zx, zy, zw, zh = x0 + bw * 0.3, y0 + bh * 0.22, bw * 0.4, bh * 0.4
    if kind == 'rect':
        return {'type': 'rect', 'box': [zx / w, zy / h, zw / w, zh / h]}
    if kind == 'quad':
        return {'type': 'quad', 'points': [[zx / w, zy / h], [(zx + zw) / w, zy / h], [(zx + zw) / w, (zy + zh) / h],
                                           [zx / w, (zy + zh) / h]]}
    return {'type': 'mesh', 'points': [[(zx + zw * i / 3) / w, (zy + zh * j / 3) / h] for j in range(4) for i in range(4)]}


def check_zone(zone):
    if not isinstance(zone, dict) or zone.get('type') not in ZONES:
        raise ValueError('Kiểu vùng in không hợp lệ.')
    kind = zone['type']

    def nums(values, n):
        if not isinstance(values, list) or len(values) != n:
            raise ValueError('Toạ độ vùng in không hợp lệ.')
        out = [float(v) for v in values]
        if not all(-0.5 <= v <= 1.5 for v in out):
            raise ValueError('Vùng in nằm ngoài ảnh.')
        return out

    if kind == 'rect':
        box = nums(zone.get('box'), 4)
        if box[2] <= 0.01 or box[3] <= 0.01:
            raise ValueError('Vùng in quá nhỏ.')
        return {'type': kind, 'box': box}
    if kind in ('quad', 'mesh'):
        n = 4 if kind == 'quad' else 16
        points = zone.get('points')
        if not isinstance(points, list) or len(points) != n:
            raise ValueError('Số điểm của vùng in không đúng.')
        return {'type': kind, 'points': [nums(p, 2) for p in points]}
    box = nums(zone.get('box'), 4)
    if box[2] - box[0] <= 0.01 or box[3] - box[1] <= 0.01:
        raise ValueError('Thân cốc quá nhỏ.')
    wrap, sag, taper = float(zone.get('wrap', 150)), float(zone.get('sag', 0.05)), float(zone.get('taper', 1.0))
    if not (30 <= wrap <= 180 and 0 <= sag <= 0.3 and 0.6 <= taper <= 1.4):
        raise ValueError('Thông số cốc không hợp lệ (góc 30-180°, độ cong 0-0,3, độ thuôn 0,6-1,4).')
    return {'type': kind, 'box': box, 'wrap': wrap, 'sag': sag, 'taper': taper}


def _fit(art, aspect, mode):
    """Make the design match the zone's aspect ratio without stretching it: 'contain' pads
    (design at the top, centred, like a chest print), 'cover' crops, 'stretch' leaves it."""
    h, w = art.shape[:2]
    if mode == 'stretch' or abs(w / h - aspect) < 1e-3:
        return art
    if mode == 'contain':
        if w / h > aspect:
            pad = np.zeros((int(round(w / aspect)), w, 4), art.dtype)
            pad[:h] = art
        else:
            nw = int(round(h * aspect))
            pad = np.zeros((h, nw, 4), art.dtype)
            pad[:, (nw - w) // 2:(nw - w) // 2 + w] = art
        return pad
    if w / h > aspect:
        nw = int(round(h * aspect))
        return art[:, (w - nw) // 2:(w - nw) // 2 + nw]
    return art[:int(round(w / aspect))]


def _warp_quad(art, quad, size):
    h, w = art.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    matrix = cv2.getPerspectiveTransform(src, quad)
    return cv2.warpPerspective(art, matrix, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _premultiply(art):
    out = art.astype(np.float32)
    out[..., :3] *= out[..., 3:4] / 255
    return out


def place(art_rgba, zone, size, fit='contain'):
    """Design (premultiplied float RGBA) warped into the zone on a canvas of size (w, h)."""
    w, h = size
    art = art_rgba
    kind = zone['type']
    if kind == 'rect':
        x, y, bw, bh = zone['box']
        quad = _px([[x, y], [x + bw, y], [x + bw, y + bh], [x, y + bh]], w, h)
    elif kind in ('quad', 'mesh'):
        pts = _px(zone['points'], w, h)
        quad = pts if kind == 'quad' else pts[[0, 3, 15, 12]]
    if kind in ('rect', 'quad', 'mesh'):
        top, left = np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[3] - quad[0])
        art = _premultiply(_fit(art, max(top, 1) / max(left, 1), fit))
        if kind != 'mesh':
            return _warp_quad(art, quad, size)
        # Mesh: each of the 3 × 3 cells has its own perspective transform. The whole design is
        # warped with it and only the cell is kept, so cell edges are full pixels and meet cleanly.
        ah, aw = art.shape[:2]
        out = np.zeros((h, w, 4), np.float32)
        owner = np.zeros((h, w), bool)
        grid = pts.reshape(4, 4, 2)
        for j in range(3):
            for i in range(3):
                x0, x1, y0, y1 = aw * i / 3, aw * (i + 1) / 3, ah * j / 3, ah * (j + 1) / 3
                cell = np.array([grid[j, i], grid[j, i + 1], grid[j + 1, i + 1], grid[j + 1, i]], np.float32)
                src = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)
                warped = cv2.warpPerspective(art, cv2.getPerspectiveTransform(src, cell), size, flags=cv2.INTER_LINEAR)
                inside = np.zeros((h, w), np.uint8)
                cv2.fillConvexPoly(inside, np.round(cell * 16).astype(np.int32), 1, cv2.LINE_8, 4)
                grown = (cv2.dilate(inside, np.ones((3, 3), np.uint8)) > 0) & ~owner
                take = (inside > 0) | grown
                out[take] = warped[take]
                owner |= inside > 0
        return out
    return _place_cylinder(_premultiply(art), zone, size, fit)


def _place_cylinder(art, zone, size, fit):
    """Wrap the design around a vertical cylinder seen from the side (and a little from above)."""
    w, h = size
    x0, y0, x1, y1 = zone['box'][0] * w, zone['box'][1] * h, zone['box'][2] * w, zone['box'][3] * h
    half = np.radians(zone['wrap']) / 2
    bh = y1 - y0
    sag = zone['sag'] * bh
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = np.clip((yy - y0) / max(bh, 1), 0, 1)
    cx = (x0 + x1) / 2
    radius = (x1 - x0) / 2 * (1 + (zone['taper'] - 1) * t)        # taper: bottom vs. top width
    xn = (xx - cx) / np.maximum(radius, 1)
    theta = np.arcsin(np.clip(xn, -1, 1))
    ah, aw = art.shape[:2]
    # The visible arc spans the design's full width; the design keeps its aspect ratio over the arc.
    arc_len = 2 * half * (x1 - x0) / 2
    art = _fit(art, max(arc_len, 1) / max(bh - sag, 1), fit) if fit != 'stretch' else art
    ah, aw = art.shape[:2]
    u = (theta / half + 1) / 2 * aw
    v = (yy - y0 - sag * np.cos(theta)) / max(bh - sag, 1) * ah
    valid = (np.abs(xn) <= 1) & (np.abs(theta) <= half) & (v >= 0) & (v < ah)
    out = cv2.remap(art, u.astype(np.float32), v.astype(np.float32), cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    out[~valid] = 0
    return out


# ---- render ------------------------------------------------------------------
def render(bgr, maps, art_rgba, zone, options=None):
    """Composite the design onto the blank. art_rgba: uint8 H×W×4 (RGB order). Returns BGR."""
    o = {**DEFAULTS, **(options or {})}
    h, w = bgr.shape[:2]
    zone = check_zone(zone)
    placed = place(art_rgba, zone, (w, h), o.get('fit', 'contain'))
    if zone['type'] == 'cylinder':
        # Curved bodies get darker towards the sides; the photo's own light already carries most of it.
        disp = np.zeros((h, w), np.float32)
    else:
        disp = maps['disp']
    shift = float(o['fold']) * max(h, w) / 400   # ~5 px at 2000 px for the strongest folds
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    placed = cv2.remap(placed, xx + disp * shift, yy + disp * shift, cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    product = maps['mask'] > 0
    if zone['type'] == 'cylinder' or product.mean() < 0.03:
        product = np.ones_like(product)   # the zone itself bounds a mug; a failed mask clips nothing
    alpha = placed[..., 3] / 255 * product
    rgb = np.where(alpha[..., None] > 1e-4, placed[..., :3] / np.maximum(placed[..., 3:4] / 255, 1e-4), 0)
    ink = rgb[..., ::-1]   # to BGR
    light = 1 + (maps['light'] - 1) * float(o['light'])
    shaded = ink * light[..., None] + maps['texture'][..., None] * float(o['texture']) * (ink.mean(-1, keepdims=True) / 255 + 0.3)
    shaded = np.clip(shaded, 0, 255)
    out = bgr.astype(np.float32) * (1 - alpha[..., None]) + shaded * alpha[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def flat(bgr, art_rgba, zone, fit='contain'):
    """Design pasted with no folds or light, to check its position."""
    h, w = bgr.shape[:2]
    placed = place(art_rgba, check_zone(zone), (w, h), fit)
    alpha = placed[..., 3:4] / 255
    rgb = np.where(alpha > 1e-4, placed[..., :3] / np.maximum(alpha, 1e-4), 0)[..., ::-1]
    return np.clip(bgr * (1 - alpha) + rgb * alpha, 0, 255).astype(np.uint8)
