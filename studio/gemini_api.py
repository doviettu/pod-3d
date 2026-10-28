"""Local Gemini configuration and single-request print-art editing (stdlib + Pillow)."""

import base64
import http.client
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request
import warnings

from PIL import Image

DEFAULT_CONFIG = Path.home() / ".config/pod-studio/settings.json"
# Standard output-image fees only; input/thinking can add charges. Checked 2026-10-24:
# https://ai.google.dev/gemini-api/docs/pricing
PRICES = {
    "gemini-nano-banana-2.1": {"1K": .0336, "2K": .0504, "4K": .113},
    "gemini-3-pro-image": {"1K": .134, "2K": .134, "4K": .24},
}
EDGES = {"1K": 1024, "2K": 2048, "4K": 4096}
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"
TIMEOUT = 120
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
PROMPT = (
    "Clean up this supplied print artwork conservatively: reduce compression noise "
    "and clarify existing edges. Preserve all existing text, spelling, shapes, colors, "
    "layout, and relative positions; do not invent details or redesign. Keep the exact "
    "square canvas and the artwork's position within its padding. Return one image."
)


class GeminiError(RuntimeError):
    """A user-safe Vietnamese error; never contains provider bodies or credentials."""


def _validate(model, image_size, key=""):
    if not isinstance(model, str) or model not in PRICES:
        raise GeminiError("Mô hình không được hỗ trợ.")
    if not isinstance(image_size, str) or image_size not in EDGES:
        raise GeminiError("Kích thước ảnh phải là 1K, 2K hoặc 4K.")
    if not isinstance(key, str) or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise GeminiError("Khóa API không hợp lệ.")


def _atomic_write(path, writer):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)  # mkstemp permissions are 0600.
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
            with self.path.open("rb") as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError
            self._data = json.loads(data)
            if not isinstance(self._data, dict):
                raise ValueError
        except FileNotFoundError:
            self._data = {}
        except (OSError, ValueError, RecursionError):
            raise GeminiError("Không thể đọc cấu hình; hãy kiểm tra tệp cài đặt.") from None
        _validate(self.model, self.image_size, self._data.get("api_key", ""))

    @property
    def api_key(self):
        return (self._data.get("api_key") or os.environ.get("GEMINI_API_KEY", "").strip()
                or os.environ.get("GOOGLE_API_KEY", "").strip())

    @property
    def model(self):
        return self._data.get("model", "gemini-nano-banana-2.1")

    @property
    def image_size(self):
        return self._data.get("image_size", "2K")

    def public(self):
        return {"key_configured": bool(self.api_key),
                "key_source": "file" if self._data.get("api_key") else
                              "environment" if self.api_key else "none",
                "model": self.model, "image_size": self.image_size,
                "estimated_image_cost_usd": PRICES[self.model][self.image_size]}

    def update(self, api_key=None, model=None, image_size=None):
        if api_key is not None and not isinstance(api_key, str):
            raise GeminiError("Khóa API không hợp lệ.")
        data = {"api_key": (api_key or "").strip() or self._data.get("api_key", ""),
                "model": self.model if model is None else model,
                "image_size": self.image_size if image_size is None else image_size}
        _validate(data["model"], data["image_size"], data["api_key"])
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            _atomic_write(self.path, lambda stream: stream.write(json.dumps(data).encode()))
        except OSError:
            raise GeminiError("Không thể lưu cấu hình riêng tư.") from None
        self._data = data
        return self.public()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the key or repeat a paid request.


