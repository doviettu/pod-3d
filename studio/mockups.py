"""Library of blank product photos (presets) for sales mockups, grouped, stored under runtime.

runtime/mockups/<id>/ holds blank.jpg, maps.npz (baked once), preset.json (group, name, zone,
options). Photos are the seller's own or bought with a commercial licence; they never go to git.
"""
from datetime import datetime
import io
import json
from pathlib import Path
import re
import shutil
import threading
import unicodedata

import cv2
import numpy as np
from PIL import Image, ImageOps

import mockup_render as render

ID = re.compile(r'[a-z0-9-]{3,80}')
MAX_EDGE = 3000
KINDS = ('tee', 'mug')


def slug(text):
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')[:40] or 'mockup'


def decode_rgba(raw):
    """Design bytes -> RGBA uint8 array (RGB order)."""
    with Image.open(io.BytesIO(raw)) as image:
        return np.asarray(ImageOps.exif_transpose(image).convert('RGBA'))


class MockupLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._maps = {}

    def _folder(self, preset_id):
        if not ID.fullmatch(preset_id or ''):
            raise KeyError('mockup')
        folder = self.root / preset_id
        if not (folder / 'preset.json').is_file():
            raise KeyError('mockup')
        return folder

    def get(self, preset_id):
        preset = json.loads((self._folder(preset_id) / 'preset.json').read_text())
        return {**preset, 'blank_url': f'/api/v2/mockups/{preset_id}/blank.jpg'}

    def list(self):
        out = []
        for path in sorted(self.root.glob('*/preset.json')):
            try:
                out.append(self.get(path.parent.name))
            except (KeyError, OSError, ValueError):
                continue
        return sorted(out, key=lambda p: (p['group'], p['name']))

    def groups(self):
        return sorted({p['group'] for p in self.list()})

    def create(self, raw, group, name, kind='tee'):
        if kind not in KINDS:
            raise ValueError('Loại mockup phải là áo (tee) hoặc cốc (mug).')
        group = (group or '').strip()[:60] or 'Chung'
        name = (name or '').strip()[:80] or 'Mockup'
        with Image.open(io.BytesIO(raw)) as image:
            photo = ImageOps.exif_transpose(image).convert('RGB')
        photo.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
        bgr = np.asarray(photo)[..., ::-1].copy()
        maps = render.bake(bgr)
        with self.lock:
            stem = f'{slug(group)}-{slug(name)}-{datetime.now():%y%m%d}'
            n = 1
            while (self.root / f'{stem}-{n}').exists():
                n += 1
            preset_id = f'{stem}-{n}'
            folder = self.root / preset_id
            folder.mkdir()
        try:
            cv2.imwrite(str(folder / 'blank.jpg'), bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            np.savez_compressed(folder / 'maps.npz', **maps)
            preset = {'id': preset_id, 'group': group, 'name': name, 'kind': kind, 'size': [bgr.shape[1], bgr.shape[0]],
                      'zone': render.default_zone(maps['mask'], 'cylinder' if kind == 'mug' else 'rect'),
                      'options': dict(render.DEFAULTS), 'created': datetime.now().isoformat(timespec='seconds')}
            (folder / 'preset.json').write_text(json.dumps(preset, ensure_ascii=False, indent=2))
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return self.get(preset_id)

    def update(self, preset_id, data):
        folder = self._folder(preset_id)
        with self.lock:
            preset = json.loads((folder / 'preset.json').read_text())
            if 'zone' in data:
                preset['zone'] = render.check_zone(data['zone'])
            if 'options' in data:
                preset['options'] = self.check_options(data['options'])
            for key, limit in (('group', 60), ('name', 80)):
                if isinstance(data.get(key), str) and data[key].strip():
                    preset[key] = data[key].strip()[:limit]
            (folder / 'preset.json').write_text(json.dumps(preset, ensure_ascii=False, indent=2))
        return self.get(preset_id)

    @staticmethod
    def check_options(options):
        if not isinstance(options, dict):
            raise ValueError('Tuỳ chọn render không hợp lệ.')
        out = dict(render.DEFAULTS)
        for key, (lo, hi) in {'fold': (0, 3), 'light': (0, 1.5), 'texture': (0, 2)}.items():
            if key in options:
                value = float(options[key])
                if not lo <= value <= hi:
                    raise ValueError(f'{key} phải từ {lo} đến {hi}.')
                out[key] = value
        if options.get('fit', 'contain') not in ('contain', 'cover', 'stretch'):
            raise ValueError('Cách đặt art không hợp lệ.')
        out['fit'] = options.get('fit', 'contain')
        return out

    def delete(self, preset_id):
        folder = self._folder(preset_id)
        with self.lock:
            shutil.rmtree(folder)
            self._maps.pop(preset_id, None)

    def _load(self, preset_id):
        folder = self._folder(preset_id)
        if preset_id not in self._maps:
            with np.load(folder / 'maps.npz') as data:
                maps = {k: data[k] for k in data.files}
            self._maps = {preset_id: (cv2.imread(str(folder / 'blank.jpg')), maps)}   # keep one in memory
        return self._maps[preset_id]

    def render(self, preset_id, art_rgba, zone=None, options=None, flat=False):
        preset = self.get(preset_id)
        blank, maps = self._load(preset_id)
        zone = render.check_zone(zone or preset['zone'])
        options = self.check_options(options) if options is not None else preset['options']
        if flat:
            return render.flat(blank, art_rgba, zone, options.get('fit', 'contain'))
        return render.render(blank, maps, art_rgba, zone, options)

    def diagnostic(self, preset_id, name):
        if name not in ('mask', 'light', 'disp', 'texture'):
            raise KeyError('mockup')
        _, maps = self._load(preset_id)
        return render.diagnostics(maps)[name]

    def file(self, preset_id, name):
        if name not in ('blank.jpg', 'preview.jpg'):
            raise FileNotFoundError()
        path = self._folder(preset_id) / name
        if not path.is_file():
            raise FileNotFoundError()
        return path
