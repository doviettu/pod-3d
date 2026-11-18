"""Tests for the seamless pattern tile: straight and half-drop repeats, noise voted out, the
measured period, seam check, refusal on non-repeating art, and the studio endpoint."""
import base64
import io
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pattern_tile as pt  # noqa: E402


def motif(h=37, w=53, seed=1):
    """A smooth, high-contrast periodic motif (blurred noise, made periodic by wrap padding)."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (h, w, 3)).astype(np.float32)
    big = np.tile(base, (3, 3, 1))
    big = cv2.GaussianBlur(big, (0, 0), 2.0)
    m = big[h:2 * h, w:2 * w]
    m = (m - m.min()) / (m.max() - m.min()) * 255
    return m.astype(np.uint8)


def fabric(m, rows=9, cols=8, half_drop=False, noise=0, seed=2):
    h, w = m.shape[:2]
    out = np.zeros((rows * h, cols * w, 3), np.uint8)
    for c in range(cols):
        col = np.tile(m, (rows + 1, 1, 1))
        shift = (h // 2) if (half_drop and c % 2) else 0
        out[:, c * w:(c + 1) * w] = col[shift:shift + rows * h]
    if noise:
        rng = np.random.default_rng(seed)
        out = np.clip(out.astype(np.int16) + rng.normal(0, noise, out.shape), 0, 255).astype(np.uint8)
    return out[5:-7, 9:-4]   # do not start on a cell boundary


def cyclic_error(tile, m):
    """Smallest mean abs difference between tile and any cyclic shift of m (same size)."""
    best = 1e9
    for dy in range(m.shape[0]):
        for dx in range(m.shape[1]):
            d = np.abs(np.roll(m, (dy, dx), (0, 1)).astype(np.int16) - tile).mean()
            best = min(best, d)
    return best


class TileTest(unittest.TestCase):
    def test_straight_repeat(self):
        m = motif()
        tile, info = pt.extract(fabric(m), edge=None)
        self.assertEqual(info['period_px'], [53, 37])
        self.assertGreater(info['copies'], 10)
        self.assertLess(cyclic_error(tile, m), 4)
        self.assertTrue(info['seamless'], info)

    def test_noise_is_voted_out(self):
        m = motif()
        noisy = fabric(m, noise=25)
        tile, info = pt.extract(noisy, edge=None)
        self.assertLess(cyclic_error(tile, m), 6)      # input noise is ~20 per channel

    def test_half_drop_gives_two_repeat_cell(self):
        m = motif(h=40)
        tile, info = pt.extract(fabric(m, rows=10, half_drop=True), edge=None)
        self.assertEqual(info['period_px'], [106, 40])
        self.assertTrue(info['half_drop'])
        self.assertTrue(info['seamless'], info)

    def test_resize_and_preview(self):
        tile, info = pt.extract(fabric(motif()), edge=1024)
        self.assertEqual(max(info['size']), 1024)
        self.assertEqual(tuple(info['size']), (tile.shape[1], tile.shape[0]))
        self.assertTrue(info['seamless'], info)
        grid = pt.preview(tile, 3, edge=900)
        self.assertEqual(max(grid.shape[:2]), 900)

    def test_band_repeats_one_way(self):
        # Two stacked bands, both repeating every 61 px left-right; nothing repeats top-bottom.
        band = np.vstack([np.tile(motif(h=37, w=61), (1, 9, 1)),
                          np.tile(motif(h=180, w=61, seed=7), (1, 9, 1))])[:, 7:-5]
        tile, info = pt.extract(band, edge=None)
        self.assertEqual(info['kind'], 'band-x')
        self.assertEqual(info['period_px'][0], 61)
        self.assertEqual(info['period_px'][1], band.shape[0])
        self.assertTrue(info['seamless'], info)
        grid = pt.preview(tile, kind='band-x')
        self.assertEqual(grid.shape[1], 3 * tile.shape[1])

    def test_refuses_non_repeating_art(self):
        img = np.full((400, 400, 3), 240, np.uint8)
        cv2.circle(img, (200, 200), 120, (30, 30, 200), -1)
        cv2.putText(img, 'HO HO', (90, 220), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 5)
        with self.assertRaises(ValueError):
            pt.extract(img)


class EndpointTest(unittest.TestCase):
    def test_pattern_tile_endpoint(self):
        from PIL import Image
        import v2
        raw = io.BytesIO()
        Image.fromarray(fabric(motif())[..., ::-1]).save(raw, 'PNG')
        out = v2.pattern_tile({'art_base64': base64.b64encode(raw.getvalue()).decode(), 'edge': 512})
        tile = Image.open(io.BytesIO(base64.b64decode(out['png_base64'])))
        grid = Image.open(io.BytesIO(base64.b64decode(out['preview_base64'])))
        self.assertEqual(max(tile.size), 512)
        self.assertEqual(out['period_px'], [53, 37])
        self.assertTrue(out['seamless'])
        self.assertEqual(grid.mode, 'RGB')
        with self.assertRaises(ValueError):
            v2.pattern_tile({'art_base64': base64.b64encode(raw.getvalue()).decode(), 'edge': 99999})


if __name__ == '__main__':
    unittest.main()
