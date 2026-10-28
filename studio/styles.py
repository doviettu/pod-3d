"""Style library for 3D: garment styles, the panel set of each, and the calibrated templates
(one per mockup shot) that belong to a style.

A *style* says what gets printed (sweater: Front, Back, Sleeve, Collar, Hem, Wrist). A *template*
is one mockup shot of that style with unwrap maps, so the engine can cut it. Detection first
looks for the exact template (same shot, compared by outline), then suggests the nearest style;
a photo nothing matches is left for the user to pick a style and mark a new template.
"""
from datetime import datetime
import json
from pathlib import Path
import re

import cv2
import numpy as np

import garment_styles

# Main panels are at least 4500 x 5400 at 300 DPI; narrow trims keep the factory ratio.
# Sizes come from factory sample files where we have them (sweater, hoodie).
STYLES = [
    {'id': 'sweater', 'label': 'Sweater (áo len)', 'sample_files': True, 'panels': [
        ('Front', (6048, 6559), 'main'), ('Back', (6048, 6559), 'main'), ('Sleeve', (4720, 5744), 'main'),
        ('Collar', (4456, 627), 'trim'), ('Hem', (10397, 1240), 'trim'), ('Wrist', (2478, 1255), 'trim')]},
    {'id': 'hoodie_pullover', 'label': 'Hoodie chui đầu', 'sample_files': True, 'panels': [
        ('Front', (5100, 6300), 'main'), ('Back', (5100, 6300), 'main'), ('Hood', (1800, 1150), 'main'),
        ('Left', (1500, 2100), 'main'), ('Right', (1500, 2100), 'main')]},
    {'id': 'hoodie_zip', 'label': 'Hoodie khoá kéo', 'sample_files': False, 'panels': [
        ('FrontLeft', (2700, 6300), 'main'), ('FrontRight', (2700, 6300), 'main'), ('Back', (5100, 6300), 'main'),
        ('Hood', (1800, 1150), 'main'), ('Left', (1500, 2100), 'main'), ('Right', (1500, 2100), 'main')]},
    {'id': 'tshirt', 'label': 'Áo thun in tràn', 'sample_files': False, 'panels': [
        ('Front', (4500, 5400), 'main'), ('Back', (4500, 5400), 'main'), ('Left', (4500, 3150), 'main'),
        ('Right', (4500, 3150), 'main'), ('Collar', (4500, 450), 'trim')]},
]
BY_ID = {s['id']: s for s in STYLES}

# Where each panel usually sits on a ghost mockup shot with the front on the left and the back
# on the right: the starting quads for marking a new template.
DEFAULT_QUADS = {
    'Front': [[0.12, 0.22], [0.40, 0.22], [0.40, 0.90], [0.12, 0.90]],
    'FrontLeft': [[0.12, 0.22], [0.26, 0.22], [0.26, 0.90], [0.12, 0.90]],
    'FrontRight': [[0.26, 0.22], [0.40, 0.22], [0.40, 0.90], [0.26, 0.90]],
    'Back': [[0.60, 0.20], [0.88, 0.20], [0.88, 0.90], [0.60, 0.90]],
    'Hood': [[0.65, 0.03], [0.85, 0.03], [0.85, 0.22], [0.65, 0.22]],
    'Left': [[0.41, 0.24], [0.47, 0.24], [0.49, 0.80], [0.43, 0.80]],
    'Right': [[0.05, 0.24], [0.11, 0.24], [0.09, 0.80], [0.03, 0.80]],
    'Sleeve': [[0.41, 0.24], [0.47, 0.24], [0.49, 0.80], [0.43, 0.80]],
}

OUTLINE = 64
SAME_SHOT = 0.9     # outline IoU of the same mockup shot: 0.99+ measured; other shots 0.59-0.71
NEAR_STYLE = 0.78  # same garment type in another shot; a tee vs a hoodie measured 0.70
NAME = re.compile(r'[a-z][a-z0-9_-]{0,63}')


def outline(bgr):
    """Garment outline on a light backdrop, the same test the engine uses (pod3d.silhouette)."""
    small = cv2.resize(bgr, (OUTLINE, OUTLINE), interpolation=cv2.INTER_AREA)
    return small.min(2) < 235


