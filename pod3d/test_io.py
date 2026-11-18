"""pod3d.io writes print files with DPI and an embedded sRGB profile."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from pod3d import io


class WriteTests(unittest.TestCase):
    def test_png_and_jpg_carry_dpi_and_srgb(self):
        img = np.full((40, 30, 3), (10, 120, 240), np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('a.png', 'a.jpg'):
                path = Path(tmp) / name
                io.write(path, img, dpi=300)
                with Image.open(path) as im:
                    self.assertEqual(round(im.info['dpi'][0]), 300)
                    self.assertEqual(im.info.get('icc_profile'), io.srgb_icc())
                self.assertEqual(io.read(path).shape, img.shape)


if __name__ == '__main__':
    unittest.main()
