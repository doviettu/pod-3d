"""HTTP side of the AI worker queue (see pod3d/offload.py and studio/worker.py).

GET  /api/worker/next          long-poll; 204 when idle, else 200 + .npz body + X-Task-* headers
POST /api/worker/result/<id>   .npz body, or header X-Task-Error with a short message
GET  /api/worker/status        queue numbers (also needs the token)
Auth: header `Authorization: Bearer <token>`, token in runtime/worker/token (created on the server
with `python studio/worker_api.py token`). The online login does not apply to these paths.
"""
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
TOKEN = HERE / 'runtime' / 'worker' / 'token'
MAX_RESULT = 400 * 1024 * 1024
PREFIX = '/api/worker/'


def token(path=TOKEN):
    try:
        value = Path(path).read_text().strip()
    except OSError:
        return None
    return value if len(value) >= 32 else None


def create_token(path=TOKEN):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if token(path) is None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_urlsafe(32))
    path.chmod(0o600)
    return token(path)


def reply(handler, status, body=b'', ctype='application/json; charset=utf-8', headers=None):
    handler.send_response(status)
    handler.send_header('Content-Type', ctype)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def handle(handler, broker, token_path=TOKEN):
    """Serve /api/worker/*; returns False when the path is not a worker path."""
    path = urlsplit(handler.path).path
    if not path.startswith(PREFIX):
        return False
    if broker is None:
        reply(handler, 404, b'{"error":"worker queue off"}')
        return True
    expected = token(token_path)
    supplied = handler.headers.get('Authorization', '')
    if expected is None or not hmac.compare_digest(supplied.encode(), ('Bearer ' + expected).encode()):
        reply(handler, 401, b'{"error":"worker token"}')
        return True
    from pod3d import offload
    if handler.command == 'GET' and path == PREFIX + 'status':
        reply(handler, 200, json.dumps(broker.status()).encode())
    elif handler.command == 'GET' and path == PREFIX + 'next':
        task = broker.next()
        if task is None:
            reply(handler, 204)
        else:
            reply(handler, 200, task.payload, 'application/octet-stream', offload.headers_for(task))
    elif handler.command == 'POST' and re.fullmatch(PREFIX + 'result/[a-f0-9]{32}', path):
        task_id = path.rsplit('/', 1)[1]
        error = handler.headers.get('X-Task-Error')
        length = handler.headers.get('Content-Length', '0')
        if not length.isdigit() or int(length) > MAX_RESULT:
            reply(handler, 413, b'{"error":"too large"}')
            return True
        body = handler.rfile.read(int(length)) if int(length) else b''
        ok = broker.finish(task_id, None if error else body, error[:300] if error else None)
        reply(handler, 200 if ok else 410, json.dumps({'accepted': ok}).encode())
    else:
        reply(handler, 404, b'{"error":"not found"}')
    return True


if __name__ == '__main__':
    if sys.argv[1:] == ['token']:
        create_token()
        print('Đã có token worker trong', TOKEN, '(không in ra đây; chép sang Mac bằng scp).')
    else:
        print('Dùng: python studio/worker_api.py token')
