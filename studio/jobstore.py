"""SQLite job index for POD Studio v2: one row per job, 2D and 3D alike.

Files stay in runtime/jobs/<id>/; the database is the list the UI, the
Drive sync and the Sheet read from. Legacy 3D jobs keep their report.json
and are linked here by legacy_id.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid

KINDS = ('2d', '3d')
MODES = ('local', 'ai')
STATUSES = ('queued', 'running', 'review', 'approved', 'failed', 'interrupted')
RIGHTS = ('unknown', 'green', 'yellow', 'red')
JSON_FIELDS = ('params', 'outputs', 'logs')

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    sku TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    sides TEXT,
    source_url TEXT,
    source_sha256 TEXT,
    rights TEXT NOT NULL DEFAULT 'unknown',
    rights_reason TEXT,
    params TEXT NOT NULL DEFAULT '{}',
    outputs TEXT NOT NULL DEFAULT '{}',
    logs TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    legacy_id TEXT,
    drive_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS jobs_sha ON jobs(source_sha256);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class JobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript(SCHEMA)
            # A restart drops the in-memory queue; never resubmit silently (AI jobs cost money).
            self.db.execute("UPDATE jobs SET status='interrupted', error=?, updated_at=? "
                            "WHERE status IN ('queued','running')",
                            ('Ứng dụng đã khởi động lại; hãy chạy lại công việc.', now()))

    def _row(self, row):
        if row is None:
            raise KeyError('job')
        job = dict(row)
        for field in JSON_FIELDS:
            job[field] = json.loads(job[field])
        return job

    def _sku(self, kind):
        day = datetime.now().strftime('%y%m%d')
        prefix = f'{kind.upper()}-{day}-'
        row = self.db.execute('SELECT sku FROM jobs WHERE sku LIKE ? ORDER BY sku DESC LIMIT 1',
                              (prefix + '%',)).fetchone()
        number = int(row['sku'].rsplit('-', 1)[1]) + 1 if row else 1
        return f'{prefix}{number:03d}'

    def create(self, kind, mode, *, sides=None, source_url=None, source_sha256=None, params=None):
        if kind not in KINDS or mode not in MODES:
            raise ValueError('Loại công việc không hợp lệ.')
        job_id = uuid.uuid4().hex
        stamp = now()
        with self.lock, self.db:
            sku = self._sku(kind)
            self.db.execute(
                'INSERT INTO jobs (id, sku, kind, mode, status, sides, source_url, source_sha256, params,'
                ' created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (job_id, sku, kind, mode, 'queued', sides, source_url, source_sha256,
                 json.dumps(params or {}, ensure_ascii=False), stamp, stamp))
        return self.get(job_id)

    def get(self, job_id):
        with self.lock:
            return self._row(self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def list(self, limit=100, kind=None):
        query, args = 'SELECT * FROM jobs', []
        if kind:
            query += ' WHERE kind=?'
            args.append(kind)
        query += ' ORDER BY created_at DESC, sku DESC LIMIT ?'
        args.append(int(limit))
        with self.lock:
            return [self._row(r) for r in self.db.execute(query, args).fetchall()]

    def find_by_sha(self, sha, kind):
        with self.lock:
            rows = self.db.execute("SELECT * FROM jobs WHERE source_sha256=? AND kind=? AND status IN "
                                   "('review','approved') ORDER BY created_at DESC", (sha, kind)).fetchall()
            return [self._row(r) for r in rows]

    def update(self, job_id, **changes):
        if not changes:
            return self.get(job_id)
        if 'status' in changes and changes['status'] not in STATUSES:
            raise ValueError('Trạng thái không hợp lệ.')
        if 'rights' in changes and changes['rights'] not in RIGHTS:
            raise ValueError('Mức bản quyền không hợp lệ.')
        allowed = {'status', 'sides', 'rights', 'rights_reason', 'params', 'outputs', 'error',
                   'cost_usd', 'legacy_id', 'drive_url', 'source_url', 'source_sha256'}
        if not set(changes) <= allowed:
            raise ValueError('Trường không hợp lệ: ' + ', '.join(sorted(set(changes) - allowed)))
        values = {k: json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v for k, v in changes.items()}
        values['updated_at'] = now()
        assignments = ', '.join(f'{k}=?' for k in values)
        with self.lock, self.db:
            cur = self.db.execute(f'UPDATE jobs SET {assignments} WHERE id=?', (*values.values(), job_id))
            if cur.rowcount != 1:
                raise KeyError('job')
        return self.get(job_id)

    def log(self, job_id, message):
        with self.lock, self.db:
            row = self.db.execute('SELECT logs FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError('job')
            logs = json.loads(row['logs'])[-199:] + [{'at': now(), 'message': str(message)[:500]}]
            self.db.execute('UPDATE jobs SET logs=?, updated_at=? WHERE id=?',
                            (json.dumps(logs, ensure_ascii=False), now(), job_id))

    def close(self):
        with self.lock:
            self.db.close()
