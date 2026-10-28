"""Tests for POD Studio v2: job store, image links, 2D art extraction and the v2 service."""
from concurrent.futures import ThreadPoolExecutor
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
import art2d  # noqa: E402
import sources  # noqa: E402
from jobstore import JobStore  # noqa: E402
from v2 import StudioV2  # noqa: E402


def shirt(art_bgr, text, size=(900, 800)):
    img = np.full((*size, 3), 250, np.uint8)
    pts = np.array([[250, 100], [550, 100], [720, 220], [650, 320], [600, 280], [600, 850],
                    [200, 850], [200, 280], [150, 320], [80, 220]])
    cv2.fillPoly(img, [pts], (40, 40, 40))
    cv2.circle(img, (400, 420), 110, art_bgr, -1)
    cv2.putText(img, text, (300, 620), cv2.FONT_HERSHEY_SIMPLEX, 2, (20, 20, 200), 6)
    return img


def jpeg(bgr):
    ok, data = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return data.tobytes()


class FakeEngine:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1)


class JobStoreTests(unittest.TestCase):
    def test_sku_sequence_update_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / 'db.sqlite')
            a = store.create('2d', 'local', params={'x': 1})
            b = store.create('2d', 'local')
            c = store.create('3d', 'ai')
            self.assertTrue(a['sku'].startswith('2D-') and a['sku'].endswith('-001'))
            self.assertTrue(b['sku'].endswith('-002'))
            self.assertTrue(c['sku'].startswith('3D-') and c['sku'].endswith('-001'))
            store.update(a['id'], status='running', outputs={'sides': {}})
            store.log(a['id'], 'hello')
            self.assertEqual(store.get(a['id'])['logs'][-1]['message'], 'hello')
            with self.assertRaises(ValueError):
                store.update(a['id'], rights='purple')
            store.close()
            again = JobStore(Path(tmp) / 'db.sqlite')
            self.assertEqual(again.get(a['id'])['status'], 'interrupted')
            self.assertEqual(again.get(c['id'])['status'], 'interrupted')


class SourceTests(unittest.TestCase):
    def test_shop_links_upgrade_to_largest(self):
        self.assertEqual(sources.upgrade_url('https://i.etsystatic.com/123/r/il/ab/456/il_794xN.456_xyz.jpg'),
                         'https://i.etsystatic.com/123/r/il/ab/456/il_fullxfull.456_xyz.jpg')
        self.assertEqual(sources.upgrade_url('https://cdn.shopify.com/s/files/1/p/shirt_800x.jpg?v=12'),
                         'https://cdn.shopify.com/s/files/1/p/shirt.jpg')
        self.assertEqual(sources.upgrade_url('https://m.media-amazon.com/images/I/71abc._AC_SX679_.jpg'),
                         'https://m.media-amazon.com/images/I/71abc.jpg')
        self.assertEqual(sources.upgrade_url('https://example.com/a_800x.jpg'), 'https://example.com/a_800x.jpg')

    def test_url_falls_back_to_original_and_rejects_private_hosts(self):
        good = jpeg(shirt((30, 200, 240), 'A'))
        calls = []

        def download(url):
            calls.append(url)
            if 'fullxfull' in url:
                raise ValueError('404')
            return good
        raw, used = sources.from_url('https://i.etsystatic.com/1/il_794xN.1.jpg', download)
        self.assertEqual(raw, good)
        self.assertEqual(used, 'https://i.etsystatic.com/1/il_794xN.1.jpg')
        self.assertEqual(len(calls), 2)
        with self.assertRaises(ValueError):
            sources._download('http://127.0.0.1/x.jpg')
        with self.assertRaises(ValueError):
            sources._download('file:///etc/passwd')

    def test_rejects_non_images(self):
        with self.assertRaises(ValueError):
            sources.load({'image_base64': base64.b64encode(b'not an image').decode()})


