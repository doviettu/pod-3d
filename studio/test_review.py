import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PIL import Image
import test_studio
from review import FontLibrary, ps_name


def encode_png(size=(5, 5), color=(0, 0, 255, 180)):
    stream = io.BytesIO()
    Image.new('RGBA', size, color).save(stream, 'PNG')
    return base64.b64encode(stream.getvalue()).decode()


class ReviewTests(unittest.TestCase):
    setUp = test_studio.StudioTests.setUp
    fake_run = test_studio.StudioTests.fake_run
    finish = test_studio.StudioTests.finish
    def test_review_layers_are_real_and_original_is_unchanged(self):
        job = self.finish()
        source = self.engine.file_path(job['id'], 'local/Front.png')
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.engine.postprocess(job['id'], 'review', {'panel': 'Front', 'source': 'local',
            'text_layers': [{'text': 'OK', 'font_id': 'system-arial', 'font_size': 8, 'x': 1, 'y': 1, 'color': '#ffffff'}],
            'logo_layers': [{'image_base64': encode_png(), 'x': 10, 'y': 12, 'width': 5}],
            'erasures': [{'x': 0, 'y': 0, 'width': 20, 'height': 10, 'color': '#000000'}], 'approved': False})
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertFalse(result['spec']['approved'])
        self.assertNotIn('image_base64', json.dumps(result['spec']))
        composite, layers = self.engine.review.render(job['id'], 'Front', result['spec'])
        self.assertEqual(composite.size, (20, 24))
        self.assertEqual(composite.getpixel((0, 0)), (0, 0, 0, 255))
        self.assertTrue(any(layer.get('text', {}).get('value') == 'OK' for layer in layers))
        self.assertTrue(any(layer['name'].startswith('Logo') for layer in layers))
        png = self.engine.root / job['id'] / 'reviews' / result['revision'] / 'Front-draft.png'
        with Image.open(png) as image:
            self.assertLess(abs(image.info['dpi'][0] - 300), 1)
        again = self.engine.postprocess(job['id'], 'review', {'panel': 'Front', **result['spec'], 'approved': True})
        self.assertNotEqual(again['revision'], result['revision'])
        self.assertTrue(again['spec']['approved'])
        self.assertTrue(png.is_file())

    def test_bad_review_cannot_change_approval_or_escape_files(self):
        job = self.finish()
        bad = [{'panel': '../Front'}, {'panel': 'Front', 'logo_layers': [{'asset_id': '../../private', 'x': 0, 'y': 0, 'width': 1}]},
               {'panel': 'Front', 'text_layers': [{'text': 'abc', 'font_id': 'system-arial', 'font_size': 12, 'x': 19, 'y': 20, 'color': '#ffffff'}]},
               {'panel': 'Front', 'erasures': [{'x': 0, 'y': 0, 'width': -1, 'height': 4, 'color': '#000000'}]},
               {'panel': 'Front', 'text_layers': [None]}]
        for request in bad:
            with self.subTest(request=request), self.assertRaises((ValueError, FileNotFoundError)):
                self.engine.postprocess(job['id'], 'review', request)
            self.assertNotIn('reviews', self.engine.get_job(job['id']))
        self.assertFalse(self.engine.postprocess_lock.locked())

    def test_postprocess_waits_for_other_jobs(self):
        job = self.finish()
        self.engine.jobs[job['id']]['status'] = 'running'
        with self.assertRaisesRegex(ValueError, 'hàng đợi'):
            self.engine.postprocess(job['id'], 'review', {'panel': 'Front'})
        self.assertFalse(self.engine.postprocess_lock.locked())

    def test_font_comparison_is_local_and_preserves_font_metadata(self):
        library = self.engine.review.fonts
        font = library.path('system-arial').read_bytes()
        added = library.add({'filename': 'Arial.ttf', 'data_base64': base64.b64encode(font).decode(),
            'source_url': 'https://www.dafont.com/faq.php', 'license_confirmed': True})
        self.assertEqual(added['postscript_name'], ps_name(font))
        self.assertEqual(library.path(added['id']).read_bytes(), font)
        with Image.open(io.BytesIO(library.preview(added['id'], 'MERRY CHRISTMAS'))) as image:
            self.assertEqual(image.size, (1000, 100))
        for request in ({'data_base64': base64.b64encode(font).decode(), 'source_url': 'https://evil.invalid/font'},
                        {'data_base64': base64.b64encode(b'not font').decode()}):
            with self.assertRaises(ValueError):
                library.add(request)

    def test_vtracer_generates_real_svg_from_selected_region(self):
        job = self.finish()
        result = self.engine.postprocess(job['id'], 'vectorize', {'panel': 'Front', 'bbox': [0, 0, 20, 24]})
        path = self.engine.file_path(job['id'], result['url'].split('/' + job['id'] + '/', 1)[1])
        root = ET.parse(path).getroot()
        self.assertTrue(root.tag.endswith('svg'))
        self.assertTrue(any(element.tag.endswith('path') for element in root.iter()))

    def test_layered_psd_keeps_native_text_and_logo(self):
        job = self.finish()
        data = {'panel': 'Front', 'text_layers': [{'text': 'OK', 'font_id': 'system-arial', 'font_size': 8,
                 'x': 1, 'y': 1, 'color': '#ffffff'}],
                'logo_layers': [{'image_base64': encode_png(), 'x': 10, 'y': 12, 'width': 5}]}
        saved = self.engine.postprocess(job['id'], 'review', data)
        exported = self.engine.postprocess(job['id'], 'psd', {'panel': 'Front'})
        self.assertEqual(saved['revision'], exported['revision'])
        path = self.engine.root / job['id'] / 'reviews' / saved['revision'] / 'Front.psd'
        self.assertEqual(path.read_bytes()[:4], b'8BPS')
        self.assertIn('psd_url', exported)


if __name__ == '__main__':
    unittest.main()
