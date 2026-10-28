"""2D pipeline: find the printed art on a shirt photo and export it as a transparent
4500 x 5400 PNG at 300 DPI, one file per printed side.

Steps: garment mask -> fabric colour -> art box -> shading flatten -> alpha from
fabric colour (with colour decontamination) -> upscale -> place on canvas.
Everything is local and deterministic; boxes found here can be overridden by the UI.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

CANVAS = (4500, 5400)
DPI = 300
WORK_MAX = 2048          # longest side used for detection
ALPHA_LOW, ALPHA_HIGH = 8.0, 22.0   # colour distance from fabric: transparent below, opaque above
SIDES = ('auto', 'front', 'back', 'both')


def _lab(rgb):
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32) * np.array([100 / 255, 1, 1], np.float32) \
        - np.array([0, 128, 128], np.float32)


def _distance(lab, ref):
    # Shading changes lightness far more than hue, so lightness counts half.
    d = lab - np.asarray(ref, np.float32)
    return np.sqrt((0.5 * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)


def _fill_holes(mask):
    h, w = mask.shape
    flood = mask.copy()
    canvas = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, canvas, (0, 0), 255)
    return mask | cv2.bitwise_not(flood)


def garment_mask(rgb):
    """Foreground mask of the garment(s). Uses rembg when installed, else the border colour."""
    try:
        from rembg import remove  # optional: BiRefNet/U2Net matting
        alpha = np.asarray(remove(Image.fromarray(rgb), only_mask=True))
        mask = (alpha > 127).astype(np.uint8) * 255
    except Exception:  # noqa: BLE001 - optional dependency or model missing
        lab = _lab(rgb)
        h, w = rgb.shape[:2]
        b = max(2, min(h, w) // 100)
        border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
                                 lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
        background = np.median(border, axis=0)
        spread = np.percentile(_distance(border, background), 95)
        mask = (_distance(lab, background) > max(10.0, spread * 1.5)).astype(np.uint8) * 255
    k = max(3, min(mask.shape) // 150) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k * 3, k * 3), np.uint8))
    return _fill_holes(mask)


def split_garments(mask):
    """Bounding boxes (x, y, w, h) of garments, left to right. Two shirts side by side -> two boxes."""
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    total = stats[1:, cv2.CC_STAT_AREA].sum() if n > 1 else 0
    boxes = [tuple(int(v) for v in stats[i, :4]) for i in range(1, n)
             if total and stats[i, cv2.CC_STAT_AREA] >= 0.15 * total]
    boxes.sort(key=lambda b: b[0])
    if not boxes:
        h, w = mask.shape
        return [(0, 0, w, h)]
    return boxes[:2]


def fabric_color(lab, mask):
    """Dominant Lab colour inside the mask (the shirt fabric)."""
    pixels = lab[mask > 0]
    if len(pixels) == 0:
        return np.array([95, 0, 0], np.float32)
    rng = np.random.default_rng(0)
    sample = pixels[rng.choice(len(pixels), min(len(pixels), 40000), replace=False)]
    k = 3 if len(sample) >= 3 else 1
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, centers = cv2.kmeans(sample.astype(np.float32), k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    return centers[np.bincount(labels.ravel()).argmax()]


def art_box(lab, gmask, box, fabric):
    """Box (x, y, w, h) around the print inside one garment box, or None when nothing stands out."""
    x, y, w, h = box
    region = np.zeros_like(gmask)
    region[y:y + h, x:x + w] = gmask[y:y + h, x:x + w]
    # Stay clear of the garment outline, seams and side shadows.
    margin = max(3, int(0.03 * min(w, h)))
    inner = cv2.erode(region, np.ones((margin * 2 + 1, margin * 2 + 1), np.uint8))
    art = ((_distance(lab, fabric) > ALPHA_HIGH) & (inner > 0)).astype(np.uint8) * 255
    k = max(3, min(w, h) // 120) | 1
    art = cv2.morphologyEx(art, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    art = cv2.morphologyEx(art, cv2.MORPH_CLOSE, np.ones((k * 5, k * 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(art)
    keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 0.002 * w * h]
    if not keep:
        return None
    biggest = max(stats[i, cv2.CC_STAT_AREA] for i in keep)
    keep = [i for i in keep if stats[i, cv2.CC_STAT_AREA] >= 0.02 * biggest]
    x0 = min(stats[i, 0] for i in keep)
    y0 = min(stats[i, 1] for i in keep)
    x1 = max(stats[i, 0] + stats[i, 2] for i in keep)
    y1 = max(stats[i, 1] + stats[i, 3] for i in keep)
    pad = int(0.02 * max(x1 - x0, y1 - y0)) + 2
    x0, y0 = max(x, x0 - pad), max(y, y0 - pad)
    x1, y1 = min(x + w, x1 + pad), min(y + h, y1 + pad)
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def detect(rgb):
    """Find garments and the art box on each. Boxes are normalised to 0..1 of the image."""
    h, w = rgb.shape[:2]
    scale = min(1.0, WORK_MAX / max(h, w))
    small = cv2.resize(rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    sh, sw = small.shape[:2]
    lab = _lab(small)
    gmask = garment_mask(small)
    result = []
    for box in split_garments(gmask):
        x, y, bw, bh = box
        sub = np.zeros_like(gmask)
        sub[y:y + bh, x:x + bw] = gmask[y:y + bh, x:x + bw]
        fabric = fabric_color(lab, cv2.erode(sub, np.ones((5, 5), np.uint8)))
        art = art_box(lab, gmask, box, fabric)
        coverage = 0.0 if art is None else (art[2] * art[3]) / max(1, bw * bh)
        result.append({
            'garment': [x / sw, y / sh, bw / sw, bh / sh],
            'coverage': round(coverage, 3),
            'art': None if art is None else [art[0] / sw, art[1] / sh, art[2] / sw, art[3] / sh],
            'fabric_lab': [round(float(v), 2) for v in fabric],
        })
    return {'width': w, 'height': h, 'garments': result}


def assign_sides(regions, sides):
    """regions: list of (source_index, garment_dict) in reading order -> list of (side, region)."""
    if sides not in SIDES:
        raise ValueError('Mặt in phải là auto, front, back hoặc both.')
    usable = [r for r in regions if r[1].get('art')]
    if not usable:
        return []
    if len(usable) >= 2 and sides in ('auto', 'both'):
        return [('front', usable[0]), ('back', usable[1])]
    if sides == 'back':
        return [('back', usable[1] if len(usable) >= 2 else usable[0])]
    return [('front', usable[0])]


def flatten_shading(rgb, fabric_mask, fabric):
    """Divide out the slow light/shadow changes measured on the plain fabric around the art."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    light = lab[..., 0]
    m = (fabric_mask > 0).astype(np.float32)
    if m.sum() < 0.02 * m.size:
        return rgb
    sigma = max(rgb.shape[:2]) / 8
    est = cv2.GaussianBlur(light * m, (0, 0), sigma) / np.maximum(cv2.GaussianBlur(m, (0, 0), sigma), 1e-3)
    gain = np.clip((fabric[0] * 255 / 100) / np.maximum(est, 1), 0.6, 1.6)
    lab[..., 0] = np.clip(light * gain, 0, 255)
    return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2RGB)


