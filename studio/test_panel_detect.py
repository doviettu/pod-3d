"""Tests for the SAM 3 panel proposals, with a fake segmenter standing in for the model."""
from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel_detect as pd  # noqa: E402
import styles  # noqa: E402


def poly(h, w, pts):
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [np.int32([[x * w, y * h] for x, y in pts])], 1)
    return m > 0


def hoodie_shot(h=800, w=1200):
    """Front view on the left, back view with hood on the right, as the default quads expect.
    Returns (bgr, fake segment result)."""
    front = poly(h, w, [(0.12, 0.22), (0.40, 0.22), (0.40, 0.90), (0.12, 0.90)])
    back = poly(h, w, [(0.60, 0.20), (0.88, 0.20), (0.88, 0.90), (0.60, 0.90)])
    hood = poly(h, w, [(0.66, 0.04), (0.84, 0.04), (0.84, 0.21), (0.66, 0.21)])
    sl = [poly(h, w, q) for q in ([(0.40, 0.24), (0.46, 0.24), (0.49, 0.80), (0.43, 0.80)],
                                  [(0.06, 0.24), (0.12, 0.24), (0.09, 0.80), (0.03, 0.80)],
                                  [(0.88, 0.24), (0.94, 0.24), (0.97, 0.80), (0.91, 0.80)])]
    # SAM returns whole garments: body + its sleeves (+ hood); the code must cut them out.
    garments = [front | sl[0] | sl[1], back | sl[2] | hood]
    img = np.full((h, w, 3), 240, np.uint8)
    for g in garments:
        img[g] = (60, 40, 140)
    found = {'hoodie': [(g, 0.9) for g in garments], 'sleeve': [(s, 0.8) for s in sl] + [(sl[0], 0.1)],
             'hood': [(hood, 0.85)]}
    return img, found


def fake(found):
    calls = []

    def segment(rgb, prompts):
        calls.append((rgb.shape, list(prompts)))
        return {p: found.get(p, []) for p in prompts}
    segment.calls = calls
    return segment


def near(q, want, tol=0.03):
    return np.abs(np.asarray(q) - np.asarray(want)).max() <= tol


