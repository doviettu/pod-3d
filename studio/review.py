"""Local, non-destructive artwork review and layered PSD export."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / '.vendor'))
ASSET_ID = re.compile(r'[a-f0-9]{64}')
HEX_COLOR = re.compile(r'#[0-9a-fA-F]{6}')


def node_binary():
    return shutil.which('node') or ('/opt/homebrew/bin/node' if Path('/opt/homebrew/bin/node').is_file() else None)


def decode(value, limit):
    if not isinstance(value, str) or len(value) > (limit * 4 // 3 + 4):
        raise ValueError('Tệp tải lên quá lớn hoặc không hợp lệ.')
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError('Dữ liệu tệp không hợp lệ.') from None
    if not raw or len(raw) > limit:
        raise ValueError('Tệp tải lên quá lớn hoặc rỗng.')
    return raw


def number(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Tọa độ hoặc kích thước nằm ngoài panel.')
    return value


def color(value):
    if not isinstance(value, str) or not HEX_COLOR.fullmatch(value):
        raise ValueError('Màu phải có dạng #RRGGBB.')
    return value


def ps_name(raw):
    """Read only the SFNT name table; the font itself is validated by FreeType."""
    tables = struct.unpack_from('>H', raw, 4)[0]
    if tables > 256:
        raise ValueError('Font có bảng dữ liệu không hợp lệ.')
    for index in range(tables):
        tag, _, offset, length = struct.unpack_from('>4sIII', raw, 12 + 16 * index)
        if tag != b'name':
            continue
        if offset + length > len(raw):
            raise ValueError('Bảng tên font bị thiếu dữ liệu.')
        _, count, strings = struct.unpack_from('>HHH', raw, offset)
        for i in range(min(count, 1024)):
            platform, encoding, language, name, size, start = struct.unpack_from('>6H', raw, offset + 6 + 12 * i)
            if name == 6 and size <= 512 and strings + start + size <= length:
                value = raw[offset + strings + start:offset + strings + start + size]
                text = value.decode('utf-16-be' if platform in (0, 3) else 'mac_roman')
                if text and all(ord(c) >= 32 for c in text):
                    return text[:120]
    raise ValueError('Font thiếu tên PostScript; hãy dùng tệp TTF/OTF gốc.')


class FontLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self):
        fonts = []
        for id_, filename in (('system-arial', 'Arial.ttf'), ('system-times', 'Times New Roman.ttf')):
            path = Path('/System/Library/Fonts/Supplemental') / filename
            if path.is_file():
                font = ImageFont.truetype(str(path), 32)
                fonts.append({'id': id_, 'name': ' '.join(font.getname()),
                              'postscript_name': ps_name(path.read_bytes()), 'source_url': '',
                              'license_confirmed': True})
        for path in sorted(self.root.glob('*.json')):
            if ASSET_ID.fullmatch(path.stem) and (self.root / (path.stem + '.font')).is_file():
                fonts.append(json.loads(path.read_text()))
        return fonts

    def path(self, font_id):
        known = {f['id'] for f in self.list()}
        if font_id not in known:
            raise ValueError('Hãy chọn font trong thư viện đã nạp.')
        if font_id == 'system-arial':
            return Path('/System/Library/Fonts/Supplemental/Arial.ttf')
        if font_id == 'system-times':
            return Path('/System/Library/Fonts/Supplemental/Times New Roman.ttf')
        return self.root / (font_id + '.font')

    def add(self, data):
        raw = decode(data.get('data_base64'), 5 * 1024 * 1024)
        if raw[:4] not in (b'\x00\x01\x00\x00', b'OTTO'):
            raise ValueError('Chỉ nhận tệp font TTF hoặc OTF đã giải nén.')
        source = data.get('source_url', '')
        if not isinstance(source, str):
            raise ValueError('Nguồn font phải là liên kết DaFont.')
        parsed = urlsplit(source)
        if source and (parsed.scheme != 'https' or parsed.hostname not in ('dafont.com', 'www.dafont.com')
                       or parsed.username or parsed.password or len(source) > 500):
            raise ValueError('Nguồn font phải là liên kết HTTPS của DaFont.')
        try:
            font = ImageFont.truetype(io.BytesIO(raw), 32)
            name = ps_name(raw)
        except (OSError, ValueError, struct.error, UnicodeError):
            raise ValueError('Không đọc được font TTF/OTF này.') from None
        id_ = hashlib.sha256(raw).hexdigest()
        result = {'id': id_, 'name': ' '.join(font.getname())[:160], 'postscript_name': name,
                  'source_url': source, 'license_confirmed': data.get('license_confirmed') is True}
        (self.root / (id_ + '.font')).write_bytes(raw)
        (self.root / (id_ + '.json')).write_text(json.dumps(result, ensure_ascii=False))
        return result

    def preview(self, font_id, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 120 or '\n' in text:
            raise ValueError('Mẫu font cần từ 1 đến 120 ký tự trên một dòng.')
        font = ImageFont.truetype(str(self.path(font_id)), 42)
        image = Image.new('RGB', (1000, 100), 'white')
        ImageDraw.Draw(image).text((12, 20), text, font=font, fill='black')
        stream = io.BytesIO()
        image.save(stream, 'PNG')
        return stream.getvalue()


class Review:
    def __init__(self, engine, fonts):
        self.engine, self.fonts = engine, fonts

    def folder(self, job_id):
        self.engine.get_job(job_id)
        return self.engine.root / job_id

    def source(self, job_id, panel, source='local'):
        job = self.engine.get_job(job_id)
        tpl = self.engine.templates.load(job['template'])
        if panel not in {p.name for p in tpl.panels} or source not in ('local', 'openai'):
            raise ValueError('Panel hoặc nguồn artwork không hợp lệ.')
        return self.engine.file_path(job_id, f'{source}/{panel}.png'), tpl.panel(panel)

    def asset(self, job_id, asset_id):
        if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
            raise ValueError('Mã ảnh không hợp lệ.')
        path = self.folder(job_id) / 'review-assets' / (asset_id + '.png')
        if not path.is_file():
            raise ValueError('Không tìm thấy ảnh đã nạp.')
        return path

    def store_image(self, job_id, raw):
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG' or image.width * image.height > 50_000_000 or getattr(image, 'n_frames', 1) != 1:
                    raise ValueError('Ảnh phải là PNG tĩnh, tối đa 50 triệu pixel.')
                image.verify()
        except (OSError, Image.DecompressionBombError):
            raise ValueError('Không đọc được ảnh PNG.') from None
        id_ = hashlib.sha256(raw).hexdigest()
        folder = self.folder(job_id) / 'review-assets'
        folder.mkdir(exist_ok=True)
        path = folder / (id_ + '.png')
        if not path.exists():
            path.write_bytes(raw)
            self.engine.add_artifact(job_id, path, 'image', 'Nguồn logo hoặc artwork.png')
        return id_

    def view(self, job_id, panel):
        source, info = self.source(job_id, panel)
        job = self.engine.get_job(job_id)
        saved = job.get('reviews', {}).get(panel)
        result = {'panel': panel, 'width': info.size[0], 'height': info.size[1],
                  'source_url': self.engine.url(job_id, source), 'revision': None, 'spec': None,
                  'logo_assets': []}
        if saved:
            folder = self.folder(job_id) / 'reviews' / saved['revision']
            spec = json.loads((folder / 'spec.json').read_text())
            chosen, _ = self.source(job_id, panel, spec['source'])
            result.update(revision=saved['revision'], spec=spec,
                          source_url=self.engine.url(job_id, chosen),
                          preview_url=self.engine.url(job_id, folder / 'preview.jpg'),
                          png_url=self.engine.url(job_id, folder / (panel + ('-approved.png' if spec['approved'] else '-draft.png'))))
            if (folder / (panel + '.psd')).is_file():
                result['psd_url'] = self.engine.url(job_id, folder / (panel + '.psd'))
            result['logo_assets'] = [{'asset_id': p['asset_id'], 'url': self.engine.url(job_id, self.asset(job_id, p['asset_id']))}
                                     for p in spec['logo_layers']]
        return result

    def save(self, job_id, data):
        panel, source_name = data.get('panel'), data.get('source', 'local')
        source, info = self.source(job_id, panel, source_name)
        w, h = info.size
        approved = data.get('approved', False)
        if type(approved) is not bool:
            raise ValueError('Trạng thái duyệt phải là bật hoặc tắt.')
        spec = {'source': source_name, 'approved': approved, 'text_layers': [], 'logo_layers': [], 'erasures': []}
        counts = {'text_layers': 4, 'logo_layers': 4, 'erasures': 4}
        for key, limit in counts.items():
            if not isinstance(data.get(key, []), list) or len(data.get(key, [])) > limit:
                raise ValueError('Mỗi loại có tối đa bốn lớp hoặc vùng sửa.')
            if any(not isinstance(entry, dict) for entry in data.get(key, [])):
                raise ValueError('Thông tin lớp chữ/logo không hợp lệ.')
        if data.get('artwork_base64'):
            spec['artwork_asset_id'] = self.store_image(job_id, decode(data['artwork_base64'], 32 * 1024 * 1024))
        elif data.get('artwork_asset_id'):
            spec['artwork_asset_id'] = data['artwork_asset_id']
        if spec.get('artwork_asset_id'):
            with Image.open(self.asset(job_id, spec['artwork_asset_id'])) as image:
                if image.size != (w, h):
                    raise ValueError('Artwork thay thế phải đúng kích thước panel.')
        for entry in data.get('text_layers', []):
            text = entry.get('text')
            if not isinstance(text, str) or not 1 <= len(text) <= 120 or any(ord(c) < 32 for c in text):
                raise ValueError('Mỗi lớp chữ cần từ 1 đến 120 ký tự, một dòng.')
            font_id = entry.get('font_id')
            self.fonts.path(font_id)
            spec['text_layers'].append({'text': text, 'font_id': font_id,
                'font_size': number(entry.get('font_size'), 1, min(1200, h)),
                'x': number(entry.get('x'), 0, w - 1), 'y': number(entry.get('y'), 0, h - 1),
                'color': color(entry.get('color'))})
        for entry in data.get('logo_layers', []):
            asset_id = self.store_image(job_id, decode(entry['image_base64'], 16 * 1024 * 1024)) if entry.get('image_base64') else entry.get('asset_id')
            self.asset(job_id, asset_id)
            spec['logo_layers'].append({'asset_id': asset_id, 'x': number(entry.get('x'), 0, w - 1),
                'y': number(entry.get('y'), 0, h - 1), 'width': number(entry.get('width'), 1, w)})
        for entry in data.get('erasures', []):
            x, y = number(entry.get('x'), 0, w - 1), number(entry.get('y'), 0, h - 1)
            spec['erasures'].append({'x': x, 'y': y, 'width': number(entry.get('width'), 1, w - x),
                'height': number(entry.get('height'), 1, h - y), 'color': color(entry.get('color'))})
        # Render/validate everything before publishing a revision or changing approval.
        composite, layers = self.render(job_id, panel, spec)
        revision = uuid.uuid4().hex
        folder = self.folder(job_id) / 'reviews' / revision
        folder.mkdir(parents=True)
        (folder / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2))
        dpi = self.engine.templates.load(self.engine.get_job(job_id)['template']).dpi
        png = folder / (panel + ('-approved.png' if approved else '-draft.png'))
        composite.convert('RGB').save(png, dpi=(dpi, dpi))
        composite.thumbnail((900, 900))
        composite.convert('RGB').save(folder / 'preview.jpg', quality=90)
        self.engine.add_artifact(job_id, folder / 'preview.jpg', 'image', panel + ' — xem trước hậu kỳ.jpg')
        self.engine.add_artifact(job_id, png, 'image', panel + (' — đã duyệt.png' if approved else ' — bản nháp.png'))
        self.engine.add_artifact(job_id, folder / 'spec.json', 'json', panel + ' — lớp và font.json')
        reviews = self.engine.get_job(job_id).get('reviews', {})
        reviews[panel] = {'revision': revision, 'approved': approved}
        self.engine.update(job_id, reviews=reviews)
        return self.view(job_id, panel)

    def render(self, job_id, panel, spec):
        source, info = self.source(job_id, panel, spec['source'])
        original = Image.open(source).convert('RGBA')
        background = Image.open(self.asset(job_id, spec['artwork_asset_id'])).convert('RGBA') if spec.get('artwork_asset_id') else original.copy()
        draw = ImageDraw.Draw(background)
        for erasure in spec['erasures']:
            x, y = erasure['x'], erasure['y']
            draw.rectangle((x, y, x + erasure['width'] - 1, y + erasure['height'] - 1), fill=erasure['color'])
        layers = [{'name': 'Reference — original', 'image': original, 'left': 0, 'top': 0, 'hidden': True},
                  {'name': 'Artwork', 'image': background, 'left': 0, 'top': 0}]
        composite = background.copy()
        for index, entry in enumerate(spec['logo_layers']):
            logo = Image.open(self.asset(job_id, entry['asset_id'])).convert('RGBA')
            height = max(1, round(logo.height * entry['width'] / logo.width))
            if entry['x'] + entry['width'] > composite.width or entry['y'] + height > composite.height:
                raise ValueError('Logo vượt ra ngoài canvas panel.')
            logo = logo.resize((entry['width'], height), Image.Resampling.LANCZOS)
            composite.alpha_composite(logo, (entry['x'], entry['y']))
            layers.append({'name': f'Logo {index + 1}', 'image': logo, 'left': entry['x'], 'top': entry['y']})
        fonts = {f['id']: f for f in self.fonts.list()}
        for index, entry in enumerate(spec['text_layers']):
            font = ImageFont.truetype(str(self.fonts.path(entry['font_id'])), entry['font_size'])
            bounds = font.getbbox(entry['text'], anchor='ls')
            width, height = max(1, bounds[2] - bounds[0]), max(1, bounds[3] - bounds[1])
            if entry['x'] + width > composite.width or entry['y'] + height > composite.height:
                raise ValueError('Chữ vượt ra ngoài canvas panel; giảm cỡ chữ hoặc đổi vị trí.')
            image = Image.new('RGBA', (width, height))
            ImageDraw.Draw(image).text((-bounds[0], -bounds[1]), entry['text'], anchor='ls', font=font, fill=entry['color'])
            composite.alpha_composite(image, (entry['x'], entry['y']))
            layers.append({'name': f'Text {index + 1} — {entry["text"][:40]}', 'image': image,
                'left': entry['x'], 'top': entry['y'], 'text': {'value': entry['text'],
                'font_name': fonts[entry['font_id']]['postscript_name'], 'font_size': entry['font_size'],
                'color': entry['color'], 'baseline_x': entry['x'] - bounds[0], 'baseline_y': entry['y'] - bounds[1]}})
        return composite, list(reversed(layers))

    def export_psd(self, job_id, panel):
        view = self.view(job_id, panel)
        if not view['revision']:
            raise ValueError('Hãy lưu bản xem trước chữ/logo trước khi xuất PSD.')
        folder = self.folder(job_id) / 'reviews' / view['revision']
        target = folder / (panel + '.psd')
        if not target.is_file():
            node = node_binary()
            if not node:
                raise ValueError('Chưa tìm thấy Node.js để xuất PSD.')
            image, layers = self.render(job_id, panel, view['spec'])
            dpi = self.engine.templates.load(self.engine.get_job(job_id)['template']).dpi
            with tempfile.TemporaryDirectory(dir=folder) as temp:
                temp = Path(temp)
                manifest = {'width': image.width, 'height': image.height, 'dpi': dpi, 'layers': []}
                composite_path = temp / 'composite.rgba'
                composite_path.write_bytes(image.tobytes())
                manifest['composite'] = {'width': image.width, 'height': image.height, 'rgba_file': composite_path.name}
                for index, layer in enumerate(layers):
                    pixels = layer.pop('image')
                    path = temp / f'{index}.rgba'
                    path.write_bytes(pixels.tobytes())
                    manifest['layers'].append({**layer, 'width': pixels.width, 'height': pixels.height, 'rgba_file': path.name})
                spec_path = temp / 'manifest.json'
                spec_path.write_text(json.dumps(manifest))
                result = subprocess.run([node, '--max-old-space-size=2048', str(HERE / 'psd_export.cjs'), str(spec_path), str(target)],
                                        capture_output=True, timeout=180, text=True)
                if result.returncode:
                    raise ValueError('Xuất PSD chưa hoàn tất; bản PNG và các lớp đã lưu vẫn còn nguyên.')
            self.engine.add_artifact(job_id, target, 'psd', panel + ' — PSD có lớp.psd')
        return self.view(job_id, panel)

    def tools(self):
        return {'vtracer': (HERE / '.vendor/vtracer').is_dir(),
                'sam2': (HERE / 'repos/sam2/sam2').is_dir() and (HERE / 'runtime/models/sam2.1_hiera_tiny.pt').is_file(),
                'psd': (HERE / 'node_modules/ag-psd').is_dir() and node_binary() is not None}

    def crop(self, job_id, data):
        source, info = self.source(job_id, data.get('panel'), data.get('source', 'local'))
        bbox = data.get('bbox')
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError('Chọn vùng x, y, rộng và cao trên panel.')
        x, y = number(bbox[0], 0, info.size[0] - 1), number(bbox[1], 0, info.size[1] - 1)
        w, h = number(bbox[2], 1, info.size[0] - x), number(bbox[3], 1, info.size[1] - y)
        return source, (x, y, w, h)

    def segment(self, job_id, data):
        if not self.tools()['sam2']:
            raise ValueError('SAM 2 tiny chưa sẵn sàng; cần repo và model đã cài.')
        source, (x, y, w, h) = self.crop(job_id, data)
        with tempfile.TemporaryDirectory(dir=self.folder(job_id)) as temp:
            output = Path(temp) / 'logo.png'
            result = subprocess.run([sys.executable, str(HERE / 'segment_asset.py'), str(source), str(output), str(x), str(y), str(w), str(h)],
                                    capture_output=True, timeout=180, text=True)
            if result.returncode or not output.is_file():
                raise ValueError('SAM 2 chưa tách được vùng này; chọn vùng nhỏ/rõ hơn rồi thử lại.')
            asset_id = self.store_image(job_id, output.read_bytes())
        return {'asset_id': asset_id, 'url': self.engine.url(job_id, self.asset(job_id, asset_id)),
                'width': w, 'height': h, 'x': x, 'y': y}

    def vectorize(self, job_id, data):
        if not self.tools()['vtracer']:
            raise ValueError('VTracer chưa được cài.')
        import vtracer
        folder = self.folder(job_id) / 'vectors'
        folder.mkdir(exist_ok=True)
        if data.get('asset_id'):
            source = self.asset(job_id, data['asset_id'])
        else:
            path, (x, y, w, h) = self.crop(job_id, data)
            if w * h > 4_000_000:
                raise ValueError('Chỉ vector hóa vùng tối đa 4 triệu pixel để giữ nhẹ trên Mac.')
            source = folder / (uuid.uuid4().hex + '.png')
            with Image.open(path) as image:
                image.crop((x, y, x + w, y + h)).save(source)
        with Image.open(source) as image:
            if image.width * image.height > 4_000_000:
                raise ValueError('Chỉ vector hóa vùng tối đa 4 triệu pixel để giữ nhẹ trên Mac.')
        target = folder / (uuid.uuid4().hex + '.svg')
        vtracer.convert_image_to_svg_py(str(source), str(target))
        self.engine.add_artifact(job_id, target, 'svg', 'Logo hoặc họa tiết — VTracer.svg')
        return {'name': 'Logo hoặc họa tiết — VTracer.svg', 'url': self.engine.url(job_id, target)}
