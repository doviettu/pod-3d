"""Find the panels of a new mockup shot automatically with SAM 3, as starting quads for marking.

SAM 3 (Meta, SAM License: commercial use allowed) segments every instance of a short text
prompt. We ask for the whole garment, its sleeves and its hood, cut sleeves and hood out of the
garment to get the body, and name the segments by layout (see assign: front view on the left,
back view on the right, or a front view alone). Each segment becomes 4 corners. The user still checks and drags the corners before saving; nothing here is final.

The weights are gated on Hugging Face (facebook/sam3): request access there, then
`.venv/bin/hf auth login` once on the Mac. Needs transformers >= 5, torch and torchvision.

Run by hand: .venv/bin/python studio/panel_detect.py mockup.jpg --style hoodie_pullover
"""
import argparse
import gc
from pathlib import Path
import sys

import cv2
import numpy as np

import styles

MODEL = 'facebook/sam3'
WORK_EDGE = 1600   # the model sees ~1008 px; masks come back at this size
SCORE = 0.4        # instance confidence kept
GARMENT = {'sweater': 'sweater', 'hoodie_pullover': 'hoodie', 'hoodie_zip': 'hoodie', 'tshirt': 't-shirt'}
ROLE = {'Front': 'body', 'Back': 'body', 'FrontLeft': 'body', 'FrontRight': 'body',
        'Left': 'sleeve', 'Right': 'sleeve', 'Sleeve': 'sleeve', 'Hood': 'hood'}


class Unavailable(RuntimeError):
    """SAM 3 cannot run here; the message says what to do."""


def sam3_segmenter(device=None, load=None):
    """Returns segment(rgb, prompts) -> {prompt: [(mask, score)]} running SAM 3 locally.
    The model is loaded per call and freed afterwards: detection runs once per new template, and
    16 GB is shared with LaMa and the upscaler. load: () -> (processor, model), for tests."""
    try:
        import torch
        from transformers import Sam3Model, Sam3Processor
    except ImportError as exc:
        raise Unavailable('Chưa cài thư viện cho SAM 3. Trên Mac chạy: cd ~/pod-3d && '
                          'uv pip install --python .venv/bin/python -U "transformers>=5" torchvision huggingface_hub') from exc

    def segment(rgb, prompts):
        dev = device or ('mps' if torch.backends.mps.is_available() else 'cpu')
        try:
            processor, model = load() if load else (Sam3Processor.from_pretrained(MODEL), Sam3Model.from_pretrained(MODEL))
            model = model.to(dev).eval()
        except OSError as exc:
            text = str(exc).lower()
            if 'gated' in text or '401' in text or '403' in text or 'token' in text or 'access' in text:
                raise Unavailable('Chưa tải được SAM 3: tài khoản Hugging Face chưa được duyệt quyền '
                                  'facebook/sam3 hoặc Mac chưa đăng nhập (.venv/bin/hf auth login).') from exc
            raise Unavailable(f'Chưa tải được SAM 3: {exc}'[:300]) from exc
        from PIL import Image
        image = Image.fromarray(rgb)
        out = {}
        try:
            with torch.inference_mode():
                pixels = processor(images=image, return_tensors='pt').to(dev)
                vision = model.get_vision_features(pixel_values=pixels.pixel_values)
                for prompt in prompts:
                    text = processor(text=prompt, return_tensors='pt').to(dev)
                    result = model(vision_embeds=vision, input_ids=text.input_ids, attention_mask=text.attention_mask)
                    found = processor.post_process_instance_segmentation(
                        result, threshold=SCORE, mask_threshold=0.5,
                        target_sizes=[rgb.shape[:2]])[0]
                    out[prompt] = [(m.cpu().numpy().astype(bool), float(s))
                                   for m, s in zip(found['masks'], found['scores'])]
        finally:
            del model
            gc.collect()
            if dev == 'mps':
                torch.mps.empty_cache()
        return out

    return segment


def _clean(mask, min_share=0.002):
    """Largest part of a mask, holes closed; None when too small to be a panel."""
    m = mask.astype(np.uint8)
    k = max(3, round(min(m.shape) / 200)) | 1
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n < 2:
        return None
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[big, cv2.CC_STAT_AREA] < min_share * m.size:
        return None
    return labels == big


def _order(pts):
    """4 points -> top-left, top-right, bottom-right, bottom-left."""
    pts = sorted(map(tuple, pts), key=lambda p: p[1])
    top = sorted(pts[:2], key=lambda p: p[0])
    bottom = sorted(pts[2:], key=lambda p: -p[0])
    return [list(p) for p in top + bottom]


