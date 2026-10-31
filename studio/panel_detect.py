"""Find the panels of a new mockup shot automatically with SAM 3, as starting quads for marking.

SAM 3 (Meta, SAM License: commercial use allowed) segments every instance of a short text
prompt. We ask for the whole garment, its sleeves and its hood, cut sleeves and hood out of the
garment to get the body, and give each style panel the segment nearest to where that panel
usually sits (styles.DEFAULT_QUADS: front on the left, back on the right). Each segment becomes
4 corners. The user still checks and drags the corners before saving; nothing here is final.

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
MAX_DIST = 0.35    # a segment farther than this (image widths) from a panel's usual place is not it
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


def _centre(points):
    return np.asarray(points, np.float64).mean(0)


def propose(bgr, style_id, segment):
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

    def parts(prompt):
        return [m for m in (_clean(mask) for mask, score in found.get(prompt, []) if score >= SCORE) if m is not None]

    sleeves, hoods = parts('sleeve'), parts('hood')
    cut = np.zeros((sh, sw), bool)
    for m in sleeves + hoods:
        cut |= m
    cut = cv2.dilate(cut.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    bodies = [b for b in (_clean(m & ~cut) for m in parts(garment)) if b is not None]

    names = [n for n, _, kind in style['panels'] if kind == 'main' and n in ROLE]
    if 'FrontLeft' in names:
        # Full zip: the front body splits at its middle; FrontLeft is the half on the image's left.
        defaults = styles.DEFAULT_QUADS
        front = min(bodies, key=lambda b: np.hypot(*(_centre(np.argwhere(b)[:, ::-1]) / [sw, sh] - _centre(defaults['Front']))),
                    default=None)
        if front is not None:
            mid = int(round(np.nonzero(front.any(0))[0].mean()))
            left, right = front.copy(), front.copy()
            left[:, mid:] = False
            right[:, :mid] = False
            bodies = [b for b in bodies if b is not front] + [b for b in (_clean(left), _clean(right)) if b is not None]
    pools = {'body': bodies, 'sleeve': sleeves, 'hood': hoods}

    # Greedy: the closest (segment, panel) pair first, each segment used once.
    pairs = []
    for name in names:
        want = _centre(styles.DEFAULT_QUADS[name])
        for i, m in enumerate(pools[ROLE[name]]):
            ys, xs = np.nonzero(m)
            d = float(np.hypot(xs.mean() / sw - want[0], ys.mean() / sh - want[1]))
            if d <= MAX_DIST:
                pairs.append((d, name, ROLE[name], i))
    quads, used = {}, set()
    for d, name, role, i in sorted(pairs):
        if name in quads or (role, i) in used:
            continue
        used.add((role, i))
        corners = quad(pools[role][i], slanted=role == 'sleeve')
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


def main(argv=None):
    ap = argparse.ArgumentParser(description='Thử SAM 3 tìm các mảnh áo trên một ảnh mockup.')
    ap.add_argument('image')
    ap.add_argument('--style', default='hoodie_pullover', choices=sorted(GARMENT))
    a = ap.parse_args(argv)
    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        sys.exit('Không đọc được ảnh.')
    try:
        result = propose(bgr, a.style, sam3_segmenter())
    except Unavailable as exc:
        sys.exit(str(exc))
    out = Path(a.image).with_name(Path(a.image).stem + '.sam3.jpg')
    cv2.imwrite(str(out), overlay(bgr, result['quads']))
    print('Tìm thấy:', ', '.join(result['found']) or 'không mảnh nào')
    if result['missing']:
        print('Chưa thấy:', ', '.join(result['missing']))
    print('Số vùng SAM 3 trả về:', result['counts'])
    print('Ảnh kiểm tra:', out)


if __name__ == '__main__':
    main()
