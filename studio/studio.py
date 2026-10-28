"""Local, single-user web interface for the existing pod3d engine."""
import argparse
import base64
import binascii
import copy
import hashlib
import io
import json
import re
import sys
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, unquote, urlsplit

from PIL import Image

from openai_api import Settings, check_connection, reconstruct_artwork, place_artwork
import ai_sheet
import openai_api
from review import FontLibrary, Review
from garment_preview import create_garment_preview
from online_auth import OnlineAuth, is_online
from reference_library import ReferenceLibrary
from design_spec import build_design_spec, panel_prompt, patch_prompt, design_spec_hash, GENERATION_PANELS
from local_artwork import run as run_local
from assembly_proof import create_assembly_proof, proof_is_current
from masked_repair import repair_image
from product_templates import ProductTemplates
from template_detection import detect_template
from market_library import load_market_library
from v2 import StudioV2

HERE = Path(__file__).resolve().parent
DEFAULT_ENGINE = HERE.parent if (HERE.parent / "pod3d").is_dir() else Path.home() / "pod-3d"
MAX_UPLOAD = 32 * 1024 * 1024
MAX_BODY = 45 * 1024 * 1024
MAX_PIXELS = 50_000_000
JOB_ID = re.compile(r"[a-f0-9]{32}")
STATIC = {"/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/vendor/openseadragon.min.js": ("openseadragon.min.js", "text/javascript; charset=utf-8")}


def now():
    return datetime.now(timezone.utc).isoformat()


def load_pod(root):
    if not (root / "pod3d" / "pipeline.py").is_file():
        raise ValueError("Không tìm thấy bộ xử lý pod3d tại thư mục đã chọn.")
    sys.path.insert(0, str(root))
    from pod3d import pipeline, template
    return SimpleNamespace(run=partial(run_local, pipeline), upscale=pipeline.upscale), ProductTemplates(template, HERE / 'templates')


