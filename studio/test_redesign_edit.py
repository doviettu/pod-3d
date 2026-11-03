"""Tests for Redesign step 2 (local edits, variants, Skill checks) and the Claude analysis
(Claude Code is faked; no subprocess runs)."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redesign as rd  # noqa: E402
import redesign_ai  # noqa: E402
import redesign_edit as ed  # noqa: E402
from test_redesign import design, fake_ocr  # noqa: E402


def layer(scene, label):
    return next(l for l in scene['layers'] if l['label'] == label)


class EditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = rd.RedesignStore(self.root, ocr=fake_ocr)

    def tearDown(self):
        self.tmp.cleanup()

    def image(self, out):
        name = out['url'].rsplit('/', 1)[1]
        with Image.open(self.root / out['url'].split('/')[4] / name) as im:
            return np.asarray(im.convert('RGBA'))

    def test_untouched_layers_keep_every_pixel(self):
        scene = self.store.analyze(design(), 't')
        square = layer(scene, 'Hình 2')
        out = self.store.edit(scene['id'], {'layers': {square['id']: {'remove': True}}})
        result, source = self.image(out), design()
        x, y, w, h = layer(scene, 'Hình 1')['bbox']
        self.assertTrue(np.array_equal(result[y:y + h, x:x + w], source[y:y + h, x:x + w]))
        self.assertEqual(result[500, 490, 3], 0)        # the square is gone, transparent
        self.assertEqual(out['n'], 1)
        self.assertEqual(self.store.edit(scene['id'], {})['n'], 2)

    def test_remove_on_opaque_fills_background(self):
        scene = self.store.analyze(design(opaque=True), 't')
        square = layer(scene, 'Hình 2')
        result = self.image(self.store.edit(scene['id'], {'layers': {square['id']: {'remove': True}}}))
        self.assertEqual(list(result[500, 490]), [245, 240, 230, 255])

    def test_layer_colour_move_scale_and_effects(self):
        scene = self.store.analyze(design(), 't')
        circle, square = layer(scene, 'Hình 1'), layer(scene, 'Hình 2')
        out = self.store.edit(scene['id'], {'layers': {
            circle['id']: {'color': '#10a040'},
            square['id']: {'dx': -70, 'dy': 0, 'scale': 0.5, 'effect': 'outline', 'effect_color': '#ffffff'}}})
        result = self.image(out)
        self.assertEqual(list(result[300, 300, :3]), [16, 160, 64])
        self.assertEqual(result[500, 490, 3], 0)                    # moved away
        self.assertEqual(list(result[515, 70, :3]), [40, 120, 220])  # new centre: 490 - 420 = 70
        ys, xs = np.nonzero(result[..., 3] > 0)
        self.assertTrue(((result[..., :3] == 255).all(2) & (result[..., 3] == 255)).any())   # white outline drawn

    def test_retype_text(self):
        scene = self.store.analyze(design(), 't')
        text = layer(scene, 'GOOD VIBES')
        result = self.image(self.store.edit(scene['id'], {'layers': {text['id']: {'text': 'HI'}}}))
        x, y, w, h = text['bbox']
        box = result[y - 4:y + h + 4, x - 4:x + w + 4, 3]
        self.assertGreater((box > 0).mean(), 0.05)
        self.assertLess((box > 0).mean(), 0.9)    # letters now, not the solid stand-in bar
        self.assertTrue(set(np.unique(result[..., 3])) <= {0, 255})
        with self.assertRaises(ValueError):
            self.store.edit(scene['id'], {'layers': {layer(scene, 'Hình 1')['id']: {'text': 'nope'}}})

    def test_palette_map_and_bad_input(self):
        scene = self.store.analyze(design(), 't')
        orange = next(p['hex'] for p in scene['palette'] if p['hex'].startswith('#e'))
        result = self.image(self.store.edit(scene['id'], {'palette': {orange: '#000080'}}))
        self.assertLessEqual(np.abs(result[300, 300, :3].astype(int) - [0, 0, 128]).max(), 2)
        self.assertEqual(list(result[500, 490, :3]), [40, 120, 220])
        for bad in ({'palette': {'#123456': '#000000'}}, {'layers': {'L99': {'remove': True}}},
                    {'layers': {'L1': {'scale': 9}}}, {'layers': {'L1': {'effect': 'glow'}}},
                    {'layers': {'L1': {'color': 'red'}}}):
            with self.assertRaises(ValueError, msg=bad):
                self.store.edit(scene['id'], bad)

    def test_variants(self):
        scene = self.store.analyze(design(), 't')
        out = self.store.variants(scene['id'], {'presets': ['halloween', 'christmas']})
        self.assertEqual([v['label'] for v in out['variants']], ['Halloween', 'Christmas'])
        first = self.image(out['variants'][0])
        self.assertFalse(np.array_equal(first[300, 300], design()[300, 300]))
        self.assertEqual(self.store.file(scene['id'], f'var-{out["n"]}-1.png').name, f'var-{out["n"]}-1.png')
        with self.assertRaises(ValueError):
            self.store.variants(scene['id'], {'presets': []})
        with self.assertRaises(ValueError):
            self.store.variants(scene['id'], {'presets': ['nope']})

    def test_checks(self):
        clean = {c['id']: c['ok'] for c in ed.check(design())}
        self.assertEqual(clean, {'colours': True, 'space': True, 'halo': True, 'edge': True})
        busy = np.zeros((100, 100, 4), np.uint8)
        busy[10:90, 10:90] = [200, 30, 30, 120]
        checks = {c['id']: c['ok'] for c in ed.check(busy)}
        self.assertFalse(checks['halo'])
        self.assertTrue(checks['edge'])


ANSWER = '''Here you go:
{"subject": "A cat in a witch hat", "text": ["GOOD VIBES"], "layout": "text above, cat centred",
 "style": "vintage retro", "typography": "bold slab", "motifs": ["stars", 3], "effects": "outline",
 "print_issues": [], "sells": {"hook": "pun", "formula": "A [holiday] phrase on a [animal]", "niche": "Cats > Halloween"},
 "layers": {"L1": "cat", "L99": "ghost", "L3": "headline"},
 "concepts": [{"title": "Giáng sinh", "goal": "Christmas version", "layers": {"L1": {"action": "change", "note": "santa hat"}, "L9": {"action": "remove"}}},
              {"title": "Bad", "layers": {"L1": {"action": "explode"}}}]}'''


class AnalysisTests(unittest.TestCase):
    def test_parse_checks_everything(self):
        scene = {'layers': [{'id': 'L1'}, {'id': 'L2'}, {'id': 'L3'}]}
        a = redesign_ai.parse(ANSWER, scene)
        self.assertEqual(a['subject'], 'A cat in a witch hat')
        self.assertEqual(a['motifs'], ['stars'])
        self.assertEqual(a['layers'], {'L1': 'cat', 'L3': 'headline'})
        self.assertEqual(a['sells']['tone'], '')
        self.assertEqual(len(a['concepts']), 1)
        self.assertEqual(a['concepts'][0]['layers'], {'L1': {'action': 'change', 'note': 'santa hat'}})
        for bad in ('no json', '{"subject": ', '{}'):
            with self.assertRaises(ValueError):
                redesign_ai.parse(bad, scene)

    def test_store_runs_claude_and_brief_uses_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = rd.RedesignStore(Path(tmp), ocr=fake_ocr)
            scene = store.analyze(design(), 't')
            seen = {}

            def runner(text, cwd):
                seen['text'] = text
                return ANSWER
            scene = store.analyze_ai(scene['id'], runner)
            self.assertIn('boxes.jpg', seen['text'])
            self.assertTrue((Path(tmp) / scene['id'] / 'boxes.jpg').is_file())
            self.assertEqual(scene['layers'][0]['label'], 'cat')
            self.assertEqual(layer(scene, 'GOOD VIBES')['type'], 'text')   # text layers keep their words
            self.assertEqual(store.get(scene['id'])['analysis']['style'], 'vintage retro')
            out = store.prompt(scene['id'], {'layers': {'L1': {'action': 'change', 'note': 'x'}}})
            self.assertIn('Style: vintage retro', out['prompt'])
            self.assertIn('keep this formula', out['prompt'])

    def test_run_claude_reads_result_event(self):
        class Proc:
            returncode = 0
            stderr = ''
            stdout = '\n'.join([json.dumps({'type': 'assistant'}),
                                json.dumps({'type': 'result', 'subtype': 'success', 'result': '{"a": 1}'})])
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            return Proc()
        text = redesign_ai.run_claude('hello', Path('/tmp'), finder=lambda: '/bin/claude', run=run)
        self.assertEqual(text, '{"a": 1}')
        cmd, kw = calls[0]
        self.assertIn('--restricted', cmd)
        self.assertNotIn('Bash', cmd)
        self.assertEqual(kw['input'], 'hello')
        self.assertNotIn('ANTHROPIC_API_KEY', kw['env'])
        with self.assertRaises(ValueError):
            redesign_ai.run_claude('x', Path('/tmp'), finder=lambda: None, run=run)


if __name__ == '__main__':
    unittest.main()
