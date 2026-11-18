"""Nightly backup of the Studio's own state to Google Drive ("POD Studio/Sao lưu").

What goes in (small, not elsewhere): a consistent copy of runtime/studio.db, rights/budget/ledger/
Drive-queue files, mockup presets, fonts, redesign scenes, and studio/templates (incl. templates made on
the page). Left out: job files (approved results are already on Drive; the rest can be made again),
logs, caches, and every secret (passwords, tokens, API keys).
Keeps the newest KEEP archives on Drive and deletes older ones it made itself.

    python studio/backup.py            # run once (cron: see deploy/oracle/README.md)
    python studio/backup.py --local    # only write the archive to runtime/backups/, no upload
"""
import argparse
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
RUNTIME = HERE / 'runtime'
FOLDER = 'Sao lưu'
PREFIX = 'pod-studio-backup-'
KEEP = 14
FILES = ['rights.json', 'ai-budget.json', 'ai-ledger.jsonl', 'drive-queue.json']
DIRS = ['mockups', 'fonts', 'redesign']
SECRET_NAMES = {'access.json', 'token', 'google.json', 'mail.json', 'settings.json', 'openai-settings.json'}


def make_archive(out_dir, runtime=RUNTIME, templates=HERE / 'templates', stamp=None):
    stamp = stamp or datetime.now().strftime('%Y%m%d-%H%M')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f'{PREFIX}{stamp}.tar.gz'
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(runtime) / 'studio.db'
        snapshot = Path(tmp) / 'studio.db'
        if db.is_file():
            src = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            dst = sqlite3.connect(snapshot)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()

        def keep(info):
            return None if Path(info.name).name in SECRET_NAMES else info

        with tarfile.open(target, 'w:gz') as tar:
            if snapshot.is_file():
                tar.add(snapshot, 'runtime/studio.db')
            for name in FILES:
                if (Path(runtime) / name).is_file():
                    tar.add(Path(runtime) / name, f'runtime/{name}', filter=keep)
            for name in DIRS:
                if (Path(runtime) / name).is_dir():
                    tar.add(Path(runtime) / name, f'runtime/{name}', filter=keep)
            if Path(templates).is_dir():
                tar.add(templates, 'templates', filter=keep)
    return target


def upload(google, archive, keep=KEEP):
    """Upload to POD Studio/Sao lưu and prune older archives. Returns the Drive file id."""
    import gdrive
    root = google.config.load().get('root_id')
    if not google.alive(root):
        root = google.folder(gdrive.ROOT_NAME, None)
    folder = google.folder(FOLDER, root)
    file_id = google.upload(archive, archive.name, folder)
    old = sorted(name for name in google.children(folder) if name.startswith(PREFIX))[:-keep]
    found = google.children(folder)
    for name in old:
        google.call('DELETE', f'{gdrive.DRIVE}/{found[name]}', ok=(200, 204), raw=True)
    return file_id


def main():
    parser = argparse.ArgumentParser(description='Sao lưu POD Studio lên Google Drive')
    parser.add_argument('--local', action='store_true', help='chỉ tạo file, không tải lên')
    args = parser.parse_args()
    out = RUNTIME / 'backups'
    archive = make_archive(out)
    for old in sorted(out.glob(PREFIX + '*.tar.gz'))[:-3]:   # keep 3 copies on the server too
        old.unlink()
    print(f'Đã tạo {archive} ({archive.stat().st_size / 1e6:.1f} MB)')
    if args.local:
        return
    import gdrive
    google = gdrive.Google(gdrive.GoogleConfig())
    if not google.config.load().get('refresh_token'):
        raise SystemExit('Google Drive chưa kết nối: chỉ giữ bản trên máy chủ.')
    upload(google, archive)
    print('Đã tải lên Google Drive: POD Studio/' + FOLDER)


if __name__ == '__main__':
    main()
