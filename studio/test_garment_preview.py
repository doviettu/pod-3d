import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote, unquote

import cv2
import numpy as np
from PIL import Image

import garment_preview as preview


class FakeEngine:
    def __init__(self, root, template):
        self.root = root
        self.templates = SimpleNamespace(load=lambda name: template)
        self.job = {"template": "synthetic", "artifacts": [], "reviews": {}, "previews": [],
                    "filename": "../../never-read.png", "panels": ["Front"]}
        self.requests = []

    def get_job(self, job_id):
        return copy.deepcopy(self.job)

    def url(self, job_id, path):
        relative = Path(path).resolve().relative_to((self.root / job_id).resolve())
        return "/files/" + job_id + "/" + quote(relative.as_posix())

    def add_artifact(self, job_id, path, kind, name=None):
        self.job["artifacts"].append({"url": self.url(job_id, path), "kind": kind, "name": name})

    def file_path(self, job_id, relative):
        self.requests.append(relative)
        folder = (self.root / job_id).resolve()
        target = (folder / relative).resolve()
        allowed = {a["url"] for a in self.job["artifacts"]}
        if (not target.is_relative_to(folder) or not target.is_file()
                or self.url(job_id, target) not in allowed):
            raise FileNotFoundError(relative)
        return target


class GarmentPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.job_id = "a" * 32
        self.folder = self.root / self.job_id
        self.folder.mkdir()
        self.map_dir = self.folder / "maps"
        self.map_dir.mkdir()
        self.names = ["Front", "Back", "Sleeve"]
        self.template = SimpleNamespace(
            panels=[SimpleNamespace(name=n, size=(9, 9)) for n in self.names],
            map_path=lambda n: self.map_dir / ("map_" + n + ".npz"),
            panel=lambda n: next(p for p in self.template.panels if p.name == n))
        self.engine = FakeEngine(self.root, self.template)
        self.texture = np.zeros((9, 9, 3), np.uint8)
        yy, xx = np.mgrid[:9, :9]
        self.texture[..., 0] = xx * 20
        self.texture[..., 1] = yy * 23
        self.texture[..., 2] = xx * 7 + yy * 5
        self.original = np.full((20, 20, 3), [11, 17, 29], np.uint8)
        Image.fromarray(self.original).save(self.folder / "input.png")
        yy, xx = np.mgrid[:3, :3] * 4
        self.grid = np.stack((14 - yy, 2 + xx), axis=-1).astype(np.float32)
        self.cov = np.ones((3, 3), np.float32)
        self.write_map()

    def write_map(self, **changes):
        data = dict(grid=self.grid, cov=self.cov, work_size=[9, 9], step=4, mockup_size=[20, 20])
        data.update(changes)
        path = self.template.map_path("Front")
        np.savez(path, **data)
        return path

    def panel(self, relative, color=None, recorded=True):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(self.texture if color is None else np.full_like(self.texture, color)).save(path)
        if recorded:
            self.engine.add_artifact(self.job_id, path, "image")
        return path

    def review(self, approved=False, revision="b" * 32, recorded=True):
        relative = f"reviews/{revision}/Front-{'approved' if approved else 'draft'}.png"
        self.panel(relative, [210, 40, 80], recorded)
        (self.folder / "reviews" / revision / "spec.json").write_text(json.dumps({"approved": approved}))
        # The spec is authoritative, even if report metadata disagrees.
        self.engine.job["reviews"]["Front"] = {"revision": revision, "approved": not approved}
        return relative

    def test_inverse_orientation_nonuniform_texture_and_exact_coverage(self):
        cov = self.cov.copy()
        cov[0, 0] = 0.49
        path = self.write_map(cov=cov)
        actual = self.original.copy()
        self.assertGreater(preview._project(actual, self.texture, preview._load_map(path)), 0)
        expected = self.original.copy()
        for y in range(20):
            for x in range(20):
                sx, sy = y - 2, 14 - x
                if 0 <= sx <= 8 and 0 <= sy <= 8 and (sx >= 4 or sy >= 4):
                    expected[y, x] = self.texture[sy, sx]
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(actual[8, 12], self.texture[2, 6])
        np.testing.assert_array_equal(actual[4, 8], self.texture[6, 2])

    def test_scaling_matches_mockup_dimensions_and_warps_small_rois(self):
        canvas = np.zeros((40, 60, 3), np.uint8)
        real_warp = cv2.warpAffine
        with patch.object(preview.cv2, "warpAffine", wraps=real_warp) as warp:
            preview._project(canvas, self.texture, preview._load_map(self.template.map_path("Front")))
        np.testing.assert_array_equal(canvas[16, 36], self.texture[2, 6])
        self.assertGreater(warp.call_count, 0)
        for call in warp.call_args_list:
            self.assertLessEqual(call.args[2][0], 13)
            self.assertLessEqual(call.args[2][1], 9)
            self.assertLess(call.args[0].shape[0], 40)

    def test_white_background_is_clipped_but_enclosed_white_artwork_is_preserved(self):
        canvas = np.full((30, 30, 3), 255, np.uint8)
        cv2.rectangle(canvas, (8, 8), (21, 21), (120, 30, 40), thickness=2)
        mask = preview._foreground(canvas)
        self.assertFalse(mask[0, 0])
        self.assertTrue(mask[14, 14])
        self.assertFalse(preview._foreground(np.full_like(canvas, 255)).any())
        self.assertTrue(preview._foreground(np.full_like(canvas, 20)).all())
        original = self.original.copy()
        clip = np.zeros(original.shape[:2], bool)
        clip[7:10, 7:10] = True
        coverage = np.zeros(clip.shape, bool)
        preview._project(original, self.texture, preview._load_map(self.template.map_path('Front')), clip=clip, coverage=coverage)
        np.testing.assert_array_equal(original[~clip], self.original[~clip])
        self.assertTrue((original[clip] != self.original[clip]).any())
        self.assertFalse(coverage[~clip].any())
        self.assertTrue(coverage[clip].all())

    def test_invalid_hidden_and_degenerate_triangles_never_paint(self):
        for grid, cov in (
            (np.zeros_like(self.grid), self.cov),
            (self.grid, np.zeros_like(self.cov)),
            (np.full_like(self.grid, -1), self.cov),
            (np.full_like(self.grid, 21), self.cov),
            (np.full_like(self.grid, np.nan), self.cov),
            (self.grid, np.full_like(self.cov, np.nan)),
        ):
            with self.subTest(grid=grid[0, 0], cov=cov[0, 0]):
                canvas = self.original.copy()
                mapping = preview._load_map(self.write_map(grid=grid, cov=cov))
                self.assertEqual(preview._project(canvas, self.texture, mapping), 0)
                np.testing.assert_array_equal(canvas, self.original)

    def test_publishes_immutable_allowlisted_preview_and_draft_metadata(self):
        self.panel("local/Front.png")
        before = (self.folder / "input.png").read_bytes()
        result = preview.create_garment_preview(self.engine, self.job_id)
        self.assertEqual(result["kind"], "calibrated-2d")
        self.assertEqual(result["panels"], ["Front"])
        self.assertEqual(result["draft_panels"], ["Front"])
        self.assertEqual(result["missing_panels"], self.names)
        self.assertEqual(result["unmapped_panels"], ["Back", "Sleeve"])
        self.assertFalse(result["quality_approved"])
        relative = unquote(result["url"].split("/" + self.job_id + "/", 1)[1])
        self.assertRegex(relative, r"^previews/garment-[0-9a-f]{32}\.jpg$")
        target = self.engine.file_path(self.job_id, relative)
        content = target.read_bytes()
        self.assertEqual(self.engine.job["artifacts"][-1]["name"], "Xem trên áo — bản kiểm tra 2D.jpg")
        self.assertEqual(self.engine.job["artifacts"][-1]["kind"], "image")
        with Image.open(target) as image:
            self.assertEqual(image.height, 20 + preview.LABEL_HEIGHT)
            self.assertLessEqual(max(image.size), 1600)
            self.assertGreater(np.asarray(image)[28:76].std(), 0)
            np.testing.assert_allclose(image.getpixel((0, 0)), self.original[0, 0], atol=3)
        second = preview.create_garment_preview(self.engine, self.job_id)
        self.assertNotEqual(second["url"], result["url"])
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual((self.folder / "input.png").read_bytes(), before)
        self.assertEqual(self.engine.job["reviews"], {})

    def test_current_review_then_recorded_openai_then_recorded_local(self):
        self.panel("local/Front.png")
        self.panel("openai/Front.png", [40, 180, 90])
        self.assertEqual(preview.create_garment_preview(self.engine, self.job_id)["sources"]["Front"],
                         "openai/Front.png")
        relative = self.review(False)
        draft = preview.create_garment_preview(self.engine, self.job_id)
        self.assertEqual(draft["sources"]["Front"], relative)
        self.assertEqual(draft["draft_panels"], ["Front"])
        self.assertIn("Front", draft["missing_panels"])
        relative = self.review(True, "c" * 32)
        approved = preview.create_garment_preview(self.engine, self.job_id)
        self.assertEqual(approved["sources"]["Front"], relative)
        self.assertEqual(approved["draft_panels"], [])
        self.assertEqual(approved["missing_panels"], ["Back", "Sleeve"])
        self.assertTrue(approved["draft"])
        self.assertFalse(approved["quality_approved"])

    def test_unrecorded_or_stale_files_are_not_selected(self):
        self.panel("local/Front.png")
        self.panel("openai/Front.png", recorded=False)
        self.review(True, "c" * 32)
        self.engine.job["reviews"].clear()
        result = preview.create_garment_preview(self.engine, self.job_id)
        self.assertEqual(result["sources"]["Front"], "local/Front.png")
        relative = self.review(False, recorded=False)
        result = preview.create_garment_preview(self.engine, self.job_id)
        self.assertEqual(result["sources"]["Front"], "local/Front.png")
        self.assertIn(relative, self.engine.requests)
        self.engine.job["artifacts"] = []
        with self.assertRaisesRegex(ValueError, "vùng phủ"):
            preview.create_garment_preview(self.engine, self.job_id)

    def test_review_suffix_comes_only_from_current_spec(self):
        self.panel("local/Front.png")
        relative = self.review(False)
        (self.folder / relative).rename(self.folder / relative.replace("-draft", "-approved"))
        self.engine.add_artifact(self.job_id, self.folder / relative.replace("-draft", "-approved"), "image")
        result = preview.create_garment_preview(self.engine, self.job_id)
        self.assertEqual(result["sources"]["Front"], "local/Front.png")
        self.assertIn("Front", result["missing_panels"])

    def test_bad_review_revision_is_rejected_before_path_access(self):
        for revision in ("../escape", "/tmp/escape", "a" * 31, "g" * 32, "a" * 32 + "/..", None):
            with self.subTest(revision=revision):
                self.engine.job["reviews"]["Front"] = {"revision": revision}
                with self.assertRaisesRegex(ValueError, "Phiên bản"):
                    preview.create_garment_preview(self.engine, self.job_id)
        self.assertEqual(self.engine.requests, [])

    def test_bad_review_spec_is_friendly_error(self):
        self.review()
        path = self.folder / "reviews" / ("b" * 32) / "spec.json"
        for raw in ('not-json', '[]', '{"approved": "yes"}', '{}'):
            with self.subTest(raw=raw):
                path.write_text(raw)
                with self.assertRaisesRegex(ValueError, "bản duyệt"):
                    preview.create_garment_preview(self.engine, self.job_id)

    def test_traversal_and_symlinks_cannot_escape_job_folder(self):
        for job_id in ("../escape", "/tmp/escape", "a" * 32 + "/.."):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                preview.create_garment_preview(self.engine, job_id)
        self.template.panels[0].name = "../Front"
        with self.assertRaisesRegex(ValueError, "Tên mảnh"):
            preview.create_garment_preview(self.engine, self.job_id)
        self.template.panels[0].name = "Front"
        self.panel("local/Front.png")
        for relative in ("previews", "reviews"):
            link = self.folder / relative
            link.symlink_to(self.root, target_is_directory=True)
            if relative == "reviews":
                self.engine.job["reviews"]["Front"] = {"revision": "b" * 32}
            with self.assertRaisesRegex(ValueError, "Đường dẫn"):
                preview.create_garment_preview(self.engine, self.job_id)
            link.unlink()
        self.engine.job["reviews"].clear()
        source = self.folder / "input.png"
        source.rename(self.folder / "other.png")
        source.symlink_to(self.folder / "other.png")
        with self.assertRaisesRegex(ValueError, "Đường dẫn"):
            preview.create_garment_preview(self.engine, self.job_id)

    def test_exactly_one_fixed_original_is_required(self):
        self.panel("local/Front.png")
        Image.fromarray(self.original).save(self.folder / "input.jpg")
        with self.assertRaisesRegex(ValueError, "đúng một"):
            preview.create_garment_preview(self.engine, self.job_id)
        (self.folder / "input.jpg").unlink()
        (self.folder / "input.png").rename(self.folder / "arbitrary.png")
        self.engine.job["source"] = str(self.folder / "arbitrary.png")
        with self.assertRaisesRegex(ValueError, "đúng một"):
            preview.create_garment_preview(self.engine, self.job_id)

    def test_no_coverage_map_or_visible_coverage_is_rejected(self):
        self.panel("local/Front.png")
        self.template.map_path("Front").unlink()
        with self.assertRaisesRegex(ValueError, "hiệu chuẩn"):
            preview.create_garment_preview(self.engine, self.job_id)
        self.write_map(cov=np.zeros_like(self.cov))
        with self.assertRaisesRegex(ValueError, "vùng phủ"):
            preview.create_garment_preview(self.engine, self.job_id)
        self.assertFalse((self.folder / "previews").exists())

    def test_map_validation_and_pickle_are_rejected(self):
        for changes in ({"cov": np.ones((2, 2))}, {"step": 0}, {"work_size": [-1, 9]},
                        {"mockup_size": [0, 20]}, {"grid": np.array([{"bad": "object"}], object)}):
            with self.subTest(changes=list(changes)), self.assertRaisesRegex(ValueError, "UV"):
                preview._load_map(self.write_map(**changes))

    def test_image_limits_dimensions_and_normalized_output(self):
        self.panel("local/Front.png")
        with patch.object(preview, "MAX_PIXELS", 399), self.assertRaisesRegex(ValueError, "50 triệu"):
            preview.create_garment_preview(self.engine, self.job_id)
        Image.new("RGB", (8, 9)).save(self.folder / "local/Front.png")
        with self.assertRaisesRegex(ValueError, "không hợp lệ"):
            preview.create_garment_preview(self.engine, self.job_id)
        self.panel("local/Front.png")
        Image.new("RGB", (800, 2000)).save(self.folder / "input.png")
        result = preview.create_garment_preview(self.engine, self.job_id)
        target = self.folder / unquote(result["url"].split("/" + self.job_id + "/", 1)[1])
        with Image.open(target) as image:
            self.assertEqual(max(image.size), 1600)


if __name__ == "__main__":
    unittest.main()
