"""POD Studio v2 service: 2D and 3D jobs behind one SQLite job list.

2D jobs run art2d locally. 3D jobs are handed to the existing Engine (template
unwrap, OpenAI restore, review, PSD) and linked by legacy_id. Both share the
Engine's single worker so only one heavy job runs at a time on a 16 GB Mac.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
from urllib.parse import unquote

import numpy as np

import art2d
import gdrive
import listing
import preflight
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
    def __init__(self, engine, runtime, google_config=None, google_transport=None):
        self.engine = engine
        self.runtime = Path(runtime)
        self.jobs_dir = self.runtime / 'v2-jobs'
        self.cache = self.runtime / 'sources'
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.runtime / 'studio.db')
        self.rights = rights.RightsChecker(self.runtime)
        self.lock = threading.Lock()
        self.google = gdrive.Google(gdrive.GoogleConfig(google_config), google_transport or gdrive.urllib_transport)
        self.drive = gdrive.DriveSync(self.google, self.runtime / 'drive-queue.json', self._drive_bundle,
                                      self._drive_tabs, lambda job_id, url: self.store.update(job_id, drive_url=url),
                                      log=self.store.log)
        if self.drive.snapshot() and self.google.config.public()['connected']:
            self.drive.kick()   # finish what was queued before a restart

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
        def missing(name):
            try:
                return self.engine.template_info(name)['missing']
            except (AttributeError, KeyError, OSError, ValueError):
                return []
        return self.library.detect(bgr, missing)

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
            try:
                self.preflight(job_id)
            except Exception as exc:  # noqa: BLE001 - the check is advice, never fails the job
                store.log(job_id, f'Không kiểm tra được file in: {exc}'[:300])
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
        queue = self.drive.snapshot()
        return [dict(self._refresh(j), sync=queue.get(j['id'])) for j in self.store.list(100, kind)]

    def get(self, job_id):
        if not JOB_ID.fullmatch(job_id or ''):
            raise KeyError('job')
        return dict(self._refresh(self.store.get(job_id)), sync=self.drive.snapshot().get(job_id))

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
        job = self.store.update(job_id, status='approved')
        if self.google.config.public()['connected']:
            self.drive.enqueue([job_id])
        return self.get(job_id)

    # ---- print check, real photo, listing set ---------------------------------
    def _set_extra(self, job_id, key, value):
        with self.lock:
            extras = self.store.get(job_id)['extras']
            return self.store.update(job_id, extras={**extras, key: value})

    def _print_files(self, job):
        """[(label, path, expected size or None)] for the finished print files of a job."""
        if job['kind'] == '2d':
            folder = self.jobs_dir / job['id']
            return [(side, folder / out['file'], None) for side, out in job['outputs'].get('sides', {}).items()]
        try:
            sizes = {p['name']: p['size'] for p in self.engine.template_info(job['params'].get('template'))['panels']}
        except (AttributeError, KeyError, OSError, ValueError, TypeError):
            sizes = {}
        files = []
        for item in job['outputs'].get('artifacts', []):
            name, url = item.get('name', ''), item.get('url', '')
            if item.get('kind') == 'image' and name.endswith(' local.png') and url.startswith('/files/'):
                panel = name[:-len(' local.png')]
                files.append((panel, self.engine.file_path(job['legacy_id'], unquote(url.split('/', 3)[3])),
                              sizes.get(panel)))
        return files

    def preflight_profiles(self):
        return preflight.catalog()

    def preflight(self, job_id, data=None):
        job = self.get(job_id)
        if job['status'] not in ('review', 'approved'):
            raise ValueError('Chỉ kiểm tra được job đã xử lý xong.')
        profile = ((data or {}).get('profile') or job['extras'].get('preflight', {}).get('profile')
                   or ('dtg' if job['kind'] == '2d' else 'aop'))
        files = self._print_files(job)
        if not files:
            raise ValueError('Job chưa có file in để kiểm tra.')
        report = preflight.run(files, profile)
        self.store.log(job_id, f'Kiểm tra file in ({report["label"]}): ' +
                       {'ok': 'đạt', 'warn': 'có cảnh báo', 'error': 'có lỗi'}[report['status']])
        return self._set_extra(job_id, 'preflight', report)

    def real_photo(self, job_id, data):
        """Store the seller's own photo of the printed product (Etsy wants one in every listing)."""
        job = self.get(job_id)
        raw = sources.from_base64((data or {}).get('image_base64'))
        if len(raw) > sources.MAX_BYTES:
            raise ValueError('Ảnh vượt quá giới hạn 32 MB.')
        suffix, width, height = sources.check_image(raw)
        if min(width, height) < 1000:
            raise ValueError(f'Ảnh thật {width} × {height} px quá nhỏ; Etsy nên từ 2000 px, tối thiểu 1000 px.')
        folder = self.jobs_dir / job_id
        folder.mkdir(exist_ok=True)
        for old in folder.glob('real-photo.*'):
            old.unlink()
        (folder / ('real-photo' + suffix)).write_bytes(raw)
        info = {'file': 'real-photo' + suffix, 'width': width, 'height': height,
                'added': datetime.now().isoformat(timespec='seconds')}
        self.store.log(job_id, 'Đã thêm ảnh mẫu thật.')
        return self._set_extra(job_id, 'real_photo', info)

    def listing(self, job_id):
        job = self.get(job_id)
        if job['status'] not in ('review', 'approved'):
            raise ValueError('Chỉ tạo bộ ảnh cho job đã xử lý xong.')
        folder = self.jobs_dir / job_id
        photo = job['extras'].get('real_photo')
        real = folder / photo['file'] if photo else None
        if job['kind'] == '2d':
            mockups = sorted(folder.glob('source[12].*'))
            sides = job['outputs'].get('sides', {})
            designs = [(side, folder / out['file'], list(listing.SHIRT_COLORS) if side == 'front' else ['trang', 'den'])
                       for side, out in sides.items()]
            panels = []
        else:
            try:
                mockups = [self._cached(job.get('source_sha256'))]
            except ValueError:
                mockups = []
            designs = []
            panels = [(label, path) for label, path, _ in self._print_files(job)]
        result = listing.build(folder / 'listing', real, mockups, designs, panels)
        result['built'] = datetime.now().isoformat(timespec='seconds')
        self.store.log(job_id, f'Đã tạo bộ ảnh listing: {len(result["files"])} ảnh.')
        return self._set_extra(job_id, 'listing', result)

    def file_path(self, job_id, relative):
        if not JOB_ID.fullmatch(job_id or ''):
            raise FileNotFoundError()
        folder = (self.jobs_dir / job_id).resolve()
        target = (folder / relative).resolve()
        if folder not in target.parents or not target.is_file():
            raise FileNotFoundError()
        return target

    # ---- Google Drive + Sheet ---------------------------------------------
    def google_status(self):
        pending = [j['id'] for j in self.store.list(1000) if j['status'] == 'approved' and not j.get('drive_url')]
        return {**self.drive.status(), 'unsynced': len(pending)}

    def google_client(self, data):
        self.google.config.set_client(data.get('json'))
        return self.google_status()

    def google_connect(self, redirect_uri):
        return {'url': self.google.start(redirect_uri)}

    def google_callback(self, state, code):
        self.google.finish(state, code)
        self.drive.kick()
        return self.google_status()

    def google_disconnect(self):
        self.google.disconnect()
        return self.google_status()

    def google_sync(self, data):
        """Queue one job, or every approved job not yet on Drive plus the ones that failed."""
        if not self.google.config.public()['connected']:
            raise ValueError('Chưa kết nối Google.')
        job_id = data.get('job_id')
        if job_id:
            if self.get(job_id)['status'] != 'approved':
                raise ValueError('Chỉ đồng bộ được job đã duyệt.')
            ids = [job_id]
        else:
            failed = [j for j, e in self.drive.snapshot().items() if e['state'] == 'error']
            ids = failed + [j['id'] for j in self.store.list(1000)
                            if j['status'] == 'approved' and not j.get('drive_url') and j['id'] not in failed]
        self.drive.enqueue(ids)
        return {**self.google_status(), 'queued_now': len(ids)}

    def _drive_bundle(self, job_id):
        job = self.get(job_id)
        created = datetime.fromisoformat(job['created_at']).astimezone()
        files, notes, parts = [], [], ''
        if job['kind'] == '2d':
            folder = self.jobs_dir / job_id
            for i, src in enumerate(sorted(folder.glob('source[12].*'))):
                files.append((src, ('source' if i == 0 else 'source-2') + src.suffix.lower()))
            sides = job['outputs'].get('sides', {})
            for side, out in sides.items():
                files.append((folder / out['file'], out['file']))
                files.append((folder / out['preview'], out['preview']))
                notes += [f'{side}: {w}' for w in out.get('warnings', [])]
            files += [(p, p.name) for p in sorted(folder.glob('*.svg'))]
            parts = ' + '.join({'front': 'Trước', 'back': 'Sau'}.get(s, s) for s in sides)
            style = ''
        else:
            legacy = job.get('legacy_id')
            try:
                src = self._cached(job.get('source_sha256'))
            except ValueError:
                src = None
            if src:
                files.append((src, 'source' + src.suffix.lower()))
            used = set()
            for item in job['outputs'].get('artifacts', []):
                relative = item['url'].split('/', 3)[3] if item.get('url', '').startswith('/files/') else None
                if not relative:
                    continue
                path = self.engine.file_path(legacy, unquote(relative))
                name = path.name if path.name not in used else f'{path.parent.name}-{path.name}'
                used.add(name)
                files.append((path, name))
            previews = job['outputs'].get('previews', [])
            if previews:
                first = previews[0]
                url = first.get('openai_url') or first.get('local_url') or ''
                if url.startswith('/files/'):
                    path = self.engine.file_path(legacy, unquote(url.split('/', 3)[3]))
                    files.append((path, f'preview-{first.get("panel", "mockup")}{path.suffix.lower()}'))
            panels = job['params'].get('panels') or [p.get('panel') for p in previews]
            parts = ', '.join(p for p in panels if p)
            template = job['params'].get('template') or ''
            label = self.engine.style_label(template) if template and hasattr(self.engine, 'style_label') else ''
            style = f'{label} ({template})' if label else template
        photo = job['extras'].get('real_photo')
        if photo:
            files.append((self.jobs_dir / job_id / photo['file'], photo['file']))
        rights_label = {'unknown': 'Chưa kiểm', 'green': 'Xanh', 'yellow': 'Vàng', 'red': 'Đỏ'}[job['rights']]

        def row(urls):
            return [gdrive.cell(job['sku']), created.strftime('%Y-%m-%d %H:%M'), job['kind'].upper(), gdrive.cell(parts),
                    'AI' if job['mode'] == 'ai' else 'Local', gdrive.cell(job.get('source_url') or ''), gdrive.cell(style),
                    'Đã duyệt', gdrive.link(urls['preview'], 'Xem'), gdrive.link(urls['folder'], 'Mở thư mục'),
                    round(float(job.get('cost_usd') or 0), 4), rights_label, gdrive.cell(job.get('rights_reason') or ''),
                    gdrive.cell('; '.join(notes)[:500]), urls['synced']]
        return {'kind': job['kind'], 'month': created.strftime('%Y-%m'), 'sku': job['sku'],
                'red': job['rights'] == 'red', 'files': [(p, n) for p, n in files if Path(p).is_file()], 'row': row}

    def _drive_tabs(self):
        library = [['Kiểu dáng', 'Mảnh', 'Rộng (px)', 'Cao (px)', 'Loại', 'Khuôn đã có']]
        try:
            catalog = self.styles()['styles']
        except Exception:  # noqa: BLE001 - the tab is a convenience copy
            catalog = []
        for st in catalog:
            names = ', '.join(t['name'] for t in st['templates'])
            for i, panel in enumerate(st['panels']):
                library.append([st['label'], panel['name'], panel['size'][0], panel['size'][1], panel['kind'],
                                names if i == 0 else ''])
        config = self.rights.config()
        ip = [['Loại', 'Giá trị', 'SKU', 'Lý do', 'Ngày']]
        ip += [['Từ khoá', w, '', '', ''] for w in config['keywords']]
        ip += [['Danh sách đen (pHash)', e['hash'], e.get('sku') or '', e.get('reason') or '', e.get('added') or '']
               for e in self.rights.blacklist()]
        budget = getattr(self.engine, 'budget', None)
        groups = {}
        for entry in (budget.entries() if budget else []):
            g = groups.setdefault(entry.get('prompt_version') or 'panel (cũ)', [0, 0, 0.0])
            g[0] += 1
            g[1] += 1 if entry.get('ok', True) else 0
            g[2] += float(entry.get('usd') or 0)
        prompts = [['Phiên bản prompt', 'Số lệnh', 'Thành công', 'Tổng chi ước tính (USD)', 'TB mỗi lệnh (USD)']]
        prompts += [[v, n, ok, round(usd, 4), round(usd / n, 4) if n else 0] for v, (n, ok, usd) in sorted(groups.items())]
        status = budget.status() if budget else {}
        settings = [['Mục', 'Giá trị'],
                    ['Quy tắc SKU', '2D-YYMMDD-NNN / 3D-YYMMDD-NNN (đếm theo ngày)'],
                    ['File in 2D', f'{art2d.CANVAS[0]} × {art2d.CANVAS[1]} px, {art2d.DPI} DPI, PNG trong suốt'],
                    ['Trần AI mỗi ngày (USD)', status.get('daily_usd', '')],
                    ['Trần AI mỗi tháng (USD)', status.get('monthly_usd', '')],
                    ['Đã chi hôm nay (ước tính)', status.get('spent_today_usd', '')],
                    ['Đã chi tháng này (ước tính)', status.get('spent_month_usd', '')],
                    ['Ghi chú', 'SQLite trên máy là bản gốc; Sheet là bản sao để xem và lọc.']]
        return {'Kiểu dáng': library, 'Bản quyền': ip, 'Prompt': prompts, 'Cài đặt': settings}