def runtime_info(root):
    try:
        import torch
        mps = torch.backends.mps.is_available()
    except ImportError:
        mps = False
    return {"python": sys.version.split()[0], "mps": mps, "provider": "openai",
            "engine_root": str(root)}


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class Engine:
    def __init__(self, settings, engine_root=DEFAULT_ENGINE, jobs_dir=HERE / "runtime" / "jobs"):
        self.settings = settings
        self.pipeline, self.templates = load_pod(Path(engine_root))
        self.runtime = runtime_info(engine_root)
        self.references = ReferenceLibrary(Path(engine_root) / 'samples')
        self.root = Path(jobs_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ai_cache = ai_sheet.Cache(self.root.parent / 'ai-cache')
        self.budget = ai_sheet.Budget(self.root.parent)
        self.lock = threading.RLock()
        self.postprocess_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pod")
        self.jobs = {}
        self.review = Review(self, FontLibrary(self.root.parent / 'fonts'))
        for path in sorted(self.root.glob("*/report.json")):
            if not JOB_ID.fullmatch(path.parent.name):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job["id"] != path.parent.name:
                    continue
                if job["status"] in ("queued", "running"):
                    job.update(status="interrupted", error="Ứng dụng đã khởi động lại; hãy tạo công việc mới.", updated_at=now())
                    write_json(path, job)
                self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError):
                continue

    def template_info(self, name):
        tpl = self.templates.load(name)
        names = [p.name for p in tpl.panels]
        missing = [p.name for p in tpl.panels
                   if p.source == "unwrap" and not tpl.map_path(p.name).is_file()]
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", n) for n in names) or len(set(names)) != len(names):
            missing.append("Tên mảnh không hợp lệ")
        for panel in tpl.panels:
            if panel.source not in ("solid", "unwrap", "copy"):
                missing.append(panel.name)
            if panel.source == "copy" and panel.copy_of not in names:
                missing.append(panel.name)
        if any(p.source == "unwrap" for p in tpl.panels) and not tpl.silhouette_path.is_file():
            missing.append("silhouette.png")
        metadata = self.templates.metadata(name) if hasattr(self.templates, 'metadata') else {}
        reference = self.templates.reference_path(name) if hasattr(self.templates, 'reference_path') else None
        return {'name': name, 'ready': not missing, 'missing': missing,
                'label': metadata.get('label', name), 'production_ready': metadata.get('production_ready', True),
                'note': metadata.get('note', ''), 'size_basis': metadata.get('size_basis', 'factory-template'),
                'visible_views': metadata.get('visible_views', []), 'missing_views': metadata.get('missing_views', []),
                'reference_resolution': metadata.get('reference_resolution'),
                'reference_url': '/templates/' + name + '/reference.jpg' if reference else None,
                'panels': [{'name': p.name, 'size': list(p.size)} for p in tpl.panels]}

    def list_jobs(self):
        with self.lock:
            return [self.with_quality(copy.deepcopy(job)) for job in
                    sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)[:50]]

    def with_quality(self, job):
        try:
            required = [p.name for p in self.templates.load(job['template']).panels] if job.get('template') else []
        except (OSError, ValueError, KeyError):
            required = []
        approved = []
        for panel in required:
            saved = job.get('reviews', {}).get(panel, {})
            revision = saved.get('revision', '')
            if saved.get('approved') is True and isinstance(revision, str) and JOB_ID.fullmatch(revision):
                folder = self.root / job['id'] / 'reviews' / revision
                if (folder / (panel + '-approved.png')).is_file():
                    approved.append(panel)
        job.update(required_review_panels=required,
                   review_summary={'approved_panels': approved, 'required_panels': required},
                   quality_status='approved' if required and len(approved) == len(required)
                                  else 'partially_reviewed' if approved else 'awaiting_review',
                   review_required=not required or len(approved) != len(required))
        return job

    def status(self):
        return {"app": "pod-studio", "settings": self.settings.public(), "runtime": self.runtime,
                "tools": self.review.tools(),
                "templates": [self.template_info(n) for n in self.templates.available()],
                "jobs": self.list_jobs()}

    def get_job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise FileNotFoundError("Không tìm thấy công việc.")
            return self.with_quality(copy.deepcopy(self.jobs[job_id]))

    def update(self, job_id, **changes):
        with self.lock:
            self.jobs[job_id].update(changes, updated_at=now())
            write_json(self.root / job_id / "report.json", self.jobs[job_id])

    def log(self, job_id, message):
        with self.lock:
            logs = (self.jobs[job_id]["logs"] + [str(message)[:500]])[-200:]
            self.update(job_id, logs=logs)

    def detect_product(self, data):
        encoded = data.get('image_base64')
        if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_BODY:
            raise ValueError('Ảnh nhận diện không hợp lệ hoặc quá lớn.')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Dữ liệu ảnh nhận diện không hợp lệ.') from None
        references = self.templates.detection_references() if hasattr(self.templates, 'detection_references') else []
        return {**detect_template(raw, references), 'paid_calls': 0}

    def design_plan(self, data):
        name = data.get('template')
        if name not in self.templates.available():
            raise ValueError('Hãy chọn khuôn có sẵn.')
        template = self.templates.load(name)
        expected = {p.name: list(p.size) for p in template.panels}
        if data.get('design_geometry') is not None and data['design_geometry'] != expected:
            raise ValueError('Bản mô tả không khớp kích thước mẫu xưởng đang chọn.')
        spec = build_design_spec(template, data.get('design_notes', ''), data.get('design_data'))
        return {'spec': spec, 'spec_hash': design_spec_hash(spec), 'paid_calls': 0,
                'prompts': {panel: panel_prompt(spec, panel) for panel in spec['panels']
                            if panel in GENERATION_PANELS}}

    def submit(self, data):
        name = data.get("template")
        if name not in self.templates.available():
            raise ValueError("Hãy chọn một khuôn có sẵn.")
        info = self.template_info(name)
        if not info["ready"]:
            raise ValueError("Khuôn chưa đủ dữ liệu: " + ", ".join(info["missing"]))
        if data.get("gemini"):
            raise ValueError("Ứng dụng đã chuyển sang OpenAI. Hãy tải lại giao diện và chọn OpenAI.")
        for option in ("use_model", "openai", "use_reference"):
            if not isinstance(data.get(option, False), bool):
                raise ValueError("Tuỳ chọn xử lý phải là bật hoặc tắt.")
        use_openai = data.get("openai", False)
        ai_mode = data.get("ai_mode", "panel")  # the legacy page sends none; the v2 page asks for "sheet"
        if ai_mode not in ("sheet", "panel"):
            raise ValueError("Cách gọi AI phải là gộp 1 lệnh hoặc từng mảnh.")
        panels = data.get("panels", ["Front"]) if use_openai else []
        auto_panels = use_openai and ai_mode == "sheet" and panels == "auto"
        if auto_panels:
            panels = []
        elif not isinstance(panels, list) or (use_openai and not panels):
            raise ValueError("Hãy chọn mảnh cần chỉnh bằng OpenAI.")
        if any(not isinstance(p, str) for p in panels):
            raise ValueError("Hãy chọn mảnh cần chỉnh bằng OpenAI.")
        allowed = {p["name"] for p in info["panels"]} & set(GENERATION_PANELS)
        most = ai_sheet.MAX_SHEET_PANELS if ai_mode == "sheet" else 3
        if len(panels) > most or len(set(panels)) != len(panels) or not set(panels) <= allowed:
            raise ValueError(f'Chọn tối đa {most} mảnh có trong mẫu để phục hồi bằng OpenAI.')
        notes = data.get('design_notes', '')
        if not isinstance(notes, str) or len(notes) > 2000 or any(ord(c) < 32 and c not in '\n\t' for c in notes):
            raise ValueError('Ghi chú thiết kế tối đa 2000 ký tự.')
        if use_openai and not self.settings.api_key:
            raise ValueError("Chưa có khóa OpenAI. Hãy thêm khóa tại phần Cấu hình.")
        encoded = data.get("image_base64")
        if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_BODY:
            raise ValueError("Ảnh tải lên không hợp lệ hoặc quá lớn.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Dữ liệu ảnh tải lên không hợp lệ.") from None
        if len(raw) > MAX_UPLOAD:
            raise ValueError("Ảnh vượt quá giới hạn 32 MB.")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if (image.format not in ("JPEG", "PNG", "WEBP")
                        or image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1):
                    raise ValueError("Chỉ nhận JPEG, PNG, WebP tối đa 50 triệu pixel.")
                suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image.format]
                image.verify()
        except (OSError, Image.DecompressionBombError):
            raise ValueError("Không đọc được ảnh tải lên.") from None
        brief = self.design_plan(data)['spec']
        config = SimpleNamespace(api_key=self.settings.api_key, model=self.settings.model,
                                 image_size=self.settings.image_size, quality=self.settings.quality)
        if use_openai:
            self.budget.check(self.ai_estimate(name, panels, ai_mode, brief, hashlib.sha256(raw).hexdigest(),
                                               notes, config)['usd'])
        with self.lock:
            if self.postprocess_lock.locked():
                raise ValueError('Đang hậu kỳ hoặc xuất PSD; đợi tác vụ này xong rồi tạo công việc mới.')
            if sum(j["status"] in ("queued", "running") for j in self.jobs.values()) >= 4:
                raise ValueError("Hàng đợi đã đủ 4 công việc. Hãy đợi xử lý xong.")
            job_id = uuid.uuid4().hex
            folder = self.root / job_id
            folder.mkdir()
            (folder / ("input" + suffix)).write_bytes(raw)
            filename = data.get("filename", "mockup")
            job = {"id": job_id, "status": "queued", "created_at": now(), "updated_at": now(),
                   "template": name, "filename": str(filename)[:200], "openai": use_openai,
                   "provider": "openai" if use_openai else "local",
                   "use_model": data.get("use_model", True), "panels": panels,
                   "ai_mode": ai_mode if use_openai else None, "auto_panels": auto_panels,
                   "model": config.model if use_openai else None, "image_size": config.image_size if use_openai else None,
                   "quality": config.quality if use_openai else None,
                   "workflow": 'shared-artwork-v2' if use_openai else 'offline-coverage-v1',
                   "use_reference": data.get('use_reference', True), "design_spec": brief,
                   "design_spec_hash": design_spec_hash(brief),
                   "design_notes": notes, "stage": 'queued',
                   "estimated_image_cost_usd": None if use_openai else 0,
                   "input_sha256": hashlib.sha256(raw).hexdigest(),
                   "logs": [], "artifacts": [], "previews": [], "review_required": True}
            self.jobs[job_id] = job
            self.update(job_id)
            self.executor.submit(self.process, job_id, folder / ("input" + suffix), config)
            return self.with_quality(copy.deepcopy(job))

    def ai_estimate(self, name, panels, ai_mode, brief, source_sha, notes='', config=None):
        """Calls and estimated USD for an AI job; 0 when the sheet is already cached."""
        config = config or self.settings
        price = self.budget.price(config.image_size, config.quality)
        if ai_mode == 'panel':
            return {'calls': len(panels), 'usd': round(price * len(panels), 4), 'cached': False}
        if panels:
            tpl = self.templates.load(name)
            planned = ai_sheet.plan([(n, tuple(tpl.panel(n).size)) for n in panels], brief, config, source_sha,
                                    self.style_label(name), notes)
            if self.ai_cache.get(planned['key']):
                return {'calls': 0, 'usd': 0.0, 'cached': True}
        return {'calls': 1, 'usd': round(price, 4), 'cached': False}

    def style_label(self, name):
        try:
            import styles
            folder = getattr(self.templates.load(name), 'dir', None)
            data = json.loads((Path(folder) / 'template.json').read_text()) if folder else {}
            sid = styles.style_of(name, data)
            return styles.BY_ID[sid]['label'] if sid in styles.BY_ID else ''
        except (OSError, ValueError, KeyError, TypeError):
            return ''

    def url(self, job_id, path):
        relative = Path(path).resolve().relative_to((self.root / job_id).resolve()).as_posix()
        return "/files/" + job_id + "/" + quote(relative)

    def postprocess(self, job_id, action, data):
        if not self.postprocess_lock.acquire(blocking=False):
            raise ValueError('Đang có tác vụ hậu kỳ; hãy đợi xử lý xong.')
        try:
            with self.lock:
                if any(j['status'] in ('queued', 'running') for j in self.jobs.values()):
                    raise ValueError('Hãy đợi hàng đợi xử lý ảnh xong trước khi hậu kỳ.')
            if action == 'assembly-proof':
                result = create_assembly_proof(self, job_id)
                self.update(job_id, assembly_proof=result)
                return result
            if action == 'repair-patch':
                return self.repair_patch(job_id, data)
            if action == 'psd':
                return self.review.export_psd(job_id, data.get('panel'))
            if action == 'garment-preview':
                result = create_garment_preview(self, job_id)
                job = self.get_job(job_id)
                self.update(job_id, garment_preview={**result,
                    'review_revisions': {panel: saved['revision'] for panel, saved in job.get('reviews', {}).items()}})
                return result
            if action == 'approved-export':
                return self.approved_export(job_id)
            return {'review': self.review.save, 'segment': self.review.segment,
                    'vectorize': self.review.vectorize}[action](job_id, data)
        finally:
            self.postprocess_lock.release()

    def repair_patch(self, job_id, data):
        from PIL import ImageFont
        job = self.get_job(job_id)
        if not self.settings.api_key:
            raise ValueError('Chưa cấu hình khóa OpenAI.')
        panel = data.get('panel')
        view = self.review.view(job_id, panel)
        saved = copy.deepcopy(view['spec'] or {'source': data.get('source', 'local'),
                    'text_layers': [], 'logo_layers': [], 'erasures': [], 'approved': False})
        if saved['source'] != data.get('source', 'local'):
            raise ValueError('Nguồn đã thay đổi; hãy lưu bản duyệt trước khi phục hồi vùng.')
        base, info = self.review.source(job_id, panel, saved['source'])
        if saved.get('artwork_asset_id'):
            base = self.review.asset(job_id, saved['artwork_asset_id'])
        protected = []
        for row in saved['text_layers']:
            font = ImageFont.truetype(str(self.review.fonts.path(row['font_id'])), row['font_size'])
            box = font.getbbox(row['text'], anchor='ls')
            protected.append([row['x'], row['y'], max(1, box[2] - box[0]), max(1, box[3] - box[1])])
        for row in saved['logo_layers']:
            with Image.open(self.review.asset(job_id, row['asset_id'])) as image:
                height = max(1, round(image.height * row['width'] / image.width))
            protected.append([row['x'], row['y'], row['width'], height])
        folder = self.root / job_id
        inputs = [p for p in folder.glob('input.*') if p.suffix in ('.jpg', '.png', '.webp')]
        if len(inputs) != 1:
            raise ValueError('Không tìm được mockup gốc duy nhất.')
        target_dir = folder / 'patches'
        target_dir.mkdir(exist_ok=True)
        token = uuid.uuid4().hex
        target = target_dir / (token + '.png')
        spec = job.get('design_spec') or build_design_spec(self.templates.load(job['template']), job.get('design_notes', ''))
        config = SimpleNamespace(api_key=self.settings.api_key, model=self.settings.model,
                                 image_size=self.settings.image_size, quality=self.settings.quality)
        self.update(job_id, repair_attempted=True, estimated_image_cost_usd=None)
        result = repair_image(base, target, config, panel=panel, spec=spec, editable_box=data.get('bbox'),
                              protected_boxes=protected, mockup=inputs[0])
        self.add_artifact(job_id, target, 'image', panel + ' — vùng phục hồi OpenAI, chưa duyệt.png')
        write_json(target_dir / (token + '.json'), result)
        self.add_artifact(job_id, target_dir / (token + '.json'), 'json', panel + ' — kiểm tra khóa pixel.json')
        saved['artwork_asset_id'] = self.review.store_image(job_id, target.read_bytes())
        saved.update(panel=panel, approved=False)
        self.review.save(job_id, saved)
        self.update(job_id, api_repairs=job.get('api_repairs', []) + [result])
        self.log(job_id, panel + ': đã sửa một vùng qua OpenAI, khóa nguyên vùng ngoài; cần duyệt lại.')
        return self.review.view(job_id, panel)

    def approved_export(self, job_id):
        job = self.get_job(job_id)
        if job['quality_status'] != 'approved':
            missing = set(job['required_review_panels']) - set(job['review_summary']['approved_panels'])
            raise ValueError('Chưa duyệt đủ các mảnh để xuất bộ in: ' + ', '.join(sorted(missing)))
        if not self.template_info(job['template']).get('production_ready', True):
            raise ValueError('Mẫu này đang là bản nháp hình học; cần bổ sung và xác nhận kích thước in xưởng trước khi xuất bộ sản xuất.')
        revisions = {panel: job['reviews'][panel]['revision'] for panel in job['required_review_panels']}
        if job.get('garment_preview', {}).get('review_revisions') != revisions:
            raise ValueError('Hãy tạo bản xem trên áo sau lần duyệt mới nhất trước khi xuất bộ in.')
        preview = job.get('garment_preview', {})
        prefix = '/files/' + job_id + '/'
        try:
            if preview.get('kind') != 'calibrated-2d' or not isinstance(preview.get('url'), str) or not preview['url'].startswith(prefix):
                raise ValueError
            preview_path = self.file_path(job_id, unquote(preview['url'][len(prefix):]))
            with Image.open(preview_path) as image:
                image.verify()
        except (ValueError, OSError, KeyError):
            raise ValueError('Bản xem trên áo không còn hợp lệ; hãy tạo lại trước khi xuất.') from None
        proof = job.get('assembly_proof')
        if (not proof_is_current(self, job, proof) or proof.get('missing_approvals')
                or any(proof['sources'][panel]['path'] != f'reviews/{revision}/{panel}-approved.png'
                       for panel, revision in revisions.items())):
            raise ValueError('Hãy tạo lại bảng đối chiếu đủ các mảnh theo phiên bản đã duyệt mới nhất.')
        folder = self.root / job_id
        token = uuid.uuid4().hex
        target = folder / ('approved-' + token + '.zip')
        temp = target.with_suffix('.zip.tmp')
        manifest = {'job_id': job_id, 'created_at': now(), 'approval': 'manual', 'reviews': {},
                    'assembly_manifest': proof['manifest'], 'design_spec_hash': job.get('design_spec_hash'),
                    'source_mode': job.get('workflow'), 'physical_seam_verified': False}
        try:
            with zipfile.ZipFile(temp, 'w', zipfile.ZIP_STORED) as archive:
                for panel in job['required_review_panels']:
                    revision = job['reviews'][panel]['revision']
                    relative = f'reviews/{revision}/{panel}-approved.png'
                    source = self.file_path(job_id, relative)
                    with Image.open(source) as image:
                        if image.size != tuple(self.templates.load(job['template']).panel(panel).size):
                            raise ValueError('Mảnh đã duyệt không còn đúng kích thước khuôn.')
                    archive.write(source, panel + '.png')
                    manifest['reviews'][panel] = revision
                archive.writestr('approval.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        name = 'Bộ đã duyệt — phiên bản ' + token[:8] + '.zip'
        self.add_artifact(job_id, target, 'approved_zip', name)
        return {'url': self.url(job_id, target), 'name': name, 'revisions': manifest['reviews']}

    def add_artifact(self, job_id, path, kind, name=None):
        job = self.get_job(job_id)
        entry = {"name": name or path.name, "url": self.url(job_id, path), "kind": kind}
        self.update(job_id, artifacts=job["artifacts"] + [entry])

    def preview(self, source, target):
        with Image.open(source) as image:
            image.thumbnail((700, 700))
            image.convert("RGB").save(target, quality=85)

    def validate_outputs(self, tpl, folder):
        expected = {p.name + ".png" for p in tpl.panels}
        # macOS stores AppleDouble sidecars alongside images on this external drive.
        actual = {p.name for p in folder.glob("*.png") if not p.name.startswith("._")}
        if actual != expected:
            raise ValueError("Đầu ra chưa đủ các mảnh bắt buộc; chưa được coi là hoàn tất.")
        for panel in tpl.panels:
            with Image.open(folder / (panel.name + ".png")) as image:
                if image.size != tuple(panel.size):
                    raise ValueError(f"Mảnh {panel.name} không đúng kích thước khuôn.")

    def make_archive(self, tpl, local, target, edited=None, selected=()):
        temp = target.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_STORED) as archive:
                for panel in tpl.panels:
                    name = panel.name + ".png"
                    source = edited / name if panel.name in selected else local / name
                    archive.write(source, name)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

    def process(self, job_id, source, config):
        try:
            job = self.get_job(job_id)
            self.update(job_id, status="running", stage='local_reference')
            tpl = self.templates.load(job["template"])
            if not self.template_info(job["template"])["ready"]:
                raise ValueError("Dữ liệu khuôn đã thay đổi. Hãy kiểm tra lại khuôn.")
            folder = self.root / job_id
            local = folder / "local"
            thumbs = folder / "previews"
            thumbs.mkdir()
            source_thumb = thumbs / 'mockup-original.jpg'
            self.preview(source, source_thumb)
            self.add_artifact(job_id, source_thumb, 'image', 'Mockup gốc — để đối chiếu.jpg')
            self.update(job_id, source_preview_url=self.url(job_id, source_thumb))
            self.log(job_id, "Đang xử lý trên Mac; mỗi lần chỉ chạy một công việc.")
            plan = {'spec': job['design_spec'], 'spec_hash': job['design_spec_hash'],
                    'prompts': {p.name: panel_prompt(job['design_spec'], p.name) for p in tpl.panels
                                if p.name in GENERATION_PANELS}}
            write_json(folder / 'design-plan.json', plan)
            self.add_artifact(job_id, folder / 'design-plan.json', 'json', 'Thiết kế chung và yêu cầu OpenAI.json')
            match = self.references.find(source, tpl) if job.get('use_reference', True) else None
            if match:
                provenance = self.references.restore(match, local, tpl)
                write_json(folder / 'source-original.json', provenance)
                self.add_artifact(job_id, folder / 'source-original.json', 'json', 'Nguồn bộ artwork gốc.json')
                self.update(job_id, workflow='source-original', provider='reference', openai=False,
                            panels=[], estimated_image_cost_usd=0, source_original=provenance)
                self.log(job_id, 'Khớp chính xác mockup trong thư viện: dùng đủ file gốc, không gọi API hoặc suy ngược.')
                job = self.get_job(job_id)
                local_zip = folder / 'local.zip'
            else:
                if job['openai']:
                    self.log(job_id, 'Bản local chỉ làm đối chiếu; bỏ upscale local để giữ chi tiết nguồn.')
                local_zip = Path(self.pipeline.run(tpl, source, local, log=lambda s: self.log(job_id, s),
                                                  use_model=job['use_model'] and not job['openai'], fmt='png', force=False,
                                                  design_spec=job['design_spec']))
                quality_path = folder / 'local-quality.json'
                if quality_path.is_file():
                    self.update(job_id, local_quality=json.loads(quality_path.read_text()))
                    self.add_artifact(job_id, quality_path, 'json', 'Vùng nhìn thấy và vùng suy luận.json')
            self.validate_outputs(tpl, local)
            self.make_archive(tpl, local, local_zip)
            with zipfile.ZipFile(local_zip) as archive:
                if set(archive.namelist()) != {p.name + ".png" for p in tpl.panels}:
                    raise ValueError("ZIP local không đúng danh sách mảnh.")
            self.add_artifact(job_id, local_zip, "zip", "Bộ artwork gốc — chờ duyệt.zip" if job.get("workflow") == "source-original" else "Bộ in local.zip")
            previews = []
            for panel in tpl.panels:
                image = local / (panel.name + ".png")
                thumb = thumbs / (panel.name + "-local.jpg")
                self.preview(image, thumb)
                previews.append({"panel": panel.name, "local_url": self.url(job_id, thumb)})
                self.add_artifact(job_id, image, "image", panel.name + " local.png")
            self.update(job_id, previews=previews)
            if job["openai"]:
                edited = folder / "openai"
                edited.mkdir()
                artwork = folder / 'artwork'
                artwork.mkdir()
                results = []
                sheet_mode = job.get('ai_mode') == 'sheet'
                if sheet_mode:
                    names = job['panels'] or ai_sheet.pick_weak_panels(
                        self.get_job(job_id).get('local_quality'),
                        [p.name for p in tpl.panels if p.name in GENERATION_PANELS and p.source == 'unwrap'])
                    if not names:
                        self.log(job_id, 'Local đã thấy đủ các mảnh: không cần gửi AI, không tốn phí.')
                    else:
                        self.update(job_id, stage='restore_artwork', panels=names)
                        made, meta = ai_sheet.run_sheet(
                            source, artwork, config, openai_api._request,
                            panel_sizes=[(n, tuple(tpl.panel(n).size)) for n in names], spec=job['design_spec'],
                            source_sha=job['input_sha256'], cache=self.ai_cache, budget=self.budget, job_id=job_id,
                            style_label=self.style_label(job['template']), notes=job['design_notes'],
                            log=lambda m: self.log(job_id, m))
                        self.update(job_id, ai_sheet=meta, estimated_image_cost_usd=0 if meta['cache_hit'] else meta['estimated_usd'])
                    job = self.get_job(job_id)
                for name in job["panels"]:
                    self.update(job_id, stage='restore_artwork')
                    if not sheet_mode:
                        self.log(job_id, f"OpenAI phục hồi artwork {name} từ mockup gốc; một yêu cầu có phí, không tự thử lại.")
                    target = edited / (name + ".png")
                    master = artwork / (name + '.png')
                    if sheet_mode:
                        result = {'panel': name, 'workflow': 'sheet', 'cache_hit': job['ai_sheet']['cache_hit']}
                    else:
                        usd = self.budget.price(config.image_size, config.quality)
                        self.budget.check(usd)
                        result = reconstruct_artwork(source, master, config, panel=name,
                                                     canvas_size=tuple(tpl.panel(name).size), notes=job['design_notes'],
                                                     design_spec=job['design_spec'])
                        self.budget.record({'job': job_id, 'usd': usd, 'ok': True, 'panels': [name],
                                            'prompt_version': 'panel-v2', 'usage': result.get('usage', {})})
                    self.add_artifact(job_id, master, 'image', name + ' — artwork 2D gốc, chưa duyệt.png')
                    upscale = None
                    if job['use_model']:
                        self.update(job_id, stage='upscale_artwork')
                        self.log(job_id, f'Nâng nét artwork {name} đã phục hồi trên Mac; không nâng bản trải lỗi.')
                        upscale = lambda image, factor: self.pipeline.upscale.upscale(
                            image, factor, log=lambda message: self.log(job_id, message))
                    self.update(job_id, stage='place_on_template')
                    place_artwork(master, target, tpl.panel(name).size, tpl.dpi, upscale=upscale)
                    with Image.open(target) as image:
                        if image.size != tuple(tpl.panel(name).size):
                            raise ValueError(f"OpenAI trả mảnh {name} sai kích thước.")
                    results.append({"panel": name, **result})
                    thumb = thumbs / (name + "-openai.jpg")
                    self.preview(target, thumb)
                    next(p for p in previews if p["panel"] == name)["openai_url"] = self.url(job_id, thumb)
                    self.update(job_id, previews=previews, api_results=results)
                    self.add_artifact(job_id, target, "image", name + " OpenAI — nền chờ chữ/logo.png")
                if job["panels"]:
                    ai_zip = folder / "openai.zip"
                    self.make_archive(tpl, local, ai_zip, edited, job["panels"])
                    self.add_artifact(job_id, ai_zip, "zip", "Bộ OpenAI — cần duyệt.zip")
                self.log(job_id, "Đã ghép artwork lên canvas khuôn cố định. Thêm chữ/logo riêng, duyệt từng mảnh và xem lại trên áo trước khi xuất bộ đã duyệt.")
            self.add_artifact(job_id, folder / "report.json", "json", "Báo cáo.json")
            self.log(job_id, 'Xử lý xong — chưa xác nhận chất lượng in. Bản local và OpenAI đều cần duyệt thủ công.')
            self.update(job_id, status="succeeded", stage='awaiting_review')
        except Exception as exc:
            message = str(exc)
            if config.api_key:
                message = message.replace(config.api_key, "[khóa đã ẩn]")
            message = re.sub(r"(?:AIza|sk-)[0-9A-Za-z_-]{20,}", "[khóa đã ẩn]", message)
            self.update(job_id, status="failed", error=message[:500])

    def file_path(self, job_id, relative):
        job = self.get_job(job_id)
        folder = (self.root / job_id).resolve()
        target = (folder / relative).resolve()
        url = self.url(job_id, self.root / job_id / relative)
        allowed = {a["url"] for a in job["artifacts"]}
        for p in job["previews"]:
            allowed.add(p["local_url"])
            if "gemini_url" in p:
                allowed.add(p["gemini_url"])
            if "openai_url" in p:
                allowed.add(p["openai_url"])
        if not target.is_relative_to(folder) or url not in allowed or not target.is_file():
            raise FileNotFoundError("Không tìm thấy tệp kết quả.")
        return target


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def headers_ok(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in hosts and (not origin or origin in {"http://" + h for h in hosts})

    def send_bytes(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, data):
        self.send_bytes(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if not self.headers_ok():
            self.send_json(403, {"error": "Chỉ nhận yêu cầu từ giao diện local."})
            return
        if not getattr(self.server, 'online_auth', OnlineAuth(HERE / 'runtime/online/access.json')).handle(self):
            return
        path = urlsplit(self.path).path
        engine = self.server.engine
        try:
            if path in ("/", "/index.html"):
                self.send_bytes(200, (HERE / "app.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/legacy":
                self.send_bytes(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path in STATIC:
                self.send_bytes(200, (HERE / "static" / STATIC[path][0]).read_bytes(), STATIC[path][1])
            elif path.startswith("/api/v2/") or path.startswith("/v2files/"):
                self.v2_get(path)
            elif re.fullmatch(r'/templates/[a-z][a-z0-9_-]{0,63}/reference[.]jpg', path):
                name = path.split('/')[2]
                target = engine.templates.thumbnail_path(name) if hasattr(engine.templates, 'thumbnail_path') else engine.templates.reference_path(name)
                if target is None:
                    raise FileNotFoundError()
                self.send_bytes(200, target.read_bytes(), 'image/jpeg')
            elif path == '/review.js':
                self.send_bytes(200, (HERE / 'review.js').read_bytes(), 'text/javascript; charset=utf-8')
            elif path == '/api/fonts':
                self.send_json(200, engine.review.fonts.list())
            elif path == '/api/font-preview':
                query = parse_qs(urlsplit(self.path).query)
                self.send_bytes(200, engine.review.fonts.preview(query.get('font_id', [''])[0], query.get('text', [''])[0]), 'image/png')
            elif path == "/api/hoodie-library":
                self.send_json(200, load_market_library())
            elif path == "/api/status":
                self.send_json(200, {**engine.status(), 'online_access': is_online(self)})
            elif path == "/api/jobs":
                self.send_json(200, engine.list_jobs())
            elif re.fullmatch(r"/api/jobs/[a-f0-9]{32}", path):
                self.send_json(200, engine.get_job(path.rsplit("/", 1)[1]))
            elif re.fullmatch(r'/api/jobs/[a-f0-9]{32}/review', path):
                panel = parse_qs(urlsplit(self.path).query).get('panel', ['Front'])[0]
                self.send_json(200, engine.review.view(path.split('/')[3], panel))
            elif path.startswith("/files/"):
                parts = path.split("/", 3)
                if len(parts) != 4 or not JOB_ID.fullmatch(parts[2]):
                    raise FileNotFoundError()
                target = engine.file_path(parts[2], unquote(parts[3]))
                kind = {".png": "image/png", ".jpg": "image/jpeg", ".zip": "application/zip", ".json": "application/json",
                        '.psd': 'image/vnd.adobe.photoshop', '.svg': 'image/svg+xml'}[target.suffix]
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(target.stat().st_size))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                if target.suffix in (".zip", ".json", '.psd', '.svg'):
                    self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
                self.end_headers()
                with target.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        self.wfile.write(block)
            else:
                raise FileNotFoundError()
        except (FileNotFoundError, ValueError, KeyError):
            self.send_json(404, {"error": "Không tìm thấy nội dung."})

    def v2_get(self, path):
        v2 = self.server.v2
        query = parse_qs(urlsplit(self.path).query)
        if path == "/api/v2/jobs":
            kind = query.get("kind", [None])[0]
            self.send_json(200, v2.list(kind if kind in ("2d", "3d") else None))
        elif re.fullmatch(r"/api/v2/jobs/[a-f0-9]{32}", path):
            self.send_json(200, v2.get(path.rsplit("/", 1)[1]))
        elif path == "/api/v2/ai-budget":
            self.send_json(200, v2.ai_budget())
        elif path == "/api/v2/rights-config":
            self.send_json(200, v2.rights_config())
        elif path == "/api/v2/styles":
            self.send_json(200, v2.styles())
        elif re.fullmatch(r"/api/v2/templates/[a-z][a-z0-9_-]{0,63}/preview", path):
            self.send_file(v2.template_preview(path.split("/")[4]))
        elif re.fullmatch(r"/api/v2/sources/[a-f0-9]{64}", path):
            target = v2.source_file(path.rsplit("/", 1)[1])
            self.send_file(target)
        elif path.startswith("/v2files/"):
            parts = path.split("/", 3)
            if len(parts) != 4:
                raise FileNotFoundError()
            self.send_file(v2.file_path(parts[2], unquote(parts[3])))
        else:
            raise FileNotFoundError()

    def send_file(self, target):
        kind = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".json": "application/json",
                ".zip": "application/zip", ".dzi": "application/xml"}.get(target.suffix)
        if kind is None:
            raise FileNotFoundError()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=3600")
        if "download" in parse_qs(urlsplit(self.path).query):
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.end_headers()
        with target.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                self.wfile.write(block)

    def do_POST(self):
        if not self.headers_ok():
            self.send_json(403, {"error": "Chỉ nhận yêu cầu từ giao diện local."})
            return
        if not getattr(self.server, 'online_auth', OnlineAuth(HERE / 'runtime/online/access.json')).handle(self):
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Yêu cầu phải có định dạng JSON."})
            return
        length = self.headers.get("Content-Length", "")
        limit = MAX_BODY if self.path in ('/api/v2/inspect', '/api/v2/jobs', '/api/jobs', '/api/fonts', '/api/detect-template') or re.fullmatch(r'/api/jobs/[a-f0-9]{32}/review', self.path) else 128 * 1024 if self.path in ('/api/design-plan', '/api/v2/rights-config') else 16 * 1024
        if not length.isdigit() or len(length) > 9 or not 0 < int(length) <= limit:
            self.send_json(413, {"error": "Dữ liệu quá lớn hoặc thiếu độ dài."})
            return
        try:
            data = json.loads(self.rfile.read(int(length)))
            if not isinstance(data, dict):
                raise ValueError("Yêu cầu phải là một đối tượng JSON.")
            engine = self.server.engine
            if self.path == "/api/v2/inspect":
                self.send_json(200, self.server.v2.inspect(data))
            elif self.path == "/api/v2/jobs":
                self.send_json(202, self.server.v2.submit(data))
            elif self.path == "/api/v2/ai-estimate":
                self.send_json(200, self.server.v2.ai_estimate(data))
            elif self.path == "/api/v2/ai-budget":
                self.send_json(200, self.server.v2.ai_budget(data))
            elif self.path == "/api/v2/rights-config":
                self.send_json(200, self.server.v2.rights_config(data))
            elif self.path == "/api/v2/detect-style":
                self.send_json(200, self.server.v2.detect_style(data))
            elif self.path == "/api/v2/templates":
                self.send_json(201, self.server.v2.create_template(data))
            elif re.fullmatch(r"/api/v2/jobs/[a-f0-9]{32}/rights", self.path):
                self.send_json(200, self.server.v2.set_rights(self.path.split("/")[4], data))
            elif re.fullmatch(r"/api/v2/jobs/[a-f0-9]{32}/approve", self.path):
                self.send_json(200, self.server.v2.approve(self.path.split("/")[4]))
            elif self.path == "/api/settings":
                allowed = {"api_key", "model", "image_size", "quality"}
                if not set(data) <= allowed:
                    raise ValueError("Cấu hình không hợp lệ.")
                self.send_json(200, engine.settings.update(**data))
            elif self.path == "/api/check":
                result = check_connection(engine.settings)
                self.send_json(200, {"ok": True, "message": "Đã xác thực quyền đọc model. Chưa gọi tạo ảnh có phí.", **result})
            elif self.path == '/api/detect-template':
                self.send_json(200, engine.detect_product(data))
            elif self.path == "/api/design-plan":
                self.send_json(200, engine.design_plan(data))
            elif self.path == "/api/jobs":
                self.send_json(202, engine.submit(data))
            elif self.path == '/api/fonts':
                self.send_json(200, engine.review.fonts.add(data))
            elif re.fullmatch(r'/api/jobs/[a-f0-9]{32}/(?:review|psd|segment|vectorize|repair-patch|assembly-proof|garment-preview|approved-export)', self.path):
                parts = self.path.split('/')
                self.send_json(200, engine.postprocess(parts[3], parts[4], data))
            else:
                self.send_json(404, {"error": "Không tìm thấy chức năng."})
        except KeyError:
            self.send_json(404, {"error": "Không tìm thấy công việc."})
        except (ValueError, OSError, RuntimeError, TypeError, RecursionError) as exc:
            message = str(exc)
            key = self.server.engine.settings.api_key
            if key:
                message = message.replace(key, "[khóa đã ẩn]")
            self.send_json(400, {"error": message[:500]})


def main():
    parser = argparse.ArgumentParser(description="POD Studio — giao diện local trên Mac")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--engine-root", type=Path, default=DEFAULT_ENGINE)
    parser.add_argument("--jobs-dir", type=Path, default=HERE / "runtime" / "jobs")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--check", action="store_true", help="kiểm tra cài đặt, không gọi API")
    args = parser.parse_args()
    settings = Settings(args.config) if args.config else Settings()
    engine = Engine(settings, args.engine_root, args.jobs_dir)
    if args.check:
        print(json.dumps(engine.status(), ensure_ascii=False, indent=2))
        engine.executor.shutdown()
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.engine = engine
    server.v2 = StudioV2(engine, args.jobs_dir.parent)
    server.online_auth = OnlineAuth(HERE / 'runtime/online/access.json')
    print(f"POD Studio: http://127.0.0.1:{server.server_port}", flush=True)
    print("Chế độ local mặc định. OpenAI chỉ phát sinh phí khi chọn và bắt đầu công việc.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
