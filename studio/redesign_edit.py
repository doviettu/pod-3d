"""Redesign, step 2: change a split design on this Mac, without any AI call.

Per layer (from the step 1 split): remove it, retype a text layer in a chosen font, give it one
colour, move or scale it, and add an outline, a hard shadow or a distressed print look. For the
whole design: map palette colours to new ones, or make palette variants from a preset theme.
Layers that are not touched keep every pixel. check() runs the measurable rules of the
"chống AI-look" and print Skills (colour count, empty space, soft halos, art cut by the edge).
"""
import re
import zlib

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HEX = re.compile(r'#[0-9a-fA-F]{6}')
EFFECTS = ('none', 'outline', 'shadow', 'distress')
MAX_TEXT = 120
# Theme palettes for variants, dark to light; the design's own colours are mapped onto them by
# brightness rank, so the darkest ink stays the darkest.
PRESETS = {
    'halloween': ('Halloween', ('#1b1b1f', '#4b2a6b', '#d9531e', '#f29d38', '#f6e7c8')),
    'christmas': ('Christmas', ('#1d2b22', '#1f6b3a', '#b3202a', '#e0b44c', '#f7f3ea')),
    'valentine': ('Valentine', ('#3b1020', '#9e1b3c', '#e0457b', '#f5a3bd', '#fbe7ee')),
    'st-patrick': ('St. Patrick', ('#13281a', '#1e7a3c', '#47b04b', '#e3c55a', '#f3f7ea')),
    'vintage': ('Vintage', ('#2b2420', '#6e4a32', '#b3683a', '#d9a75f', '#efe3c8')),
    'retro-70s': ('Retro 70s', ('#3a1f14', '#b5452a', '#e07b2e', '#efb43d', '#f4e4c1')),
    'mono-black': ('Đen trắng', ('#111111', '#444444', '#888888', '#cccccc', '#ffffff')),
    'pastel': ('Pastel', ('#5a5a72', '#8fa8d8', '#e8a0b4', '#f5d48f', '#fbf6ee')),
}


