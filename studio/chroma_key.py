"""Remove a flat key background (green / blue / pink / cyan screen, or white / black) from a design
and return a transparent RGBA image with soft edges and no colour fringe.

Image models cannot write a real alpha channel, so the reliable way to get a transparent print
file from Gemini / GPT Image is: ask for the art on a flat #00FF00 background, then key it out
here. Everything is local, CPU only, no model (about 2.5 s for 3000 x 4000; 15 s with matting).

Steps:
1. detect_key: the dominant colour of the image border (histogram, then refined by averaging
   every border pixel within 40 RGB of it). coverage = share of the border that matches.
2. background_surface (local_bg, on by default): AI green screens have gradients and vignetting,
   so the screen is fitted as a low-degree polynomial of x, y (trimmed least squares on pixels
   near the key). Every pixel is then keyed against its own screen colour. On a synthetic
   gradient screen this cut the composite error over a black shirt from 5.5 to 3.9 (0-255),
   and for the "safe" preset from 16 to 5.
3. matte: alpha from the distance to the screen colour in YCbCr. For a coloured key the chroma
   counts fully and lightness only a quarter, so shadows on the screen still key out.
4. connected_only (preset "safe"): only transparent regions that touch the image border are
   removed, so green leaves or text inside the design survive. remove_holes=True also clears
   enclosed regions that are almost pure key (the inside of an "O").
5. choke (preset "max"): shrink the matte by N px for dirty JPEG edges. Strokes thinner than
   about 2 px disappear, so use "balanced" for fine line art.
6. edge band: near the matte edge, re-estimate alpha from the pixel's own mix with the screen,
   un-mix the colour (F = (C - (1 - a) K) / a) and remove what is left of the key's chroma
   (despill). A thin white stroke on green comes out white with a soft alpha, not green-tinted.
7. matting (opt-in): closed-form alpha (matting.py) on a 10 px band around the edge, for glow,
   smoke and hair; the larger of it and the linear alpha is kept. On two synthetic cases it cut
   the composite error over black from 4.2 to 3.7 and from 3.9 to 1.8, for about 6x the time.

The browser module chroma-key.js implements steps 1 and 3-6 with the same parameters.
"""
import cv2
import numpy as np

KEY_COLORS = {
    'green': (0, 255, 0),
    'blue': (0, 71, 187),
    'pink': (255, 0, 255),
    'cyan': (0, 255, 255),
    'white': (255, 255, 255),
    'black': (0, 0, 0),
}

PRESETS = {
    # Most AI art drawn on a flat screen.
    'balanced': dict(tolerance=0.10, softness=0.12, despill=1.0, connected_only=False, choke=0, edge_radius=2),
    # Designs with details close to the key colour (green leaves on a green screen).
    'safe': dict(tolerance=0.07, softness=0.08, despill=0.7, connected_only=True, remove_holes=False,
                 choke=0, edge_radius=1),
    # Dirty edges, heavy JPEG. Thin strokes may vanish.
    'max': dict(tolerance=0.14, softness=0.14, despill=1.0, connected_only=False, choke=1, edge_radius=3),
}
DEFAULTS = dict(remove_holes=False, min_hole_area=64, refine_edges=True, local_bg=True, matting=False)
LOCAL_BG_TOL = 0.22     # pixels this close to the key count as screen when fitting the local background
LOCAL_BG_EDGE = 128     # the background surface is fitted at this size (it is smooth)
NEUTRAL_CHROMA = 0.08   # a key with less chroma than this is white / black / grey
MIN_COVERAGE = 0.6      # is_keyed: share of the border that must match the key


def hex_to_rgb(value):
    value = str(value).strip().lstrip('#')
    if len(value) != 6 or any(c not in '0123456789abcdefABCDEF' for c in value):
        raise ValueError(f'Mã màu không hợp lệ: {value}')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return '#' + ''.join(f'{int(round(v)):02X}' for v in rgb)