def cut_out(rgb, fabric):
    """RGBA float image: alpha from distance to fabric colour, colours freed from the fabric tint."""
    lab = _lab(rgb)
    d = _distance(lab, fabric)
    alpha = np.clip((d - ALPHA_LOW) / (ALPHA_HIGH - ALPHA_LOW), 0, 1)
    alpha = cv2.GaussianBlur(alpha, (0, 0), 0.7)
    alpha[alpha < 0.12] = 0  # JPEG speckle on the fabric
    fabric_rgb = cv2.cvtColor(np.uint8([[[(fabric[0]) * 255 / 100, fabric[1] + 128, fabric[2] + 128]]]),
                              cv2.COLOR_LAB2RGB)[0, 0].astype(np.float32)
    img = rgb.astype(np.float32)
    a = np.maximum(alpha, 0.05)[..., None]
    clean = np.clip(fabric_rgb + (img - fabric_rgb) / a, 0, 255)
    clean = np.where(alpha[..., None] > 0.98, img, clean)
    # Edge and transparent pixels take the colour of nearby solid art, so the upscaler and
    # resizing never pull the fabric colour back in as a dark or light halo.
    w = alpha ** 2
    solid = alpha >= 0.9
    filled = clean.copy()
    for sigma in (1.5, 4, 12, 40):
        weight = cv2.GaussianBlur(w, (0, 0), sigma)
        spread = cv2.GaussianBlur(clean * w[..., None], (0, 0), sigma) / np.maximum(weight, 1e-4)[..., None]
        todo = ~solid & (weight > 1e-3)
        filled[todo] = spread[todo]
        solid = solid | todo
    clean = filled
    return clean, alpha


