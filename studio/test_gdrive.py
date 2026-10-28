"""Tests for the Google Drive + Sheet sync against an in-memory fake of the Google APIs (no network)."""
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gdrive  # noqa: E402

CLIENT = {'installed': {'client_id': '123-abc.apps.googleusercontent.com', 'client_secret': 'shh',
                        'redirect_uris': ['http://localhost']}}


class FakeGoogle:
    """Just enough of OAuth, Drive v3 and Sheets v4 for the sync code."""

    def __init__(self):
        self.files = {}      # id -> {name, mimeType, parents, data, trashed}
        self.sheets = {}     # id -> {tab: rows}
        self.sessions = {}
        self.calls = []
        self.down = False
        self.revoked = False
        self.grant_ok = True
        self.n = 0

    def _id(self):
        self.n += 1
        return f'f{self.n}'

    def __call__(self, method, url, headers, body):
        if self.down:
            raise gdrive.GoogleError('Lỗi mạng (giả lập)', retry=True)
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        path = unquote(parts.path)
        self.calls.append((method, path))
        if url == gdrive.TOKEN_URL:
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            if form['grant_type'] == 'authorization_code':
                assert form['code_verifier'] and form['code'] == 'good-code'
                return self.ok({'access_token': 'at1', 'refresh_token': 'rt1', 'expires_in': 3600})
            if not self.grant_ok:
                return 400, {}, json.dumps({'error': 'invalid_grant'}).encode()
            return self.ok({'access_token': 'at2', 'expires_in': 3600})
        if url.startswith(gdrive.REVOKE_URL):
            self.revoked = True
            return 200, {}, b''
        assert headers.get('Authorization', '').startswith('Bearer ') or path.startswith('/upload/session')
        if path == '/drive/v3/files' and method == 'GET':
            return self.ok({'files': self.query(q['q'])})
        if path == '/drive/v3/files' and method == 'POST':
            meta = json.loads(body)
            fid = self._id()
            self.files[fid] = {**meta, 'parents': meta.get('parents', ['root']), 'trashed': False}
            if meta['mimeType'] == gdrive.SHEET_MIME:
                self.sheets[fid] = {'Sheet1': []}
            return self.ok({'id': fid})
        m = re.fullmatch(r'/drive/v3/files/(\w+)', path)
        if m and method == 'GET':
            f = self.files.get(m.group(1))
            return self.ok({'id': m.group(1), 'trashed': f['trashed']}) if f else (404, {}, b'{"error":{"message":"nf"}}')
        if path.startswith('/upload/drive/v3/files'):
            meta = json.loads(body)
            sid = f'/upload/session/{len(self.sessions)}'
            fid = path.rsplit('/', 1)[1] if method == 'PATCH' else None
            self.sessions[sid] = {'meta': meta, 'id': fid, 'data': b''}
            return 200, {'location': 'https://www.googleapis.com' + sid}, b''
        if path.startswith('/upload/session/'):
            s = self.sessions[path]
            s['data'] += body
            total = int(headers['Content-Range'].rsplit('/', 1)[1])
            if len(s['data']) < total:
                return 308, {'range': f'bytes=0-{len(s["data"]) - 1}'}, b''
            if s['id']:
                self.files[s['id']].update(data=s['data'], name=s['meta']['name'])
                return self.ok({'id': s['id']})
            fid = self._id()
            self.files[fid] = {'name': s['meta']['name'], 'mimeType': 'file', 'parents': s['meta']['parents'],
                               'data': s['data'], 'trashed': False}
            return self.ok({'id': fid})
        return self.sheet_call(method, path, q, body)

    def sheet_call(self, method, path, q, body):
        m = re.fullmatch(r'/v4/spreadsheets/(\w+)(.*)', path)
        sid, rest = m.group(1), m.group(2)
        book = self.sheets[sid]
        if rest == '' and method == 'GET':
            return self.ok({'sheets': [{'properties': {'sheetId': i, 'title': t}} for i, t in enumerate(book)]})
        if rest == ':batchUpdate':
            for r in json.loads(body)['requests']:
                if 'updateSheetProperties' in r:
                    title = r['updateSheetProperties']['properties']['title']
                    old = list(book)[r['updateSheetProperties']['properties']['sheetId']]
                    book[title] = book.pop(old)
                elif 'addSheet' in r:
                    book[r['addSheet']['properties']['title']] = []
            return self.ok({})
        if rest == '/values:batchClear':
            for rng in json.loads(body)['ranges']:
                book[self.tab(rng)] = book[self.tab(rng)][:0]
            return self.ok({})
        if rest == '/values:batchUpdate':
            for d in json.loads(body)['data']:
                book[self.tab(d['range'])] = d['values']
            return self.ok({})
        m = re.fullmatch(r'/values/(.+?)(:append)?', rest)
        rng, append = m.group(1), m.group(2)
        tab, cells = self.tab(rng), rng.split('!')[1]
        rows = book[tab]
        if append:
            rows.extend(json.loads(body)['values'])
        elif method == 'GET':
            return self.ok({'values': [[r[0]] for r in rows]})
        else:
            start = int(re.match(r'[A-Z]+(\d+)', cells).group(1)) - 1
            values = json.loads(body)['values']
            while len(rows) < start + len(values):
                rows.append([])
            rows[start:start + len(values)] = values
        return self.ok({})

    @staticmethod
    def tab(rng):
        return rng.split('!')[0].strip("'").replace("''", "'")

    @staticmethod
    def ok(data):
        return 200, {}, json.dumps(data).encode()

    def query(self, q):
        parent = re.search(r"'([^']+)' in parents", q).group(1)
        name = re.search(r"name='([^']+)'", q)
        out = []
        for fid, f in self.files.items():
            if f['trashed'] or parent not in f['parents']:
                continue
            if name and f['name'] != name.group(1):
                continue
            if 'folder' in q and f['mimeType'] != gdrive.FOLDER_MIME:
                continue
            out.append({'id': fid, 'name': f['name'], 'mimeType': f['mimeType']})
        return out

    def path_of(self, fid):
        names = []
        while fid in self.files:
            names.append(self.files[fid]['name'])
            fid = self.files[fid]['parents'][0]
        return '/'.join(reversed(names))

    def find(self, path):
        return [fid for fid in self.files if self.path_of(fid) == path]


class GoogleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fake = FakeGoogle()
        self.config = gdrive.GoogleConfig(self.root / 'cfg' / 'google.json')
        self.google = gdrive.Google(self.config, self.fake)

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self):
        self.config.set_client(json.dumps(CLIENT))
        url = self.google.start('http://127.0.0.1:8765/google/callback')
        state = parse_qs(urlsplit(url).query)['state'][0]
        self.google.finish(state, 'good-code')

    def test_client_file_is_private_and_secret_never_shown(self):
        with self.assertRaises(ValueError):
            self.config.set_client('{"installed": {"client_id": "x", "client_secret": "y"}}')
        with self.assertRaises(ValueError):
            self.config.set_client('not json')
        with self.assertRaises(ValueError):
            self.config.set_client(json.dumps({'web': CLIENT['installed']}))
        self.config.set_client(json.dumps(CLIENT))
        self.assertEqual(stat.S_IMODE(os.stat(self.config.path).st_mode), 0o600)
        public = self.config.public()
        self.assertTrue(public['client'])
        self.assertFalse(public['connected'])
        self.assertNotIn('shh', json.dumps(public))

    def test_sign_in_uses_pkce_and_checks_state(self):
        self.config.set_client(CLIENT)
        url = self.google.start('http://127.0.0.1:8765/google/callback')
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['scope'], [gdrive.SCOPE])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['access_type'], ['offline'])
        with self.assertRaises(ValueError):
            self.google.finish('wrong-state', 'good-code')
        self.google.finish(query['state'][0], 'good-code')
        self.assertTrue(self.config.public()['connected'])
        with self.assertRaises(ValueError):   # a state is used once
            self.google.finish(query['state'][0], 'good-code')
        self.google.disconnect()
        self.assertTrue(self.fake.revoked)
        self.assertFalse(self.config.public()['connected'])

    def test_cells_never_become_formulas(self):
        self.assertEqual(gdrive.cell('=IMPORTXML("x")'), '\'=IMPORTXML("x")')
        self.assertEqual(gdrive.cell('Áo đẹp'), 'Áo đẹp')
        self.assertEqual(gdrive.cell(1.5), 1.5)
        self.assertEqual(gdrive.link('https://a/"b', 'X'), '=HYPERLINK("https://a/""b";"X")')

    def make_job(self, sku, red=False, kind='2d'):
        folder = self.root / sku
        folder.mkdir()
        (folder / 'front.png').write_bytes(b'P' * (gdrive.CHUNK + 10))   # forces two upload chunks
        (folder / 'front-preview.jpg').write_bytes(b'J' * 100)
        (folder / 'source.jpg').write_bytes(b'S' * 50)
        files = [(folder / 'source.jpg', 'source.jpg'), (folder / 'front.png', 'front.png'),
                 (folder / 'front-preview.jpg', 'front-preview.jpg')]
        return {'kind': kind, 'month': '2026-10', 'sku': sku, 'red': red, 'files': files,
                'row': lambda urls: [sku, '2026-10-28', kind.upper(), 'Trước', 'Local', gdrive.cell('=evil()'), '',
                                     'Đã duyệt', gdrive.link(urls['preview'], 'Xem'), gdrive.link(urls['folder'], 'Mở'),
                                     0, 'Đỏ' if red else 'Xanh', '', '', urls['synced']]}

    def make_sync(self, jobs, done):
        clock = [1000.0]
        sync = gdrive.DriveSync(self.google, self.root / 'queue.json', lambda j: jobs[j],
                                lambda: {'Cài đặt': [['Mục', 'Giá trị'], ['a', 1]]},
                                lambda j, url: done.__setitem__(j, url), clock=lambda: clock[0])
        return sync, clock

    def test_approved_job_lands_in_month_folder_and_sheet_once(self):
        self.connect()
        jobs = {'j1': self.make_job('2D-261028-001')}
        done = {}
        sync, _ = self.make_sync(jobs, done)
        sync._set('j1', {'state': 'queued', 'attempts': 0, 'next': 0, 'error': None})
        self.assertIsNone(sync.run_due())
        self.assertIn('j1', done)
        files = self.fake.find('POD Studio/2D/2026-10/2D-261028-001/front.png')
        self.assertEqual(len(files), 1)
        self.assertEqual(len(self.fake.files[files[0]]['data']), gdrive.CHUNK + 10)
        sheet_id = self.config.load()['sheet_id']
        book = self.fake.sheets[sheet_id]
        self.assertEqual(list(book), list(gdrive.TABS))
        self.assertEqual(book['Jobs'][0], gdrive.JOB_COLUMNS)
        self.assertEqual(book['Jobs'][1][0], '2D-261028-001')
        self.assertEqual(book['Jobs'][1][5], "'=evil()")
        self.assertIn('HYPERLINK', book['Jobs'][1][9])
        self.assertEqual(book['Cài đặt'], [['Mục', 'Giá trị'], ['a', 1]])
        self.assertEqual(self.fake.path_of(sheet_id), 'POD Studio/POD Quản lý')
        # Re-syncing rewrites the same row and the same files.
        sync.enqueue(['j1'])
        sync.thread.join(5)
        self.assertEqual(len(book['Jobs']), 2)
        self.assertEqual(len(self.fake.find('POD Studio/2D/2026-10/2D-261028-001/front.png')), 1)
        self.assertEqual(sync.snapshot(), {})

    def test_red_job_goes_to_rights_folder_with_ip_suffix(self):
        self.connect()
        jobs = {'j2': self.make_job('2D-261028-002', red=True)}
        sync, _ = self.make_sync(jobs, {})
        sync._set('j2', {'state': 'queued', 'attempts': 0, 'next': 0, 'error': None})
        sync.run_due()
        self.assertEqual(len(self.fake.find('POD Studio/⚠ Bản quyền/2D-261028-002/front_IP.png')), 1)
        self.assertFalse(self.fake.find('POD Studio/2D/2026-10/2D-261028-002'))

    def test_network_error_retries_later_then_succeeds(self):
        self.connect()
        jobs = {'j3': self.make_job('3D-261028-001', kind='3d')}
        done = {}
        sync, clock = self.make_sync(jobs, done)
        sync._set('j3', {'state': 'queued', 'attempts': 0, 'next': 0, 'error': None})
        self.fake.down = True
        wait = sync.run_due()
        self.assertEqual(wait, gdrive.RETRY_DELAYS[0])
        self.assertEqual(sync.snapshot()['j3']['state'], 'retry')
        self.assertEqual(sync.run_due(), gdrive.RETRY_DELAYS[0])   # not due yet: nothing sent
        self.fake.down = False
        clock[0] += gdrive.RETRY_DELAYS[0] + 1
        self.assertIsNone(sync.run_due())
        self.assertIn('j3', done)
        self.assertTrue(self.fake.find('POD Studio/3D/2026-10/3D-261028-001/front.png'))

    def test_revoked_sign_in_pauses_queue(self):
        self.connect()
        self.config.update(expires_at=0)
        self.fake.grant_ok = False
        sync, _ = self.make_sync({'j4': self.make_job('2D-261028-004')}, {})
        sync._set('j4', {'state': 'queued', 'attempts': 0, 'next': 0, 'error': None})
        self.assertIsNone(sync.run_due())
        self.assertTrue(self.config.public()['needs_reconnect'])
        self.assertEqual(sync.snapshot()['j4']['state'], 'queued')
        self.assertIn('Kết nối', sync.status()['last_error'])

    def test_deleted_root_folder_is_made_again(self):
        self.connect()
        jobs = {'a': self.make_job('2D-261028-005'), 'b': self.make_job('2D-261028-006')}
        sync, _ = self.make_sync(jobs, {})
        sync._set('a', {'state': 'queued', 'attempts': 0, 'next': 0, 'error': None})
        sync.run_due()
        old_root = self.config.load()['root_id']
        self.fake.files[old_root]['trashed'] = True
        sync._set('b', {'state': 'queued', 'attempts': 0, 'next': 0, 'error': None})
        sync.run_due()
        self.assertNotEqual(self.config.load()['root_id'], old_root)
        self.assertTrue(self.fake.find('POD Studio/2D/2026-10/2D-261028-006/front.png'))


if __name__ == '__main__':
    unittest.main()
