import unittest
import numpy as np
from artwork_quality import exterior_background, locked_composite, stable_sleeve, repeat_bands


class ArtworkQualityTests(unittest.TestCase):
    def test_white_artwork_is_not_exterior_background(self):
        image = np.full((40, 50, 3), 255, np.uint8)
        image[5:35, 5:45] = [30, 30, 160]
        image[12:25, 15:30] = 255
        mask = exterior_background(image)
        self.assertTrue(mask[0, 0])
        self.assertFalse(mask[16, 20])
        self.assertFalse(mask[6, 6])

    def test_pixel_lock_is_exact_and_rejects_soft_or_mismatched_masks(self):
        original = np.arange(90, dtype=np.uint8).reshape(5, 6, 3)
        editable = np.zeros((5, 6), bool)
        editable[:, :2] = True
        result = locked_composite(original, np.full_like(original, 255), editable)
        np.testing.assert_array_equal(result[~editable], original[~editable])
        self.assertTrue((result[editable] == 255).all())
        with self.assertRaises(ValueError):
            locked_composite(original, original, editable.astype(float))
        with self.assertRaises(ValueError):
            locked_composite(original, original[:2], editable)

    def test_sleeve_uses_one_phase_for_entire_height_and_keeps_observed_pixels(self):
        image = np.full((60, 120, 3), 255, np.uint8)
        known = np.zeros((60, 120), bool)
        known[:, 40:80] = True
        row = np.arange(40, dtype=np.uint8)
        image[:, 40:80] = np.stack((row, row + 30, row + 100), -1)[None]
        image[30:, 40:80] += 10
        output, holes = stable_sleeve(image, known)
        np.testing.assert_array_equal(output[known], image[known])
        self.assertFalse(holes.any())
        self.assertFalse((output == 255).all(2).any())
        np.testing.assert_array_equal(output[30].astype(int) - output[0].astype(int), 10)

    def test_two_complete_repeats_extend_a_wide_motif_without_rewriting_source(self):
        rng = np.random.default_rng(4)
        unit = rng.integers(20, 220, (40, 350, 3), dtype=np.uint8)
        full = unit[:, np.arange(1500) % 350]
        known = np.zeros((40, 1500), bool)
        known[:, 310:1150] = True
        source = np.where(known[..., None], full, 255).astype(np.uint8)
        output, holes, periods = repeat_bands(source, known, bands=[(0, 40)], max_error=1)
        self.assertEqual(periods[0]['period_px'], 350)
        self.assertFalse(holes.any())
        np.testing.assert_array_equal(output, full)

    def test_nonrepeating_picture_and_unknown_sleeve_do_not_claim_a_repeat(self):
        rng = np.random.default_rng(10)
        image = rng.integers(0, 255, (45, 160, 3), dtype=np.uint8)
        known = np.zeros((45, 160), bool)
        known[:, 30:130] = True
        output, holes, periods = repeat_bands(image, known, max_error=2)
        self.assertFalse(periods)
        self.assertTrue(holes.any())
        np.testing.assert_array_equal(output, image)
        with self.assertRaises(ValueError):
            stable_sleeve(image, np.zeros_like(known))


if __name__ == '__main__':
    unittest.main()