def quad(mask, slanted=False):
    """Corners of a panel mask in pixels. Upright panels (body, hood) take the outline's extreme
    points along the four diagonals, which keeps a trapezoid; slanted ones (sleeves) take the
    smallest rotated rectangle around the outline."""
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], 1).astype(np.float32)
    hull = cv2.convexHull(pts).reshape(-1, 2)
    if slanted:
        return _order(cv2.boxPoints(cv2.minAreaRect(hull)))
    x, y = hull[:, 0], hull[:, 1]
    return _order([hull[np.argmin(x + y)], hull[np.argmax(x - y)], hull[np.argmax(x + y)], hull[np.argmin(x - y)]])


def _cx(mask):
    return float(np.nonzero(mask.any(0))[0].mean())


def _thick(mask):
    """The mask without thin parts (drawstrings hanging from the hood), else the mask itself."""
    k = max(5, round(min(mask.shape) * 0.04)) | 1
    opened = _clean(cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0)
    return opened if opened is not None else mask


def _balance(body, pair, min_iou=0.85):
    """SAM often gives the strip of sleeve next to the shoulder to the body on one side only. A
    front or back view is close to mirror-symmetric, so each sleeve also takes the mirror image
    of the other (inside the garment outline), and the body gives those pixels up. In place;
    skipped when the outline is not symmetric enough to trust the mirror."""
    garment = body | pair[0] | pair[1]
    xs = np.nonzero(garment.any(0))[0]
    centre = (xs.min() + xs.max()) / 2
    flip = np.float32([[-1, 0, 2 * centre], [0, 1, 0]])
    h, w = garment.shape

    def mirror(m):
        return cv2.warpAffine(m.astype(np.uint8), flip, (w, h), flags=cv2.INTER_NEAREST) > 0

    g2 = mirror(garment)
    if (garment & g2).sum() < min_iou * (garment | g2).sum():
        return
    grown = [pair[0] | (mirror(pair[1]) & garment), pair[1] | (mirror(pair[0]) & garment)]
    for s, g in zip(pair, grown):
        s |= g
    body &= ~(pair[0] | pair[1])


def assign(bodies, sleeves, hoods, zip_front=False):
    """{panel: (mask, role)}. Views are read left to right: the first (largest two) body is the
    front, the next the back, and a photo with one view is a front. A sleeve belongs to the body
    whose middle is closest. On the front view the sleeve on the image's left is the wearer's
    Right; on the back view it is the wearer's Left. The hood goes with the back view when there
    is one (that is where its print shows), else with the front."""
    out = {}
    views = sorted(sorted(bodies, key=lambda b: -int(b.sum()))[:2], key=_cx)
    front = views[0] if views else None
    back = views[1] if len(views) > 1 else None
    by_view = {0: [], 1: []}
    for s in sleeves:
        if views:
            i = min(range(len(views)), key=lambda v: abs(_cx(s) - _cx(views[v])))
            by_view[i].append(s)
    for i, view in enumerate(views):
        by_view[i] = sorted(by_view[i], key=lambda s: -int(s.sum()))[:2]
        if len(by_view[i]) == 2:
            _balance(view, by_view[i])
    if zip_front and front is not None:
        mid = int(round(_cx(front)))
        left, right = front.copy(), front.copy()
        left[:, mid:] = False
        right[:, :mid] = False
        for name, half in (('FrontLeft', _clean(left)), ('FrontRight', _clean(right))):
            if half is not None:
                out[name] = (half, 'body')
    elif front is not None:
        out['Front'] = (front, 'body')
    if back is not None:
        out['Back'] = (back, 'body')
    for i, mirrored in ((0, False), (1, True)):
        own = by_view[i]
        if not own or (i == 1 and ('Left' in out or 'Right' in out or 'Sleeve' in out)):
            continue
        out.setdefault('Sleeve', (own[0], 'sleeve'))
        centre = _cx(views[i])
        for s in own:
            image_left = _cx(s) < centre
            name = 'Left' if image_left == mirrored else 'Right'
            out.setdefault(name, (s, 'sleeve'))
    if hoods:
        anchor = back if back is not None else front
        pick = max(hoods, key=lambda m: int(m.sum()))
        if anchor is not None:
            xs = np.nonzero(anchor.any(0))[0]
            over = [m for m in hoods if xs.min() <= _cx(m) <= xs.max()]
            pick = max(over, key=lambda m: int(m.sum())) if over else min(hoods, key=lambda m: abs(_cx(m) - _cx(anchor)))
        out['Hood'] = (pick, 'hood')
    return out


