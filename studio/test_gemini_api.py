"""Offline checks only: every HTTP opener is mocked, including accidental requests."""

import base64
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from PIL import Image

import gemini_api as api

SECRET = "test-secret-do-not-expose"


def png(size, color="red", format="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=format)
    return buffer.getvalue()


def response_image(size=(1024, 1024), **extra):
    return {"candidates": [{"content": {"parts": [{"inlineData": {
        "mimeType": "image/png", "data": base64.b64encode(png(size)).decode()}}]}}], **extra}


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = api.Settings(self.root / "private" / "settings.json")
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        opener = patch.object(api.urllib.request, "build_opener")
        self.build_opener = opener.start()
        self.addCleanup(opener.stop)
        self.open = self.build_opener.return_value.open
        self.open.side_effect = AssertionError("Unexpected HTTP request")
        self.source, self.destination = self.root / "in.png", self.root / "out.png"
        Image.new("RGB", (240, 120), "blue").save(self.source, dpi=(300, 240))

    def reply(self, data):
        self.open.side_effect = None
        self.open.return_value.__enter__.return_value.read.return_value = (
            data if isinstance(data, bytes) else json.dumps(data).encode())

    def configured(self, image_size="1K"):
        self.settings.update(api_key=SECRET, image_size=image_size)

    def assert_safe_error(self, action):
        with self.assertRaises(api.GeminiError) as caught:
            action()
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__ or caught.exception.__context__ is None)
        return str(caught.exception)

    def test_default_path_defaults_and_exact_public_interface(self):
        self.assertEqual(api.DEFAULT_CONFIG, Path.home() / ".config/pod-studio/settings.json")
        self.assertEqual(self.settings.public(), {
            "key_configured": False, "key_source": "none", "model": "gemini-nano-banana-2.1",
            "image_size": "2K", "estimated_image_cost_usd": .0504})
        self.assertEqual(self.settings.api_key, "")
        self.assertFalse(self.settings.path.exists())

    def test_key_precedence_blank_updates_and_no_environment_persistence(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-env", "GOOGLE_API_KEY": "google-env"}):
            self.assertEqual(self.settings.api_key, "gemini-env")
            self.settings.update(image_size="4K")
            self.assertEqual(self.settings.public()["key_source"], "environment")
            self.assertNotIn("gemini-env", self.settings.path.read_text())
            self.settings.update(api_key=SECRET)
            self.settings.update(api_key=" \n\t", model="gemini-3-pro-image")
            restored = api.Settings(self.settings.path)
            self.assertEqual(restored.api_key, SECRET)
            self.assertEqual(restored.public()["key_source"], "file")
            self.assertNotIn(SECRET, json.dumps(restored.public()))
            self.assertEqual(restored.model, "gemini-3-pro-image")
        with patch.dict(os.environ, {"GEMINI_API_KEY": " ", "GOOGLE_API_KEY": "google-env"}):
            self.assertEqual(api.Settings(self.root / "missing").api_key, "google-env")

    def test_prices_for_only_allowed_models_and_sizes(self):
        prices = {"gemini-nano-banana-2.1": (.0336, .0504, .113),
                  "gemini-3-pro-image": (.134, .134, .24)}
        for model, costs in prices.items():
            for size, cost in zip(("1K", "2K", "4K"), costs):
                self.assertEqual(self.settings.update(model=model, image_size=size)[
                    "estimated_image_cost_usd"], cost)
        for kwargs in ({"model": SECRET}, {"model": "../escape"}, {"model": []},
                       {"image_size": "2k"}, {"image_size": {}}, {"api_key": []},
                       {"api_key": SECRET + "\r\nInjected: yes"}):
            before = self.settings.path.read_bytes()
            self.assert_safe_error(lambda: self.settings.update(**kwargs))
            self.assertEqual(self.settings.path.read_bytes(), before)

    def test_settings_private_atomic_and_failed_save_preserves_existing(self):
        self.configured()
        self.assertEqual(stat.S_IMODE(self.settings.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.settings.path.parent.stat().st_mode), 0o700)
        before = self.settings.path.read_bytes()
        self.settings.path.chmod(0o644)
        self.settings.path.parent.chmod(0o755)
        self.settings.update()
        self.assertEqual(stat.S_IMODE(self.settings.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.settings.path.parent.stat().st_mode), 0o700)
        with patch.object(api.os, "replace", side_effect=OSError(SECRET)) as replace:
            self.assert_safe_error(lambda: self.settings.update(api_key="replacement"))
            self.assertEqual(Path(replace.call_args.args[0]).parent, self.settings.path.parent)
        self.assertEqual(self.settings.path.read_bytes(), before)
        self.assertEqual(self.settings.api_key, SECRET)
        self.assertEqual(list(self.settings.path.parent.iterdir()), [self.settings.path])

    def test_corrupt_config_does_not_echo_contents_or_overwrite(self):
        self.settings.path.parent.mkdir()
        for content in (SECRET, '[]', '{"model":"' + SECRET + '"}',
                        '{"api_key":42}', "x" * 65537):
            self.settings.path.write_text(content)
            self.assert_safe_error(lambda: api.Settings(self.settings.path))
            self.assertEqual(self.settings.path.read_text(), content)

    def test_metadata_check_is_one_get_and_key_is_header_only(self):
        self.configured()
        self.reply({"name": "models/" + self.settings.model, "description": SECRET})
        result = api.check_connection(self.settings)
        self.assertTrue(result["ok"])
        self.assertNotIn(SECRET, json.dumps(result))
        self.open.assert_called_once()
        request = self.open.call_args.args[0]
        self.assertEqual(request.full_url, api.BASE_URL + self.settings.model)
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("X-goog-api-key"), SECRET)
        self.assertNotIn(SECRET, request.full_url)
        self.assertGreater(self.open.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(self.open.call_args.kwargs["timeout"], 180)

    def test_missing_key_and_bad_destination_never_contact_google(self):
        self.assert_safe_error(lambda: api.check_connection(self.settings))
        self.assert_safe_error(lambda: api.edit_image(self.source, self.destination, self.settings))
        self.configured()
        for path in (self.source, self.root / "missing" / "out.png", self.root / "out.pdf"):
            self.assert_safe_error(lambda: api.edit_image(self.source, path, self.settings))
        alias = self.root / "alias.png"
        os.link(self.source, alias)
        self.assert_safe_error(lambda: api.edit_image(self.source, alias, self.settings))
        self.open.assert_not_called()

    def test_redirect_handler_does_not_forward_credentials_or_repeat_request(self):
        # Exercise urllib's real redirect machinery against a fake HTTP transport.
        class Redirect(urllib.request.HTTPSHandler):
            def https_open(inner, request):
                transport.append(request)
                response = io.BytesIO(b"do not follow")
                response.code, response.msg = 302, "Found"
                response.info = lambda: {"Location": "https://example.invalid/" + SECRET}
                response.geturl = lambda: request.full_url
                return response

        self.configured()
        transport = []
        # build_opener is patched in setUp; construct a director explicitly.
        director = urllib.request.OpenerDirector()
        for handler in (Redirect(), urllib.request.HTTPDefaultErrorHandler(),
                        api._NoRedirect(), urllib.request.HTTPErrorProcessor()):
            director.add_handler(handler)
        self.build_opener.return_value = director
        self.assert_safe_error(lambda: api.check_connection(self.settings))
        self.assertEqual(len(transport), 1)
        self.assertTrue(transport[0].full_url.startswith(api.BASE_URL))

    def test_post_schema_padding_crop_dpi_and_numeric_usage(self):
        self.configured()
        # A different color in the padding catches resizing the whole square instead of cropping.
        generated = Image.new("RGB", (1024, 1024), "red")
        generated.paste("blue", (0, 256, 1024, 768))
        buffer = io.BytesIO()
        generated.save(buffer, format="PNG")
        result = response_image(usageMetadata={"promptTokenCount": 12, "totalTokenCount": 42,
            "candidatesTokenCount": True, "thoughtsTokenCount": float("inf"),
            "toolUsePromptTokenCount": SECRET, "cachedContentTokenCount": -1,
            "unknownNumber": 33, SECRET: 2, "nested": {"key": SECRET}}, description=SECRET)
        result["candidates"][0]["content"]["parts"][0]["inlineData"]["data"] = (
            base64.b64encode(buffer.getvalue()).decode())
        self.reply(result)
        original_bytes = self.source.read_bytes()
        report = api.edit_image(self.source, self.destination, self.settings)
        self.open.assert_called_once()
        request = self.open.call_args.args[0]
        self.assertEqual(request.full_url, api.BASE_URL + self.settings.model + ":generateContent")
        self.assertEqual(request.get_method(), "POST")
        payload = json.loads(request.data)
        self.assertEqual(payload["generationConfig"], {"responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "1:1", "imageSize": "1K"}})
        inline = payload["contents"][0]["parts"][1]["inlineData"]
        self.assertEqual(inline["mimeType"], "image/png")
        with Image.open(io.BytesIO(base64.b64decode(inline["data"]))) as sent:
            self.assertEqual(sent.size, (240, 240))
            self.assertEqual(sent.getpixel((120, 0)), (255, 255, 255))
            self.assertEqual(sent.getpixel((120, 60)), (0, 0, 255))
        with Image.open(self.destination) as output:
            self.assertEqual(output.size, (240, 120))
            self.assertEqual(output.getpixel((120, 60)), (0, 0, 255))
            self.assertGreater(output.getpixel((120, 0))[2], 240)
            self.assertAlmostEqual(output.info["dpi"][0], 300, places=1)
            self.assertAlmostEqual(output.info["dpi"][1], 240, places=1)
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual(report["usageMetadata"], {"promptTokenCount": 12, "totalTokenCount": 42})
        self.assertEqual(report["raw_dimensions"], [1024, 1024])
        self.assertEqual(report["estimated_image_cost_usd"], .0336)
        self.assertTrue(report["manual_review_required"])
        self.assertGreaterEqual(report["seconds"], 0)
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertIn("không phải tổng", report["cost_note"])

    def test_large_portrait_thumbnail_and_odd_padding_use_normalized_crop(self):
        self.configured()
        Image.new("RGB", (1001, 2400), "blue").save(self.source)
        self.reply(response_image())
        original_crop = Image.Image.crop
        with patch.object(Image.Image, "crop", autospec=True, side_effect=original_crop) as crop:
            api.edit_image(self.source, self.destination, self.settings)
        payload = json.loads(self.open.call_args.args[0].data)
        image_bytes = base64.b64decode(payload["contents"][0]["parts"][1]["inlineData"]["data"])
        with Image.open(io.BytesIO(image_bytes)) as sent:
            self.assertEqual(sent.size, (1600, 1600))
            self.assertEqual(sent.getpixel((465, 800)), (255, 255, 255))
            self.assertEqual(sent.getpixel((466, 800)), (0, 0, 255))
        last_box = crop.call_args.args[1]
        self.assertEqual(last_box, tuple(round(v * 1024 / 1600) for v in (466, 0, 1133, 1600)))
        with Image.open(self.destination) as output:
            self.assertEqual(output.size, (1001, 2400))

    def test_api_failures_are_safe_and_never_retried_or_overwrite_output(self):
        self.configured()
        self.destination.write_bytes(b"existing")
        for failure in [urllib.error.HTTPError(api.BASE_URL, code, SECRET, {}, io.BytesIO(
                        SECRET.encode())) for code in (400, 401, 403, 404, 429, 500, 503)] + [
                        urllib.error.URLError(SECRET), TimeoutError(SECRET)]:
            with self.subTest(failure=type(failure).__name__):
                self.open.reset_mock()
                self.open.side_effect = failure
                self.assert_safe_error(lambda: api.edit_image(self.source, self.destination, self.settings))
                self.open.assert_called_once()
                self.assertEqual(self.destination.read_bytes(), b"existing")

    def test_bounded_read_and_invalid_json_are_safe(self):
        self.configured()
        for raw in (b"not json " + SECRET.encode(), b"[]", b'{"error":"' + SECRET.encode() + b'"}',
                    b"x" * 257):
            self.reply(raw)
            with patch.object(api, "MAX_RESPONSE_BYTES", 256):
                self.assert_safe_error(lambda: api.check_connection(self.settings))
                self.open.return_value.__enter__.return_value.read.assert_called_with(257)

    def test_wrong_canvas_format_and_malformed_images_leave_existing_output(self):
        self.configured()
        self.destination.write_bytes(b"existing")
        invalid = [response_image((1024, 512)), response_image((512, 512)),
                   {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": SECRET}]}}]}]
        for bad_data, mime in ((SECRET, "image/png"),
                               (base64.b64encode(png((1024, 1024), format="JPEG")).decode(), "image/png"),
                               (base64.b64encode(png((1024, 1024))).decode(), "text/plain")):
            result = response_image()
            result["candidates"][0]["content"]["parts"][0]["inlineData"] = {
                "mimeType": mime, "data": bad_data}
            invalid.append(result)
        for result in invalid:
            self.open.reset_mock()
            self.reply(result)
            self.assert_safe_error(lambda: api.edit_image(self.source, self.destination, self.settings))
            self.open.assert_called_once()
            self.assertEqual(self.destination.read_bytes(), b"existing")

    def test_output_save_failure_is_atomic_and_not_retried(self):
        self.configured()
        self.destination.write_bytes(b"existing")
        self.reply(response_image())
        with patch.object(api.os, "replace", side_effect=OSError(SECRET)):
            self.assert_safe_error(lambda: api.edit_image(self.source, self.destination, self.settings))
        self.open.assert_called_once()
        self.assertEqual(self.destination.read_bytes(), b"existing")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["in.png", "out.png", "private"])


if __name__ == "__main__":
    unittest.main()