class Art2DTests(unittest.TestCase):
    def test_two_shirts_give_front_and_back_canvases(self):
        rgb = cv2.cvtColor(np.concatenate([shirt((240, 200, 30), 'FRONT'), shirt((30, 200, 240), 'BACK')], 1),
                           cv2.COLOR_BGR2RGB)
        with tempfile.TemporaryDirectory() as tmp:
            outputs, report = art2d.run([rgb], tmp, log=lambda m: None)
            self.assertEqual(set(outputs), {'front', 'back'})
            for side in ('front', 'back'):
                with Image.open(Path(tmp) / f'{side}.png') as im:
                    self.assertEqual(im.size, (4500, 5400))
                    self.assertEqual(im.mode, 'RGBA')
                    self.assertAlmostEqual(im.info['dpi'][0], 300, places=0)
                    alpha = np.asarray(im)[..., 3]
                self.assertEqual(alpha[-50:].max(), 0)          # below the art stays transparent
                self.assertGreater((alpha > 200).mean(), 0.2)   # the art itself is opaque
            box = report['sides']['front']['source_box']
            self.assertLess(abs(box[0] - 283), 25)
            self.assertLess(box[0] + box[2], 800)                # front comes from the left shirt

    def test_side_choice_and_no_art(self):
        regions = [(0, {'art': [0, 0, .1, .1]}), (0, {'art': [.5, 0, .1, .1]})]
        self.assertEqual([s for s, _ in art2d.assign_sides(regions, 'both')], ['front', 'back'])
        self.assertEqual(art2d.assign_sides(regions, 'back')[0][1], regions[1])
        self.assertEqual(art2d.assign_sides(regions[:1], 'back')[0][0], 'back')
        self.assertEqual(art2d.assign_sides([(0, {'art': None})], 'auto'), [])
        plain = np.full((400, 400, 3), 250, np.uint8)
        cv2.rectangle(plain, (100, 50), (300, 380), (40, 40, 40), -1)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            art2d.run([plain], tmp, log=lambda m: None)

    def test_manual_box_overrides_detection(self):
        rgb = cv2.cvtColor(shirt((240, 200, 30), 'X'), cv2.COLOR_BGR2RGB)
        with tempfile.TemporaryDirectory() as tmp:
            outputs, report = art2d.run([rgb], tmp, sides='front', log=lambda m: None,
                                        boxes={'front': {'source': 0, 'box': [0.3, 0.3, 0.4, 0.3]}})
            self.assertEqual(report['sides']['front']['source_box'], [240, 270, 320, 270])


class StudioV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = FakeEngine()
        self.v2 = StudioV2(self.engine, self.tmp.name)
        self.image = base64.b64encode(jpeg(np.concatenate(
            [shirt((240, 200, 30), 'F'), shirt((30, 200, 240), 'B')], 1))).decode()

    def tearDown(self):
        self.engine.executor.shutdown(wait=True)
        self.v2.store.close()
        self.tmp.cleanup()

    def test_inspect_submit_dedupe_and_files(self):
        found = self.v2.inspect({'sources': [{'image_base64': self.image}]})
        sid = found['sources'][0]['source_id']
        self.assertEqual(len(found['sources'][0]['detection']['garments']), 2)
        job = self.v2.submit({'kind': '2d', 'sources': [{'source_id': sid}]})
        self.engine.executor.submit(lambda: None).result()   # wait for the worker
        done = self.v2.get(job['id'])
        self.assertEqual(done['status'], 'review', done.get('error'))
        self.assertEqual(set(done['outputs']['sides']), {'front', 'back'})
        self.assertTrue(self.v2.file_path(job['id'], 'front.png').is_file())
        with self.assertRaises(FileNotFoundError):
            self.v2.file_path(job['id'], '../../studio.db')
        again = self.v2.submit({'kind': '2d', 'sources': [{'source_id': sid}]})
        self.assertTrue(again.get('duplicate'))
        self.assertEqual(again['id'], job['id'])
        self.assertEqual(self.v2.inspect({'sources': [{'source_id': sid}]})['duplicates'], [job['sku']])

    def test_rights_and_approval_rules(self):
        sid = self.v2.inspect({'sources': [{'image_base64': self.image}]})['sources'][0]['source_id']
        job = self.v2.submit({'kind': '2d', 'sources': [{'source_id': sid}]})
        self.engine.executor.submit(lambda: None).result()
        self.v2.set_rights(job['id'], {'rights': 'yellow', 'reason': 'logo?'})
        with self.assertRaises(ValueError):
            self.v2.approve(job['id'])
        with self.assertRaises(ValueError):
            self.v2.set_rights(job['id'], {'rights': 'green', 'reason': ''})
        self.v2.set_rights(job['id'], {'rights': 'green', 'reason': 'tự thiết kế'})
        self.assertEqual(self.v2.approve(job['id'])['status'], 'approved')

    def test_bad_input(self):
        for data in ({'kind': '4d'}, {'kind': '2d', 'sources': []},
                     {'kind': '2d', 'sources': [{'image_base64': self.image}], 'sides': 'top'},
                     {'kind': '2d', 'sources': [{'image_base64': self.image}], 'boxes': {'front': {'box': [2, 0, 1, 1]}}}):
            with self.assertRaises(ValueError):
                self.v2.submit(data)


if __name__ == '__main__':
    unittest.main()
