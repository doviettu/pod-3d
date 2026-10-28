"""Single-request OpenAI image edits; credentials stay on the local server."""
import base64
import hashlib
import design_spec as design
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request
import warnings

from PIL import Image, ImageDraw

DEFAULT_CONFIG = Path.home() / '.config/pod-studio/openai-settings.json'
MODELS = ('gpt-image-2.5-flare', 'gpt-image-2.5-sunburst')
EDGES = {'1K': 1024, '2K': 2048}
QUALITIES = ('low', 'medium', 'high')
BASE_URL = 'https://api.openai.com/v1/'
TIMEOUT = 180
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
PROMPT = (
    'Clean up this supplied print artwork conservatively: reduce compression noise '
    'and clarify existing edges. Preserve all existing text, spelling, shapes, colors, '
    'layout, and relative positions; do not invent details or redesign. Keep the exact '
    'square canvas and the artwork position within its padding. Return one image.'
)


class OpenAIError(RuntimeError):
    """Safe user-facing error; provider bodies and credentials are never exposed."""


def _validate(model, image_size, quality, key=''):
    if model not in MODELS or not isinstance(model, str):
        raise OpenAIError('Mô hình OpenAI không được hỗ trợ.')
    if not isinstance(image_size, str) or image_size not in EDGES or quality not in QUALITIES:
        raise OpenAIError('Kích thước hoặc chất lượng ảnh không hợp lệ.')
    if (not isinstance(key, str) or len(key) > 512
            or any(ord(c) < 33 or ord(c) > 126 for c in key)
            or (key and not key.startswith('sk-'))):
        raise OpenAIError('Khóa OpenAI không hợp lệ; hãy dùng API key OpenAI riêng.')


def _atomic_write(path, writer):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Settings:
    def __init__(self, path=DEFAULT_CONFIG):
        self.path = Path(path)
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
            self._data = json.loads(raw)
            if not isinstance(self._data, dict):
                raise ValueError
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError, RecursionError):
            raise OpenAIError('Không thể đọc cấu hình OpenAI riêng tư.') from None
        _validate(self.model, self.image_size, self.quality, self.api_key)

    @property
    def api_key(self):
        return self._data.get('api_key') or os.environ.get('OPENAI_API_KEY', '').strip()

    @property
    def model(self):
        return self._data.get('model', MODELS[0])

    @property
    def image_size(self):
        return self._data.get('image_size', '1K')

    @property
    def quality(self):
        return self._data.get('quality', 'medium')

    def public(self):
        return {'provider': 'openai', 'key_configured': bool(self.api_key),
                'key_source': 'file' if self._data.get('api_key') else
                              'environment' if self.api_key else 'none',
                'model': self.model, 'image_size': self.image_size, 'quality': self.quality,
                'estimated_image_cost_usd': None}

    def update(self, api_key=None, model=None, image_size=None, quality=None):
        if api_key is not None and not isinstance(api_key, str):
            raise OpenAIError('Khóa OpenAI không hợp lệ.')
        data = {'api_key': (api_key or '').strip() or self._data.get('api_key', ''),
                'model': self.model if model is None else model,
                'image_size': self.image_size if image_size is None else image_size,
                'quality': self.quality if quality is None else quality}
        _validate(data['model'], data['image_size'], data['quality'], data['api_key'])
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            _atomic_write(self.path, lambda stream: stream.write(json.dumps(data).encode()))
        except OSError:
            raise OpenAIError('Không thể lưu cấu hình OpenAI riêng tư.') from None
        self._data = data
        return self.public()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials or repeat a paid edit.


