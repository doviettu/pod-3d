"""Tests for the sales-mockup renderer, the preset library and the job hookup."""
import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mockup_render as mr  # noqa: E402
from mockups import MockupLibrary  # noqa: E402
from test_gdrive import FakeGoogle  # noqa: E402
from test_v2 import FakeEngine, jpeg, shirt  # noqa: E402
from v2 import StudioV2  # noqa: E402


def blank_tee(h=900, w=900, color=(40, 40, 45)):
    img = np.full((h, w, 3), 236, np.uint8)
    pts = np.array([[320, 110], [580, 110], [760, 230], [690, 350], [630, 320], [630, 820], [270, 820],
                    [270, 320], [210, 350], [140, 230]])
    cv2.fillPoly(img, [pts], color)
    yy, xx = np.mgrid[0:h, 0:w]
    inside = np.zeros((h, w), np.uint8)
    cv2.fillPoly(inside, [pts], 1)
    folds = 14 * np.sin(xx / 21 + yy / 50)
    return np.clip(img + (folds * inside)[..., None], 0, 255).astype(np.uint8), inside


def art(color=(250, 210, 30)):
    a = np.zeros((500, 400, 4), np.uint8)
    cv2.rectangle(a, (20, 20), (380, 480), (*color, 255), -1)
    return a


class RenderTests(unittest.TestCase):
    def test_mask_light_and_zones(self):
        img, inside = blank_tee()
        maps = mr.bake(img)
        overlap = ((maps['mask'] > 0) == (inside > 0)).mean()
        self.assertGreater(overlap, 0.97)
        self.assertAlmostEqual(float(np.median(maps['light'][inside > 0])), 1.0, delta=0.05)
        for kind in ('rect', 'quad', 'mesh'):
            zone = mr.default_zone(maps['mask'], kind)
            out = mr.render(img, maps, art(), zone)
            x, y = int(450), int(400)
            self.assertGreater(int(out[y, x, 2]), 150, kind)          # yellow print on the chest (BGR)
            self.assertTrue((out[20, 20] == img[20, 20]).all())       # background untouched

    def test_dark_tee_keeps_print_bright_and_flat_has_no_folds(self):
        img, inside = blank_tee()
        maps = mr.bake(img)
        zone = mr.default_zone(maps['mask'], 'rect')
        out = mr.render(img, maps, art(), zone)
        flat = mr.flat(img, art(), zone)
        region = inside.astype(bool) & (np.abs(flat.astype(int) - img).sum(-1) > 60)
        self.assertGreater(out[region][:, 2].mean(), 180)           # not blacked out by the dark fabric
        self.assertTrue((flat[region] == flat[region][0]).all(axis=1).mean() > 0.95)

    def test_cylinder_wraps_and_squeezes_edges(self):
        mug = np.full((800, 1000, 3), 245, np.uint8)
        maps = mr.bake(mug)
        stripes = np.zeros((300, 600, 4), np.uint8)
        stripes[..., 3] = 255
        for x in range(0, 600, 60):
            stripes[:, x:x + 10, :3] = 255
        zone = {'type': 'cylinder', 'box': [0.3, 0.2, 0.7, 0.8], 'wrap': 160, 'sag': 0.0, 'taper': 1.0}
        out = mr.render(mug, maps, stripes, zone, {'fit': 'stretch', 'fold': 0, 'texture': 0})
        row = out[400, 300:700, 0].astype(int)
        bright = np.nonzero(np.diff((row > 128).astype(int)) == 1)[0]
        gaps = np.diff(bright)
        self.assertGreater(len(gaps), 6)
        self.assertGreater(gaps[len(gaps) // 2], 1.5 * max(gaps[0], gaps[-1]))   # stripes bunch up towards the sides

    def test_bad_zones(self):
        for zone in ({'type': 'blob'}, {'type': 'rect', 'box': [0, 0, 0, 0.5]}, {'type': 'quad', 'points': [[0, 0]]},
                     {'type': 'cylinder', 'box': [0.1, 0.1, 0.5, 0.9], 'wrap': 400}):
            with self.assertRaises(ValueError):
                mr.check_zone(zone)


class LibraryTests(unittest.TestCase):
    def test_create_update_render_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = MockupLibrary(tmp)
            img, _ = blank_tee()
            preset = lib.create(jpeg(img), 'Tee đen trơn', 'Mặt trước')
            self.assertEqual(preset['zone']['type'], 'rect')
            self.assertEqual(lib.groups(), ['Tee đen trơn'])
            moved = lib.update(preset['id'], {'zone': mr.default_zone(np.ones((10, 10), np.uint8) * 255, 'quad'),
                                               'options': {'fold': 2, 'fit': 'cover'}})
            self.assertEqual(moved['zone']['type'], 'quad')
            self.assertEqual(moved['options']['fold'], 2)
            with self.assertRaises(ValueError):
                lib.update(preset['id'], {'options': {'fold': 99}})
            self.assertEqual(lib.render(preset['id'], art()).shape, img.shape)
            self.assertEqual(lib.diagnostic(preset['id'], 'disp').dtype, np.uint8)
            lib.delete(preset['id'])
            self.assertEqual(lib.list(), [])
            with self.assertRaises(KeyError):
                lib.get('../etc')


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

    def test_job_mockups_feed_the_listing_set(self):
        img, _ = blank_tee()
        preset = self.v2.mockup_create({'image_base64': base64.b64encode(jpeg(img)).decode(), 'group': 'Tee', 'name': 'A'})
        image = base64.b64encode(jpeg(np.concatenate([shirt((240, 200, 30), 'F'), shirt((30, 200, 240), 'B')], 1))).decode()
        sid = self.v2.inspect({'sources': [{'image_base64': image}]})['sources'][0]['source_id']
        job = self.v2.submit({'kind': '2d', 'sources': [{'source_id': sid}]})
        self.engine.executor.submit(lambda: None).result()
        self.assertIn('preview.jpg', self.v2.mockup_preview(preset['id'], {'job_id': job['id'], 'side': 'back'})['url'])
        done = self.v2.job_mockups(job['id'], {'group': 'Tee'})
        files = done['extras']['mockups']['files']
        self.assertEqual(len(files), 1)
        self.assertTrue(self.v2.file_path(job['id'], files[0]['file']).is_file())
        photo = io.BytesIO()
        Image.new('RGB', (2000, 2000), (90, 120, 160)).save(photo, 'JPEG')
        self.v2.real_photo(job['id'], {'image_base64': base64.b64encode(photo.getvalue()).decode()})
        built = self.v2.listing(job['id'])['extras']['listing']['files']
        self.assertEqual([f['file'] for f in built[:2]], ['01-anh-that.jpg', '02-mockup-1.jpg'])
        self.assertEqual(len(built), 9)   # real + rendered + source + 4 front colours + 2 back
        with self.assertRaises(ValueError):
            self.v2.job_mockups(job['id'], {'group': 'Không có'})


if __name__ == '__main__':
    unittest.main()
