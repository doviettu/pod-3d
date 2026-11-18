"""Tests for the nightly backup and the "Chất lượng cao" switch (no network, fake model/Google)."""
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import backup  # noqa: E402
from pod3d import quality, upscale  # noqa: E402


class BackupTests(unittest.TestCase):
    def test_archive_has_state_but_no_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runtime, templates = tmp / 'runtime', tmp / 'templates'
            (runtime / 'online').mkdir(parents=True)
            (runtime / 'online' / 'access.json').write_text('{"password":"x"}')
            (runtime / 'jobs').mkdir()
            (runtime / 'jobs' / 'big.png').write_bytes(b'0' * 10)
            (runtime / 'mockups' / 'm1').mkdir(parents=True)
            (runtime / 'mockups' / 'm1' / 'preset.json').write_text('{}')
            (runtime / 'mockups' / 'm1' / 'token').write_text('secret')
            (runtime / 'rights.json').write_text('{}')
            (templates / 'hoodie-x').mkdir(parents=True)
            (templates / 'hoodie-x' / 'template.json').write_text('{}')
            db = sqlite3.connect(runtime / 'studio.db')
            db.execute('create table jobs (id text)')
            db.execute("insert into jobs values ('a')")
            db.commit()
            db.close()
            archive = backup.make_archive(tmp / 'out', runtime, templates, stamp='20261118-0200')
            names = tarfile.open(archive).getnames()
            self.assertIn('runtime/studio.db', names)
            self.assertIn('runtime/rights.json', names)
            self.assertIn('runtime/mockups/m1/preset.json', names)
            self.assertIn('templates/hoodie-x/template.json', names)
            self.assertFalse([n for n in names if 'online' in n or 'jobs' in n or n.endswith('/token')])

    def test_upload_prunes_old_archives(self):
        class FakeGoogle:
            def __init__(self):
                self.files = {f'{backup.PREFIX}2026110{i}-0200.tar.gz': f'id{i}' for i in range(1, 6)}
                self.deleted = []
                self.config = type('C', (), {'load': lambda s: {'root_id': 'root'}})()

            def alive(self, fid):
                return True

            def folder(self, name, parent):
                return 'backups'

            def upload(self, path, name, parent):
                self.files[name] = 'new'
                return 'new'

            def children(self, folder):
                return dict(self.files)

            def call(self, method, url, **kw):
                self.deleted.append(url.rsplit('/', 1)[1])
                return 204, {}, b''

        google = FakeGoogle()
        archive = Path(tempfile.mkdtemp()) / f'{backup.PREFIX}20261118-0200.tar.gz'
        archive.write_bytes(b'x')
        backup.upload(google, archive, keep=3)
        self.assertEqual(sorted(google.deleted), ['id1', 'id2', 'id3'])


class QualityTests(unittest.TestCase):
    def test_tta_matches_plain_for_equivariant_model(self):
        import torch

        class Up:
            scale = 2

            def __init__(self):
                self.model = torch.nn.Conv2d(3, 3, 1)

            def __call__(self, x):
                return torch.nn.functional.interpolate(x, scale_factor=2, mode='nearest')

        img = (np.random.default_rng(0).random((50, 70, 3)) * 255).astype(np.uint8)
        plain = upscale._run_model(Up(), img)
        with quality.hq_scope(True):
            self.assertTrue(quality.enabled())
            hq = upscale._run_model(Up(), img)
        self.assertFalse(quality.enabled())
        self.assertEqual(plain.shape, (100, 140, 3))
        self.assertLessEqual(int(np.abs(plain.astype(int) - hq).max()), 1)


if __name__ == '__main__':
    unittest.main()