def propose(bgr, style_id, segment, debug=None):
    """{'quads': {panel: [[x, y] x4] normalized}, 'found': [...], 'missing': [...]} for the main
    panels of a style. segment: see sam3_segmenter (tests pass a fake)."""
    style = styles.BY_ID.get(style_id)
    if not style or style_id not in GARMENT:
        raise ValueError('Hãy chọn kiểu dáng.')
    h, w = bgr.shape[:2]
    scale = min(1.0, WORK_EDGE / max(h, w))
    small = cv2.resize(bgr, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    sh, sw = small.shape[:2]
    garment = GARMENT[style_id]
    prompts = [garment, 'sleeve'] + (['hood'] if style_id.startswith('hoodie') else [])
    found = segment(np.ascontiguousarray(small[..., ::-1]), prompts)

    if debug is not None:
        debug.update(found)

    def parts(prompt):
        return [m for m in (_clean(mask) for mask, score in found.get(prompt, []) if score >= SCORE) if m is not None]

    sleeves, hoods = parts('sleeve'), parts('hood')
    cut = np.zeros((sh, sw), bool)
    for m in sleeves + hoods:
        cut |= m
    cut = cv2.dilate(cut.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    bodies = [b for b in (_clean(m & ~cut) for m in parts(garment)) if b is not None]

    names = [n for n, _, kind in style['panels'] if kind == 'main' and n in ROLE]
    chosen = assign(bodies, sleeves, hoods, zip_front='FrontLeft' in names)
    quads = {}
    for name, (mask, role) in chosen.items():
        if name in names:
            corners = quad(_thick(mask) if role == 'hood' else mask, slanted=role == 'sleeve')
            quads[name] = [[round(min(1, max(0, x / sw)), 4), round(min(1, max(0, y / sh)), 4)] for x, y in corners]
    return {'quads': quads, 'found': [n for n in names if n in quads],
            'missing': [n for n in names if n not in quads],
            'counts': {'body': len(bodies), 'sleeve': len(sleeves), 'hood': len(hoods)}}


def overlay(bgr, quads):
    """The mockup with the proposed quads drawn on, to check by eye."""
    out = bgr.copy()
    h, w = out.shape[:2]
    for name, q in quads.items():
        pts = np.int32([[x * w, y * h] for x, y in q])
        cv2.polylines(out, [pts], True, (0, 90, 255), max(2, w // 500))
        cx, cy = pts.mean(0).astype(int)
        cv2.putText(out, name, (cx - 30, cy), cv2.FONT_HERSHEY_SIMPLEX, w / 1500, (0, 90, 255), max(2, w // 700))
    return out


def raw_size(raw, bgr):
    for regions in raw.values():
        if regions:
            return regions[0][0].shape[::-1]
    return bgr.shape[1::-1]


def main(argv=None):
    ap = argparse.ArgumentParser(description='Thử SAM 3 tìm các mảnh áo trên một ảnh mockup.')
    ap.add_argument('image')
    ap.add_argument('--style', default='hoodie_pullover', choices=sorted(GARMENT))
    a = ap.parse_args(argv)
    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        sys.exit('Không đọc được ảnh.')
    raw = {}
    try:
        result = propose(bgr, a.style, sam3_segmenter(), debug=raw)
    except Unavailable as exc:
        sys.exit(str(exc))
    out = Path(a.image).with_name(Path(a.image).stem + '.sam3.jpg')
    cv2.imwrite(str(out), overlay(bgr, result['quads']))
    print('Tìm thấy:', ', '.join(result['found']) or 'không mảnh nào')
    if result['missing']:
        print('Chưa thấy:', ', '.join(result['missing']))
    print('Số vùng SAM 3 trả về:', result['counts'])
    print('Ảnh kiểm tra:', out)
    # Every raw SAM 3 region in its own colour (garment blue, sleeve green, hood red), to tune by.
    small = cv2.resize(bgr, raw_size(raw, bgr), interpolation=cv2.INTER_AREA)
    tint = small.astype(np.float32)
    for prompt, colour in zip(raw, ((255, 120, 0), (0, 200, 0), (0, 0, 255))):
        for mask, score in raw[prompt]:
            tint[mask] = tint[mask] * 0.6 + np.float32(colour) * 0.4
    masks = Path(a.image).with_name(Path(a.image).stem + '.sam3-vung.jpg')
    cv2.imwrite(str(masks), tint.astype(np.uint8))
    print('Ảnh các vùng SAM 3:', masks)


if __name__ == '__main__':
    main()
