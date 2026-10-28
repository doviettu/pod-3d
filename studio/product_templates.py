"""App-owned product layouts layered over the original engine templates."""
import json
import math
from pathlib import Path
import re

NAME = re.compile(r'[a-z][a-z0-9_-]{0,63}')


class ProductTemplate:
    def __init__(self, data, directory, panel_type):
        self.name = data['name']
        self.dpi = data['dpi']
        self.dir = directory
        self.panels = [panel_type(**{**p, 'size': tuple(p['size'])}) for p in data['panels']]

    def panel(self, name):
        return next(p for p in self.panels if p.name == name)

    def map_path(self, name):
        if name not in {p.name for p in self.panels}:
            raise ValueError('Mảnh không có trong mẫu.')
        return self.dir / ('map_' + name + '.npz')

    @property
    def color_path(self):
        return self.dir / 'color.npz'

    @property
    def silhouette_path(self):
        return self.dir / 'silhouette.png'


class ProductTemplates:
    def __init__(self, base, root):
        self.base, self.root = base, Path(root).resolve()

    def _data(self, name):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('Tên mẫu không hợp lệ.')
        directory = self.root / name
        if directory.is_symlink():
            raise ValueError('Thư mục mẫu không được là liên kết.')
        path = directory / 'template.json'
        if not path.is_file():
            return None
        if name in self.base.available():
            raise ValueError('Mẫu mới không được ghi đè mẫu gốc đã dùng trong lịch sử.')
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError('Đường dẫn mẫu không hợp lệ.')
        raw = path.read_bytes()
        if len(raw) > 65536:
            raise ValueError('Cấu hình mẫu quá lớn.')
        data = json.loads(raw)
        if data.get('name') != name or type(data.get('dpi')) is not int or not 1 <= data['dpi'] <= 1200:
            raise ValueError('Cấu hình mẫu không hợp lệ.')
        if type(data.get('panels')) is not list or not 1 <= len(data['panels']) <= 9:
            raise ValueError('Danh sách mảnh không hợp lệ.')
        names = set()
        for p in data['panels']:
            if (not isinstance(p, dict) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,49}', str(p.get('name','')))
                    or p['name'] in names or p.get('source') not in ('solid','unwrap','copy')):
                raise ValueError('Mảnh mẫu không hợp lệ.')
            names.add(p['name'])
            size = p.get('size')
            if (type(size) is not list or len(size) != 2 or any(type(v) is not int or v < 1 for v in size)
                    or size[0] * size[1] > 50_000_000):
                raise ValueError('Kích thước mảnh không hợp lệ.')
            for key in ('quad','curve','hide','sample','region'):
                if key in p:
                    def check(value):
                        if isinstance(value, list):
                            if not value or len(value) > 32:
                                raise ValueError('Vùng hình học không hợp lệ.')
                            for item in value:
                                check(item)
                        elif type(value) not in (int,float) or not math.isfinite(value) or not 0 <= value <= 1:
                            raise ValueError('Tọa độ phải nằm trong ảnh tham chiếu.')
                    check(p[key])
        metadata = data.get('metadata', {})
        if type(metadata) is not dict or type(metadata.get('production_ready',False)) is not bool:
            raise ValueError('Thông tin mẫu không hợp lệ.')
        return data

    def available(self):
        local = [p.name for p in self.root.iterdir() if p.is_dir() and not p.is_symlink()
                 and NAME.fullmatch(p.name) and (p / 'template.json').is_file()] if self.root.is_dir() else []
        return sorted(set(self.base.available()) | set(local))

    def load(self, name):
        data = self._data(name)
        return ProductTemplate(data, self.root / name, self.base.Panel) if data else self.base.load(name)

    def metadata(self, name):
        data = self._data(name)
        if data:
            return {'label': data.get('label', name), **data.get('metadata', {})}
        labels = {'sweater':'Sweater · 6 mảnh chuẩn', 'hoodie':'Hoodie · Mẫu gốc',
                  'hoodie_back_front_34':'Hoodie · Sau trái / trước phải · Góc 3/4',
                  'hoodie_front_back':'Hoodie · Trước trái / sau phải',
                  'hoodie_model_front_back':'Hoodie người mặc · Trước / sau'}
        return {'label': labels.get(name,name), 'production_ready': True}

    def reference_path(self, name):
        data = self._data(name)
        path = self.root / name / 'reference.jpg' if data else self.root / 'reference-assets' / (name + '.jpg')
        if path.is_symlink() or not path.resolve().is_relative_to(self.root) or not path.is_file():
            return None
        return path

    def thumbnail_path(self, name):
        reference = self.reference_path(name)
        if reference is None:
            return None
        thumbnail = reference.with_name(reference.stem + '-thumb.jpg')
        return thumbnail if thumbnail.is_file() and not thumbnail.is_symlink() else reference

    def detection_references(self):
        references = []
        for name in self.available():
            path = self.reference_path(name)
            if path:
                meta = self.metadata(name)
                tpl = self.load(name)
                ready = tpl.silhouette_path.is_file() and all(
                    p.source != 'unwrap' or tpl.map_path(p.name).is_file() for p in tpl.panels)
                references.append({'id':name, 'label':meta['label'], 'path':path,
                    'background':meta.get('background','white' if name == 'sweater' else 'complex'), 'ready':ready,
                    'allow_geometry':meta.get('allow_geometry',name == 'sweater')})
        return references
