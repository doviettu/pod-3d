"""Form login for the HTTPS gateway; localhost access stays local."""
import base64
from collections import deque
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

COOKIE = '__Host-pod_session'
TTL = 12 * 60 * 60
PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Đăng nhập · POD Studio</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#233448;font:16px system-ui;
min-height:100vh;display:grid;place-items:center;padding:24px}main{background:white;border:1px solid #e0e6ed;
border-radius:18px;padding:32px;width:100%;max-width:420px;box-shadow:0 8px 32px #23344809}
.brand{color:#2463dc;font-weight:750}h1{font-size:27px;margin:20px 0 8px}p,small{color:#5e6e82;line-height:1.6}
label{display:block;margin:20px 0 6px;font-size:14px;font-weight:600}input,button{font:inherit;width:100%;
border:1px solid #c8d2df;border-radius:9px;padding:12px}button{margin:24px 0 16px;background:#2463dc;
color:white;border:0;font-weight:650;cursor:pointer}:focus-visible{outline:3px solid #89adf5;outline-offset:3px}
.error{color:#9c3224}small{display:block;font-size:12px}</style></head>
<body><main><div class="brand">POD Studio</div><h1>Đăng nhập</h1>
<p>Mở xưởng thiết kế trên Mac của bạn.</p><!--ERROR-->
<form action="/login" method="post">
<label for="username">Tài khoản</label><input id="username" name="username" autocomplete="username" value="studio" required maxlength="64">
<label for="password">Mật khẩu</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="512">
<button type="submit">Đăng nhập</button></form>
<small>Dùng mật khẩu truy cập đã tạo trên Mac. Đây không phải OpenAI API key.</small></main></body></html>"""


def is_online(handler):
    return handler.headers.get('X-POD-Online') == '1'


class OnlineAuth:
    def __init__(self, path):
        self.path = Path(path)
        self.failures = deque(maxlen=10)
        self.lock = threading.Lock()

    def credentials(self):
        with self.path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError()
        data = json.loads(raw)
        password = data.get('password')
        if data.get('username') != 'studio' or not isinstance(password, str) or not 24 <= len(password) <= 512:
            raise ValueError()
        return password

    def signature(self, password, payload):
        key = hashlib.sha256(('POD Studio session\0' + password).encode()).digest()
        return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, password):
        payload = str(int(time.time()) + TTL) + '.' + secrets.token_hex(16)
        return payload + '.' + self.signature(password, payload)

    def valid_session(self, password, value):
        if len(value) > 8192:
            return False
        try:
            cookies = SimpleCookie()
            cookies.load(value)
            item = cookies.get(COOKIE)
            if item is None:
                return False
            expiry, nonce, signature = item.value.split('.')
            if not expiry.isdigit() or not re.fullmatch('[a-f0-9]{32}', nonce) or not re.fullmatch('[a-f0-9]{64}', signature):
                return False
            if not time.time() < int(expiry) <= time.time() + TTL + 30:
                return False
            return hmac.compare_digest(signature, self.signature(password, expiry + '.' + nonce))
        except (ValueError, CookieError):
            return False

    def valid_basic(self, password, value):
        if not value.startswith('Basic ') or len(value) > 2048:
            return False
        try:
            decoded = base64.b64decode(value[6:], validate=True).decode()
            username, supplied = decoded.split(':', 1)
            return hmac.compare_digest(username.encode(), b'studio') and hmac.compare_digest(supplied.encode(), password.encode())
        except (ValueError, UnicodeError):
            return False

    def reply(self, handler, status, body=b'', *, location=None, cookie=None, html=False):
        handler.send_response(status)
        handler.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        # Keep the browser's same-origin form Origin; no-referrer turns it into null.
        handler.send_header('Referrer-Policy', 'same-origin')
        if html:
            handler.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        if location:
            handler.send_header('Location', location)
        if cookie:
            handler.send_header('Set-Cookie', cookie)
        handler.end_headers()
        handler.wfile.write(body)

    def page(self, handler, status=200, message=''):
        error = '<p class="error" role="alert">' + message + '</p>' if message else ''
        self.reply(handler, status, PAGE.replace('<!--ERROR-->', error).encode(), html=True)

    def handle(self, handler):
        if not is_online(handler):
            return True
        try:
            password = self.credentials()
        except (OSError, ValueError, TypeError, AttributeError):
            self.reply(handler, 503, b'{"error":"Online login is not configured."}')
            return False
        path = urlsplit(handler.path).path
        if path == '/login':
            if handler.command == 'GET':
                self.page(handler)
                return False
            length = handler.headers.get('Content-Length', '')
            if handler.headers.get_content_type() != 'application/x-www-form-urlencoded':
                self.page(handler, 415, 'Hãy đăng nhập bằng biểu mẫu bên dưới.')
                return False
            if not length.isdigit() or not 0 < int(length) <= 4096:
                self.page(handler, 413, 'Thông tin đăng nhập quá dài.')
                return False
            try:
                fields = parse_qs(handler.rfile.read(int(length)).decode(), max_num_fields=2, keep_blank_values=True)
                username, supplied = fields.get('username', ['']), fields.get('password', [''])
                if len(username) != 1 or len(supplied) != 1:
                    raise ValueError()
            except (ValueError, UnicodeError):
                self.page(handler, 400, 'Thông tin đăng nhập không hợp lệ.')
                return False
            with self.lock:
                now = time.time()
                while self.failures and self.failures[0] <= now - 60:
                    self.failures.popleft()
                if len(self.failures) >= 10:
                    self.page(handler, 429, 'Đã thử quá nhiều lần. Hãy chờ một phút rồi thử lại.')
                    return False
                valid = (hmac.compare_digest(username[0].encode(), b'studio')
                         and hmac.compare_digest(supplied[0].encode(), password.encode()))
                if not valid:
                    self.failures.append(now)
            if valid:
                self.reply(handler, 303, location='/', cookie=COOKIE + '=' + self.issue(password)
                           + '; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=' + str(TTL))
            else:
                self.page(handler, 401, 'Tài khoản hoặc mật khẩu chưa đúng.')
            return False
        # A browser's cached Basic header must not bypass logout from the form session.
        authenticated = self.valid_session(password, handler.headers.get('Cookie', '')) or (
            handler.headers.get('X-POD-API-Client') == '1'
            and self.valid_basic(password, handler.headers.get('Authorization', '')))
        if path == '/logout' and handler.command == 'POST':
            self.reply(handler, 303, location='/login', cookie=COOKIE + '=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0')
            return False
        if authenticated:
            return True
        if handler.command == 'GET' and path in ('/', '/index.html'):
            self.reply(handler, 303, location='/login')
        else:
            self.reply(handler, 401, json.dumps({'error': 'Hãy đăng nhập lại để tiếp tục.'}, ensure_ascii=False).encode())
        return False
