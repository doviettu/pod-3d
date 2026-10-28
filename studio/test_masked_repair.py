import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

import masked_repair as repair
import openai_api as api
from design_spec import build_design_spec, design_spec_hash


class MaskedRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.target, self.mockup = [self.root / n for n in
                                                ("source.png", "target.png", "mockup.png")]
        self.settings = SimpleNamespace(model=api.MODELS[0], image_size="1K",
                                        quality="medium", api_key="sk-offline-test")
        self.spec = build_design_spec({"panels": [{"name": "Sleeve", "size": [120, 80]}]},
            supplied={"text_layers": [{"panel": "Sleeve", "bounds": [0.25, 0.25, 0.5, 0.5],
                                      "provenance": "human: protected logo"}]})
        Image.new("RGB", (120, 80), "red").save(self.source, dpi=(300, 240))
        Image.new("RGB", (30, 60), "green").save(self.mockup)
        self.target.write_bytes(b"previous result")
        # Even a misplaced call cannot reach a real socket in this suite.
        guard = patch("openai_api.urllib.request.build_opener",
                      side_effect=AssertionError("Network forbidden in tests"))
        guard.start()
        self.addCleanup(guard.stop)
        request = patch("openai_api._request", return_value=self.response())
        self.request = request.start()
        self.addCleanup(request.stop)

    def response(self, size=(1024, 1024), fmt="PNG"):
        stream = io.BytesIO()
        Image.new("RGB", size, "blue").save(stream, fmt)
        return {"data": [{"b64_json": base64.b64encode(stream.getvalue()).decode()}],
                "usage": {"secret": "sk-provider-secret"}, "untrusted": "do not echo"}

    def run_repair(self, **overrides):
        args = {"panel": "Sleeve", "spec": self.spec, "editable_box": [12, 8, 84, 56],
                "protected_boxes": ([60, 8, 12, 56],), "mockup": self.mockup}
        args.update(overrides)
        return repair.repair_image(self.source, self.target, self.settings, **args)

    def decode(self, reference):
        raw = base64.b64decode(reference["image_url"].split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            return image.copy()

    def test_full_canvas_response_preserves_every_locked_pixel_and_dpi(self):
        before_spec = copy.deepcopy(self.spec)
        with Image.open(self.source) as image:
            original, dpi = image.copy(), image.info["dpi"]
        metadata = self.run_repair()
        self.request.assert_called_once()
        with Image.open(self.target) as output:
            self.assertEqual(output.size, original.size)
            self.assertEqual(output.mode, "RGB")
            self.assertEqual(output.info["dpi"], dpi)
            changed = 0
            for y in range(80):
                for x in range(120):
                    allowed = (12 <= x < 96 and 8 <= y < 64
                               and not 60 <= x < 72
                               and not (30 <= x < 60 and 20 <= y < 40))
                    if allowed:
                        self.assertEqual(output.getpixel((x, y)), (0, 0, 255))
                        changed += 1
                    else:
                        self.assertEqual(output.getpixel((x, y)), original.getpixel((x, y)))
        self.assertEqual(metadata["changed_pixels"], changed)
        self.assertEqual(metadata["locked_changed_pixels"], 0)
        self.assertIs(metadata["pixel_lock_verified"], True)
        self.assertEqual(metadata["paid_calls"], 1)
        self.assertTrue(metadata["manual_review_required"])
        self.assertEqual(metadata["quality_status"], "awaiting_review")
        self.assertNotIn("secret", json.dumps(metadata))
        self.assertNotIn("sk-offline", json.dumps(metadata))
        self.assertEqual(self.spec, before_spec)
        payload = self.request.call_args.args[1]
        self.assertEqual(metadata["prompt_hash"], hashlib.sha256(payload["prompt"].encode()).hexdigest())
        self.assertEqual(metadata["input_spec_hash"], design_spec_hash(self.spec))
        self.assertEqual(len(metadata["spec_hash"]), 64)
        self.assertIn("manual: explicit editable_box", payload["prompt"])

    def assert_hoodie_panel_pixel_lock(self, panel):
        spec = build_design_spec({"panels": [{"name": panel, "size": [120, 80]}]},
            supplied={"text_layers": [{"panel": panel, "bounds": [0.25, 0.25, 0.5, 0.5],
                                      "provenance": "human: protected logo"}]})
        before = copy.deepcopy(spec)
        metadata = self.run_repair(panel=panel, spec=spec)
        self.request.assert_called_once()
        self.assertEqual(spec, before)
        self.assertEqual(metadata["panel"], panel)
        self.assertTrue(metadata["pixel_lock_verified"])
        self.assertEqual(metadata["locked_changed_pixels"], 0)
        with Image.open(self.source) as original, Image.open(self.target) as output:
            self.assertEqual(output.size, original.size)
            self.assertEqual(output.info["dpi"], original.info["dpi"])
            changed = 0
            for y in range(80):
                for x in range(120):
                    allowed = (12 <= x < 96 and 8 <= y < 64
                               and not 60 <= x < 72
                               and not (30 <= x < 60 and 20 <= y < 40))
                    expected = (0, 0, 255) if allowed else original.getpixel((x, y))
                    self.assertEqual(output.getpixel((x, y)), expected)
                    changed += allowed
        self.assertGreater(changed, 0)
        self.assertEqual(metadata["changed_pixels"], changed)
        prompt = self.request.call_args.args[1]["prompt"]
        if panel == "Hood":
            self.assertIn("Hood exterior laid flat", prompt)
            self.assertIn("No front/back hoodie silhouette", prompt)
        else:
            self.assertIn("wearer's own right sleeve", prompt)
            self.assertIn("shoulder at top and cuff downward", prompt)
            self.assertIn("Never automatically mirror", prompt)
            self.assertIn("Do not clone body portraits", prompt)

    def test_hood_mock_repair_preserves_all_locked_pixels(self):
        self.assert_hoodie_panel_pixel_lock("Hood")

    def test_right_mock_repair_preserves_all_locked_pixels(self):
        self.assert_hoodie_panel_pixel_lock("Right")

    def test_trim_panels_in_spec_are_rejected_before_request(self):
        for panel in ("Collar", "Hem", "Wrist"):
            spec = build_design_spec({"panels": [{"name": panel, "size": [120, 80]}]})
            with self.subTest(panel=panel), self.assertRaises(api.OpenAIError):
                self.run_repair(panel=panel, spec=spec)
        self.request.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"previous result")

    def test_repair_does_not_iterate_pixels_with_getdata(self):
        with patch.object(Image.Image, "getdata", side_effect=AssertionError("Use bulk mask operations")):
            metadata = self.run_repair()
        self.assertTrue(metadata["pixel_lock_verified"])
        self.assertEqual(metadata["locked_changed_pixels"], 0)
        self.request.assert_called_once()

    def test_payload_image_order_mask_schema_and_working_dimensions(self):
        self.run_repair()
        payload = self.request.call_args.args[1]
        self.assertEqual(payload["n"], 1)
        self.assertEqual(payload["model"], self.settings.model)
        self.assertEqual(payload["size"], "1024x1024")
        self.assertEqual(payload["output_format"], "png")
        self.assertEqual(len(payload["images"]), 2)
        self.assertEqual(set(payload["mask"]), {"image_url"})
        canvas, context = [self.decode(ref) for ref in payload["images"]]
        mask = self.decode(payload["mask"])
        self.assertEqual(canvas.size, (1024, 1024))
        self.assertEqual(canvas.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(canvas.getpixel((500, 500)), (255, 0, 0))
        self.assertEqual(context.size, (30, 60))
        self.assertEqual(context.getpixel((0, 0)), (0, 128, 0))
        self.assertEqual(mask.mode, "RGBA")
        self.assertEqual(set(mask.getchannel("A").getdata()), {0, 255})
        self.assertEqual(mask.getpixel((0, 0))[3], 255)
        self.assertEqual(mask.getpixel((140, 260))[3], 0)
        self.assertIn("Image 1 = editable approved flat canvas", payload["prompt"])
        self.assertIn("Image 2 = ORIGINALMOCKUP", payload["prompt"])

    def test_working_mask_rounds_inward_and_protections_outward(self):
        size = (2051, 1025)
        Image.new("RGB", size, "red").save(self.source)
        self.spec = build_design_spec({"panels": [{"name": "Sleeve", "size": list(size)}]})
        selected = [103, 101, 1200, 700]
        protected = [401, 302, 101, 99]
        self.run_repair(editable_box=selected, protected_boxes=[protected])
        mask = self.decode(self.request.call_args.args[1]["mask"]).getchannel("A")
        iw, ih, left, top = 1024, round(1025 * 1024 / 2051), 0, (1024 - round(1025 * 1024 / 2051)) // 2
        self.assertIsNotNone(mask.getbbox())
        editable_count = 0
        for y in range(1024):
            for x in range(1024):
                if mask.getpixel((x, y)) == 0:
                    editable_count += 1
                    l, r = (x - left) * size[0] / iw, (x + 1 - left) * size[0] / iw
                    t, b = (y - top) * size[1] / ih, (y + 1 - top) * size[1] / ih
                    self.assertGreaterEqual(l, 103)
                    self.assertLessEqual(r, 1303)
                    self.assertGreaterEqual(t, 101)
                    self.assertLessEqual(b, 801)
                    self.assertTrue(r <= 401 or l >= 502 or b <= 302 or t >= 401)
        self.assertGreater(editable_count, 0)

    def test_invalid_boxes_fail_before_request(self):
        for value in ([0, 0, 0, 1], [-1, 0, 1, 1], [119, 0, 2, 1],
                      [0, 0, 1, 81], [0, 0, 1.0, 1], [True, 0, 1, 1],
                      [0, 0, 1], None):
            for field in ("editable_box", "protected_boxes"):
                args = {field: [value] if field == "protected_boxes" else value}
                with self.subTest(field=field, value=value), self.assertRaises(api.OpenAIError):
                    self.run_repair(**args)
        self.request.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"previous result")

    def test_fully_protected_selection_and_text_have_zero_calls(self):
        for args in ({"protected_boxes": [[12, 8, 84, 56]]},
                     {"editable_box": [31, 21, 10, 10], "protected_boxes": []}):
            with self.assertRaises(api.OpenAIError):
                self.run_repair(**args)
        self.request.assert_not_called()

    def test_downsampled_empty_selection_has_zero_calls(self):
        Image.new("RGB", (4000, 1000), "red").save(self.source)
        self.spec = build_design_spec({"panels": [{"name": "Sleeve", "size": [4000, 1000]}]})
        with self.assertRaises(api.OpenAIError):
            self.run_repair(editable_box=[1, 1, 1, 1], protected_boxes=[])
        self.request.assert_not_called()

    def test_source_and_spec_validation_precedes_request(self):
        for image in (Image.new("RGBA", (120, 80)), Image.new("RGB", (100, 80))):
            image.save(self.source)
            with self.assertRaises(api.OpenAIError):
                self.run_repair()
        Image.new("RGB", (120, 80)).save(self.source)
        with patch.object(repair, "MAX_PIXELS", 100):
            with self.assertRaises(api.OpenAIError):
                self.run_repair()
        for args in ({"panel": "Collar"}, {"panel": "../Sleeve"}, {"protected_boxes": [[0, 0, 1, 1]] * 65},
                     {"spec": {"secret": float("nan")}}, {"mockup": self.root / "missing.png"}):
            with self.assertRaises(api.OpenAIError):
                self.run_repair(**args)
        self.request.assert_not_called()

    def test_input_paths_cannot_be_overwritten_even_by_alias(self):
        saved_target = self.target
        source_bytes = self.source.read_bytes()
        for path in (self.source, self.mockup, self.root / "out.jpg",
                     self.root / "missing" / "out.png"):
            self.target = path
            with self.assertRaises(api.OpenAIError):
                self.run_repair()
        self.target = self.root / "alias.png"
        os.link(self.source, self.target)
        with self.assertRaises(api.OpenAIError):
            self.run_repair()
        self.target = saved_target
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.request.assert_not_called()

    def test_bad_responses_preserve_destination_and_never_retry(self):
        bad_responses = [None, {}, {"data": []}, {"data": [{}, {}]},
                         {"data": [{"b64_json": "not base64!"}]},
                         {"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]},
                         self.response(size=(512, 512)), self.response(fmt="JPEG")]
        for result in bad_responses:
            self.request.reset_mock()
            self.request.return_value = result
            with self.subTest(result_type=type(result)), self.assertRaises(api.OpenAIError) as caught:
                self.run_repair()
            self.request.assert_called_once()
            self.assertEqual(self.target.read_bytes(), b"previous result")
            self.assertNotIn("sk-", str(caught.exception))

    def test_request_failure_has_one_call_and_preserves_destination(self):
        self.request.side_effect = api.OpenAIError("Safe failure; no retry")
        with self.assertRaises(api.OpenAIError):
            self.run_repair()
        self.request.assert_called_once()
        self.assertEqual(self.target.read_bytes(), b"previous result")

    def test_actual_diff_rejects_broken_compositor_before_save(self):
        def broken(spec, panel, original, edited, mask):
            return edited.copy()
        with patch.object(repair, "apply_patch_mask", side_effect=broken):
            with self.assertRaises(api.OpenAIError):
                self.run_repair()
        self.request.assert_called_once()
        self.assertEqual(self.target.read_bytes(), b"previous result")

    def test_portrait_without_dpi_and_2k_settings(self):
        self.settings.image_size = "2K"
        Image.new("RGB", (80, 120), "red").save(self.source)
        self.spec = build_design_spec({"panels": [{"name": "Sleeve", "size": [80, 120]}]})
        self.request.return_value = self.response(size=(2048, 2048))
        metadata = self.run_repair(editable_box=[8, 12, 56, 84], protected_boxes=[])
        with Image.open(self.target) as output:
            self.assertEqual(output.size, (80, 120))
            self.assertNotIn("dpi", output.info)
        self.assertEqual(metadata["raw_dimensions"], [2048, 2048])
        mask = self.decode(self.request.call_args.args[1]["mask"])
        self.assertEqual(mask.getpixel((0, 1000))[3], 255)
        self.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