def rgb(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('Màu phải có dạng #rrggbb.')
    return np.array([int(value[i:i + 2], 16) for i in (1, 3, 5)], np.int16)


def hexcode(c):
    return '#%02x%02x%02x' % tuple(int(v) for v in c)


def layer_masks(scene, cut):
    """Full-canvas mask per layer id, rebuilt from the cut-out PNGs. cut(file) -> RGBA array."""
    w, h = scene['size']
    out = {}
    for layer in scene['layers']:
        x, y, bw, bh = layer['bbox']
        mask = np.zeros((h, w), bool)
        mask[y:y + bh, x:x + bw] = cut(layer['file'])[..., 3] > 0
        out[layer['id']] = mask
    return out


def _clean(edits, scene):
    """Validated edits {layer id: {...}} with defaults; unknown layers and fields are refused."""
    if not isinstance(edits, dict):
        raise ValueError('Danh sách sửa không hợp lệ.')
    known = {l['id']: l for l in scene['layers']}
    out = {}
    for lid, e in edits.items():
        if lid not in known or not isinstance(e, dict):
            raise ValueError(f'Lớp {lid} không có trong thiết kế.')
        c = {'remove': e.get('remove') is True}
        text = e.get('text')
        if text not in (None, ''):
            if known[lid]['type'] != 'text' or not isinstance(text, str) or len(text) > MAX_TEXT or not text.strip():
                raise ValueError(f'Chữ mới cho {lid} cần 1 đến {MAX_TEXT} ký tự, và chỉ dùng cho lớp chữ.')
            c['text'] = text.strip()
            c['font'] = e.get('font') if isinstance(e.get('font'), str) else ''
        if e.get('color'):
            c['color'] = rgb(e['color'])
        for key, low, high, default in (('dx', -100, 100, 0), ('dy', -100, 100, 0), ('scale', 0.2, 4, 1)):
            v = e.get(key, default)
            if type(v) not in (int, float) or not low <= v <= high:
                raise ValueError(f'{key} của {lid} phải trong khoảng {low} đến {high}.')
            c[key] = float(v)
        effect = e.get('effect') or 'none'
        if effect not in EFFECTS:
            raise ValueError('Hiệu ứng không hợp lệ.')
        c['effect'] = effect
        c['effect_color'] = rgb(e['effect_color']) if e.get('effect_color') else np.array([17, 17, 17], np.int16)
        c['moved'] = bool(c.get('text') or c['dx'] or c['dy'] or c['scale'] != 1 or effect != 'none')
        if c['remove'] or c['moved'] or 'color' in c:
            out[lid] = c
    return out


def _palette_map(mapping, palette):
    if not mapping:
        return []
    if not isinstance(mapping, dict):
        raise ValueError('Bảng đổi màu không hợp lệ.')
    known = {p['hex'].lower() for p in palette}
    pairs = []
    for src, dst in mapping.items():
        if src.lower() not in known:
            raise ValueError(f'Màu {src} không có trong bảng màu của thiết kế.')
        if dst and dst.lower() != src.lower():
            pairs.append((rgb(src), rgb(dst)))
    return pairs


def recolor_palette(image, ink, palette, pairs):
    """Shift every ink pixel whose nearest palette colour is mapped by (new - old): shading stays."""
    if not pairs:
        return image
    centres = np.array([rgb(p['hex']) for p in palette], np.int32)
    px = image[..., :3][ink].astype(np.int32)
    nearest = np.argmin(((px[:, None, :] - centres[None]) ** 2).sum(2), 1)
    shift = np.zeros_like(centres)
    for src, dst in pairs:
        i = int(np.argmin(((centres - src) ** 2).sum(1)))
        shift[i] = dst - src
    out = image.copy()
    out[..., :3][ink] = np.clip(px + shift[nearest], 0, 255).astype(np.uint8)
    return out


def preset_pairs(palette, preset):
    """Map the design palette onto a preset theme by brightness rank."""
    if preset not in PRESETS:
        raise ValueError('Bộ màu không có.')
    colours = PRESETS[preset][1]
    lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    ranked = sorted(palette, key=lambda p: lum(rgb(p['hex'])))
    n = len(ranked)
    out = {}
    for i, p in enumerate(ranked):
        out[p['hex']] = colours[round(i * (len(colours) - 1) / max(1, n - 1))]
    return out


def _erase(image, mask, others, background):
    """Take a layer out: background colour (opaque design), transparency, or inpaint when the
    layer sits on another layer's ink."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    grown = (cv2.dilate(mask.astype(np.uint8), k) > 0) & ~(others & ~mask)
    ring = (cv2.dilate(grown.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))) > 0) & ~grown
    on_ink = ring.any() and (others & ring).sum() > 0.5 * ring.sum()
    out = image.copy()
    if on_ink:
        rgb_ = cv2.inpaint(np.ascontiguousarray(out[..., :3]), grown.astype(np.uint8), 5, cv2.INPAINT_TELEA)
        out[..., :3][grown] = rgb_[grown]
        out[..., 3][grown] = 255
    elif background is not None:
        out[grown] = [*background, 255]
    else:
        out[..., 3][grown] = 0
    return out


def find_font(font_id, size, resolve):
    if font_id and resolve:
        try:
            return ImageFont.truetype(str(resolve(font_id)), size)
        except (OSError, ValueError):
            pass
    for path in ('/System/Library/Fonts/Supplemental/Arial Bold.ttf', '/System/Library/Fonts/Supplemental/Arial.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render_text(text, box, colour, font_id='', resolve=None):
    """New words drawn to fill the old text box: the largest size whose width fits the box and
    whose height stays within 1.2x of it. Returns an RGBA crop."""
    bw, bh = box
    lo, hi, best = 4, max(8, bh * 3), None
    probe = ImageDraw.Draw(Image.new('L', (1, 1)))
    while lo <= hi:
        mid = (lo + hi) // 2
        font = find_font(font_id, mid, resolve)
        l, t, r, b = probe.multiline_textbbox((0, 0), text, font=font, align='center')
        if r - l <= bw and b - t <= bh * 1.2:
            best, lo = (font, (l, t, r, b)), mid + 1
        else:
            hi = mid - 1
    if best is None:
        best = (find_font(font_id, 4, resolve), probe.multiline_textbbox((0, 0), text, font=find_font(font_id, 4, resolve)))
    font, (l, t, r, b) = best
    image = Image.new('RGBA', (max(1, r - l), max(1, b - t)), (0, 0, 0, 0))
    ImageDraw.Draw(image).multiline_text((-l, -t), text, font=font, fill=(*[int(v) for v in colour], 255), align='center')
    out = np.asarray(image).copy()
    out[..., 3] = np.where(out[..., 3] >= 128, 255, 0)   # hard edges for print
    return out


def effect(crop, kind, colour, edge, seed=0):
    """Outline, hard drop shadow or distressed wear on an RGBA crop; returns (crop, (ox, oy)) where
    ox, oy is how far the crop grew to the left and top."""
    if kind == 'none':
        return crop, (0, 0)
    if kind == 'distress':
        h, w = crop.shape[:2]
        rng = np.random.default_rng(seed)
        noise = rng.random((max(2, h // 6), max(2, w // 6))).astype(np.float32)
        noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_CUBIC)
        speck = rng.random((h, w)) < 0.04
        out = crop.copy()
        out[..., 3][(noise > 0.78) | speck] = 0
        return out, (0, 0)
    width = max(2, round(edge * 0.006))
    pad = width * 2 + 2
    big = np.zeros((crop.shape[0] + 2 * pad, crop.shape[1] + 2 * pad, 4), np.uint8)
    big[pad:-pad, pad:-pad] = crop
    alpha = big[..., 3] > 0
    if kind == 'outline':
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * width + 1, 2 * width + 1))
        under = cv2.dilate(alpha.astype(np.uint8), k) > 0
    else:   # shadow: the shape moved down-right by 1.5 widths
        s = round(width * 1.5)
        under = np.zeros_like(alpha)
        under[s:, s:] = alpha[:-s, :-s]
    base = np.zeros_like(big)
    base[under] = [*[int(v) for v in colour], 255]
    out = Image.alpha_composite(Image.fromarray(base), Image.fromarray(big))
    return np.asarray(out).copy(), (pad, pad)


def apply(source, scene, cut, edits=None, palette_map=None, resolve_font=None):
    """The edited design as an RGBA array. cut(file) reads a layer cut-out; resolve_font(id) -> path."""
    edits = _clean(edits or {}, scene)
    pairs = _palette_map(palette_map, scene.get('palette', []))
    h, w = source.shape[:2]
    masks = layer_masks(scene, cut)
    ink = np.any(list(masks.values()), 0) if masks else np.zeros((h, w), bool)
    background = scene.get('background')
    image = recolor_palette(source, ink, scene.get('palette', []), pairs)
    pasted = []
    for layer in scene['layers']:
        e = edits.get(layer['id'])
        if not e:
            continue
        mask = masks[layer['id']]
        if 'color' in e and not e['moved'] and not e['remove']:
            px = image[..., :3][mask].astype(np.int16)
            image[..., :3][mask] = np.clip(px - np.median(px, 0) + e['color'], 0, 255).astype(np.uint8)
            continue
        others = ink & ~mask
        x, y, bw, bh = layer['bbox']
        crop = image[y:y + bh, x:x + bw].copy()
        crop[..., 3] = np.where(mask[y:y + bh, x:x + bw], crop[..., 3], 0)
        image = _erase(image, mask, others, background)
        if e['remove']:
            continue
        if e.get('text'):
            colour = e.get('color', rgb(layer['color']) if layer.get('color') else np.array([17, 17, 17]))
            crop = render_text(e['text'], (bw, bh), colour, e.get('font'), resolve_font)
        elif 'color' in e:
            visible = crop[..., 3] > 0
            px = crop[..., :3][visible].astype(np.int16)
            crop[..., :3][visible] = np.clip(px - np.median(px, 0) + e['color'], 0, 255).astype(np.uint8)
        if e['scale'] != 1:
            size = (max(1, round(crop.shape[1] * e['scale'])), max(1, round(crop.shape[0] * e['scale'])))
            crop = np.asarray(Image.fromarray(crop).resize(size, Image.Resampling.LANCZOS)).copy()
        crop, (ox, oy) = effect(crop, e['effect'], e['effect_color'], max(w, h), zlib.crc32(layer['id'].encode()))
        cx = x + bw / 2 + e['dx'] / 100 * w
        cy = y + bh / 2 + e['dy'] / 100 * h
        inner_w, inner_h = crop.shape[1] - 2 * ox, crop.shape[0] - 2 * oy
        pasted.append((crop, round(cx - inner_w / 2) - ox, round(cy - inner_h / 2) - oy))
    canvas = Image.fromarray(image)
    for crop, px_, py_ in pasted:
        layer_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        layer_img.paste(Image.fromarray(crop), (px_, py_))
        canvas = Image.alpha_composite(canvas, layer_img)
    return np.asarray(canvas).copy()


def ink_of(image, background=None):
    """Design pixels: opaque ones, or on a solid background the ones far from its colour."""
    if background is None:
        return image[..., 3] >= 128
    return np.abs(image[..., :3].astype(np.int16) - np.array(background)).max(2) > 28


def check(image, background=None):
    """Measurable rules from the Skills: [{id, ok, label, detail}]."""
    h, w = image.shape[:2]
    alpha = image[..., 3]
    ink = ink_of(image, background)
    out = []
    from redesign import palette as find_palette   # same k-means as step 1
    colours = [p for p in find_palette(np.ascontiguousarray(image[..., :3]), ink, 8) if p['pct'] >= 3]
    out.append({'id': 'colours', 'ok': len(colours) <= 6, 'label': 'Bảng màu giới hạn',
                'detail': f'{len(colours)} màu chính (nên tối đa 6)'})
    empty = 100 - ink.mean() * 100
    out.append({'id': 'space', 'ok': bool(empty >= 25), 'label': 'Có khoảng trống',
                'detail': f'{empty:.0f}% diện tích trống (nên ít nhất 25%)'})
    if background is None:
        visible = alpha > 8
        soft = ((alpha > 8) & (alpha < 247)).sum() / max(1, visible.sum()) * 100
        out.append({'id': 'halo', 'ok': bool(soft <= 3), 'label': 'Viền cứng, không quầng mờ',
                    'detail': f'{soft:.1f}% điểm ảnh bán trong suốt (nên dưới 3%)'})
    m = max(2, round(min(w, h) * 0.01))
    edge = ink[:m].any() or ink[-m:].any() or ink[:, :m].any() or ink[:, -m:].any()
    out.append({'id': 'edge', 'ok': not bool(edge), 'label': 'Không bị cắt mép',
                'detail': 'Art chạm mép khung' if edge else 'Cách mép khung ít nhất 1%'})
    return out