def _ycc(rgb):
    """rgb float in 0..1, shape (..., 3) -> Y, Cb, Cr (BT.601; Cb/Cr in -0.5..0.5)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return y, (b - y) * 0.564, (r - y) * 0.713


def _nearest_name(rgb):
    name, best = 'custom', 1e9
    for n, c in KEY_COLORS.items():
        d = float(np.linalg.norm(np.subtract(rgb, c, dtype=np.float32)))
        if d < best:
            name, best = n, d
    return name if best < 60 else 'custom'


def _border(img, step=2):
    h, w = img.shape[:2]
    parts = [img[0, ::step], img[-1, ::step], img[1:-1:step, 0], img[1:-1:step, -1]]
    return np.concatenate([p.reshape(-1, img.shape[2]) for p in parts])


def detect_key(img, step=2):
    """Dominant border colour of an RGB/RGBA uint8 image.
    Returns {rgb, hex, name, coverage, neutral} or None (no opaque border pixels)."""
    px = _border(img, step)
    if img.shape[2] == 4:
        px = px[px[:, 3] >= 128]
    px = px[:, :3].astype(np.int32)
    if not len(px):
        return None
    bins = (px[:, 0] >> 4) << 8 | (px[:, 1] >> 4) << 4 | (px[:, 2] >> 4)
    values, counts = np.unique(bins, return_counts=True)
    mean = px[bins == values[counts.argmax()]].mean(0)
    near = None
    for _ in range(2):   # gather every border pixel near the peak, so bin edges do not matter
        near = np.linalg.norm(px - mean, axis=1) < 40
        mean = px[near].mean(0)
    rgb = tuple(int(round(v)) for v in mean)
    _, cb, cr = _ycc(np.asarray(rgb, np.float32) / 255)
    return {'rgb': rgb, 'hex': rgb_to_hex(rgb), 'name': _nearest_name(rgb),
            'coverage': float(near.mean()), 'neutral': bool(np.hypot(cb, cr) < NEUTRAL_CHROMA)}


def is_keyed(rgba, min_coverage=MIN_COVERAGE):
    """The detected key when the image is opaque and sits on a flat coloured screen, else None.
    White and black borders are not treated as a screen (plain designs often have them)."""
    if rgba.shape[2] == 4 and (rgba[..., 3] < 250).mean() > 0.01:
        return None
    key = detect_key(rgba)
    if key is None or key['neutral'] or key['coverage'] < min_coverage:
        return None
    return key


def background_surface(img, key, luma_w=0.25, tol=LOCAL_BG_TOL, edge=LOCAL_BG_EDGE, degree=2):
    """Per-pixel screen colour (h, w, 3 floats 0..1). AI green screens and lit backdrops have
    gradients and vignetting, so one key colour is wrong somewhere. Those are low-frequency, so
    the screen is modelled as a low-degree polynomial in x, y per channel, fitted to pixels near
    the key by trimmed least squares (refit on the pixels close to the last fit). A low-order
    surface cannot bend around half-transparent art (smoke, glow), which a local average would
    swallow as background."""
    rgb = img[..., :3].astype(np.float32) / 255
    h, w = rgb.shape[:2]
    s = min(1.0, edge / max(h, w))
    size = (max(2, round(w * s)), max(2, round(h * s)))
    small = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
    k = np.asarray(key, np.float32) / 255
    y, cb, cr = _ycc(small)
    ky, kcb, kcr = (float(v) for v in _ycc(k))
    near = np.sqrt(((y - ky) * luma_w) ** 2 + (cb - kcb) ** 2 + (cr - kcr) ** 2) < tol
    near = cv2.erode(near.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    if near.sum() < 50:
        return np.broadcast_to(k, (h, w, 3)).copy()

    def basis(yy, xx):
        return np.stack([xx ** i * yy ** j for i in range(degree + 1) for j in range(degree + 1 - i)], -1)

    gy, gx = np.mgrid[0:size[1], 0:size[0]].astype(np.float32)
    terms = basis(gy[near] / size[1] - 0.5, gx[near] / size[0] - 0.5)
    target = small[near]
    keep = np.ones(len(target), bool)
    for _ in range(6):   # trimmed fit: drop pixels far off the surface (smoke, glow, AA edges)
        coef, *_ = np.linalg.lstsq(terms[keep], target[keep], rcond=None)
        res = np.linalg.norm(terms @ coef - target, axis=1)
        scale = 1.4826 * np.median(res[keep]) + 1e-4
        new = res < 2.5 * scale
        if new.sum() < 30 or (new == keep).all():
            break
        keep = new
    fy, fx = np.mgrid[0:h, 0:w].astype(np.float32)
    surf = basis(fy / h - 0.5, fx / w - 0.5) @ coef
    return np.clip(surf, 0, 1).astype(np.float32)


def _erode(a, r):
    if r <= 0:
        return a
    return cv2.erode(a, np.ones((2 * r + 1, 2 * r + 1), np.uint8), borderType=cv2.BORDER_REPLICATE)


def _keep_border_connected(alpha, dist, t0, remove_holes, min_hole_area):
    """Restore every transparent region that does not touch the border (unless it is a hole of
    almost pure key and remove_holes is on)."""
    cand = (alpha < 1).astype(np.uint8)
    n, labels = cv2.connectedComponents(cand, connectivity=4)
    if n <= 1:
        return alpha
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    inner = np.ones(n, bool)
    inner[0] = False
    inner[edge] = False
    if remove_holes:
        core = dist < t0
        flat = labels.ravel()
        core_n = np.bincount(flat, weights=core.ravel(), minlength=n)
        core_d = np.bincount(flat, weights=(dist * core).ravel(), minlength=n)
        with np.errstate(invalid='ignore', divide='ignore'):
            hole = (core_n >= min_hole_area) & (core_d / np.maximum(core_n, 1) < t0 * 0.6)
        inner &= ~hole
    out = alpha.copy()
    out[inner[labels]] = 1.0
    return out


def remove_key(img, preset='balanced', key='auto', allow_neutral_auto=False, **overrides):
    """Key out the background of an RGB or RGBA uint8 array.

    key: 'auto', a name in KEY_COLORS, '#RRGGBB' or an (r, g, b) tuple.
    overrides: tolerance, softness, despill, connected_only, choke, edge_radius, refine_edges,
    remove_holes, min_hole_area, local_bg (fit the screen's gradient, default on),
    matting (closed-form alpha on the edge band: better glow / hair, several seconds).
    Returns (rgba uint8, info) with info = {key, key_hex, detected, transparent, partial, opaque}
    (the last three are shares of the image, 0..1).
    """
    if preset not in PRESETS:
        raise ValueError(f'Chế độ không hợp lệ: {preset}')
    o = {**DEFAULTS, **PRESETS[preset], **overrides}
    img = np.ascontiguousarray(img)
    if img.ndim != 3 or img.shape[2] not in (3, 4):
        raise ValueError('Ảnh phải là RGB hoặc RGBA.')

    detected = None
    if key in (None, 'auto'):
        detected = detect_key(img)
        if detected is None:
            raise ValueError('Không dò được màu nền ở mép ảnh.')
        if detected['neutral'] and not allow_neutral_auto:
            raise ValueError(f'Nền dò được là màu trung tính {detected["hex"]}. '
                             'Chọn nền trắng/đen thủ công hoặc bật tự dò cả trắng/đen.')
        key = detected['rgb']
    elif isinstance(key, str):
        key = KEY_COLORS.get(key) or hex_to_rgb(key)
    key = tuple(int(v) for v in key)

    k0 = np.asarray(key, np.float32) / 255
    _, cb0, cr0 = _ycc(k0)
    chromatic = float(np.hypot(cb0, cr0)) >= NEUTRAL_CHROMA
    luma_w = 0.25 if chromatic else 1.0
    if o['local_bg']:
        k = background_surface(img, key, luma_w)      # (h, w, 3): the screen colour at each pixel
    else:
        k = np.broadcast_to(k0, img.shape[:2] + (3,))
    k_y, k_cb, k_cr = _ycc(k)
    k_mag = np.maximum(np.hypot(k_cb, k_cr), 1e-6)

    rgb = img[..., :3].astype(np.float32) / 255
    src_a = img[..., 3].astype(np.float32) / 255 if img.shape[2] == 4 else np.ones(img.shape[:2], np.float32)
    y, cb, cr = _ycc(rgb)
    dist = np.sqrt(((y - k_y) * luma_w) ** 2 + (cb - k_cb) ** 2 + (cr - k_cr) ** 2)
    t0, span = o['tolerance'], max(o['softness'], 1e-4)
    alpha = np.clip((dist - t0) / span, 0, 1).astype(np.float32)

    if o['connected_only']:
        alpha = _keep_border_connected(alpha, dist, t0, o['remove_holes'], o['min_hole_area'])
    alpha = _erode(alpha, int(o['choke']))

    def edge_pass(alpha, refine):
        """Re-estimate alpha (when refine) and un-mix / despill the colour on the edge band."""
        out_rgb = rgb.copy()
        alpha = alpha.copy()
        band = (_erode(alpha, max(1, int(o['edge_radius']))) < 1) & (alpha > 0)
        if not band.any():
            return alpha, out_rgb
        c = rgb[band]
        a = alpha[band]
        by, bcb, bcr = y[band], cb[band], cr[band]
        kk, ky_, kcb, kcr, kmag = k[band], k_y[band], k_cb[band], k_cr[band], k_mag[band]
        proj = (bcb * kcb + bcr * kcr) / kmag
        spill = proj > 0
        if refine:
            # alpha from the pixel's own mix with the key: d(C, K) / d(F, K), F = C without
            # its chroma along the key direction (YCbCr is linear in RGB, so the ratio holds).
            fcb = bcb - proj * kcb / kmag
            fcr = bcr - proj * kcr / kmag
            d_f = np.sqrt(((by - ky_) * luma_w) ** 2 + (fcb - kcb) ** 2 + (fcr - kcr) ** 2)
            est = np.where(d_f > 1e-4, dist[band] / np.maximum(d_f, 1e-4), a)
            a = np.where(spill, np.minimum(a, np.maximum(est, 0)), a)
        # Un-mix: C = a F + (1 - a) K  ->  F = (C - (1 - a) K) / a, against the local screen colour.
        mix = (a > 0.02) & (a < 1) & (spill | (not refine))
        safe_a = np.where(mix, a, 1)[:, None]
        c = np.where(mix[:, None], np.clip((c - (1 - safe_a) * kk) / safe_a, 0, 1), c)
        if o['despill'] > 0:
            cy, ccb, ccr = _ycc(c)
            p2 = (ccb * kcb + ccr * kcr) / kmag
            s = np.where(spill & (p2 > 0), o['despill'] * p2 / kmag, 0)
            ccb, ccr = ccb - s * kcb, ccr - s * kcr
            c = np.clip(np.stack([cy + 1.403 * ccr, cy - 0.344 * ccb - 0.714 * ccr, cy + 1.773 * ccb], -1), 0, 1)
        out_rgb[band] = c
        alpha[band] = a
        return alpha, out_rgb

    if chromatic:
        alpha, out_rgb = edge_pass(alpha, o['refine_edges'])
        if o['matting']:
            import matting   # scipy; only loaded when asked for
            # The linear estimate tends to miss faint glow, matting tends to thin 1 px strands:
            # both under-shoot, so keep the larger of the two.
            solved = np.maximum(matting.refine_alpha(img[..., :3], alpha), alpha)
            # Colours from the first pass are better where it already saw ink (dividing by a
            # slightly-off solved alpha amplifies the error); un-mix only the pixels it missed.
            _, rgb2 = edge_pass(solved, refine=False)
            missed = (alpha < 0.05) & (solved >= 0.05)
            out_rgb[missed] = rgb2[missed]
            alpha = solved
    else:
        out_rgb = rgb

    alpha = alpha * src_a
    alpha[alpha <= 0.002] = 0
    out = np.empty(img.shape[:2] + (4,), np.uint8)
    out[..., :3] = np.round(out_rgb * 255)
    out[..., 3] = np.round(alpha * 255)
    out[alpha == 0] = 0
    n = alpha.size
    info = {'key': list(key), 'key_hex': rgb_to_hex(key), 'detected': detected,
            'transparent': float((alpha == 0).sum() / n),
            'partial': float(((alpha > 0) & (alpha < 0.998)).sum() / n),
            'opaque': float((alpha >= 0.998).sum() / n)}
    return out, info


def trim_bounds(rgba, threshold=8):
    """(x, y, w, h) of the pixels with alpha above threshold, or None when empty."""
    ys, xs = np.nonzero(rgba[..., 3] > threshold)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
