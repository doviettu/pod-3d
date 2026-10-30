"""Tests for the exact fills: twin-panel borrowing and 2D repeat (lattice) continuation."""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pod3d import repeat  # noqa: E402


def half_drop(h=600, w=560):
    img = np.full((h, w, 3), (40, 120, 30), np.uint8)
    for y in range(-90, h + 90, 90):
        for x in range(-70, w + 70, 70):
            xx = x + (35 if (y // 90) % 2 else 0)
            cv2.circle(img, (xx, y), 18, (200, 200, 240), -1)
            cv2.rectangle(img, (xx + 20, y + 20), (xx + 30, y + 40), (0, 0, 200), -1)
    return img


class LatticeTests(unittest.TestCase):
    def test_all_over_pattern_is_continued_exactly(self):
        img = half_drop()
        known = np.zeros(img.shape[:2], bool)
        known[:, 150:410] = True
        out, left, vectors = repeat.lattice_fill(img, known)
        self.assertIsNotNone(vectors)
        self.assertFalse(left.any())
        self.assertLess(np.abs(out.astype(int) - img)[~known].mean(), 6)

    def test_noise_and_gradients_are_not_patterns(self):
        known = np.zeros((600, 560), bool)
        known[:, 150:410] = True
        noise = np.random.default_rng(0).integers(0, 255, (600, 560, 3), np.uint8)
        gradient = np.tile(np.linspace(0, 255, 560).astype(np.uint8)[None, :, None], (600, 1, 3))
        self.assertIsNone(repeat.lattice(noise, known))
        self.assertIsNone(repeat.lattice(gradient, known))


class TwinTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1)
        self.art = cv2.GaussianBlur(rng.integers(0, 255, (300, 260, 3), np.uint8), (0, 0), 4)
        self.known = np.zeros((300, 260), bool)
        self.known[:, 40:200] = True
        self.twin_known = np.zeros((300, 260), bool)
        self.twin_known[:, 90:250] = True

    def test_same_art_seen_from_another_side_is_borrowed(self):
        darker = (self.art * 0.85).astype(np.uint8)   # the other view is shaded differently
        out, known, how = repeat.borrow(self.art, self.known, darker, self.twin_known)
        self.assertEqual(how, 'same')
        self.assertTrue(known[:, 200:250].all())
        self.assertLess(np.abs(out[:, 200:250].astype(int) - self.art[:, 200:250]).mean(), 4)

    def test_mirrored_twin_and_different_art(self):
        out, known, how = repeat.borrow(self.art, self.known, self.art[:, ::-1].copy(), self.twin_known[:, ::-1].copy())
        self.assertEqual(how, 'mirror')
        other = np.random.default_rng(2).integers(0, 255, self.art.shape, np.uint8)
        out, known, how = repeat.borrow(self.art, self.known, other, self.twin_known)
        self.assertIsNone(how)
        self.assertTrue((known == self.known).all())


if __name__ == '__main__':
    unittest.main()