def style_of(template_name, data=None):
    """Style id of a template: its own 'style' field, else the known mapping, else by name."""
    if data and data.get('style') in BY_ID:
        return data['style']
    known = garment_styles.style_for_template(template_name)
    if known in BY_ID:
        return known
    if known and known.startswith('hoodie'):
        return 'hoodie_pullover'
    for sid in ('sweater', 'tshirt', 'hoodie_zip', 'hoodie_pullover'):
        if template_name.startswith(sid.split('_')[0]):
            return sid
    return None


class StyleLibrary:
    def __init__(self, templates, studio_root):
        """templates: the Engine's ProductTemplates (engine + studio templates)."""
        self.templates = templates
        self.root = Path(studio_root)

    def _template_data(self, name):
        tpl = self.templates.load(name)
        path = Path(tpl.dir) / 'template.json' if hasattr(tpl, 'dir') else None
        try:
            return json.loads(path.read_text(encoding='utf-8')) if path else {}
        except (OSError, ValueError):
            return {}

    def catalog(self, template_info):
        """Styles with their panels and templates. template_info: Engine.template_info."""
        groups = {s['id']: [] for s in STYLES}
        other = []
        for name in self.templates.available():
            info = template_info(name)
            sid = style_of(name, self._template_data(name))
            entry = {k: info[k] for k in ('name', 'label', 'ready', 'production_ready', 'reference_url', 'panels')}
            entry['preview_url'] = f'/api/v2/templates/{name}/preview' if self.preview_path(name) else None
            (groups[sid] if sid in groups else other).append(entry)
        styles = [{'id': s['id'], 'label': s['label'], 'sample_files': s['sample_files'],
                   'panels': [{'name': n, 'size': list(size), 'kind': kind} for n, size, kind in s['panels']],
                   'templates': groups[s['id']]} for s in STYLES]
        return {'styles': styles, 'unsorted': other, 'default_quads': DEFAULT_QUADS}

    def preview_path(self, name):
        tpl = self.templates.load(name)
        for candidate in ('layout_preview.jpg', 'reference-thumb.jpg', 'reference.jpg'):
            path = Path(tpl.dir) / candidate
            if path.is_file() and not path.is_symlink():
                return path
        reference = self.templates.reference_path(name) if hasattr(self.templates, 'reference_path') else None
        return reference

    # ---- detection ----------------------------------------------------
    def detect(self, bgr):
        """Rank templates by outline overlap with the mockup.

        Returns selected (same shot, safe to cut), suggested_style (similar garment) and the
        ranked candidates; neither when the photo is unlike every template."""
        m = outline(bgr)
        light_backdrop = float(np.concatenate([m[0], m[-1], m[:, 0], m[:, -1]]).mean()) < 0.3
        ranked = []
        for name in self.templates.available():
            tpl = self.templates.load(name)
            path = Path(tpl.silhouette_path)
            if not path.is_file():
                continue
            ref = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if ref is None:
                continue
            ref = cv2.resize(ref, (OUTLINE, OUTLINE), interpolation=cv2.INTER_NEAREST) > 127
            iou = float((ref & m).sum() / max((ref | m).sum(), 1))
            mirrored = float((ref & m[:, ::-1]).sum() / max((ref | m[:, ::-1]).sum(), 1))
            ranked.append({'template': name, 'style': style_of(name, self._template_data(name)),
                           'score': round(iou, 4), 'mirrored': mirrored > iou + 0.05})
        ranked.sort(key=lambda c: -c['score'])
        result = {'selected': None, 'suggested_style': None, 'candidates': ranked[:6],
                  'light_backdrop': light_backdrop, 'paid_calls': 0}
        if not ranked:
            result['reason'] = 'Thư viện chưa có khuôn nào có đường viền để so.'
            return result
        best = ranked[0]
        rival = next((c for c in ranked[1:] if c['style'] != best['style']), None)
        if best['score'] >= SAME_SHOT and not best['mirrored'] and (not rival or best['score'] - rival['score'] >= 0.03):
            result.update(selected=best['template'], suggested_style=best['style'],
                          reason=f'Trùng khuôn {best["template"]} (khớp đường viền {best["score"]:.0%}).')
        elif best['score'] >= NEAR_STYLE and light_backdrop:
            result.update(suggested_style=best['style'],
                          reason=f'Giống kiểu {BY_ID.get(best["style"], {}).get("label", best["style"])} '
                                 f'({best["score"]:.0%}) nhưng không phải cùng ảnh mockup: cần tạo khuôn mới cho ảnh này.')
        else:
            result['reason'] = ('Ảnh có nền phức tạp hoặc người mặc, không so được đường viền.' if not light_backdrop
                                else 'Chưa có khuôn giống ảnh này.') + ' Hãy chọn kiểu dáng và tạo khuôn mới.'
        return result

    # ---- new template -------------------------------------------------
    def create(self, source_path, data, build_maps):
        """Write a studio template from user-marked quads and build its unwrap maps.

        data: {style, label?, panels: [{name, quad: [[x, y] x4]}]}; build_maps(name, reference_path)."""
        style = BY_ID.get(data.get('style'))
        if not style:
            raise ValueError('Hãy chọn kiểu dáng.')
        marks = data.get('panels')
        if not isinstance(marks, list) or not marks:
            raise ValueError('Hãy đánh dấu ít nhất một mảnh.')
        sizes = {n: size for n, size, _ in style['panels']}
        marked = {}
        for item in marks:
            name = item.get('name') if isinstance(item, dict) else None
            quad = item.get('quad') if isinstance(item, dict) else None
            if name not in sizes or name in marked:
                raise ValueError('Mảnh không thuộc kiểu dáng đã chọn.')
            if (not isinstance(quad, list) or len(quad) != 4
                    or not all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in p)
                               for p in quad)):
                raise ValueError(f'Bốn góc của mảnh {name} không hợp lệ.')
            if cv2.contourArea(np.float32(quad)) < 0.002:
                raise ValueError(f'Mảnh {name} quá nhỏ.')
            marked[name] = [[round(float(x), 4), round(float(y), 4)] for x, y in quad]
        label = str(data.get('label') or '').strip()[:80]
        stamp = datetime.now().strftime('%y%m%d')
        base = f'{style["id"].replace("_", "-")}-{stamp}'
        n = 1
        while (self.root / f'{base}-{n}').exists() or f'{base}-{n}' in self.templates.available():
            n += 1
        name = f'{base}-{n}'
        assert NAME.fullmatch(name)
        first = next(iter(marked.values()))
        body_sample = [min(p[0] for p in first), max(p[1] for p in first) - 0.08,
                       max(p[0] for p in first), max(p[1] for p in first) - 0.03]
        body_sample = [min(1, max(0, v)) for v in body_sample]
        panels = []
        for pname, size, kind in style['panels']:
            if pname in marked:
                quad = marked[pname]
                panels.append({'name': pname, 'size': list(size), 'source': 'unwrap', 'method': 'quad',
                               'quad': quad, 'center': round(sum(p[0] for p in quad) / 4, 4)})
            else:
                # Not marked (rib trims, hidden panels): a solid colour sampled from the body hem.
                panels.append({'name': pname, 'size': list(size), 'source': 'solid', 'sample': body_sample})
        folder = self.root / name
        folder.mkdir(parents=True)
        image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('Không đọc được ảnh mockup.')
        cv2.imwrite(str(folder / 'reference.jpg'), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        h, w = image.shape[:2]
        thumb = cv2.resize(image, (360, round(360 * h / w)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(folder / 'reference-thumb.jpg'), thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
        template = {
            'name': name, 'label': label or f'{style["label"]} · khuôn {stamp}-{n}', 'dpi': 300, 'style': style['id'],
            'note': 'Tạo trên trang từ 4 góc đánh dấu tay; mảnh không đánh dấu tô một màu.',
            'panels': panels,
            'metadata': {'production_ready': False, 'background': 'white', 'allow_geometry': True,
                         'size_basis': 'factory-template' if style['sample_files'] else 'standard-4500x5400',
                         'reference_resolution': [w, h]},
        }
        (folder / 'template.json').write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            build_maps(name, folder / 'reference.jpg')
        except Exception:
            for f in folder.iterdir():
                f.unlink()
            folder.rmdir()
            raise
        return {'name': name, 'label': template['label'], 'style': style['id'],
                'marked': list(marked), 'solid': [p['name'] for p in panels if p['source'] == 'solid']}
