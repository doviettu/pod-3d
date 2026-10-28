"""Google Drive + Sheet sync for approved jobs (stdlib only, no Google client library).

- Sign-in: OAuth for a "Desktop app" client with a loopback redirect and PKCE. Only the
  `drive.file` scope: the tool sees and edits only the files it created, and the Sheets API
  accepts that scope for a spreadsheet the tool made. It is a non-sensitive scope, so the
  consent screen can be published without Google verification (refresh tokens of apps left in
  "Testing" expire after 7 days).
- Secrets (client secret, refresh token) live in ~/.config/pod-studio/google.json, mode 600,
  never in the repo, never sent back to the page.
- SQLite stays the source of truth. Approving a job queues it; a background thread uploads its
  files to POD Studio/<2D|3D>/<YYYY-MM>/<SKU>/ (Red jobs: "⚠ Bản quyền/<SKU>/" with an _IP
  suffix) and upserts its row in the "POD Quản lý" Sheet. Network errors retry with backoff.
"""
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
import urllib.request

SCOPE = 'https://www.googleapis.com/auth/drive.file'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
DRIVE = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets'
FOLDER_MIME = 'application/vnd.google-apps.folder'
SHEET_MIME = 'application/vnd.google-apps.spreadsheet'
DEFAULT_CONFIG = Path.home() / '.config/pod-studio/google.json'
ROOT_NAME = 'POD Studio'
RIGHTS_FOLDER = '⚠ Bản quyền'
SHEET_TITLE = 'POD Quản lý'
TABS = ('Jobs', 'Kiểu dáng', 'Bản quyền', 'Prompt', 'Cài đặt')
JOB_COLUMNS = ['SKU', 'Ngày', 'Loại', 'Mặt in / mảnh', 'Chế độ', 'Link nguồn', 'Kiểu dáng', 'Trạng thái',
               'Xem trước', 'Thư mục Drive', 'Chi phí AI (USD)', 'Bản quyền', 'Lý do', 'Ghi chú', 'Đồng bộ lúc']
RIGHTS_COLUMN = JOB_COLUMNS.index('Bản quyền')
CHUNK = 8 * 1024 * 1024          # resumable upload chunk, a multiple of 256 KiB
RETRY_DELAYS = (60, 300, 1800, 7200)
PENDING_TTL = 600                # seconds an unanswered sign-in stays valid
MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
        '.svg': 'image/svg+xml', '.zip': 'application/zip', '.json': 'application/json',
        '.psd': 'image/vnd.adobe.photoshop'}


class GoogleError(RuntimeError):
    def __init__(self, message, status=None, retry=False):
        super().__init__(message)
        self.status, self.retry = status, retry


class AuthError(GoogleError):
    """The saved sign-in no longer works; the user must connect again."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):   # 308 "resume incomplete" is not a redirect here
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(method, url, headers, body, timeout=120):
    """(status, lower-case headers, bytes). Raises GoogleError(retry=True) on network failure."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()
    except (URLError, socket.timeout, ConnectionError, OSError) as exc:
        raise GoogleError(f'Lỗi mạng khi gọi Google: {exc}', retry=True) from None


def folder_url(file_id):
    return f'https://drive.google.com/drive/folders/{file_id}'


def file_url(file_id):
    return f'https://drive.google.com/file/d/{file_id}/view'


def cell(value):
    """A Sheet cell that is never read as a formula (links and names come from the web)."""
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@') else text


def link(url, label):
    if not url:
        return ''
    return '=HYPERLINK("{}";"{}")'.format(url.replace('"', '""'), str(label).replace('"', '""'))


def sheet_range(tab, cells='A1'):
    return "'" + tab.replace("'", "''") + "'!" + cells


def _q(text):
    return text.replace('\\', '\\\\').replace("'", "\\'")