def _request(settings, payload=None):
    _validate(settings.model, settings.image_size, settings.quality, settings.api_key)
    if not settings.api_key:
        raise OpenAIError('Chưa cấu hình khóa OpenAI.')
    path = 'images/edits' if payload is not None else 'models/' + settings.model
    request = urllib.request.Request(BASE_URL + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + settings.api_key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError
        result = json.loads(raw)
        if not isinstance(result, dict) or 'error' in result:
            raise ValueError
        return result
    except urllib.error.HTTPError as exc:
        code = exc.code
        quota = False
        try:
            error = json.loads(exc.read(16384)).get('error', {})
            quota = isinstance(error, dict) and error.get('code') in (
                'insufficient_quota', 'billing_hard_limit_reached', 'credit_balance_exhausted',
                'organization_spend_limit_exceeded', 'project_spend_limit_exceeded')
        except (ValueError, AttributeError, OSError, RecursionError):
            pass
        finally:
            exc.close()
        message = {400: 'Yêu cầu chỉnh ảnh không hợp lệ.', 401: 'Khóa OpenAI không được chấp nhận.',
                   403: 'Chưa có quyền dùng model; kiểm tra quyền key và xác minh tổ chức OpenAI.',
                   404: 'Không tìm thấy model OpenAI hoặc project chưa có quyền truy cập.',
                   429: 'Đã chạm giới hạn tần suất OpenAI; chưa tự động thử lại.'}.get(
                       code, 'Dịch vụ OpenAI gặp lỗi; chưa tự động thử lại.')
        if quota:
            message = 'Project OpenAI chưa đủ số dư hoặc đã chạm giới hạn chi tiêu; hãy kiểm tra Billing và Limits.'
        raise OpenAIError(message) from None
    except (OSError, ValueError, RecursionError, http.client.HTTPException, urllib.error.URLError):
        raise OpenAIError('Không nhận được phản hồi hợp lệ từ OpenAI; chưa tự động thử lại.') from None


def check_connection(settings):
    metadata = _request(settings)
    if metadata.get('id') != settings.model:
        raise OpenAIError('Thông tin model OpenAI không hợp lệ.')
    return {'ok': True, **settings.public(),
            'message': 'Đã xác thực quyền đọc model. Chưa kiểm tra hạn mức tạo ảnh có phí.'}


def edit_image(source, destination, settings):
    """Exactly one paid POST, no retries; preserve the original print canvas and DPI."""
    started = time.monotonic()
    source, destination = Path(source), Path(destination)
    _validate(settings.model, settings.image_size, settings.quality, settings.api_key)
    try:
        if (destination.suffix.lower() != '.png' or not destination.parent.is_dir()
                or source.resolve() == destination.resolve()
                or (destination.exists() and source.samefile(destination))):
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(source) as original:
                size, dpi = original.size, original.info.get('dpi')
                thumbnail = original.convert('RGBA')
            thumbnail.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            side = max(thumbnail.size)
            left, top = (side - thumbnail.width) // 2, (side - thumbnail.height) // 2
            bbox = (left, top, left + thumbnail.width, top + thumbnail.height)
            square = Image.new('RGB', (side, side), 'white')
            square.paste(thumbnail, (left, top), thumbnail)
            encoded = io.BytesIO()
            square.save(encoded, format='PNG')
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise OpenAIError('Không thể đọc ảnh hoặc đường dẫn đầu ra không hợp lệ.') from None
    edge = EDGES[settings.image_size]
    # JSON image references are supported by the Images edits endpoint (2026-10-25).
    payload = {'model': settings.model, 'prompt': PROMPT, 'n': 1,
               'size': f'{edge}x{edge}', 'quality': settings.quality, 'output_format': 'png',
               'images': [{'image_url': 'data:image/png;base64,' + base64.b64encode(encoded.getvalue()).decode()}]}
    result = _request(settings, payload)
    try:
        if len(result['data']) != 1:
            raise ValueError
        raw = base64.b64decode(result['data'][0]['b64_json'], validate=True)
        with Image.open(io.BytesIO(raw)) as generated:
            if generated.format != 'PNG' or generated.size != (edge, edge) or getattr(generated, 'n_frames', 1) != 1:
                raise ValueError
            generated.load()
            box = tuple(round(v * edge / side) for v in bbox)
            output = generated.convert('RGB').crop(box).resize(size, Image.Resampling.LANCZOS)
    except (KeyError, IndexError, TypeError, ValueError, OSError, Image.DecompressionBombError):
        raise OpenAIError('OpenAI chưa trả về đúng một ảnh PNG đúng kích thước yêu cầu.') from None
    try:
        _atomic_write(destination, lambda stream: output.save(stream, format='PNG', **({'dpi': dpi} if dpi else {})))
    except (OSError, ValueError, TypeError):
        raise OpenAIError('Không thể lưu ảnh OpenAI; chưa tự động thử lại.') from None
    usage = result.get('usage', {})
    return {'provider': 'openai', 'model': settings.model, 'image_size': settings.image_size,
            'quality': settings.quality, 'raw_dimensions': [edge, edge],
            'seconds': round(time.monotonic() - started, 3), 'estimated_image_cost_usd': None,
            'cost_note': 'Tính phí theo token ảnh và chữ; xem Usage của OpenAI để biết chi phí thực tế.',
            'manual_review_required': True,
            'usage': {k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
                      if k in ('input_tokens', 'output_tokens', 'total_tokens') and type(v) is int and v >= 0}}


def reconstruct_artwork(mockup, destination, settings, *, panel, canvas_size, notes='', design_spec=None):
    """One paid request from the original mockup; save native artwork before panel placement."""
    if (panel not in design.GENERATION_PANELS or not isinstance(canvas_size, (list, tuple)) or len(canvas_size) != 2
            or any(type(v) is not int or not 1 <= v <= 30000 for v in canvas_size)
            or canvas_size[0] * canvas_size[1] > 50_000_000
            or not isinstance(notes, str) or len(notes) > 2000):
        raise OpenAIError('Panel, ghi chú hoặc kích thước artwork không hợp lệ.')
    _validate(settings.model, settings.image_size, settings.quality, settings.api_key)
    destination = Path(destination)
    mockup = Path(mockup)
    if (destination.suffix.lower() != '.png' or not destination.parent.is_dir()
            or destination.resolve() == mockup.resolve()):
        raise OpenAIError('Đường dẫn artwork không hợp lệ.')
    started = time.monotonic()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(mockup) as source:
                if source.width * source.height > 50_000_000 or getattr(source, 'n_frames', 1) != 1:
                    raise ValueError
                reference = source.convert('RGB')
        reference.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        side = 768
        width, height = canvas_size
        scale = side / max(width, height)
        gw, gh = max(1, round(width * scale)), max(1, round(height * scale))
        left, top = (side - gw) // 2, (side - gh) // 2
        box = (left, top, left + gw, top + gh)
        guide = Image.new('RGB', (side, side), 'white')
        ImageDraw.Draw(guide).rectangle((left, top, left + gw - 1, top + gh - 1), fill='#c8d2df')
        images = []
        for image in (reference, guide):
            stream = io.BytesIO()
            image.save(stream, 'PNG')
            images.append({'image_url': 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()})
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise OpenAIError('Không đọc được mockup gốc để phục hồi artwork.') from None
    try:
        spec = design_spec if design_spec is not None else design.build_design_spec(
            {'panels': [{'name': panel, 'size': list(canvas_size)}]}, notes)
        if spec['panels'].get(panel) != list(canvas_size):
            raise ValueError
        prompt = design.panel_prompt(spec, panel)
        if len(prompt) > 32000:
            raise ValueError
        spec_hash = design.design_spec_hash(spec)
    except (ValueError, KeyError, TypeError):
        raise OpenAIError('Bản mô tả thiết kế chung không khớp panel.') from None
    edge = EDGES[settings.image_size]
    result = _request(settings, {'model': settings.model, 'prompt': prompt, 'n': 1,
        'size': f'{edge}x{edge}', 'quality': settings.quality, 'output_format': 'png', 'images': images})
    try:
        if len(result['data']) != 1:
            raise ValueError
        raw = base64.b64decode(result['data'][0]['b64_json'], validate=True)
        with Image.open(io.BytesIO(raw)) as generated:
            if generated.format != 'PNG' or generated.size != (edge, edge) or getattr(generated, 'n_frames', 1) != 1:
                raise ValueError
            generated.load()
            crop = tuple(round(value * edge / side) for value in box)
            artwork = generated.convert('RGB').crop(crop)
    except (KeyError, IndexError, TypeError, ValueError, OSError, Image.DecompressionBombError):
        raise OpenAIError('OpenAI chưa trả về artwork PNG đúng kích thước; chưa tự thử lại.') from None
    _atomic_write(destination, lambda stream: artwork.save(stream, 'PNG'))
    usage = result.get('usage', {})
    return {'provider': 'openai', 'model': settings.model, 'panel': panel,
            'workflow': 'shared-artwork-v2', 'design_spec_hash': spec_hash,
            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'image_size': settings.image_size, 'quality': settings.quality,
            'raw_dimensions': [edge, edge], 'artwork_dimensions': list(artwork.size),
            'seconds': round(time.monotonic() - started, 3), 'estimated_image_cost_usd': None,
            'manual_review_required': True, 'text_logo_separate': True,
            'usage': {k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
                      if k in ('input_tokens', 'output_tokens', 'total_tokens') and type(v) is int and v >= 0}}


def place_artwork(source, destination, size, dpi, upscale=None):
    """Place a restored master onto the template's fixed rectangular print canvas."""
    with Image.open(source) as image:
        output = image.convert('RGB')
    if upscale is not None:
        import numpy as np
        restored = upscale(np.asarray(output)[:, :, ::-1].copy(), size[0] / output.width)
        output = Image.fromarray(restored[:, :, ::-1])
    output = output.resize(tuple(size), Image.Resampling.LANCZOS)
    _atomic_write(Path(destination), lambda stream: output.save(stream, 'PNG', dpi=(dpi, dpi)))
