"""Tests for the 3D style library: template detection by outline and new templates from marks."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import styles  # noqa: E402


def garment(shift=0, width=1.0, size=600):
    """White product photo with a dark garment-shaped polygon."""
    img = np.full((size, size, 3), 255, np.uint8)
    s = size / 600
    pts = np.array([[200, 80], [400, 80], [560, 200], [500, 300], [430, 250], [430, 560],
                    [170, 560], [170, 250], [100, 300], [40, 200]], np.float32)
    pts[:, 0] = 300 + (pts[:, 0] - 300) * width + shift
    cv2.fillPoly(img, [np.int32(pts * s)], (40, 30, 30))
    return img


class FakeTemplate:
    def __init__(self, folder):
        self.dir = folder
        self.silhouette_path = folder / 'silhouette.png'


class FakeTemplates:
    def __init__(self, root):
        self.root = root

    def available(self):
        return sorted(p.name for p in self.root.iterdir() if (p / 'template.json').is_file())

    def load(self, name):
        return FakeTemplate(self.root / name)

    def reference_path(self, name):
        return None


def add_template(root, name, image, style=None):
    folder = root / name
    folder.mkdir()
    (folder / 'template.json').write_text(json.dumps({'name': name, 'style': style} if style else {'name': name}))
    cv2.imwrite(str(folder / 'silhouette.png'), styles.outline(image).astype(np.uint8) * 255)


class StyleLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        add_template(self.root, 'sweater', garment())
        add_template(self.root, 'hoodie_front_back', garment(width=0.6))
        self.lib = styles.StyleLibrary(FakeTemplates(self.root), self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_shot_is_selected(self):
        result = self.lib.detect(garment())
        self.assertEqual(result['selected'], 'sweater')
        self.assertEqual(result['suggested_style'], 'sweater')
        self.assertEqual(result['paid_calls'], 0)

    def test_similar_garment_suggests_style_but_needs_a_new_template(self):
        result = self.lib.detect(garment(shift=25))
        self.assertIsNone(result['selected'])
        self.assertEqual(result['suggested_style'], 'sweater')

    def test_unknown_photo_asks_for_a_manual_pick(self):
        odd = np.full((600, 600, 3), 255, np.uint8)
        cv2.circle(odd, (150, 450), 120, (30, 30, 30), -1)
        result = self.lib.detect(odd)
        self.assertIsNone(result['selected'])
        self.assertIsNone(result['suggested_style'])
        busy = np.random.default_rng(0).integers(0, 255, (600, 600, 3), dtype=np.uint8)
        self.assertFalse(self.lib.detect(busy)['light_backdrop'])

    def test_style_of_known_and_named_templates(self):
        self.assertEqual(styles.style_of('hoodie_front_back'), 'hoodie_pullover')
        self.assertEqual(styles.style_of('tshirt-261028-1', {'style': 'tshirt'}), 'tshirt')
        self.assertEqual(styles.style_of('sweater'), 'sweater')

    def test_create_template_from_marks(self):
        photo = self.root / 'photo.jpg'
        cv2.imwrite(str(photo), garment())
        built = []
        quad = [[0.3, 0.2], [0.7, 0.2], [0.7, 0.9], [0.3, 0.9]]
        result = self.lib.create(photo, {'style': 'tshirt', 'panels': [{'name': 'Front', 'quad': quad}]},
                                 lambda name, ref: built.append((name, ref.name)))
        self.assertEqual(built, [(result['name'], 'reference.jpg')])
        data = json.loads((self.root / result['name'] / 'template.json').read_text())
        self.assertEqual(data['style'], 'tshirt')
        front = next(p for p in data['panels'] if p['name'] == 'Front')
        self.assertEqual((front['method'], front['quad'], front['size']), ('quad', quad, [4500, 5400]))
        self.assertEqual(result['solid'], ['Back', 'Left', 'Right', 'Collar'])
        self.assertTrue((self.root / result['name'] / 'reference-thumb.jpg').is_file())
        second = self.lib.create(photo, {'style': 'tshirt', 'panels': [{'name': 'Front', 'quad': quad}]}, lambda *a: None)
        self.assertNotEqual(second['name'], result['name'])

    def test_create_rejects_bad_marks_and_cleans_up(self):
        photo = self.root / 'photo.jpg'
        cv2.imwrite(str(photo), garment())
        quad = [[0.3, 0.2], [0.7, 0.2], [0.7, 0.9], [0.3, 0.9]]
        for data in ({'style': 'nope', 'panels': [{'name': 'Front', 'quad': quad}]},
                     {'style': 'tshirt', 'panels': [{'name': 'Hood', 'quad': quad}]},
                     {'style': 'tshirt', 'panels': [{'name': 'Front', 'quad': [[0, 0], [2, 0], [1, 1], [0, 1]]}]},
                     {'style': 'tshirt', 'panels': [{'name': 'Front', 'quad': [[0.5, 0.5]] * 4}]},
                     {'style': 'tshirt', 'panels': []}):
            with self.assertRaises(ValueError):
                self.lib.create(photo, data, lambda *a: None)

        def broken(name, ref):
            raise RuntimeError('map build failed')
        before = set(p.name for p in self.root.iterdir())
        with self.assertRaises(RuntimeError):
            self.lib.create(photo, {'style': 'tshirt', 'panels': [{'name': 'Front', 'quad': quad}]}, broken)
        self.assertEqual(set(p.name for p in self.root.iterdir()), before)


if __name__ == '__main__':
    unittest.main()
