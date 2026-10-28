"""Load a source image from an upload or a web link.

Links to shop images are rewritten to the largest size the shop serves
(the biggest single quality gain for web images), and only public http(s)
hosts are fetched.
"""
import base64
import binascii
import hashlib
import io
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit
import urllib.request

from PIL import Image, ImageOps

MAX_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 50_000_000
FORMATS = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 POD-Studio'

# (host pattern, path pattern, replacement): first match wins.
UPGRADES = [
    (r'(^|\.)etsystatic\.com$', r'/il_\d+x[\dN]+\.', '/il_fullxfull.'),
    (r'(^|\.)(cdn\.shopify\.com|.*\.myshopify\.com)$', r'_(\d+x\d*|\d*x\d+|pico|icon|thumb|small|compact|medium|large|grande)(@\dx)?(?=\.\w+$)', ''),
    (r'(^|\.)(media-amazon\.com|ssl-images-amazon\.com)$', r'\._[A-Z0-9,_]+_(?=\.\w+$)', ''),
    (r'(^|\.)pinimg\.com$', r'^/(\d+x|\d+x\d+)/', '/originals/'),
    (r'(^|\.)redbubble\.net$', r'/(\d+x\d+|\d+x)(,f8f8f8-pad,\d+x\d+,f8f8f8)?', '/1000x'),
]


def upgrade_url(url):
    """Return the URL of the largest version a known shop CDN serves, else the URL unchanged."""
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    for host_re, path_re, repl in UPGRADES:
        if re.search(host_re, host):
            path = re.sub(path_re, repl, parts.path, count=1)
            query = '' if 'shopify' in host else parts.query
            return urlunsplit((parts.scheme, parts.netloc, path, query, ''))
    return url


def _public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError('Không tìm thấy máy chủ của đường dẫn ảnh.') from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError('Chỉ tải ảnh từ địa chỉ Internet công khai.')


class _NoRedirectToPrivate(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme not in ('http', 'https') or not target.hostname:
            raise ValueError('Đường dẫn ảnh chuyển hướng không hợp lệ.')
        _public_host(target.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, timeout=20):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('Đường dẫn ảnh phải bắt đầu bằng http:// hoặc https://')
    _public_host(parts.hostname)
    opener = urllib.request.build_opener(_NoRedirectToPrivate)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'image/*'})
    with opener.open(request, timeout=timeout) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Ảnh vượt quá giới hạn 32 MB.')
    return data


def check_image(raw):
    """Validate bytes; returns (suffix, width, height)."""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in FORMATS or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Chỉ nhận ảnh JPEG, PNG hoặc WebP (không nhận ảnh động).')
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Ảnh quá lớn (tối đa 50 triệu pixel).')
            size = image.size
            suffix = FORMATS[image.format]
            image.verify()
    except (OSError, Image.DecompressionBombError):
        raise ValueError('Không đọc được ảnh.') from None
    return suffix, *size


def from_base64(encoded):
    if not isinstance(encoded, str) or not encoded:
        raise ValueError('Thiếu dữ liệu ảnh.')
    if encoded.startswith('data:'):
        encoded = encoded.split(',', 1)[-1]
    if len(encoded) > MAX_BYTES * 4 // 3 + 4:
        raise ValueError('Ảnh vượt quá giới hạn 32 MB.')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Dữ liệu ảnh không hợp lệ.') from None
    return raw


def from_url(url, download=_download):
    """Fetch the upgraded URL, falling back to the original; returns (raw, used_url)."""
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError('Đường dẫn ảnh không hợp lệ.')
    url = url.strip()
    tried = []
    for candidate in dict.fromkeys([upgrade_url(url), url]):
        try:
            raw = download(candidate)
            check_image(raw)
            return raw, candidate
        except (OSError, ValueError) as exc:
            tried.append(str(exc))
    raise ValueError('Không tải được ảnh: ' + tried[-1][:200])


def load(entry, download=_download):
    """entry = {'image_base64': ...} or {'url': ...}; returns dict(raw, suffix, width, height, sha256, url)."""
    if not isinstance(entry, dict):
        raise ValueError('Nguồn ảnh không hợp lệ.')
    url = None
    if entry.get('url'):
        raw, url = from_url(entry['url'], download)
    else:
        raw = from_base64(entry.get('image_base64'))
    if len(raw) > MAX_BYTES:
        raise ValueError('Ảnh vượt quá giới hạn 32 MB.')
    suffix, width, height = check_image(raw)
    return dict(raw=raw, suffix=suffix, width=width, height=height, url=url,
                sha256=hashlib.sha256(raw).hexdigest())


def decode_rgb(raw):
    """Bytes -> RGB PIL image with EXIF orientation applied (alpha flattened on white)."""
    with Image.open(io.BytesIO(raw)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ('RGBA', 'LA', 'P'):
            image = image.convert('RGBA')
            canvas = Image.new('RGB', image.size, (255, 255, 255))
            canvas.paste(image, mask=image.getchannel('A'))
            return canvas
        return image.convert('RGB')
