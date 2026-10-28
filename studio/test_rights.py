"""Tests for the copyright screening: pHash, keywords, levels and the Red blacklist."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rights  # noqa: E402


def art(seed):
    rng = np.random.default_rng(seed)
    img = np.full((300, 260, 3), 240, np.uint8)
    for _ in range(12):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        p = tuple(int(v) for v in rng.integers(0, 260, 2))
        cv2.circle(img, p, int(rng.integers(10, 60)), c, -1)
    return img


class RightsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.checker = rights.RightsChecker(self.tmp.name)
        patch('rights.ocr_available', return_value=False).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.tmp.cleanup()

    def test_phash_survives_resize_and_jpeg_but_separates_other_art(self):
        a = art(1)
        ok, enc = cv2.imencode('.jpg', cv2.resize(a, (520, 600)), [cv2.IMWRITE_JPEG_QUALITY, 70])
        b = cv2.imdecode(enc, cv2.IMREAD_COLOR)
        self.assertLessEqual(rights.hamming(rights.phash(a), rights.phash(b)), 6)
        self.assertGreater(rights.hamming(rights.phash(a), rights.phash(art(2))), rights.HAMMING_MATCH)
        rgba = np.dstack([a, np.full(a.shape[:2], 255, np.uint8)])
        self.assertEqual(rights.phash(rgba), rights.phash(a))

    def test_keywords_match_whole_words_in_links(self):
        words = rights.DEFAULT_KEYWORDS
        self.assertEqual(rights.find_keywords('https://shop.example/listing/Street-Fighter_ugly-sweater', words),
                         ['street fighter'])
        self.assertEqual(rights.find_keywords('Pokémon party tee', words), ['pokemon'])
        self.assertEqual(rights.find_keywords('Funny cat painting starry night', words), [])
        self.assertEqual(rights.find_keywords('mariology club', words), [])   # no partial words

    def test_levels_green_yellow_red(self):
        level, reason, ev = self.checker.check([('front', art(1))], source_url='https://x.example/cat-van-gogh-tee')
        self.assertEqual(level, 'green')
        self.assertIn('không phải bảo đảm', reason)
        self.assertEqual(len(ev['hashes']), 1)
        level, reason, ev = self.checker.check([('front', art(1))], source_url='https://x.example/disney-mickey-tee')
        self.assertEqual(level, 'yellow')
        self.assertEqual(ev['keywords_in_link'], ['disney', 'mickey'])
        self.assertTrue(any(l['label'] == 'TinEye' for l in ev['links']))
        job = {'id': 'j1', 'sku': '2D-261028-001', 'rights_evidence': ev}
        self.assertEqual(self.checker.remember_red(job, 'logo Disney'), 1)
        level, reason, ev2 = self.checker.check([('front', cv2.resize(art(1), (200, 230)))])
        self.assertEqual(level, 'red')
        self.assertIn('2D-261028-001', reason)
        self.assertEqual(self.checker.forget('j1'), 1)
        self.assertEqual(self.checker.check([('front', art(1))])[0], 'green')

    def test_keyword_list_is_editable(self):
        cfg = self.checker.set_keywords(['  Acme Toons ', 'acme toons', 'Other'])
        self.assertEqual(cfg['keywords'], ['Acme Toons', 'Other'])
        self.assertEqual(self.checker.check([('a', art(3))], source_url='https://x/acme-toons')[0], 'yellow')
        with self.assertRaises(ValueError):
            self.checker.set_keywords('disney')

    def test_ocr_text_is_checked_when_tesseract_is_installed(self):
        with patch("rights.ocr_available", return_value=True), \
                patch('rights.read_text', return_value='OFFICIAL NBA FINALS'):
            level, _, ev = self.checker.check([('front', art(4))])
        self.assertEqual(level, 'yellow')
        self.assertEqual(ev['keywords_in_art'], ['nba'])
        self.assertTrue(any('USPTO' in l['label'] for l in ev['links']))


if __name__ == '__main__':
    unittest.main()
