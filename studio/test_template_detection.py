"""Offline regression checks; temporary catalogs never modify app assets."""
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, ImageDraw, ImageOps
import template_detection as detector


def encoded(image, fmt="PNG", **kwargs):
    stream = io.BytesIO()
    image.save(stream, format=fmt, **kwargs)
    return stream.getvalue()


def garment(kind):
    image = Image.new("RGB", (360, 420), "white")
    draw = ImageDraw.Draw(image)
    shapes = [
        [(100, 65), (145, 45), (215, 45), (260, 65), (320, 135),
         (270, 180), (245, 140), (245, 365), (115, 365), (115, 140), (90, 180), (40, 135)],
        [(120, 65), (145, 45), (215, 45), (240, 65), (245, 365), (115, 365)],
        [(90, 75), (140, 55), (220, 55), (270, 75), (320, 330),
         (265, 340), (240, 170), (240, 365), (120, 365), (120, 170), (95, 340), (40, 330)],
    ]
    draw.polygon(shapes[kind], fill=["navy", "maroon", "darkgreen"][kind])
    draw.ellipse((130, 100, 160, 130), fill="orange")
    draw.line((140, 175, 220, 290), fill="yellow", width=8)
    return image


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.refs = []
        for i in range(3):
            path = self.root / f"garment-{i}.png"
            path.write_bytes(encoded(garment(i)))
            self.refs.append(dict(id=f"template-{i}", label=f"Mẫu {i}",
                                  path=path, background="white", ready=True))

    def detect(self, data, refs=None):
        result = detector.detect_template(data, self.refs if refs is None else refs)
        json.dumps(result, allow_nan=False)
        self.assertEqual(set(result), {"selected_template", "suggested_template",
                                      "method", "reason", "score", "candidates"})
        self.assertNotIn(str(self.root), json.dumps(result))
        return result

    def test_exact_known_three(self):
        for ref in self.refs:
            result = self.detect(ref["path"].read_bytes())
            self.assertEqual(result["selected_template"], ref["id"])
            self.assertEqual(result["method"], "exact")

    def test_resized_jpeg(self):
        for i in range(3):
            for size in [(720, 840), (180, 210)]:
                data = encoded(garment(i).resize(size, Image.Resampling.LANCZOS),
                               "JPEG", quality=92)
                result = self.detect(data)
                self.assertEqual(result["selected_template"], f"template-{i}", result)

    def test_mirror_crop_and_unknown_are_manual(self):
        for image in (ImageOps.mirror(garment(0)),
                      garment(0).crop((20, 20, 340, 400)),
                      Image.new("RGB", (360, 420), "purple")):
            self.assertIsNone(self.detect(encoded(image))["selected_template"])

    def test_duplicate_and_near_pair_ambiguity(self):
        twin = dict(self.refs[0], id="old-template", label="Mẫu cũ")
        refs = self.refs + [twin]
        self.assertIsNone(self.detect(self.refs[0]["path"].read_bytes(), refs)["selected_template"])
        self.assertIsNone(self.detect(encoded(garment(0), "JPEG", quality=93), refs)["selected_template"])
        self.assertEqual(self.detect(self.refs[0]["path"].read_bytes(),
                                     [self.refs[0], dict(twin, ready=False)])["selected_template"],
                         "template-0")

    def test_same_id_metadata_duplicates(self):
        result = self.detect(self.refs[0]["path"].read_bytes(), [self.refs[0]] * 2)
        self.assertEqual(result["selected_template"], "template-0")
        self.assertEqual(len(result["candidates"]), 1)

    def test_complex_and_model_never_use_silhouette(self):
        changed = garment(0)
        ImageDraw.Draw(changed).rectangle((140, 150, 210, 290), fill="red")
        for background in ("complex", "model"):
            refs = [dict(self.refs[0], background=background)]
            result = self.detect(encoded(changed), refs)
            self.assertIsNone(result["selected_template"])
            self.assertEqual(result["candidates"][0]["method"], "reference-match")
            result = self.detect(encoded(garment(0), "JPEG", quality=95), refs)
            self.assertEqual(result["selected_template"], "template-0")

    def test_geometry_can_be_disabled(self):
        changed = garment(0)
        ImageDraw.Draw(changed).polygon(
            [(100, 65), (115, 140), (90, 180), (40, 135)], fill="red")
        refs = [dict(self.refs[0], allow_geometry=False)]
        self.assertIsNone(self.detect(encoded(changed), refs)["selected_template"])
        self.assertEqual(self.detect(encoded(garment(0), "JPEG", quality=95), refs)
                         ["selected_template"], "template-0")
        self.assertEqual(self.detect(self.refs[0]["path"].read_bytes(), refs)
                         ["selected_template"], "template-0")
        # A different print with unchanged geometry is eligible only when opted in.
        changed = garment(0)
        ImageDraw.Draw(changed).rectangle((140, 150, 210, 290), fill="red")
        self.assertEqual(self.detect(encoded(changed), [self.refs[0]])
                         ["selected_template"], "template-0")
        self.assertIsNone(self.detect(encoded(changed), refs)["selected_template"])

    def test_invalid_and_oversize(self):
        bad = [b"", b"not an image", encoded(garment(0), "GIF"),
               encoded(garment(0))[:100], encoded(garment(0), "JPEG")[:-100],
               b"x" * (detector.MAX_BYTES + 1)]
        animation = io.BytesIO()
        garment(0).save(animation, format="PNG", save_all=True,
                        append_images=[garment(1)])
        bad.append(animation.getvalue())
        for data in bad:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.detect(data)

    def test_dimension_limit_before_decode(self):
        for width, height in ((100000, 100000), (8000, 7000)):
            data = bytearray(encoded(garment(0)))
            data[16:24] = struct.pack(">II", width, height)
            data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
            with patch.object(Image.Image, "load", side_effect=AssertionError("decoded")):
                with self.assertRaises(ValueError):
                    self.detect(bytes(data))

    def test_invalid_reference_is_skipped(self):
        invalid = self.root / "bad.png"
        invalid.write_bytes(b"bad")
        refs = [dict(self.refs[0], path=invalid), dict(self.refs[1], ready=False)]
        self.assertEqual(self.detect(self.refs[0]["path"].read_bytes(), refs)["candidates"], [])

    def test_webp_static(self):
        result = self.detect(encoded(garment(0), "WEBP", lossless=True))
        self.assertEqual(result["selected_template"], "template-0")

    def test_catalog_and_cli(self):
        catalog = self.root / "refs.json"
        rows = [dict(ref, path=ref["path"].name) for ref in self.refs]
        catalog.write_text(json.dumps(rows))
        self.assertEqual(detector._catalog(catalog)[0]["path"], self.refs[0]["path"])
        run = subprocess.run([sys.executable, "-B", str(Path(detector.__file__)),
                              str(self.refs[1]["path"]), "--references", str(catalog)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["selected_template"], "template-1")
        for change in ({"path": "../outside.png"}, {"ready": "true"}, {"background": "other"},
                       {"background": []}, {"allow_geometry": "false"}):
            catalog.write_text(json.dumps([dict(rows[0], **change)]))
            with self.assertRaises(ValueError):
                detector._catalog(catalog)
        link = self.root / "escape.png"
        link.symlink_to(self.root.parent / "outside.png")
        catalog.write_text(json.dumps([dict(rows[0], path=link.name)]))
        with self.assertRaises(ValueError):
            detector._catalog(catalog)

    def test_actual_fixture_recompression(self):
        fixture = Path(__file__).parent / "runtime/segmentation-check/input.png"
        if not fixture.is_file():
            self.skipTest("Local photographic fixture unavailable")
        data = fixture.read_bytes()
        ref = dict(id="photo", label="Ảnh thực", path=fixture,
                   background="model", ready=True)
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((768, 768))
            jpeg = encoded(image, "JPEG", quality=95)
        self.assertEqual(self.detect(data, [ref])["selected_template"], "photo")
        a, at, ah = detector._decode(data)
        b, bt, bh = detector._decode(jpeg)
        result = self.detect(jpeg, [ref])
        self.assertEqual(result["selected_template"], "photo",
                         (result, a, b, int((ah != bh).sum()),
                          float(abs(at - bt).mean()),
                          float(detector.np.quantile(abs(at - bt), .99))))


if __name__ == "__main__":
    unittest.main()