class GoogleConfig:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('POD_GOOGLE_CONFIG') or DEFAULT_CONFIG)
        self.lock = threading.Lock()

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.chmod(temp, 0o600)
        temp.replace(self.path)

    def update(self, **changes):
        with self.lock:
            data = self.load()
            for key, value in changes.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            self.save(data)
            return data

    def set_client(self, raw):
        """raw: the client JSON downloaded from Google Cloud (text or dict)."""
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            client = data.get('installed') or data.get('web') or data
            client_id, secret = client['client_id'].strip(), client['client_secret'].strip()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Không đọc được file client OAuth. Hãy dán nội dung file JSON tải từ Google Cloud.') from None
        if not client_id.endswith('.apps.googleusercontent.com') or not secret or len(client_id) > 200 or len(secret) > 200:
            raise ValueError('Client ID không hợp lệ (phải kết thúc bằng .apps.googleusercontent.com).')
        if data.get('web') and not data.get('installed'):
            raise ValueError('Đây là client loại "Web application". Hãy tạo client loại "Desktop app".')
        current = self.load()
        changes = {'client_id': client_id, 'client_secret': secret}
        if current.get('client_id') != client_id:   # another client: old tokens are useless
            changes.update(refresh_token=None, access_token=None, expires_at=None, needs_reconnect=None)
        self.update(**changes)

    def public(self):
        data = self.load()
        cid = data.get('client_id', '')
        return {'client': bool(cid), 'client_id_hint': (cid[:10] + '…') if cid else None,
                'connected': bool(data.get('refresh_token')) and not data.get('needs_reconnect'),
                'needs_reconnect': bool(data.get('needs_reconnect')),
                'root_url': folder_url(data['root_id']) if data.get('root_id') else None,
                'sheet_url': data.get('sheet_url'), 'scope': SCOPE}


