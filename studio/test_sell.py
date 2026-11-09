"""Tests for the print check (preflight), the real product photo and the listing photo set."""
import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import listing  # noqa: E402
import preflight  # noqa: E402
from test_v2 import FakeEngine, jpeg, shirt  # noqa: E402
from test_gdrive import FakeGoogle  # noqa: E402
from v2 import StudioV2  # noqa: E402


def png(path, rgba, dpi=300):
    Image.fromarray(rgba).save(path, dpi=(dpi, dpi))
    return path


def clean_art(size=(4500, 5400)):
    art = np.zeros((size[1], size[0], 4), np.uint8)
    cv2.circle(art, (size[0] // 2, size[1] // 2), 1200, (200, 30, 30, 255), -1)
    return art


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.profiles = preflight.profiles()

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_file_passes(self):
        report = preflight.run([('front', png(self.dir / 'f.png', clean_art()), None)], 'dtg')
        self.assertEqual(report['status'], 'ok', report)
        self.assertEqual(report['files'][0]['stats']['dpi'], 300)

    def test_wrong_size_low_dpi_and_missing_dpi(self):
        small = png(self.dir / 's.png', clean_art((1500, 1800)), dpi=72)
        result = preflight.check_file(small, self.profiles['dtg'])
        self.assertEqual(result['status'], 'error')
        texts = ' '.join(i['text'] for i in result['items'])
        self.assertIn('4500 × 5400', texts)
        self.assertIn('72 DPI', texts)
        Image.fromarray(clean_art()).save(self.dir / 'n.png')
        result = preflight.check_file(self.dir / 'n.png', self.profiles['dtg'])
        self.assertIn('không ghi DPI', result['items'][0]['text'])

    def test_halo_thin_lines_and_specks_warn(self):
        art = clean_art()
        art[..., 3][(art[..., 3] == 0)] = 0
        blurred = cv2.GaussianBlur(art[..., 3], (0, 0), 40)          # soft halo all around the circle
        art[..., 3] = np.maximum(art[..., 3], blurred)
        art[..., :3][blurred > 0] = (200, 30, 30)
        cv2.line(art, (100, 100), (4400, 100), (0, 0, 0, 255), 2)      # hairline, far below 0.5 mm
        for i in range(10):
            art[300 + i * 40, 300:303, :] = (0, 0, 0, 255)             # dust specks
        result = preflight.check_file(png(self.dir / 'h.png', art), self.profiles['dtg'])
        texts = ' '.join(i['text'] for i in result['items'])
        self.assertEqual(result['status'], 'warn', result)
        self.assertIn('bán trong suốt', texts)
        self.assertIn('nét mảnh', texts)
        self.assertIn('chấm nhỏ', texts)

    def test_aop_needs_full_coverage_and_template_size(self):
        rgba = np.full((620, 1000, 4), 255, np.uint8)
        rgba[:, :50, 3] = 0
        report = preflight.run([('Hem', png(self.dir / 'hem.png', rgba), [1000, 600])], 'aop')
        texts = ' '.join(i['text'] for i in report['files'][0]['items'])
        self.assertEqual(report['status'], 'error')
        self.assertIn('trong suốt', texts)
        self.assertIn('1000 × 600', texts)

    def test_empty_and_unknown_profile(self):
        empty = png(self.dir / 'e.png', np.zeros((5400, 4500, 4), np.uint8))
        self.assertEqual(preflight.run([('front', empty, None)], 'dtg')['status'], 'error')
        with self.assertRaises(ValueError):
            preflight.run([], 'nope')


class ListingTests(unittest.TestCase):
    def test_set_sizes_order_and_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            noisy = np.random.default_rng(1).integers(0, 255, (3000, 2400, 3), np.uint8)   # hard to compress
            Image.fromarray(noisy).save(tmp / 'real.jpg', quality=95)
            Image.fromarray(noisy[:900, :900]).save(tmp / 'mock.jpg')
            png(tmp / 'front.png', clean_art())
            result = listing.build(tmp / 'out', tmp / 'real.jpg', [tmp / 'mock.jpg'],
                                   [('front', tmp / 'front.png', ['trang', 'den'])])
            names = [f['file'] for f in result['files']]
            self.assertEqual(names[0], '01-anh-that.jpg')
            self.assertEqual(len(names), 4)
            for name in names:
                path = tmp / 'out' / name
                self.assertLessEqual(path.stat().st_size, listing.MAX_BYTES)
                with Image.open(path) as image:
                    self.assertEqual(image.size, (2000, 2000))
            with zipfile.ZipFile(tmp / 'out' / result['zip']) as archive:
                self.assertEqual(set(archive.namelist()), set(names) | {'GHI-CHU.txt'})

    def test_refuses_without_real_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                listing.build(Path(tmp) / 'out', None)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = FakeEngine()
        self.v2 = StudioV2(self.engine, self.tmp.name, google_config=Path(self.tmp.name) / 'google.json',
                           google_transport=FakeGoogle())

    def tearDown(self):
        self.engine.executor.shutdown(wait=True)
        self.v2.store.close()
        self.tmp.cleanup()

    def test_2d_job_gets_preflight_then_real_photo_then_listing(self):
        image = base64.b64encode(jpeg(np.concatenate([shirt((240, 200, 30), 'F'), shirt((30, 200, 240), 'B')], 1))).decode()
        sid = self.v2.inspect({'sources': [{'image_base64': image}]})['sources'][0]['source_id']
        job = self.v2.submit({'kind': '2d', 'sources': [{'source_id': sid}]})
        self.engine.executor.submit(lambda: None).result()
        done = self.v2.get(job['id'])
        self.assertEqual(done['status'], 'review', done.get('error'))
        report = done['extras']['preflight']
        self.assertEqual(report['profile'], 'dtg')
        self.assertEqual({f['label'] for f in report['files']}, {'front', 'back'})
        self.assertEqual(self.v2.preflight(job['id'], {'profile': 'printify'})['extras']['preflight']['profile'], 'printify')

        with self.assertRaises(ValueError):
            self.v2.listing(job['id'])                                  # no real photo yet
        tiny = io.BytesIO()
        Image.new('RGB', (600, 600), 'white').save(tiny, 'JPEG')
        with self.assertRaises(ValueError):
            self.v2.real_photo(job['id'], {'image_base64': base64.b64encode(tiny.getvalue()).decode()})
        photo = io.BytesIO()
        Image.new('RGB', (2400, 2400), (90, 120, 160)).save(photo, 'JPEG')
        with_photo = self.v2.real_photo(job['id'], {'image_base64': base64.b64encode(photo.getvalue()).decode()})
        self.assertEqual(with_photo['extras']['real_photo']['file'], 'real-photo.jpg')
        self.assertIn('preflight', with_photo['extras'])               # earlier extras are kept
        built = self.v2.listing(job['id'])['extras']['listing']
        self.assertEqual(built['files'][0]['file'], '01-anh-that.jpg')
        # 1 real + 1 mockup + 4 colours of the front + 2 of the back
        self.assertEqual(len(built['files']), 8)
        self.assertTrue(self.v2.file_path(job['id'], 'listing/listing.zip').is_file())
        self.assertIn('real-photo.jpg', [n for _, n in self.v2._drive_bundle(job['id'])['files']])


if __name__ == '__main__':
    unittest.main()