def _upscale(rgb, factor, log):
    if factor <= 1.0:
        return cv2.resize(rgb, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    try:
        from pod3d.upscale import upscale  # Real-ESRGAN when the engine and model are installed
        bgr = upscale(np.ascontiguousarray(rgb[..., ::-1]), factor, log=log)
        return np.ascontiguousarray(bgr[..., ::-1])
    except ImportError:
        return cv2.resize(rgb, None, fx=factor, fy=factor, interpolation=cv2.INTER_LANCZOS4)


def render_side(rgb, box, fabric, *, background='remove', log=print):
    """Crop one art box, clean it and return (RGBA uint8 canvas, report dict)."""
    h, w = rgb.shape[:2]
    x, y, bw, bh = [int(round(v)) for v in (box[0] * w, box[1] * h, box[2] * w, box[3] * h)]
    x, y = max(0, x), max(0, y)
    bw, bh = min(w - x, bw), min(h - y, bh)
    if bw < 8 or bh < 8:
        raise ValueError('Vùng art quá nhỏ.')
    crop = rgb[y:y + bh, x:x + bw]
    fabric = np.asarray(fabric, np.float32)
    fabric_mask = (_distance(_lab(crop), fabric) < ALPHA_LOW * 1.5).astype(np.uint8)
    flat = flatten_shading(crop, fabric_mask, fabric)
    if background == 'keep':
        colour, alpha = flat.astype(np.float32), np.ones(flat.shape[:2], np.float32)
    else:
        colour, alpha = cut_out(flat, fabric)
    factor = min(CANVAS[0] / bw, CANVAS[1] / bh)
    big = _upscale(np.uint8(colour), factor, log)
    tw, th = big.shape[1], big.shape[0]
    big_alpha = cv2.resize(alpha, (tw, th), interpolation=cv2.INTER_CUBIC)
    canvas = np.zeros((CANVAS[1], CANVAS[0], 4), np.uint8)
    ox = (CANVAS[0] - tw) // 2           # centred horizontally, top aligned like POD print templates
    canvas[:th, ox:ox + tw, :3] = big[:CANVAS[1], :CANVAS[0]]
    canvas[:th, ox:ox + tw, 3] = np.uint8(np.clip(big_alpha, 0, 1) * 255)[:CANVAS[1], :CANVAS[0]]
    warnings = []
    if factor > 4:
        warnings.append(f'Ảnh nguồn nhỏ: art chỉ {bw}×{bh} px, phải phóng {factor:.1f} lần, dễ mờ. Nên tìm ảnh lớn hơn.')
    report = dict(source_box=[x, y, bw, bh], scale=round(factor, 3), placed=[ox, 0, tw, th],
                  background=background, warnings=warnings)
    return canvas, report


def save_png(canvas, path):
    Image.fromarray(canvas, 'RGBA').save(path, dpi=(DPI, DPI), optimize=False)


def save_preview(canvas, path, size=900):
    """Small JPEG on a checkerboard so transparency is visible."""
    h, w = canvas.shape[:2]
    scale = size / max(h, w)
    small = cv2.resize(canvas, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    yy, xx = np.mgrid[:small.shape[0], :small.shape[1]]
    board = np.where(((yy // 16 + xx // 16) % 2)[..., None] == 0, 236, 255).astype(np.float32)
    a = small[..., 3:4].astype(np.float32) / 255
    out = small[..., :3].astype(np.float32) * a + board * (1 - a)
    Image.fromarray(np.uint8(out)).save(path, quality=88)


def run(sources, folder, *, sides='auto', boxes=None, background='remove', log=print):
    """sources: list of RGB arrays (1 or 2). boxes: optional {'front': [x,y,w,h], 'back': [...]} normalised,
    each with 'source' index. Writes front.png/back.png + previews + report.json into folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    detections = [detect(rgb) for rgb in sources]
    regions = [(i, g) for i, d in enumerate(detections) for g in d['garments']]
    plan = assign_sides(regions, sides)
    if boxes:
        overrides = []
        for side in ('front', 'back'):
            spec = boxes.get(side)
            if spec:
                src = int(spec.get('source', 0))
                if not 0 <= src < len(sources):
                    raise ValueError('Nguồn ảnh của vùng chọn không hợp lệ.')
                garment = next((g for i, g in regions if i == src), None)
                fabric = (garment or {}).get('fabric_lab') or [95, 0, 0]
                overrides.append((side, (src, {'art': spec['box'], 'fabric_lab': fabric})))
        plan = overrides or plan
    if not plan:
        raise ValueError('Không tìm thấy vùng art trên áo. Hãy kéo khung chọn vùng art bằng tay.')
    outputs, sides_report = {}, {}
    for side, (src, region) in plan:
        log(f'{side}: tách art từ ảnh {src + 1}')
        canvas, report = render_side(sources[src], region['art'], region['fabric_lab'],
                                     background=background, log=log)
        save_png(canvas, folder / f'{side}.png')
        save_preview(canvas, folder / f'{side}-preview.jpg')
        if region.get('coverage', 0) > 0.6:
            report['warnings'].append('Art phủ gần hết áo: có thể đây là áo in tràn, nên dùng tab 3D.')
        outputs[side] = {'file': f'{side}.png', 'preview': f'{side}-preview.jpg', 'size': list(CANVAS), 'dpi': DPI,
                         'warnings': report['warnings']}
        sides_report[side] = dict(report, source=src, art=region['art'])
    report = {'canvas': list(CANVAS), 'dpi': DPI, 'detections': detections, 'sides': sides_report}
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return outputs, report