class Google:
    """Thin Drive v3 + Sheets v4 client over an injectable HTTP transport."""

    def __init__(self, config, transport=urllib_transport, clock=time.time):
        self.config = config
        self.transport = transport
        self.clock = clock
        self.pending = {}
        self.lock = threading.Lock()

    # ---- sign-in -------------------------------------------------------
    def start(self, redirect_uri):
        data = self.config.load()
        if not data.get('client_id'):
            raise ValueError('Chưa có client OAuth. Hãy dán file JSON client trước.')
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        state = secrets.token_urlsafe(24)
        now = self.clock()
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if now - v['time'] < PENDING_TTL}
            self.pending[state] = {'verifier': verifier, 'redirect_uri': redirect_uri, 'time': now}
        return AUTH_URL + '?' + urlencode({
            'client_id': data['client_id'], 'redirect_uri': redirect_uri, 'response_type': 'code', 'scope': SCOPE,
            'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': state,
            'access_type': 'offline', 'prompt': 'consent'})

    def finish(self, state, code):
        with self.lock:
            pending = self.pending.pop(state or '', None)
        if not pending or self.clock() - pending['time'] > PENDING_TTL:
            raise ValueError('Phiên đăng nhập Google đã hết hạn hoặc không khớp. Hãy bấm Kết nối lại.')
        data = self.config.load()
        token = self._token_request({'code': code, 'client_id': data['client_id'],
                                     'client_secret': data['client_secret'], 'redirect_uri': pending['redirect_uri'],
                                     'grant_type': 'authorization_code', 'code_verifier': pending['verifier']})
        if not token.get('refresh_token'):
            raise ValueError('Google không trả về refresh token. Hãy thử kết nối lại.')
        self.config.update(refresh_token=token['refresh_token'], access_token=token['access_token'],
                           expires_at=self.clock() + int(token.get('expires_in', 3600)), needs_reconnect=None)
        return self.config.public()

    def disconnect(self):
        data = self.config.load()
        if data.get('refresh_token'):
            try:   # best effort: also revoke on Google's side
                self.transport('POST', REVOKE_URL, {'Content-Type': 'application/x-www-form-urlencoded'},
                               urlencode({'token': data['refresh_token']}).encode())
            except GoogleError:
                pass
        self.config.update(refresh_token=None, access_token=None, expires_at=None, needs_reconnect=None)
        return self.config.public()

    def _token_request(self, form):
        status, _, body = self.transport('POST', TOKEN_URL, {'Content-Type': 'application/x-www-form-urlencoded'},
                                         urlencode(form).encode())
        try:
            payload = json.loads(body or b'{}')
        except ValueError:
            payload = {}
        if status == 200 and payload.get('access_token'):
            return payload
        if status >= 500 or status == 429:
            raise GoogleError(f'Google tạm lỗi ({status}).', status, retry=True)
        if payload.get('error') in ('invalid_grant', 'invalid_client', 'unauthorized_client'):
            self.config.update(needs_reconnect=True)
            raise AuthError('Đăng nhập Google đã hết hạn hoặc bị thu hồi. Hãy bấm Kết nối Google lại.', status)
        raise GoogleError(f'Google từ chối đăng nhập: {payload.get("error_description") or payload.get("error") or status}', status)

    def access_token(self, force=False):
        data = self.config.load()
        if not data.get('refresh_token') or data.get('needs_reconnect'):
            raise AuthError('Chưa kết nối Google.')
        if not force and data.get('access_token') and data.get('expires_at', 0) - 60 > self.clock():
            return data['access_token']
        token = self._token_request({'client_id': data['client_id'], 'client_secret': data['client_secret'],
                                     'refresh_token': data['refresh_token'], 'grant_type': 'refresh_token'})
        self.config.update(access_token=token['access_token'],
                           expires_at=self.clock() + int(token.get('expires_in', 3600)))
        return token['access_token']

    # ---- HTTP ------------------------------------------------------------
    def call(self, method, url, *, params=None, body=None, headers=None, raw=False, ok=(200,)):
        if params:
            url += ('&' if '?' in url else '?') + urlencode(params)
        data = body
        hdrs = dict(headers or {})
        if body is not None and not isinstance(body, bytes):
            data = json.dumps(body, ensure_ascii=False).encode()
            hdrs.setdefault('Content-Type', 'application/json; charset=utf-8')
        for attempt in (0, 1):
            hdrs['Authorization'] = 'Bearer ' + self.access_token(force=attempt == 1)
            status, rheaders, rbody = self.transport(method, url, hdrs, data)
            if status != 401:
                break
        if status in ok:
            if raw:
                return status, rheaders, rbody
            return json.loads(rbody) if rbody else {}
        try:
            reason = json.loads(rbody).get('error', {})
            message = reason.get('message') if isinstance(reason, dict) else str(reason)
        except (ValueError, AttributeError):
            message = rbody[:200].decode('utf-8', 'ignore') if rbody else ''
        if status == 401:
            self.config.update(needs_reconnect=True)
            raise AuthError('Google không chấp nhận đăng nhập. Hãy kết nối lại.', status)
        if status == 403 and 'insufficient' in (message or '').lower():
            self.config.update(needs_reconnect=True)
            raise AuthError('Thiếu quyền Google Drive. Hãy kết nối lại và cho phép quyền.', status)
        retry = status == 429 or status >= 500 or (status == 403 and 'rate' in (message or '').lower())
        raise GoogleError(f'Google lỗi {status}: {message}'[:300], status, retry=retry)

    # ---- Drive -----------------------------------------------------------
    def children(self, parent, folders_only=False):
        query = f"'{_q(parent)}' in parents and trashed=false"
        if folders_only:
            query += f" and mimeType='{FOLDER_MIME}'"
        found, token = {}, None
        while True:
            params = {'q': query, 'fields': 'nextPageToken,files(id,name,mimeType)', 'pageSize': 1000}
            if token:
                params['pageToken'] = token
            page = self.call('GET', DRIVE, params=params)
            for f in page.get('files', []):
                found.setdefault(f['name'], f['id'])
            token = page.get('nextPageToken')
            if not token:
                return found

    def folder(self, name, parent):
        """Find or create a folder; the tool only sees folders it made (drive.file)."""
        query = f"name='{_q(name)}' and mimeType='{FOLDER_MIME}' and trashed=false"
        query += f" and '{_q(parent)}' in parents" if parent else " and 'root' in parents"
        hits = self.call('GET', DRIVE, params={'q': query, 'fields': 'files(id)', 'pageSize': 1}).get('files', [])
        if hits:
            return hits[0]['id']
        meta = {'name': name, 'mimeType': FOLDER_MIME, **({'parents': [parent]} if parent else {})}
        return self.call('POST', DRIVE, params={'fields': 'id'}, body=meta)['id']

    def alive(self, file_id):
        if not file_id:
            return False
        try:
            info = self.call('GET', f'{DRIVE}/{quote(file_id)}', params={'fields': 'id,trashed'})
        except GoogleError as exc:
            if exc.status == 404:
                return False
            raise
        return not info.get('trashed')

    def upload(self, path, name, parent, existing_id=None):
        """Resumable upload in CHUNK pieces; replaces the content of existing_id instead of duplicating."""
        path = Path(path)
        size = path.stat().st_size
        mime = MIME.get(path.suffix.lower(), 'application/octet-stream')
        headers = {'X-Upload-Content-Type': mime, 'X-Upload-Content-Length': str(size)}
        if existing_id:
            _, rheaders, _ = self.call('PATCH', f'{UPLOAD}/{quote(existing_id)}', params={'uploadType': 'resumable'},
                                       body={'name': name}, headers=headers, raw=True)
        else:
            _, rheaders, _ = self.call('POST', UPLOAD, params={'uploadType': 'resumable'},
                                       body={'name': name, 'parents': [parent]}, headers=headers, raw=True)
        session = rheaders.get('location')
        if not session:
            raise GoogleError('Google không mở được phiên tải lên.', retry=True)
        with path.open('rb') as stream:
            offset = 0
            while True:
                chunk = stream.read(CHUNK)
                end = offset + len(chunk) - 1
                rng = f'bytes {offset}-{end}/{size}' if chunk else f'bytes */{size}'
                status, rheaders, rbody = self.transport('PUT', session, {'Content-Range': rng, 'Content-Type': mime}, chunk)
                if status in (200, 201):
                    return json.loads(rbody).get('id') or existing_id
                if status != 308:
                    raise GoogleError(f'Tải lên {name} lỗi {status}.', status, retry=status >= 500 or status == 429)
                offset += len(chunk)

    # ---- Sheet ------------------------------------------------------------
    def create_sheet(self, parent):
        sheet_id = self.call('POST', DRIVE, params={'fields': 'id'},
                             body={'name': SHEET_TITLE, 'mimeType': SHEET_MIME, 'parents': [parent]})['id']
        info = self.call('GET', f'{SHEETS}/{sheet_id}', params={'fields': 'sheets.properties'})
        first = info['sheets'][0]['properties']['sheetId']
        requests = [{'updateSheetProperties': {'properties': {'sheetId': first, 'title': TABS[0],
                                                             'gridProperties': {'frozenRowCount': 1}},
                                               'fields': 'title,gridProperties.frozenRowCount'}}]
        requests += [{'addSheet': {'properties': {'title': t, 'gridProperties': {'frozenRowCount': 1}}}} for t in TABS[1:]]
        colours = {'Xanh': (0.85, 0.94, 0.85), 'Vàng': (1.0, 0.95, 0.75), 'Đỏ': (0.98, 0.82, 0.82)}
        for label, (r, g, b) in colours.items():
            requests.append({'addConditionalFormatRule': {'index': 0, 'rule': {
                'ranges': [{'sheetId': first, 'startRowIndex': 1, 'startColumnIndex': RIGHTS_COLUMN,
                            'endColumnIndex': RIGHTS_COLUMN + 1}],
                'booleanRule': {'condition': {'type': 'TEXT_EQ', 'values': [{'userEnteredValue': label}]},
                                'format': {'backgroundColor': {'red': r, 'green': g, 'blue': b}}}}}})
        self.call('POST', f'{SHEETS}/{sheet_id}:batchUpdate', body={'requests': requests})
        self.call('PUT', f'{SHEETS}/{sheet_id}/values/{quote(sheet_range(TABS[0]))}',
                  params={'valueInputOption': 'RAW'}, body={'values': [JOB_COLUMNS]})
        return sheet_id, f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit'

    def upsert_row(self, sheet_id, row):
        """Rows are keyed by SKU in column A, so re-syncing a job rewrites its row."""
        tab = TABS[0]
        column = self.call('GET', f'{SHEETS}/{sheet_id}/values/{quote(sheet_range(tab, "A:A"))}').get('values', [])
        keys = [r[0] if r else '' for r in column]
        last = chr(ord('A') + len(row) - 1)
        if row[0] in keys[1:]:
            n = keys.index(row[0], 1) + 1
            self.call('PUT', f'{SHEETS}/{sheet_id}/values/{quote(sheet_range(tab, f"A{n}:{last}{n}"))}',
                      params={'valueInputOption': 'USER_ENTERED'}, body={'values': [row]})
            return n
        self.call('POST', f'{SHEETS}/{sheet_id}/values/{quote(sheet_range(tab, "A1"))}:append',
                  params={'valueInputOption': 'USER_ENTERED', 'insertDataOption': 'INSERT_ROWS'},
                  body={'values': [row]})
        return len(keys) + 1

    def write_tabs(self, sheet_id, tabs):
        """Replace whole reference tabs ({title: rows}) in two calls."""
        self.call('POST', f'{SHEETS}/{sheet_id}/values:batchClear', body={'ranges': [sheet_range(t, 'A:Z') for t in tabs]})
        self.call('POST', f'{SHEETS}/{sheet_id}/values:batchUpdate', body={
            'valueInputOption': 'RAW', 'data': [{'range': sheet_range(t), 'values': rows} for t, rows in tabs.items()]})