def _request(settings, payload=None):
    key = settings.api_key
    _validate(settings.model, settings.image_size, key)
    if not key:
        raise GeminiError("Chưa cấu hình khóa API.")
    request = urllib.request.Request(
        BASE_URL + settings.model + (":generateContent" if payload is not None else ""),
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError
        result = json.loads(raw)
        if not isinstance(result, dict) or "error" in result:
            raise ValueError
        return result
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        message = {400: "Yêu cầu ảnh không hợp lệ.", 401: "Khóa API không được chấp nhận.",
                   403: "Khóa API không có quyền truy cập.", 404: "Không tìm thấy mô hình.",
                   429: "Đã hết hạn mức hoặc gửi quá nhiều yêu cầu."}.get(
                       code, "Dịch vụ Google gặp lỗi; chưa tự động thử lại.")
        raise GeminiError(message) from None
    except (OSError, ValueError, RecursionError, http.client.HTTPException, urllib.error.URLError):
        raise GeminiError("Không thể nhận phản hồi hợp lệ từ Google; chưa tự động thử lại.") from None


def check_connection(settings):
    """Read model metadata only; no image generation or raw metadata exposure."""
    metadata = _request(settings)
    if metadata.get("name") != "models/" + settings.model:
        raise GeminiError("Thông tin mô hình trả về không hợp lệ.")
    return {"ok": True, **settings.public(), "message": "Kết nối thành công."}


def edit_image(source: Path, destination: Path, settings) -> dict:
    """One paid POST, no retries. Review manually: reconstruction is not guaranteed."""
    started = time.monotonic()
    source, destination = Path(source), Path(destination)
    formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".tif": "TIFF", ".tiff": "TIFF"}
    _validate(settings.model, settings.image_size, settings.api_key)
    if not settings.api_key:
        raise GeminiError("Chưa cấu hình khóa API.")
    try:
        if (destination.suffix.lower() not in formats or not destination.parent.is_dir()
                or source.resolve() == destination.resolve()
                or (destination.exists() and source.samefile(destination))):
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as original:
                size, dpi = original.size, original.info.get("dpi")
                thumbnail = original.convert("RGBA")
            thumbnail.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            side = max(thumbnail.size)
            left, top = (side - thumbnail.width) // 2, (side - thumbnail.height) // 2
            bbox = (left, top, left + thumbnail.width, top + thumbnail.height)
            square = Image.new("RGB", (side, side), "white")
            square.paste(thumbnail, (left, top), thumbnail)
            encoded = io.BytesIO()
            square.save(encoded, format="PNG")
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise GeminiError("Không thể đọc ảnh hoặc đường dẫn đầu ra không hợp lệ.") from None
    payload = {"contents": [{"role": "user", "parts": [
        {"text": PROMPT}, {"inlineData": {"mimeType": "image/png",
        "data": base64.b64encode(encoded.getvalue()).decode("ascii")}}]}],
        "generationConfig": {"responseModalities": ["IMAGE"],
                             "imageConfig": {"aspectRatio": "1:1", "imageSize": settings.image_size}}}
    result = _request(settings, payload)
    try:
        parts = result["candidates"][0]["content"]["parts"]
        images = [p["inlineData"] for p in parts
                  if isinstance(p, dict) and "inlineData" in p and not p.get("thought")]
        if len(images) != 1:
            raise ValueError
        image = images[0]
        expected = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
        image_bytes = base64.b64decode(image["data"], validate=True)
        with Image.open(io.BytesIO(image_bytes)) as generated:
            raw_dimensions = generated.size
            edge = EDGES[settings.image_size]
            if (generated.format != expected.get(image["mimeType"])
                    or raw_dimensions != (edge, edge) or getattr(generated, "n_frames", 1) != 1):
                raise ValueError
            generated.load()
            # Crop before filtering so padding cannot bleed into the print edges.
            x0, y0, x1, y1 = (round(v * edge / side) for v in bbox)
            box = (x0, y0, max(x0 + 1, x1), max(y0 + 1, y1))
            output = generated.convert("RGB").crop(box).resize(size, Image.Resampling.LANCZOS)
    except (KeyError, IndexError, TypeError, ValueError, OSError, Image.DecompressionBombError):
        raise GeminiError("Google không trả về một ảnh vuông đúng kích thước và định dạng yêu cầu.") from None
    try:
        _atomic_write(destination, lambda stream: output.save(
            stream, format=formats[destination.suffix.lower()], **({"dpi": dpi} if dpi else {})))
    except (OSError, ValueError, TypeError):
        raise GeminiError("Không thể lưu ảnh đầu ra.") from None
    usage = result.get("usageMetadata", {})
    fields = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount",
              "cachedContentTokenCount", "thoughtsTokenCount", "toolUsePromptTokenCount")
    return {"model": settings.model, "image_size": settings.image_size,
            "raw_dimensions": list(raw_dimensions), "seconds": round(time.monotonic() - started, 3),
            "estimated_image_cost_usd": PRICES[settings.model][settings.image_size],
            "cost_note": "Chỉ ước tính phí ảnh đầu ra, không phải tổng tiền thực tế.",
            "manual_review_required": True,
            "message": "Cần kiểm tra thủ công; không bảo đảm tái tạo trung thực bản gốc.",
            "usageMetadata": {k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
                              if k in fields and type(v) in (int, float) and v >= 0
                              and (type(v) is int or math.isfinite(v))}}
