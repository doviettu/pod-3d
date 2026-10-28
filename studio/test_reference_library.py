import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

import reference_library as reference


class ReferenceLibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.samples = self.root / "samples"
        self.sample = self.samples / "paired"
        self.sample.mkdir(parents=True)
        self.template = SimpleNamespace(name="sweater", dpi=300, panels=[
            SimpleNamespace(name="Front", size=(12, 16)),
            SimpleNamespace(name="Back", size=(10, 14)),
        ])
        self.mockup = self.sample / "arbitrary mockup.jpg"
        self.image(self.mockup, (24, 24), "blue")
        for panel in self.template.panels:
            self.image(self.sample / (panel.name + ".jpg"), panel.size, "red")
        self.source = self.root / "unrelated-upload-name.png"
        self.source.write_bytes(self.mockup.read_bytes())
        self.library = reference.ReferenceLibrary(self.samples)
        self.destination = self.root / "restored"

    @staticmethod
    def image(path, size, color, **kwargs):
        with Image.new("RGB", size, color) as image:
            image.save(path, **kwargs)

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def find(self):
        return self.library.find(self.source, self.template)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): self.sha(p)
                for p in self.root.rglob("*") if p.is_file()}

    def test_exact_match_restore_pixels_dpi_hashes_and_originals(self):
        before = self.snapshot()
        match = self.find()
        self.assertIsNotNone(match)
        self.assertEqual(match["source"]["sha256"], self.sha(self.source))
        json.dumps(match)
        manifest = self.library.restore(match, self.destination, self.template)
        self.assertEqual(manifest["mode"], "source-original")
        self.assertEqual(manifest["template"], "sweater")
        self.assertEqual(set(manifest["panels"]), {"Front", "Back"})
        self.assertNotIn("approved", manifest)
        self.assertNotIn("reconstruction", manifest)
        self.assertNotIn("seam_verified", manifest)
        json.dumps(manifest)
        self.assertEqual(sorted(p.name for p in self.destination.iterdir()),
                         ["Back.png", "Front.png"])
        for panel in self.template.panels:
            original = self.sample / (panel.name + ".jpg")
            output = self.destination / (panel.name + ".png")
            info = manifest["panels"][panel.name]
            self.assertEqual(info["sha256"], self.sha(original))
            self.assertEqual(info["output_sha256"], self.sha(output))
            with Image.open(original) as source, Image.open(output) as restored:
                self.assertEqual(restored.format, "PNG")
                self.assertEqual(restored.size, panel.size)
                self.assertEqual(restored.tobytes(), source.tobytes())
                self.assertAlmostEqual(restored.info["dpi"][0], 300, places=1)
                self.assertAlmostEqual(restored.info["dpi"][1], 300, places=1)
        for name, digest in before.items():
            self.assertEqual(self.sha(self.root / name), digest)

    def test_dimensions_must_match_exactly(self):
        self.image(self.sample / "Back.jpg", (14, 10), "red")
        self.assertIsNone(self.find())

    def test_missing_panel_is_not_a_match(self):
        (self.sample / "Back.jpg").unlink()
        self.assertIsNone(self.find())

    def test_same_name_and_same_pixels_are_not_exact_bytes(self):
        self.source = self.root / self.mockup.name
        self.image(self.source, (24, 24), "green")
        self.assertIsNone(self.find())
        with Image.open(self.mockup) as image:
            image.save(self.source, format="PNG")
        self.assertIsNone(self.find())

    def test_png_mockup_and_panels(self):
        self.mockup.unlink()
        self.mockup = self.sample / "anything.PNG"
        self.image(self.mockup, (24, 24), "blue")
        self.source.write_bytes(self.mockup.read_bytes())
        (self.sample / "Front.jpg").unlink()
        self.image(self.sample / "Front.PNG", (12, 16), "red")
        self.assertIsNotNone(self.find())

    def test_no_output_modification_on_invalid_panel_set(self):
        match = self.find()
        self.image(self.sample / "Back.jpg", (1, 1), "red")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.library.restore(match, self.destination, self.template)
        self.assertFalse(self.destination.exists())
        self.assertEqual(before, self.snapshot())
        self.assertFalse(list(self.root.glob(".reference-*")))

    def test_existing_destination_and_original_library_are_never_written(self):
        match = self.find()
        self.destination.mkdir()
        sentinel = self.destination / "Front.png"
        sentinel.write_bytes(b"keep")
        before = self.snapshot()
        for destination in (self.destination, self.sample / "new-output"):
            with self.assertRaises(ValueError):
                self.library.restore(match, destination, self.template)
        self.assertEqual(before, self.snapshot())

    def test_changed_panel_or_mockup_hash_is_rejected_on_restore(self):
        for target in (self.sample / "Back.jpg", self.mockup):
            with self.subTest(target=target):
                match = self.find()
                original = target.read_bytes()
                with Image.open(target) as image:
                    self.image(target, image.size, "green")
                with self.assertRaises(ValueError):
                    self.library.restore(match, self.destination, self.template)
                self.assertFalse(self.destination.exists())
                target.write_bytes(original)

    def test_filenames_are_untrusted_and_never_used_as_output_paths(self):
        hostile = self.sample / "<img onerror=alert(1)>.jpg"
        self.mockup.rename(hostile)
        match = self.find()
        self.assertEqual(match["source"]["path"], hostile.name)
        for name in ("../escape", "/absolute", "a/b", "a\\b", ".", "Back.png", ""):
            with self.subTest(name=name):
                template = copy.deepcopy(self.template)
                template.panels[0].name = name
                with self.assertRaises(ValueError):
                    self.library.find(self.source, template)
                with self.assertRaises(ValueError):
                    self.library.restore(match, self.destination, template)
        forged = copy.deepcopy(match)
        forged["source"]["path"] = "../unrelated-upload-name.png"
        with self.assertRaises(ValueError):
            self.library.restore(forged, self.destination, self.template)
        forged = copy.deepcopy(match)
        forged["sample"] = "../samples/paired"
        with self.assertRaises(ValueError):
            self.library.restore(forged, self.destination, self.template)
        self.assertFalse(self.destination.exists())

    def test_symlink_samples_and_panels_do_not_escape(self):
        (self.sample / "Back.jpg").rename(self.root / "external.jpg")
        (self.sample / "Back.jpg").symlink_to(self.root / "external.jpg")
        self.assertIsNone(self.find())
        (self.sample / "Back.jpg").unlink()
        (self.root / "external.jpg").rename(self.sample / "Back.jpg")
        self.sample.rename(self.root / "outside")
        self.sample.symlink_to(self.root / "outside", target_is_directory=True)
        self.assertIsNone(self.find())

    def test_symlink_mockup_and_source_are_rejected(self):
        self.mockup.rename(self.root / "mockup.jpg")
        self.mockup.symlink_to(self.root / "mockup.jpg")
        self.assertIsNone(self.find())
        self.mockup.unlink()
        (self.root / "mockup.jpg").rename(self.mockup)
        self.source.unlink()
        self.source.symlink_to(self.mockup)
        self.assertIsNone(self.find())

    def test_restore_rechecks_symlinks_and_rejects_symlink_destination(self):
        match = self.find()
        (self.sample / "Back.jpg").rename(self.root / "external.jpg")
        (self.sample / "Back.jpg").symlink_to(self.root / "external.jpg")
        with self.assertRaises((OSError, ValueError)):
            self.library.restore(match, self.destination, self.template)
        self.destination.symlink_to(self.root / "missing", target_is_directory=True)
        with self.assertRaises(ValueError):
            self.library.restore(match, self.destination, self.template)
        self.assertFalse((self.root / "missing").exists())

    def test_only_immediate_sample_directories(self):
        self.sample.rename(self.samples / "nested")
        nested = self.samples / "nested"
        nested.rename(self.root / "nested")
        (self.samples / "wrapper").mkdir()
        (self.root / "nested").rename(self.samples / "wrapper" / "nested")
        self.assertIsNone(self.find())

    def test_ambiguous_panel_names_are_rejected(self):
        self.image(self.sample / "Front.png", (12, 16), "red")
        self.assertIsNone(self.find())

    def test_corrupt_and_non_static_and_wrong_formats_are_rejected(self):
        back = self.sample / "Back.jpg"
        original = back.read_bytes()
        back.write_bytes(b"not an image")
        self.assertIsNone(self.find())
        self.image(back, (10, 14), "red", format="GIF")
        self.assertIsNone(self.find())
        with Image.new("RGB", (10, 14), "red") as first, Image.new("RGB", (10, 14), "blue") as second:
            first.save(back, format="PNG", save_all=True, append_images=[second])
        self.assertIsNone(self.find())
        back.write_bytes(original)
        with patch.object(reference, "MAX_PIXELS", 400):
            self.assertIsNone(self.find())

    def test_scan_limits_and_no_stale_cache(self):
        self.assertIsNotNone(self.find())
        (self.sample / "Back.jpg").unlink()
        self.assertIsNone(self.find())
        self.image(self.sample / "Back.jpg", (10, 14), "red")
        self.assertIsNotNone(self.find())
        with patch.object(reference, "MAX_ENTRIES", 2):
            self.assertIsNone(self.find())

    def test_malformed_matches_and_panel_bytes_are_not_mockups(self):
        for match in (None, {}, {"source": None}, {"source": {"path": []}}):
            with self.subTest(match=match):
                with self.assertRaises(ValueError):
                    self.library.restore(match, self.destination, self.template)
                self.assertFalse(self.destination.exists())
        self.source.write_bytes((self.sample / "Front.jpg").read_bytes())
        self.assertIsNone(self.find())

    def test_encode_failure_does_not_publish_partial_output(self):
        match = self.find()
        before = self.snapshot()
        with patch.object(Image.Image, "save", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.library.restore(match, self.destination, self.template)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob(".reference-*")))

    def test_invalid_template_and_changed_dpi(self):
        match = self.find()
        for dpi in (0, -1, float("nan"), True):
            template = copy.deepcopy(self.template)
            template.dpi = dpi
            with self.assertRaises(ValueError):
                self.library.find(self.source, template)
        self.template.dpi = 150
        with self.assertRaises(ValueError):
            self.library.restore(match, self.destination, self.template)


if __name__ == "__main__":
    unittest.main()
