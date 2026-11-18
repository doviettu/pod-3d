"""Tests for the chroma key: detection, the three presets, soft white strokes, enclosed green,
and the Redesign hookup (a design on a green screen is keyed before it is split)."""
from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chroma_key as ck  # noqa: E402


def green_design(key=(0, 250, 5)):
    """200x200 on a green screen: red square with a green patch inside, a 1 px anti-aliased
    edge next to a 1 px white stroke."""
    img = np.zeros((200, 200, 3), np.uint8)
    img[:] = key
    img[51:150, 51:150] = (200, 30, 30)
    img[81:120, 81:120] = (10, 240, 20)       # green that belongs to the design
    img[:, 20:22] = (128, 252, 128)           # half white, half green
    img[:, 22] = (255, 255, 255)              # thin white stroke
    return img


class DetectTest(unittest.TestCase):
    def test_detects_green_screen(self):
        key = ck.detect_key(green_design())
        self.assertEqual(key['name'], 'green')
        self.assertGreater(key['coverage'], 0.9)
        self.assertFalse(key['neutral'])

    def test_is_keyed_skips_white_and_transparent(self):
        self.assertIsNotNone(ck.is_keyed(green_design()))
        white = np.full((100, 100, 3), 255, np.uint8)
        self.assertIsNone(ck.is_keyed(white))
        rgba = np.zeros((100, 100, 4), np.uint8)
        rgba[..., 1] = 255
        self.assertIsNone(ck.is_keyed(rgba))     # already transparent

    def test_auto_refuses_neutral_unless_asked(self):
        white = np.full((50, 50, 3), 255, np.uint8)
        white[20:30, 20:30] = (0, 0, 200)
        with self.assertRaises(ValueError):
            ck.remove_key(white)
        out, _ = ck.remove_key(white, allow_neutral_auto=True)
        self.assertEqual(out[0, 0, 3], 0)
        self.assertEqual(out[25, 25, 3], 255)


class RemoveTest(unittest.TestCase):
    def test_balanced(self):
        out, info = ck.remove_key(green_design())
        self.assertEqual(out[5, 5, 3], 0)                # background gone
        self.assertEqual(out[60, 100, 3], 255)           # red kept
        self.assertEqual(out[100, 100, 3], 0)            # inner green keyed (balanced does not protect it)
        r, g, b, a = out[5, 20]
        self.assertTrue(100 < a < 160)                   # soft edge
        self.assertGreater(min(r, g, b), 240)            # white, not green-tinted
        self.assertEqual(tuple(out[5, 22]), (255, 255, 255, 255))
        self.assertAlmostEqual(info['transparent'] + info['partial'] + info['opaque'], 1, places=5)

    def test_safe_keeps_enclosed_green(self):
        out, _ = ck.remove_key(green_design(), preset='safe')
        self.assertEqual(out[5, 5, 3], 0)
        self.assertEqual(out[100, 100, 3], 255)
        out, _ = ck.remove_key(green_design(key=(10, 240, 20)), preset='safe', remove_holes=True)
        self.assertEqual(out[100, 100, 3], 0)            # a hole of pure key goes when asked

    def test_max_chokes(self):
        out, _ = ck.remove_key(green_design(), preset='max')
        self.assertEqual(out[5, 22, 3], 0)               # 1 px stroke lost, as documented
        self.assertEqual(out[100, 60, 3], 255)

    def test_keeps_source_alpha_and_explicit_key(self):
        img = np.dstack([green_design(), np.full((200, 200), 255, np.uint8)])
        img[60:70, 60:70, 3] = 0
        out, info = ck.remove_key(img, key='#00FA05')
        self.assertEqual(info['key_hex'], '#00FA05')
        self.assertEqual(out[65, 65, 3], 0)
        self.assertEqual(ck.trim_bounds(out), (20, 0, 130, 200))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            ck.remove_key(green_design(), preset='nope')
        with self.assertRaises(ValueError):
            ck.remove_key(green_design(), key='#12')


def gradient_case(seed=0):
    """Uneven AI-style green screen (gradient, vignette, noise) behind an orange disc with a soft
    glow and anti-aliased white strands. Returns (image uint8, true alpha, true colour)."""
    rng = np.random.default_rng(seed)
    h = w = 240
    yy, xx = np.mgrid[0:h, 0:w] / h
    screen = np.stack([0.05 + 0.15 * xx, 0.75 + 0.2 * yy, 0.05 + 0.1 * (1 - xx)], -1)
    screen *= (1 - ((xx - .5) ** 2 + (yy - .5) ** 2))[..., None]
    screen += rng.normal(0, 0.02, screen.shape)
    alpha = np.zeros((h, w), np.float32)
    cv2.circle(alpha, (120, 140), 55, 1, -1)
    for _ in range(30):
        x0 = int(rng.integers(70, 170))
        cv2.line(alpha, (x0, 90), (x0 + int(rng.integers(-35, 35)), 15 + int(rng.integers(0, 35))), 1, 1, cv2.LINE_AA)
    alpha = np.maximum(alpha, 0.6 * cv2.GaussianBlur(alpha, (0, 0), 6))
    colour = np.zeros((h, w, 3))
    colour[:] = (0.95, 0.55, 0.2)
    colour[yy < 0.4] = (0.98, 0.97, 0.9)
    img = np.clip((alpha[..., None] * colour + (1 - alpha[..., None]) * screen) * 255, 0, 255).astype(np.uint8)
    return img, alpha, colour


