"""Tests for Redesign step 1: layer split, cut-outs, brief and the studio hookup (no OCR binary,
no model: Tesseract output and SAM 3 are faked)."""
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
import redesign as rd  # noqa: E402

TSV = '\n'.join([
    'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext',
    '5\t1\t1\t1\t1\t1\t100\t40\t120\t40\t91.5\tGOOD',
    '5\t1\t1\t1\t1\t2\t240\t40\t160\t40\t88.0\tVIBES',
    '5\t1\t2\t1\t1\t1\t30\t500\t20\t20\t20.0\t~~',   # low confidence noise
])


def design(opaque=False):
    """A 600x600 design: a big circle, a small square, a word, and a few specks."""
    img = np.zeros((600, 600, 4), np.uint8)
    cv2.circle(img, (300, 300), 150, (230, 80, 40, 255), -1)
    cv2.rectangle(img, (440, 470), (540, 560), (40, 120, 220, 255), -1)
    cv2.rectangle(img, (100, 40), (400, 80), (20, 20, 20, 255), -1)   # stands in for the text ink
    for x in (20, 40, 60):
        cv2.circle(img, (x, 580), 2, (20, 20, 20, 255), -1)
    if opaque:
        bg = np.full((600, 600, 4), (245, 240, 230, 255), np.uint8)
        a = img[..., 3:] / 255
        img = (img * a + bg * (1 - a)).astype(np.uint8)
        img[..., 3] = 255
    return img


def fake_ocr(rgb):
    return rd.parse_tsv(TSV)


class SplitTests(unittest.TestCase):
    def test_tsv_lines(self):
        lines = rd.parse_tsv(TSV)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['text'], 'GOOD VIBES')
        self.assertEqual(lines[0]['bbox'], [100, 40, 300, 40])

    def test_layers_transparent_and_opaque(self):
        for opaque in (False, True):
            layers, info = rd.split(design(opaque), rd.parse_tsv(TSV))
            kinds = [(l['type'], l['label']) for l in layers]
            self.assertEqual(kinds[-1], ('text', 'GOOD VIBES'), kinds)
            graphics = [l for l in layers if l['type'] == 'graphic']
            self.assertEqual([g['label'] for g in graphics], ['Hình 1', 'Hình 2', 'Chi tiết nhỏ'])
            self.assertEqual(graphics[0]['bbox'][:2], [150, 150])
            self.assertEqual(graphics[0]['color'], '#e65028')
            self.assertEqual(graphics[1]['bbox'], [440, 470, 101, 91])
            self.assertEqual(info['background'] is None, not opaque)
            top = int(info['palette'][0]['hex'][1:3], 16), int(info['palette'][0]['hex'][3:5], 16)
            self.assertLess(abs(top[0] - 230) + abs(top[1] - 80), 4, info['palette'])

    def test_sam_objects_come_first(self):
        def segment(rgb, prompts):
            m = np.zeros(rgb.shape[:2], bool)
            m[150:451, 150:451] = True
            return {'animal': [(m, 0.9)], 'logo': [(m, 0.3)]}
        layers, _ = rd.split(design(), None, segment)
        self.assertEqual((layers[0]['type'], layers[0]['label']), ('object', 'animal'))
        self.assertNotIn('Hình 2', [l['label'] for l in layers if l['bbox'][0] < 400])


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = rd.RedesignStore(Path(self.tmp.name), ocr=fake_ocr)
        self.scene = self.store.analyze(design(), 'test')

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_files_and_brief(self):
        scene = self.scene
        self.assertTrue(rd.ID.fullmatch(scene['id']))
        self.assertEqual(scene['size'], [600, 600])
        first = scene['layers'][0]
        with Image.open(self.store.file(scene['id'], first['file'])) as cut:
            self.assertEqual(cut.size, tuple(first['bbox'][2:]))
            self.assertEqual(cut.mode, 'RGBA')
        with self.assertRaises(FileNotFoundError):
            self.store.file(scene['id'], '../scene.json')
        with self.assertRaises(KeyError):
            self.store.get('rd-000000-000000-1')
        text = next(l for l in scene['layers'] if l['type'] == 'text')
        out = self.store.prompt(scene['id'], {
            'goal': 'Halloween version',
            'layers': {first['id']: {'action': 'change', 'note': 'make it a pumpkin'},
                       'L2': {'action': 'remove'}},
            'skills': ['chong-ai-look', 'nope']})
        self.assertIn('CHANGE: make it a pumpkin', out['prompt'])
        self.assertIn('REMOVE', out['prompt'])
        self.assertIn(f'{text["id"]} text "GOOD VIBES"', out['prompt'])
        self.assertIn('KEEP exactly', out['prompt'])
        self.assertIn('transparent background', out['prompt'])
        self.assertIn('Goal of the redesign: Halloween version', out['prompt'])
        self.assertEqual(out['skills'], ['chong-ai-look'])
        self.assertIn('# Chống "AI-look"', out['markdown'])
        self.assertTrue((Path(self.tmp.name) / scene['id'] / 'brief.md').is_file())
        with self.assertRaises(ValueError):
            self.store.prompt(scene['id'], {'layers': {}})
        with self.assertRaises(ValueError):
            self.store.prompt(scene['id'], {'layers': {'L1': {'action': 'explode'}}})

    def test_skills_listed(self):
        ids = [s['id'] for s in rd.skills()]
        self.assertIn('chong-ai-look', ids)
        self.assertIn('in-ao-pod', ids)


class StudioTests(unittest.TestCase):
    def test_v2_upload_and_job_side(self):
        from test_v2 import FakeEngine
        from v2 import StudioV2
        with tempfile.TemporaryDirectory() as tmp:
            v2 = StudioV2(FakeEngine(), Path(tmp))
            v2.redesign.ocr = fake_ocr
            buf = io.BytesIO()
            Image.fromarray(design()).save(buf, 'PNG')
            scene = v2.redesign_analyze({'art_base64': base64.b64encode(buf.getvalue()).decode()})
            self.assertEqual(v2.redesign_get(scene['id'])['layers'][0]['url'],
                             f'/api/v2/redesign/{scene["id"]}/layer-1.png')
            self.assertTrue(scene['ocr'])
            self.assertFalse(scene['sam3'])
            calls = []

            def segment(rgb, prompts):
                calls.append(prompts)
                return {}
            v2.segmenter = segment
            scene = v2.redesign_analyze({'art_base64': base64.b64encode(buf.getvalue()).decode(), 'sam3': True})
            self.assertTrue(scene['sam3'])
            self.assertEqual(calls[0], list(rd.SAM_PROMPTS))
            with self.assertRaises((ValueError, KeyError)):
                v2.redesign_analyze({})
            self.assertTrue(v2.redesign_skills()['skills'])


if __name__ == '__main__':
    unittest.main()
