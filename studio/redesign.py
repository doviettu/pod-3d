"""Redesign, step 1: take a flat design apart into layers and write the brief for changing it.

The design is split without AI: text lines from Tesseract (when installed), graphic parts from
the ink mask (alpha, or distance from the background colour on an opaque image) split into
connected pieces, and optionally named objects from SAM 3. Every layer is saved as a cut-out
PNG. The user marks each layer keep / change / remove, and build_prompt writes one structured
brief ("change only these, keep everything else") plus the chosen Skill files, to paste into
an image tool or the Hỏi Claude tab. Nothing here calls a paid API.

runtime/redesign/<id>/ holds source.png, scene.json and layer-<n>.png.
"""
from datetime import datetime
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
SKILLS = HERE / 'skills'
ID = re.compile(r'rd-\d{6}-\d{6}-\d{1,3}')
MAX_EDGE = 6000
WORK_EDGE = 1200     # masks and components are found at this size, then scaled back
MIN_PART = 0.003     # graphic pieces smaller than this share of the canvas go to "chi tiết nhỏ"
MAX_PARTS = 12
OCR_CONF = 60
SAM_PROMPTS = ('character', 'animal', 'person', 'logo', 'flower', 'skull', 'star', 'heart')
SAM_SCORE = 0.5
ACTIONS = ('keep', 'change', 'remove')


def ink_mask(rgba):
    """Pixels that are part of the design: alpha on a transparent PNG, else everything that
    differs from the border colour. Returns (mask, background RGB or None)."""
    alpha = rgba[..., 3]
    if (alpha < 250).mean() > 0.01:
        return alpha >= 128, None
    rgb = rgba[..., :3].astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, 0)
    far = np.abs(rgb - bg).max(2) > 28
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    far = cv2.morphologyEx(far.astype(np.uint8), cv2.MORPH_OPEN, k) > 0
    return far, [int(v) for v in bg]


def tesseract_lines(rgb, exe=None, timeout=60):
    """Text lines [{text, bbox: [x, y, w, h], conf}] via Tesseract TSV, or None if not installed."""
    exe = exe or shutil.which('tesseract')
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'art.png'
        Image.fromarray(rgb).save(path)
        try:
            out = subprocess.run([exe, str(path), 'stdout', '--psm', '11', 'tsv'], capture_output=True,
                                 timeout=timeout, check=False).stdout.decode('utf-8', 'ignore')
        except (OSError, subprocess.SubprocessError):
            return None
    return parse_tsv(out)


def parse_tsv(tsv):
    lines = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split('\t')
        if len(cols) < 12 or cols[0] != '5':
            continue
        try:
            conf = float(cols[10])
            x, y, w, h = (int(v) for v in cols[6:10])
        except ValueError:
            continue
        word = cols[11].strip()
        if conf < OCR_CONF or not re.search(r'\w', word):
            continue
        key = tuple(cols[2:5])   # block, paragraph, line
        lines.setdefault(key, []).append((word, x, y, w, h, conf))
    out = []
    for words in lines.values():
        x0 = min(w[1] for w in words)
        y0 = min(w[2] for w in words)
        x1 = max(w[1] + w[3] for w in words)
        y1 = max(w[2] + w[4] for w in words)
        out.append({'text': ' '.join(w[0] for w in sorted(words, key=lambda w: w[1])),
                    'bbox': [x0, y0, x1 - x0, y1 - y0], 'conf': round(sum(w[5] for w in words) / len(words))})
    return sorted(out, key=lambda t: (t['bbox'][1], t['bbox'][0]))


def palette(rgb, mask, k=6):
    """Main ink colours [{hex, pct}] by k-means on a sample of the design's pixels."""
    pixels = rgb[mask]
    if len(pixels) < k:
        return []
    rng = np.random.default_rng(0)
    sample = pixels[rng.choice(len(pixels), min(len(pixels), 20000), replace=False)].astype(np.float32)
    k = min(k, len(np.unique(sample, axis=0)))
    _, labels, centres = cv2.kmeans(sample, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
                                    3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    order = np.argsort(counts)[::-1]
    return [{'hex': '#%02x%02x%02x' % tuple(int(v) for v in centres[i]), 'pct': round(counts[i] / len(sample) * 100, 1)}
            for i in order if counts[i]]


def _hex(rgb, mask):
    return '#%02x%02x%02x' % tuple(int(v) for v in np.median(rgb[mask], 0)) if mask.any() else None


def _bbox(mask):
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]