def composite_error(out, alpha, colour, shirt):
    a = out[..., 3:] / 255
    return float(np.abs(a * out[..., :3] / 255 + (1 - a) * shirt
                        - (alpha[..., None] * colour + (1 - alpha[..., None]) * shirt)).mean() * 255)


class UnevenScreenTest(unittest.TestCase):
    def test_local_background_beats_one_key_colour(self):
        img, alpha, colour = gradient_case()
        for preset in ('balanced', 'safe'):
            flat, _ = ck.remove_key(img, preset=preset, local_bg=False)
            fitted, _ = ck.remove_key(img, preset=preset)
            for shirt in (0.0, 1.0):
                self.assertLess(composite_error(fitted, alpha, colour, shirt),
                                composite_error(flat, alpha, colour, shirt), (preset, shirt))

    def test_surface_follows_the_gradient(self):
        img, alpha, _ = gradient_case()
        surf = ck.background_surface(img, ck.detect_key(img)['rgb'])
        corners = [surf[5, 5], surf[5, -5], surf[-5, 5], surf[-5, -5]]
        self.assertGreater(np.ptp([c[1] for c in corners]), 0.1)   # green varies across the screen

    def test_matting_on_soft_edges(self):
        try:
            import scipy  # noqa: F401
        except ImportError:
            self.skipTest('scipy not installed')
        img, alpha, colour = gradient_case()
        fast, _ = ck.remove_key(img)
        mat, _ = ck.remove_key(img, matting=True)
        semi = (alpha > 0.02) & (alpha < 0.98)
        self.assertLess(np.abs(mat[..., 3] / 255 - alpha)[semi].mean(), np.abs(fast[..., 3] / 255 - alpha)[semi].mean())
        self.assertLess(composite_error(mat, alpha, colour, 0.0), composite_error(fast, alpha, colour, 0.0))


class RedesignHookupTest(unittest.TestCase):
    def test_green_screen_design_splits_into_transparent_layers(self):
        import redesign as rd
        img = np.zeros((400, 400, 3), np.uint8)
        img[:] = (0, 255, 0)
        cv2.circle(img, (200, 200), 100, (230, 80, 40), -1)
        rgba = np.dstack([img, np.full((400, 400), 255, np.uint8)])
        with tempfile.TemporaryDirectory() as tmp:
            scene = rd.RedesignStore(tmp, ocr=None).analyze(rgba, 'green')
            layer = np.asarray(__import__('PIL.Image', fromlist=['Image']).open(Path(tmp) / scene['id'] / 'layer-1.png'))
        self.assertEqual(scene['keyed']['name'], 'green')
        self.assertIsNone(scene['background'])           # brief now asks for a transparent background
        self.assertEqual(len(scene['layers']), 1)
        self.assertEqual(layer[0, 0, 3], 0)

    def test_plain_white_design_is_left_alone(self):
        import redesign as rd
        img = np.full((300, 300, 4), 255, np.uint8)
        cv2.circle(img, (150, 150), 80, (20, 20, 200, 255), -1)
        with tempfile.TemporaryDirectory() as tmp:
            scene = rd.RedesignStore(tmp, ocr=None).analyze(img, 'white')
        self.assertIsNone(scene['keyed'])
        self.assertEqual(scene['background'], [255, 255, 255])


class EndpointTest(unittest.TestCase):
    def test_key_remove_returns_png(self):
        import base64
        import io
        from PIL import Image
        import v2
        raw = io.BytesIO()
        Image.fromarray(green_design()).save(raw, 'PNG')
        data = {'art_base64': base64.b64encode(raw.getvalue()).decode(), 'preset': 'safe'}
        out = v2.key_remove(data)
        png = Image.open(io.BytesIO(base64.b64decode(out['png_base64'])))
        self.assertEqual(png.mode, 'RGBA')
        self.assertEqual(out['key_hex'], '#00FA05')
        self.assertEqual(out['size'], [200, 200])
        self.assertEqual(png.getpixel((100, 100))[3], 255)


if __name__ == '__main__':
    unittest.main()
