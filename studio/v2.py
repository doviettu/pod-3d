"""POD Studio v2 service: 2D and 3D jobs behind one SQLite job list.

2D jobs run art2d locally. 3D jobs are handed to the existing Engine (template
unwrap, OpenAI restore, review, PSD) and linked by legacy_id. Both share the
Engine's single worker so only one heavy job runs at a time on a 16 GB Mac.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading

import numpy as np

import art2d
import rights
import sources
import styles
from jobstore import JobStore

JOB_ID = re.compile(r'[a-f0-9]{32}')
SHA = re.compile(r'[a-f0-9]{64}')
LEGACY_STATUS = {'queued': 'queued', 'running': 'running', 'succeeded': 'review',
                 'failed': 'failed', 'interrupted': 'interrupted'}
MAX_ACTIVE = 8


class StudioV2:
    def __init__(self, engine, runtime):
        self.engine = engine
        self.runtime = Path(runtime)
        self.jobs_dir = self.runtime / 'v2-jobs'
        self.cache = self.runtime / 'sources'
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.runtime / 'studio.db')
        self.rights = rights.RightsChecker(self.runtime)
        self.lock = threading.Lock()

    # ---- style library (3D) --------------------------------------------
    @property
    def library(self):
        if not hasattr(self, '_library'):
            self._library = styles.StyleLibrary(self.engine.templates, Path(__file__).resolve().parent / 'templates')
        return self._library

    def styles(self):
        return self.library.catalog(self.engine.template_info)

    def detect_style(self, data):
        import cv2
        path = self._cached((data or {}).get('source_id'))
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError('Không đọc được ảnh mockup.')
        return self.library.detect(bgr)

    def create_template(self, data):
        path = self._cached((data or {}).get('source_id'))

        def build_maps(name, reference):
            from pod3d import geometry  # the engine is on sys.path once Engine has loaded it
            geometry.run(self.engine.templates.load(name), reference, log=lambda m: None)

        with self.lock:
            created = self.library.create(path, data, build_maps)
        return {**created, 'template': self.engine.template_info(created['name'])}

    def template_preview(self, name):
        if name not in self.engine.templates.available():
            raise FileNotFoundError()
        path = self.library.preview_path(name)
        if path is None:
            raise FileNotFoundError()
        return path

    # ---- AI cost (3D) ----------------------------------------------------
    def ai_estimate(self, data):
        data = data or {}
        name = data.get('template')
        if name not in self.engine.templates.available():
            raise ValueError('Hãy chọn khuôn có sẵn.')
        mode = data.get('ai_mode', 'sheet')
        panels = data.get('panels', [])
        panels = [] if panels == 'auto' else panels
        if mode not in ('sheet', 'panel') or not isinstance(panels, list) or not all(isinstance(p, str) for p in panels):
            raise ValueError('Tuỳ chọn AI không hợp lệ.')
        tpl_panels = {p.name for p in self.engine.templates.load(name).panels}
        if not set(panels) <= tpl_panels:
            raise ValueError('Mảnh không có trong khuôn.')
        source = self._cached(data.get('source_id'))
        brief = self.engine.design_plan({'template': name, 'design_notes': data.get('design_notes', '')})['spec']
        estimate = self.engine.ai_estimate(name, panels, mode, brief, hashlib.sha256(source.read_bytes()).hexdigest(),
                                           data.get('design_notes', ''))
        return {**estimate, 'budget': self.engine.budget.status(),
                'key_configured': bool(self.engine.settings.api_key)}

    def ai_budget(self, data=None):
        return self.engine.budget.update(data) if data else self.engine.budget.status()

    # ---- sources -------------------------------------------------------
    def _cached(self, sha):
        if not SHA.fullmatch(sha or ''):
            raise ValueError('Mã ảnh nguồn không hợp lệ.')
        for suffix in sources.FORMATS.values():
            path = self.cache / (sha + suffix)
            if path.is_file():
                return path
        raise ValueError('Ảnh nguồn đã hết hạn; hãy nạp lại ảnh.')

    def load_source(self, entry, download=None):
        """Store one upload/link in the cache; returns public info incl. source_id (sha256)."""
        if isinstance(entry, dict) and entry.get('source_id'):
            path = self._cached(entry['source_id'])
            raw = path.read_bytes()
            suffix, width, height = sources.check_image(raw)
            meta = self._meta(entry['source_id'])
            return dict(source_id=entry['source_id'], width=width, height=height, url=meta.get('url'), path=path)
        info = sources.load(entry, **({'download': download} if download else {}))
        path = self.cache / (info['sha256'] + info['suffix'])
        name = entry.get('filename') if isinstance(entry.get('filename'), str) else None
        if not path.exists():
            path.write_bytes(info['raw'])
            # File names often carry the design title; kept only for the copyright keyword check.
            (self.cache / (info['sha256'] + '.json')).write_text(json.dumps({'url': info['url'], 'filename': (name or '')[:200]}))
        return dict(source_id=info['sha256'], width=info['width'], height=info['height'], url=info['url'], path=path)

    def _meta(self, sha):
        try:
            return json.loads((self.cache / (sha + '.json')).read_text())
        except (OSError, ValueError):
            return {}

    def inspect(self, data, download=None):
        """Load sources and find garments/art boxes, without creating a job."""
        entries = data.get('sources')
        if not isinstance(entries, list) or not 1 <= len(entries) <= 2:
            raise ValueError('Hãy nạp 1 hoặc 2 ảnh.')
        out = []
        for entry in entries:
            info = self.load_source(entry, download)
            rgb = np.asarray(sources.decode_rgb(info['path'].read_bytes()))
            out.append({k: v for k, v in info.items() if k != 'path'} | {'detection': art2d.detect(rgb)})
        duplicates = [j['sku'] for s in out for j in self.store.find_by_sha(s['source_id'], data.get('kind', '2d'))]
        return {'sources': out, 'duplicates': sorted(set(duplicates))}

    def source_file(self, sha):
        return self._cached(sha)

    # ---- jobs ----------------------------------------------------------
    def _active(self):
        return sum(j['status'] in ('queued', 'running') for j in self.store.list(200))

    def submit(self, data, download=None):
        kind = data.get('kind')
        if kind not in ('2d', '3d'):
            raise ValueError('Hãy chọn 2D hoặc 3D.')
        with self.lock:
            if self._active() >= MAX_ACTIVE:
                raise ValueError(f'Hàng đợi đã đủ {MAX_ACTIVE} công việc. Hãy đợi xử lý xong.')
            return self._submit_2d(data, download) if kind == '2d' else self._submit_3d(data, download)

    def _submit_2d(self, data, download):
        entries = data.get('sources')
        if not isinstance(entries, list) or not 1 <= len(entries) <= 2:
            raise ValueError('Hãy nạp 1 hoặc 2 ảnh.')
        sides = data.get('sides', 'auto')
        if sides not in art2d.SIDES:
            raise ValueError('Mặt in phải là auto, front, back hoặc both.')
        background = data.get('background', 'auto')
        background = 'auto' if background == 'remove' else background
        if background not in art2d.BACKGROUNDS:
            raise ValueError('Tuỳ chọn nền không hợp lệ.')
        boxes = data.get('boxes') or None
        if boxes is not None:
            boxes = self._check_boxes(boxes, len(entries))
        infos = [self.load_source(e, download) for e in entries]
        params = {'sides': sides, 'background': background, 'boxes': boxes,
                  'sources': [{'source_id': i['source_id'], 'url': i['url'], 'width': i['width'],
                               'height': i['height']} for i in infos]}
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        if not data.get('force'):
            for job in self.store.find_by_sha(infos[0]['source_id'], '2d'):
                if job['params'].get('key') == key:
                    return job | {'duplicate': True}
        params['key'] = key
        job = self.store.create('2d', 'local', sides=sides, source_url=infos[0]['url'],
                                source_sha256=infos[0]['source_id'], params=params)
        folder = self.jobs_dir / job['id']
        folder.mkdir()
        for n, info in enumerate(infos):
            shutil.copyfile(info['path'], folder / f'source{n + 1}{info["path"].suffix}')
        self.engine.executor.submit(self._run_2d, job['id'])
        return job

    @staticmethod
    def _check_boxes(boxes, count):
        if not isinstance(boxes, dict) or not set(boxes) <= {'front', 'back'}:
            raise ValueError('Vùng chọn không hợp lệ.')
        clean = {}
        for side, spec in boxes.items():
            if spec is None:
                continue
            box = spec.get('box') if isinstance(spec, dict) else None
            src = spec.get('source', 0) if isinstance(spec, dict) else None
            if (not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) for v in box)
                    or not isinstance(src, int) or not 0 <= src < count):
                raise ValueError('Vùng chọn không hợp lệ.')
            x, y, w, h = box
            if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 - x + 1e-3 and 0 < h <= 1 - y + 1e-3):
                raise ValueError('Vùng chọn nằm ngoài ảnh.')
            clean[side] = {'box': [float(x), float(y), float(min(w, 1 - x)), float(min(h, 1 - y))], 'source': src}
        return clean or None

    def _run_2d(self, job_id):
        store = self.store
        try:
            store.update(job_id, status='running')
            job = store.get(job_id)
            folder = self.jobs_dir / job_id
            rgbs = [np.asarray(sources.decode_rgb(p.read_bytes())) for p in sorted(folder.glob('source[12].*'))]
            p = job['params']
            outputs, report = art2d.run(rgbs, folder, sides=p['sides'], boxes=p['boxes'],
                                        background=p['background'], log=lambda m: store.log(job_id, m))
            # Screen before showing the result, so the card never appears without its rights level.
            self._screen(job_id, [(side, folder / f'{side}.png') for side in outputs])
            store.update(job_id, status='review', outputs={'sides': outputs},
                         sides='+'.join(outputs))
        except Exception as exc:  # noqa: BLE001 - every failure must reach the UI
            store.update(job_id, status='failed', error=str(exc)[:500])

    def _screen(self, job_id, images):
        """Copyright screening; never overrides a level the user already chose."""
        job = self.store.get(job_id)
        try:
            names = ' '.join(self._meta(src.get('source_id', '')).get('filename', '')
                             for src in job['params'].get('sources', []) if isinstance(src, dict))
            names += ' ' + self._meta(job.get('source_sha256') or '').get('filename', '')
            level, reason, evidence = self.rights.check(images, source_url=job.get('source_url'), extra_text=names)
        except Exception as exc:  # noqa: BLE001 - screening must not fail the job
            self.store.log(job_id, f'Không kiểm tra được bản quyền: {exc}'[:300])
            return job
        changes = {'rights_evidence': evidence}
        if job['rights'] == 'unknown':
            changes.update(rights=level, rights_reason=reason)
        self.store.log(job_id, 'Bản quyền: ' + reason)
        return self.store.update(job_id, **changes)

    def _submit_3d(self, data, download):
        entry = (data.get('sources') or [None])[0]
        info = self.load_source(entry, download)
        import base64
        payload = {k: v for k, v in data.items() if k not in ('kind', 'sources', 'force')}
        payload['image_base64'] = base64.b64encode(info['path'].read_bytes()).decode()
        payload.setdefault('filename', 'mockup' + info['path'].suffix)
        legacy = self.engine.submit(payload)
        job = self.store.create('3d', 'ai' if legacy.get('openai') else 'local', source_url=info['url'],
                                source_sha256=info['source_id'],
                                params={'template': legacy.get('template'), 'panels': legacy.get('panels')})
        job = self.store.update(job['id'], legacy_id=legacy['id'])
        return self._screen(job['id'], [('mockup', info['path'])])

    def _refresh(self, job):
        if job['kind'] != '3d' or not job.get('legacy_id') or job['status'] in ('approved',):
            return job
        try:
            legacy = self.engine.get_job(job['legacy_id'])
        except (KeyError, ValueError, FileNotFoundError):
            return job
        status = LEGACY_STATUS.get(legacy.get('status'), job['status'])
        changes = {}
        if status != job['status']:
            changes['status'] = status
        if legacy.get('error') and legacy.get('error') != job.get('error'):
            changes['error'] = legacy['error']
        outputs = {'previews': legacy.get('previews', []), 'artifacts': legacy.get('artifacts', []),
                   'stage': legacy.get('stage')}
        if outputs != job['outputs']:
            changes['outputs'] = outputs
        return self.store.update(job['id'], **changes) if changes else job

    def list(self, kind=None):
        return [self._refresh(j) for j in self.store.list(100, kind)]

    def get(self, job_id):
        if not JOB_ID.fullmatch(job_id or ''):
            raise KeyError('job')
        return self._refresh(self.store.get(job_id))

    def set_rights(self, job_id, data):
        rights, reason = data.get('rights'), data.get('reason', '')
        if rights not in ('unknown', 'green', 'yellow', 'red'):
            raise ValueError('Mức bản quyền không hợp lệ.')
        if not isinstance(reason, str) or len(reason) > 500:
            raise ValueError('Lý do tối đa 500 ký tự.')
        if rights in ('green', 'red') and not reason.strip() and self.get(job_id)['rights'] == 'yellow':
            raise ValueError('Hãy ghi lý do khi đổi mức bản quyền từ Vàng.')
        before = self.get(job_id)
        job = self.store.update(job_id, rights=rights, rights_reason=reason.strip() or None)
        if rights == 'red':
            self.rights.remember_red(job, reason)   # the same art is caught automatically next time
        elif before['rights'] == 'red':
            self.rights.forget(job_id)
        return job

    def rights_config(self, data=None):
        if data and 'keywords' in data:
            return self.rights.set_keywords(data['keywords'])
        return self.rights.config()

    def approve(self, job_id):
        job = self.get(job_id)
        if job['status'] != 'review':
            raise ValueError('Chỉ duyệt được công việc đã xử lý xong.')
        if job['rights'] == 'yellow':
            raise ValueError('Bản quyền đang ở mức Vàng: hãy chọn Xanh hoặc Đỏ trước khi duyệt.')
        return self.store.update(job_id, status='approved')

    def file_path(self, job_id, relative):
        if not JOB_ID.fullmatch(job_id or ''):
            raise FileNotFoundError()
        folder = (self.jobs_dir / job_id).resolve()
        target = (folder / relative).resolve()
        if folder not in target.parents or not target.is_file():
            raise FileNotFoundError()
        return target
