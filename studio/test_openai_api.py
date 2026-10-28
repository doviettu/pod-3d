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

from PIL import Image
import openai_api as api


class OpenAITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        self.settings = api.Settings(self.root / 'private/openai-settings.json')
        self.key = 'sk-proj-private-test-key'
        self.settings.update(api_key=self.key)
        self.source = self.root / 'source.png'
        self.target = self.root / 'edited.png'
        Image.new('RGB', (120, 80), 'red').save(self.source, dpi=(300, 300))

    def fake_network(self, result=None, error=None):
        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        opener = patch('openai_api.urllib.request.build_opener').start().return_value
        if error:
            opener.open.side_effect = error
        else:
            opener.open.return_value = Response(json.dumps(result).encode())
        return opener.open

    def image_result(self, size=(1024, 1024)):
        image = Image.new('RGB', size, 'white')
        image.paste('blue', (0, size[1] // 6, size[0], 5 * size[1] // 6))
        stream = io.BytesIO()
        image.save(stream, 'PNG')
        return {'data': [{'b64_json': base64.b64encode(stream.getvalue()).decode()}],
                'usage': {'input_tokens': 300, 'output_tokens': 200, 'total_tokens': 500,
                          'secret': self.key}}

    def test_settings_separate_private_no_google_fallback(self):
        self.assertEqual(api.DEFAULT_CONFIG.name, 'openai-settings.json')
        self.assertEqual(stat.S_IMODE(self.settings.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.settings.path.parent.stat().st_mode), 0o700)
        self.settings.update(api_key='', quality='high')
        self.assertEqual(api.Settings(self.settings.path).api_key, self.key)
        self.assertNotIn(self.key, json.dumps(self.settings.public()))
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'google-key', 'GOOGLE_API_KEY': 'google-key'}, clear=True):
            empty = api.Settings(self.root / 'missing.json')
            self.assertFalse(empty.api_key)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-env-test'}):
            self.assertEqual(api.Settings(self.root / 'missing.json').api_key, 'sk-env-test')
            self.assertEqual(self.settings.api_key, self.key)

    def test_invalid_settings_do_not_change_saved_key(self):
        before = self.settings.path.read_bytes()
        for value in ({'model': '../other'}, {'image_size': '4K'}, {'quality': 'auto'},
                      {'api_key': 'AIza-google-key'}, {'api_key': 'sk-x\nInjected: header'}):
            with self.subTest(value=value), self.assertRaises(api.OpenAIError):
                self.settings.update(**value)
            self.assertEqual(self.settings.path.read_bytes(), before)

    def test_failed_save_does_not_replace_settings(self):
        before = self.settings.path.read_bytes()
        with patch('openai_api.os.replace', side_effect=OSError), self.assertRaises(api.OpenAIError):
            self.settings.update(api_key='sk-other')
        self.assertEqual(self.settings.path.read_bytes(), before)
        self.assertEqual(self.settings.api_key, self.key)
        self.assertEqual(list(self.settings.path.parent.iterdir()), [self.settings.path])

    def test_check_uses_only_metadata_get(self):
        network = self.fake_network({'id': self.settings.model, 'private': self.key})
        result = api.check_connection(self.settings)
        network.assert_called_once()
        req = network.call_args.args[0]
        self.assertEqual(req.get_method(), 'GET')
        self.assertEqual(req.full_url, api.BASE_URL + 'models/' + self.settings.model)
        self.assertIsNone(req.data)
        self.assertTrue(result['ok'])
        self.assertNotIn(self.key, json.dumps(result))

    def test_edit_one_request_payload_crop_canvas_dpi_and_usage(self):
        network = self.fake_network(self.image_result())
        result = api.edit_image(self.source, self.target, self.settings)
        network.assert_called_once()
        req = network.call_args.args[0]
        self.assertEqual(req.full_url, 'https://api.openai.com/v1/images/edits')
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + self.key)
        payload = json.loads(req.data)
        self.assertEqual(payload['n'], 1)
        self.assertEqual(payload['size'], '1024x1024')
        self.assertEqual(payload['quality'], 'medium')
        self.assertEqual(payload['model'], self.settings.model)
        self.assertNotIn('input_fidelity', payload)
        raw = payload['images'][0]['image_url'].split(',', 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(raw))) as padded:
            self.assertEqual(padded.size, (120, 120))
            self.assertEqual(padded.getpixel((0, 0)), (255, 255, 255))
        with Image.open(self.target) as image:
            self.assertEqual(image.size, (120, 80))
            self.assertLess(abs(image.info['dpi'][0] - 300), 1)
            self.assertEqual(image.getpixel((60, 40)), (0, 0, 255))
        self.assertTrue(result['manual_review_required'])
        self.assertNotIn(self.key, json.dumps(result))
        self.assertEqual(result['usage']['total_tokens'], 500)

    def test_failures_no_retry_and_no_provider_secret_echo(self):
        for code in (401, 403, 429, 500, 302):
            with self.subTest(code=code):
                error = urllib.error.HTTPError(api.BASE_URL, code, 'error', {},
                    io.BytesIO(json.dumps({'error': {'message': self.key}}).encode()))
                network = self.fake_network(error=error)
                with self.assertRaises(api.OpenAIError) as caught:
                    api.edit_image(self.source, self.target, self.settings)
                self.assertNotIn(self.key, str(caught.exception))
                network.assert_called_once()
                self.assertFalse(self.target.exists())

    def test_quota_error_is_distinguished_from_rate_limit(self):
        error = urllib.error.HTTPError(api.BASE_URL, 429, 'error', {},
            io.BytesIO(json.dumps({'error': {'code': 'insufficient_quota', 'message': self.key}}).encode()))
        network = self.fake_network(error=error)
        with self.assertRaisesRegex(api.OpenAIError, 'Billing'):
            api.edit_image(self.source, self.target, self.settings)
        network.assert_called_once()

    def test_bad_response_preserves_existing_target(self):
        before = b'keep existing output'
        self.target.write_bytes(before)
        for response in ({'data': []}, self.image_result((512, 512)), {'data': [{'b64_json': 'bad!'}]}):
            network = self.fake_network(response)
            with self.assertRaises(api.OpenAIError):
                api.edit_image(self.source, self.target, self.settings)
            self.assertEqual(self.target.read_bytes(), before)
            network.assert_called_once()

    def test_redirects_never_forward_key(self):
        self.assertIsNone(api._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))

    def test_reconstruct_sleeve_from_original_before_placing_on_template(self):
        network = self.fake_network(self.image_result())
        master = self.root / 'artwork.png'
        before = self.source.read_bytes()
        result = api.reconstruct_artwork(self.source, master, self.settings, panel='Sleeve',
                                        canvas_size=(120, 80), notes='Keep horizontal deer bands.')
        network.assert_called_once()
        payload = json.loads(network.call_args.args[0].data)
        self.assertEqual(len(payload['images']), 2)
        self.assertIn('Sleeve length is vertical', payload['prompt'])
        self.assertIn('Generate no text', payload['prompt'])
        self.assertEqual(len(result['design_spec_hash']), 64)
        self.assertEqual(len(result['prompt_sha256']), 64)
        self.assertIn('Keep horizontal deer bands.', payload['prompt'])
        encoded = payload['images'][0]['image_url'].split(',', 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as reference:
            self.assertEqual(reference.size, (120, 80))
            self.assertEqual(reference.getpixel((0, 0)), (255, 0, 0))
        with Image.open(master) as artwork:
            self.assertEqual(artwork.size, (1024, 682))
        api.place_artwork(master, self.target, (120, 80), 300)
        with Image.open(self.target) as panel:
            self.assertEqual(panel.size, (120, 80))
            self.assertLess(abs(panel.info['dpi'][0] - 300), 1)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(result['text_logo_separate'])
        self.assertNotIn(self.key, json.dumps(result))

    def test_reconstruction_rejects_bad_panel_and_keeps_existing_master_on_error(self):
        for values in ({'panel': 'Collar'}, {'panel': '../Sleeve'}, {'canvas_size': (0, 80)}, {'notes': 'x' * 2001}):
            network = self.fake_network(self.image_result())
            with self.assertRaises(api.OpenAIError):
                api.reconstruct_artwork(self.source, self.target, self.settings,
                    **({'panel': 'Sleeve', 'canvas_size': (120, 80)} | values))
            network.assert_not_called()
        self.target.write_bytes(b'preserve existing master')
        network = self.fake_network({'data': []})
        with self.assertRaises(api.OpenAIError):
            api.reconstruct_artwork(self.source, self.target, self.settings, panel='Front', canvas_size=(120, 80))
        network.assert_called_once()
        self.assertEqual(self.target.read_bytes(), b'preserve existing master')

    def test_local_upscale_runs_on_restored_artwork_with_correct_colors(self):
        import numpy as np
        calls = []
        def upscale(image, factor):
            calls.append(factor)
            self.assertEqual(image[0, 0].tolist(), [0, 0, 255])
            return np.full((160, 240, 3), [255, 0, 0], dtype=np.uint8)
        api.place_artwork(self.source, self.target, (240, 160), 300, upscale=upscale)
        self.assertEqual(calls, [2])
        with Image.open(self.target) as image:
            self.assertEqual(image.size, (240, 160))
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))


if __name__ == '__main__':
    unittest.main()