class ProposeTests(unittest.TestCase):
    def test_hoodie_panels_found_and_cut(self):
        img, found = hoodie_shot()
        seg = fake(found)
        r = pd.propose(img, 'hoodie_pullover', seg)
        self.assertEqual(seg.calls[0][1], ['hoodie', 'sleeve', 'hood'])
        self.assertEqual(set(r['found']), {'Front', 'Back', 'Hood', 'Left', 'Right'})
        self.assertEqual(r['missing'], [])
        # Bodies come back without sleeves or hood glued on.
        self.assertTrue(near(r['quads']['Front'], styles.DEFAULT_QUADS['Front']), r['quads']['Front'])
        self.assertTrue(near(r['quads']['Back'], styles.DEFAULT_QUADS['Back']), r['quads']['Back'])
        self.assertTrue(near(r['quads']['Hood'], [[0.66, 0.04], [0.84, 0.04], [0.84, 0.21], [0.66, 0.21]]))
        # Left is the sleeve next to the front, Right the one at the far left; low scores ignored.
        self.assertLess(abs(np.mean([p[0] for p in r['quads']['Left']]) - 0.445), 0.02)
        self.assertLess(abs(np.mean([p[0] for p in r['quads']['Right']]) - 0.075), 0.02)
        tl, tr, br, bl = r['quads']['Left']
        self.assertLess(tl[1], bl[1])
        self.assertLess(tl[0], tr[0])
        self.assertEqual(r['counts'], {'body': 2, 'sleeve': 3, 'hood': 1})

    def test_zip_front_splits_and_tee_has_no_hood_prompt(self):
        img, found = hoodie_shot()
        r = pd.propose(img, 'hoodie_zip', fake(found))
        self.assertIn('FrontLeft', r['found'])
        self.assertIn('FrontRight', r['found'])
        left = max(p[0] for p in r['quads']['FrontLeft'])
        right = min(p[0] for p in r['quads']['FrontRight'])
        self.assertAlmostEqual(left, 0.26, delta=0.02)
        self.assertAlmostEqual(right, 0.26, delta=0.02)
        seg = fake({'t-shirt': found['hoodie']})
        r = pd.propose(img, 'tshirt', seg)
        self.assertEqual(seg.calls[0][1], ['t-shirt', 'sleeve'])
        self.assertEqual(set(r['found']), {'Front', 'Back'})
        self.assertEqual(set(r['missing']), {'Left', 'Right'})

    def test_front_view_only_like_a_product_photo(self):
        """One hoodie seen from the front, sleeves on both sides, drawstrings hanging off the hood."""
        h, w = 720, 680
        body = poly(h, w, [(0.20, 0.18), (0.80, 0.18), (0.78, 0.98), (0.22, 0.98)])
        sl_left = poly(h, w, [(0.02, 0.22), (0.20, 0.20), (0.18, 0.95), (0.01, 0.95)])
        sl_right = poly(h, w, [(0.80, 0.20), (0.98, 0.22), (0.99, 0.95), (0.82, 0.95)])
        hood = poly(h, w, [(0.33, 0.01), (0.67, 0.01), (0.64, 0.17), (0.36, 0.17)])
        strings = hood | poly(h, w, [(0.45, 0.17), (0.46, 0.17), (0.46, 0.58), (0.45, 0.58)]) \
            | poly(h, w, [(0.55, 0.17), (0.56, 0.17), (0.56, 0.58), (0.55, 0.58)])
        found = {'hoodie': [(body | sl_left | sl_right | hood, 0.9)],
                 'sleeve': [(sl_left, 0.8), (sl_right, 0.8)], 'hood': [(strings, 0.7)]}
        r = pd.propose(np.full((h, w, 3), 200, np.uint8), 'hoodie_pullover', fake(found))
        self.assertEqual(set(r['found']), {'Front', 'Hood', 'Left', 'Right'})
        self.assertEqual(r['missing'], ['Back'])
        self.assertLess(np.mean([p[0] for p in r['quads']['Right']]), 0.2)
        self.assertGreater(np.mean([p[0] for p in r['quads']['Left']]), 0.8)
        self.assertLess(max(p[1] for p in r['quads']['Hood']), 0.22)   # strings dropped
        self.assertTrue(near(r['quads']['Front'], [[0.2, 0.18], [0.8, 0.18], [0.78, 0.98], [0.22, 0.98]], 0.02),
                        r['quads']['Front'])

    def test_large_photo_is_shrunk_and_far_segments_ignored(self):
        h, w = 2400, 3600
        img = np.full((h, w, 3), 240, np.uint8)
        seg = fake({'sweater': [(np.zeros((1067, 1600), bool), 0.9)]})
        r = pd.propose(img, 'sweater', seg)
        self.assertEqual(seg.calls[0][0], (1067, 1600, 3))
        self.assertEqual(r['found'], [])
        corner = poly(1067, 1600, [(0.0, 0.0), (0.05, 0.0), (0.05, 0.05), (0.0, 0.05)])
        r = pd.propose(img, 'sweater', fake({'sleeve': [(corner, 0.9)]}))
        self.assertNotIn('Sleeve', r['found'])
        with self.assertRaises(ValueError):
            pd.propose(img, 'nope', seg)

    def test_overlay_and_quad_order(self):
        m = poly(400, 400, [(0.3, 0.1), (0.5, 0.15), (0.45, 0.9), (0.25, 0.85)])
        q = pd.quad(m, slanted=True)
        self.assertLess(q[0][1], q[3][1])
        self.assertLess(q[0][0], q[1][0])
        out = pd.overlay(np.zeros((400, 400, 3), np.uint8), {'Sleeve': [[p[0] / 400, p[1] / 400] for p in q]})
        self.assertGreater(out.sum(), 0)


class EndpointTests(unittest.TestCase):
    def test_auto_quads_uses_source_and_reports_missing_model(self):
        from test_v2 import FakeEngine
        from v2 import StudioV2
        img, found = hoodie_shot()
        with tempfile.TemporaryDirectory() as tmp:
            v2 = StudioV2(FakeEngine(), Path(tmp))
            ok, raw = cv2.imencode('.png', img)
            sha = 'a' * 64
            (v2.cache / (sha + '.png')).write_bytes(raw.tobytes())
            v2.segmenter = fake(found)
            r = v2.auto_quads({'source_id': sha, 'style': 'hoodie_pullover'})
            self.assertIn('Front', r['quads'])

            def broken(rgb, prompts):
                raise pd.Unavailable('Chưa tải được SAM 3')
            v2.segmenter = broken
            with self.assertRaises(RuntimeError):
                v2.auto_quads({'source_id': sha, 'style': 'hoodie_pullover'})
            with self.assertRaises(ValueError):
                v2.auto_quads({'source_id': 'x', 'style': 'hoodie_pullover'})


if __name__ == '__main__':
    unittest.main()