class DriveSync:
    """Queue of approved jobs to copy to Drive + Sheet, retried on network errors.

    bundle(job_id) -> {'kind', 'month', 'sku', 'red', 'files': [(path, name)], 'row': callable(urls) -> list}
    tabs() -> {tab title: rows} for the reference tabs; on_done(job_id, folder_url) stores the link.
    """

    def __init__(self, google, queue_path, bundle, tabs, on_done, log=None, clock=time.time):
        self.google = google
        self.path = Path(queue_path)
        self.bundle, self.tabs, self.on_done = bundle, tabs, on_done
        self.log = log or (lambda job_id, message: None)
        self.clock = clock
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = None
        self.running = False
        self.last_error = None

    # ---- queue file ----------------------------------------------------
    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temp.replace(self.path)

    def _set(self, job_id, entry):
        with self.lock:
            data = self._load()
            if entry is None:
                data.pop(job_id, None)
            else:
                data[job_id] = entry
            self._save(data)

    def snapshot(self):
        return self._load()

    def enqueue(self, job_ids):
        with self.lock:
            data = self._load()
            for job_id in job_ids:
                data[job_id] = {'state': 'queued', 'attempts': 0, 'next': 0, 'error': None}
            self._save(data)
        self.kick()
        return len(job_ids)

    def status(self):
        data = self._load()
        counts = {}
        for entry in data.values():
            counts[entry['state']] = counts.get(entry['state'], 0) + 1
        return {**self.google.config.public(), 'queue': counts, 'last_error': self.last_error}

    # ---- worker ------------------------------------------------------------
    def kick(self):
        with self.lock:
            self.wake.set()
            if not self.running:
                self.running = True
                self.thread = threading.Thread(target=self._loop, name='drive-sync', daemon=True)
                self.thread.start()

    def _loop(self):
        while True:
            self.wake.clear()
            try:
                wait = self.run_due()
            except Exception as exc:  # noqa: BLE001 - the worker must never die silently
                self.last_error = f'Đồng bộ dừng vì lỗi: {exc}'[:300]
                wait = None
            with self.lock:
                if wait is None and not self.wake.is_set():
                    self.running = False
                    return
            if wait:
                self.wake.wait(timeout=wait)

    def run_due(self):
        """Process due items once. Returns seconds until the next retry, or None when idle/paused."""
        data = self._load()
        now = self.clock()
        due = [j for j, e in data.items() if e['state'] in ('queued', 'retry') and e.get('next', 0) <= now]
        if not due:
            return self._next_wait()
        if not self.google.config.public()['connected']:
            self.last_error = 'Chưa kết nối Google: các job đã duyệt đang chờ trong hàng đợi.'
            return None
        try:
            root, sheet_id = self.ensure_tree()
        except GoogleError as exc:
            return self._failed_batch(due, exc)
        synced = 0
        for job_id in due:
            try:
                url = self.sync_job(job_id, root, sheet_id)
            except AuthError as exc:
                self.last_error = str(exc)
                return None
            except GoogleError as exc:
                self._retry_or_fail(job_id, exc)
                continue
            except (KeyError, FileNotFoundError) as exc:
                self._set(job_id, {'state': 'error', 'attempts': 0, 'next': 0, 'error': f'Không tìm thấy job hoặc file: {exc}'})
                continue
            self._set(job_id, None)
            self.on_done(job_id, url)
            self.log(job_id, 'Đã đồng bộ Google Drive + Sheet.')
            synced += 1
        if synced:
            try:
                self.google.write_tabs(sheet_id, self.tabs())
            except GoogleError as exc:
                self.last_error = f'Chưa cập nhật được các tab phụ của Sheet: {exc}'
        return self._next_wait()

    def _next_wait(self):
        data = self._load().values()
        if any(e['state'] == 'queued' for e in data):
            return 0
        later = [e['next'] for e in data if e['state'] == 'retry']
        return max(1, min(later) - self.clock()) if later else None

    def _failed_batch(self, due, exc):
        self.last_error = str(exc)
        if isinstance(exc, AuthError):
            return None
        for job_id in due:
            self._retry_or_fail(job_id, exc)
        return self._next_wait()

    def _retry_or_fail(self, job_id, exc):
        entry = self._load().get(job_id, {'attempts': 0})
        attempts = entry.get('attempts', 0) + 1
        if exc.retry:
            delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
            self._set(job_id, {'state': 'retry', 'attempts': attempts, 'next': self.clock() + delay, 'error': str(exc)})
            self.log(job_id, f'Đồng bộ Drive lỗi mạng, thử lại sau {delay // 60} phút: {exc}'[:300])
        else:
            self._set(job_id, {'state': 'error', 'attempts': attempts, 'next': 0, 'error': str(exc)})
            self.log(job_id, f'Đồng bộ Drive lỗi: {exc}'[:300])
        self.last_error = str(exc)

    def ensure_tree(self):
        """Root folder and Sheet, cached in the config and re-made if the user deleted them."""
        g = self.google
        data = g.config.load()
        root = data.get('root_id')
        if not g.alive(root):
            root = g.folder(ROOT_NAME, None)
            g.config.update(root_id=root, sheet_id=None, sheet_url=None, folders=None)
            data = g.config.load()
        sheet_id = data.get('sheet_id')
        if not g.alive(sheet_id):
            sheet_id, sheet_url = g.create_sheet(root)
            g.config.update(sheet_id=sheet_id, sheet_url=sheet_url)
        self.last_error = None
        return root, sheet_id

    def _path_folder(self, root, names):
        """Folder ids are cached per path; a cached id that went missing is looked up again."""
        g = self.google
        key = '/'.join(names)
        cached = (g.config.load().get('folders') or {}).get(key)
        if cached and g.alive(cached):
            return cached
        parent = root
        for name in names:
            parent = g.folder(name, parent)
        folders = dict(g.config.load().get('folders') or {})
        folders[key] = parent
        g.config.update(folders=folders)
        return parent

    def sync_job(self, job_id, root, sheet_id):
        b = self.bundle(job_id)
        names = [RIGHTS_FOLDER] if b['red'] else [b['kind'].upper(), b['month']]
        parent = self._path_folder(root, names)
        target = self.google.folder(b['sku'], parent)
        existing = self.google.children(target)
        ids = {}
        for path, name in b['files']:
            if b['red']:
                stem, dot, ext = name.rpartition('.')
                name = f'{stem}_IP.{ext}' if dot else name + '_IP'
            ids[name] = self.google.upload(path, name, target, existing.get(name))
        url = folder_url(target)
        preview = next((i for n, i in ids.items() if 'preview' in n), None) or next(iter(ids.values()), None)
        row = b['row']({'folder': url, 'preview': file_url(preview) if preview else None,
                        'synced': datetime.now().strftime('%Y-%m-%d %H:%M')})
        self.google.upsert_row(sheet_id, row)
        return url
