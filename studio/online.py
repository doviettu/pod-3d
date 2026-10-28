"""Password-protected HTTPS access to the existing Mac worker."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

import mailer

HERE = Path(__file__).resolve().parent
ROOT = HERE / 'runtime' / 'online'
UPSTREAM = 'http://127.0.0.1:8765'
GATEWAY = 'http://127.0.0.1:8766'


def caddy_config(host):
    if not re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com', host):
        raise ValueError('Đường dẫn HTTPS không hợp lệ.')
    return f'''{{
    admin off
    auto_https off
}}
http://:8766 {{
    bind 127.0.0.1
    header X-Robots-Tag "noindex, nofollow, noarchive"
    header Cache-Control "no-store"
    route {{
        @wrongHost not host {host}
        respond @wrongHost "Invalid host" 403
        @foreign {{
            header Origin *
            not header Origin https://{host}
        }}
        respond @foreign "Invalid origin" 403
        reverse_proxy {UPSTREAM} {{
            header_up Host 127.0.0.1:8765
            header_up Origin {UPSTREAM}
            header_up X-POD-Online 1
            transport http {{
                response_header_timeout 300s
            }}
        }}
    }}
}}
'''


def request(url, password=None, host=None, origin=None):
    headers = {}
    if password:
        token = base64.b64encode(('studio:' + password).encode()).decode()
        headers['Authorization'] = 'Basic ' + token
        headers['X-POD-API-Client'] = '1'
    if host:
        headers['Host'] = host
    if origin:
        headers['Origin'] = origin
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def credential():
    path = ROOT / 'access.json'
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'username': 'studio', 'password': secrets.token_urlsafe(24)}, stream)
    path.chmod(0o600)
    data = json.loads(path.read_text())
    if data.get('username') != 'studio' or not isinstance(data.get('password'), str) or len(data['password']) < 24:
        raise ValueError('Tệp đăng nhập không hợp lệ; chưa mở web online.')
    return data['password']


def public_dns_ready(host):
    query = urlencode({'name': host, 'type': 'A'})
    req = urllib.request.Request('https://cloudflare-dns.com/dns-query?' + query,
                                 headers={'Accept': 'application/dns-json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.load(response)
    return result.get('Status') == 0 and any(a.get('type') == 1 for a in result.get('Answer', []))


def main():
    def binary(name):
        return shutil.which(name) or (str(Path('/opt/homebrew/bin') / name)
                                     if (Path('/opt/homebrew/bin') / name).is_file() else None)
    caddy, tunnel = binary('caddy'), binary('cloudflared')
    if not caddy or not tunnel:
        raise RuntimeError('Cần cài caddy và cloudflared bằng Homebrew trước.')
    # At login both services start together: give the local app a moment to come up.
    for attempt in range(60):
        try:
            status, body = request(UPSTREAM + '/api/status')
            if status == 200 and json.loads(body).get('app') == 'pod-studio':
                break
        except (urllib.error.URLError, ValueError, OSError):
            pass
        time.sleep(2)
    else:
        raise RuntimeError('Hãy mở POD Studio trên Mac trước.')
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    ROOT.chmod(0o700)
    # Refuse to replace any existing gateway, including an earlier online session.
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 8766)) == 0:
            raise RuntimeError('Cổng online đang được dùng; không mở thêm phiên.')
    password = credential()
    children = []
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    state_file = ROOT / 'status.json'
    try:
        # shortcut: a quick tunnel changes URL on restart; use a named tunnel for a permanent domain.
        with (ROOT / 'tunnel.log').open('w') as log:
            children.append(subprocess.Popen([tunnel, 'tunnel', '--no-autoupdate', '--url', GATEWAY],
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log))
        deadline = time.monotonic() + 50
        host = None
        while time.monotonic() < deadline:
            if children[0].poll() is not None:
                raise RuntimeError('Không mở được đường truyền HTTPS; xem tunnel.log.')
            found = re.search(r'https://([a-z0-9-]+\.trycloudflare\.com)', (ROOT / 'tunnel.log').read_text())
            if found:
                host = found.group(1)
                break
            time.sleep(.25)
        if not host:
            raise RuntimeError('Chưa nhận được đường dẫn HTTPS sau 50 giây.')
        config = ROOT / 'Caddyfile'
        config.write_text(caddy_config(host))
        config.chmod(0o600)
        subprocess.run([caddy, 'validate', '--config', str(config), '--adapter', 'caddyfile'],
                       check=True, capture_output=True)
        with (ROOT / 'gateway.log').open('w') as log:
            children.append(subprocess.Popen([caddy, 'run', '--config', str(config), '--adapter', 'caddyfile'],
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log))
        for _ in range(40):
            if children[-1].poll() is not None:
                raise RuntimeError('Cổng đăng nhập không chạy; xem gateway.log.')
            try:
                code, _ = request(GATEWAY + '/api/status', host=host)
                if code == 401:
                    break
            except urllib.error.URLError:
                pass
            time.sleep(.25)
        else:
            raise RuntimeError('Cổng đăng nhập chưa sẵn sàng.')
        code, body = request(GATEWAY + '/api/status', password, host)
        assert code == 200 and json.loads(body)['app'] == 'pod-studio'
        assert request(GATEWAY + '/api/status', password, host, 'https://foreign.invalid')[0] == 403
        state = {'url': 'https://' + host, 'pid': os.getpid(),
                 'credentials_file': str(ROOT / 'access.json'), 'status': 'starting'}
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        # Verify the real HTTPS route, not just the local reverse proxy.
        for _ in range(60):
            try:
                # Avoid caching NXDOMAIN locally before the new tunnel name is published.
                if not public_dns_ready(host):
                    time.sleep(2)
                    continue
                public_code, _ = request(state['url'] + '/api/status')
                public_ok, public_body = request(state['url'] + '/api/status', password)
                if public_code == 401 and public_ok == 200 and json.loads(public_body)['app'] == 'pod-studio':
                    break
            except (urllib.error.URLError, ValueError):
                pass
            time.sleep(2)
        else:
            raise RuntimeError('Chưa xác minh được truy cập HTTPS; đóng phiên online.')
        state['status'] = 'ready'
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        print(json.dumps(state, ensure_ascii=False), flush=True)
        # Mail the new link (and login) when it changed; a mail problem never stops the tunnel.
        mailer.notify_online(state['url'], password, ROOT / 'mail-state.json',
                             log=lambda m: print(m, flush=True))
        while all(child.poll() is None for child in children):
            time.sleep(2)
        raise RuntimeError('Đường truyền online đã dừng; mở lại để lấy URL mới.')
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        state_file.write_text(json.dumps({'status': 'stopped', 'pid': os.getpid()}))


if __name__ == '__main__':
    main()