def split(rgba, text_lines=None, segment=None):
    """Layers [{type, label, bbox, mask, ...}] of a design, largest graphics first, text last.
    text_lines: from tesseract_lines (None = no OCR). segment: panel_detect-style SAM 3 call."""
    h, w = rgba.shape[:2]
    rgb = np.ascontiguousarray(rgba[..., :3])
    ink, background = ink_mask(rgba)
    layers = []
    taken = np.zeros((h, w), bool)
    for line in text_lines or []:
        x, y, bw, bh = line['bbox']
        pad = max(2, bh // 6)
        box = np.zeros((h, w), bool)
        box[max(0, y - pad):y + bh + pad, max(0, x - pad):x + bw + pad] = True
        mask = box & ink & ~taken
        if mask.sum() < 20:
            continue
        taken |= mask
        layers.append({'type': 'text', 'label': line['text'], 'text': line['text'], 'mask': mask,
                       'color': _hex(rgb, mask), 'ocr_conf': line.get('conf')})
    if segment is not None:
        scale = min(1.0, WORK_EDGE / max(h, w))
        small = cv2.resize(rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else rgb
        found = segment(np.ascontiguousarray(small), list(SAM_PROMPTS))
        for prompt in SAM_PROMPTS:
            for m, score in sorted(found.get(prompt, []), key=lambda r: -r[1]):
                if score < SAM_SCORE:
                    continue
                full = cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
                mask = full & ink & ~taken
                if mask.sum() < 0.01 * h * w:
                    continue
                taken |= mask
                layers.append({'type': 'object', 'label': prompt, 'mask': mask, 'color': _hex(rgb, mask),
                               'score': round(score, 2)})
    rest = ink & ~taken
    scale = min(1.0, WORK_EDGE / max(h, w))
    small = cv2.resize(rest.astype(np.uint8), (max(1, round(w * scale)), max(1, round(h * scale))),
                       interpolation=cv2.INTER_NEAREST)
    # Bridge the gaps inside one drawing, and wider sideways so the letters of a word or a row of
    # stars stay one layer when there is no OCR.
    k = max(3, round(max(small.shape) / 120)) | 1
    joined = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k * 3 | 1, k)))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    order = sorted(range(1, count), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    labels = cv2.resize(labels.astype(np.int32), (w, h), interpolation=cv2.INTER_NEAREST)
    small_bits = np.zeros((h, w), bool)
    n = 0
    for i in order:
        mask = (labels == i) & rest
        if not mask.any():
            continue
        if mask.sum() < MIN_PART * h * w or n >= MAX_PARTS:
            small_bits |= mask
            continue
        n += 1
        layers.append({'type': 'graphic', 'label': f'Hình {n}', 'mask': mask, 'color': _hex(rgb, mask)})
    leftover = rest & ~np.any([l['mask'] for l in layers if l['type'] == 'graphic'] or [np.zeros((h, w), bool)], 0)
    small_bits |= leftover & ~small_bits
    if small_bits.sum() >= 20:
        layers.append({'type': 'graphic', 'label': 'Chi tiết nhỏ', 'mask': small_bits, 'color': _hex(rgb, small_bits)})
    for layer in layers:
        layer['bbox'] = _bbox(layer['mask'])
        layer['area_pct'] = round(float(layer['mask'].mean() * 100), 2)
    order = {'object': 0, 'graphic': 1, 'text': 2}
    layers.sort(key=lambda l: (order[l['type']], l['label'] == 'Chi tiết nhỏ', -l['area_pct']))
    return layers, {'background': background, 'palette': palette(rgb, ink), 'ink_pct': round(float(ink.mean() * 100), 1)}


def cutout(rgba, mask, bbox):
    x, y, w, h = bbox
    out = rgba[y:y + h, x:x + w].copy()
    out[..., 3] = np.where(mask[y:y + h, x:x + w], out[..., 3], 0)
    return out


def skills(root=SKILLS):
    """Skill files: [{id, title, summary}] from studio/skills/*.md (first heading, first line)."""
    out = []
    for path in sorted(Path(root).glob('*.md')):
        lines = [l.strip() for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
        title = next((l.lstrip('# ') for l in lines if l.startswith('#')), path.stem)
        summary = next((l for l in lines if not l.startswith('#')), '')
        out.append({'id': path.stem, 'title': title, 'summary': summary[:200]})
    return out


def _box(b, size):
    """Bounding box as percentages of the canvas, which image models follow better than pixels."""
    w, h = size
    x, y, bw, bh = b
    return f'x {x / w * 100:.0f}-{(x + bw) / w * 100:.0f}%, y {y / h * 100:.0f}-{(y + bh) / h * 100:.0f}%'


def build_prompt(scene, choices, goal='', skill_ids=(), root=SKILLS):
    """choices: {layer id: {'action': keep|change|remove, 'note': str}}. Returns {prompt, skills,
    markdown}: the brief in English (image models follow it best) and the Skill files to attach."""
    size = scene['size']
    changed = []
    lines = []
    for layer in scene['layers']:
        pick = choices.get(layer['id'], {})
        action = pick.get('action', 'keep')
        if action not in ACTIONS:
            raise ValueError('Hành động cho lớp không hợp lệ.')
        note = str(pick.get('note') or '').strip()[:500]
        what = f'text "{layer["text"]}"' if layer['type'] == 'text' else f'{layer["type"]} "{layer["label"]}"'
        where = _box(layer['bbox'], size)
        colour = f', colour {layer["color"]}' if layer.get('color') else ''
        if action == 'keep':
            lines.append(f'- {layer["id"]} {what} ({where}{colour}): KEEP exactly as is.')
        elif action == 'remove':
            changed.append(layer['id'])
            lines.append(f'- {layer["id"]} {what} ({where}): REMOVE; continue the surrounding background '
                         'into the gap, add nothing new.')
        else:
            changed.append(layer['id'])
            lines.append(f'- {layer["id"]} {what} ({where}{colour}): CHANGE: {note or "redraw it in a fresh way"}. '
                         'Keep its position, size and role in the layout.')
    if not changed:
        raise ValueError('Hãy chọn ít nhất một lớp để sửa hoặc bỏ.')
    bg = scene.get('background')
    canvas = (f'opaque background {"#%02x%02x%02x" % tuple(bg)}' if bg else 'transparent background')
    palette = ', '.join(f'{p["hex"]} ({p["pct"]:.0f}%)' for p in scene.get('palette', [])[:6])
    prompt = '\n'.join([
        'Edit this print design for a garment. Change ONLY the layers marked CHANGE or REMOVE below; '
        'every other element must stay pixel-identical in position, size, colour, spelling and style.',
        f'Canvas: {size[0]} x {size[1]} px, {canvas}. Current palette: {palette}.',
        *( [f'Goal of the redesign: {goal.strip()[:600]}'] if goal.strip() else [] ),
        'Layers (positions are shares of the canvas):',
        *lines,
        'Output: one image, same canvas and aspect ratio, print-ready, no mockup, no shirt, no shadows, '
        'no watermark, no added text unless asked above. Spell all kept text exactly.',
    ])
    known = {s['id'] for s in skills(root)}
    chosen = [s for s in skill_ids if s in known]
    bodies = [(Path(root) / f'{s}.md').read_text(encoding='utf-8').strip() for s in chosen]
    markdown = '\n\n'.join(['# Brief', prompt, *(['# Skills', *bodies] if bodies else [])]) + '\n'
    return {'prompt': prompt, 'skills': chosen, 'changed': changed, 'markdown': markdown}


class RedesignStore:
    def __init__(self, root, ocr=tesseract_lines):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.ocr = ocr

    def _folder(self, rid):
        if not ID.fullmatch(rid or '') or not (self.root / rid / 'scene.json').is_file():
            raise KeyError('redesign')
        return self.root / rid

    def analyze(self, rgba, name='', segment=None):
        h, w = rgba.shape[:2]
        if max(h, w) > MAX_EDGE:
            s = MAX_EDGE / max(h, w)
            rgba = np.asarray(Image.fromarray(rgba).resize((round(w * s), round(h * s)), Image.Resampling.LANCZOS))
            h, w = rgba.shape[:2]
        text = self.ocr(np.ascontiguousarray(rgba[..., :3])) if self.ocr else None
        layers, info = split(rgba, text, segment)
        with self.lock:
            stem = f'rd-{datetime.now():%y%m%d-%H%M%S}'
            n = 1
            while (self.root / f'{stem}-{n}').exists():
                n += 1
            rid = f'{stem}-{n}'
            folder = self.root / rid
            folder.mkdir()
        Image.fromarray(rgba).save(folder / 'source.png')
        out = []
        for i, layer in enumerate(layers, 1):
            lid = f'L{i}'
            Image.fromarray(cutout(rgba, layer['mask'], layer['bbox'])).save(folder / f'layer-{i}.png')
            out.append({k: v for k, v in layer.items() if k != 'mask'} | {'id': lid, 'file': f'layer-{i}.png'})
        scene = {'id': rid, 'name': str(name)[:120], 'size': [w, h], 'created': datetime.now().isoformat(timespec='seconds'),
                 'ocr': text is not None, 'sam3': segment is not None, **info, 'layers': out}
        (folder / 'scene.json').write_text(json.dumps(scene, ensure_ascii=False, indent=1))
        return self.get(rid)

    def get(self, rid):
        scene = json.loads((self._folder(rid) / 'scene.json').read_text())
        scene['source_url'] = f'/api/v2/redesign/{rid}/source.png'
        for layer in scene['layers']:
            layer['url'] = f'/api/v2/redesign/{rid}/{layer["file"]}'
        return scene

    def file(self, rid, name):
        if not re.fullmatch(r'source\.png|layer-\d{1,3}\.png', name or ''):
            raise FileNotFoundError()
        path = self._folder(rid) / name
        if not path.is_file():
            raise FileNotFoundError()
        return path

    def prompt(self, rid, data):
        data = data or {}
        scene = self.get(rid)
        choices = data.get('layers') or {}
        if not isinstance(choices, dict):
            raise ValueError('Lựa chọn lớp không hợp lệ.')
        skill_ids = data.get('skills') or []
        if not isinstance(skill_ids, list):
            raise ValueError('Danh sách Skill không hợp lệ.')
        result = build_prompt(scene, choices, str(data.get('goal') or ''), skill_ids)
        (self._folder(rid) / 'brief.md').write_text(result['markdown'], encoding='utf-8')
        return result


def decode(raw):
    with Image.open(io.BytesIO(raw)) as image:
        return np.asarray(image.convert('RGBA'))
