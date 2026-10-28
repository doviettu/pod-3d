"""Tests for the cost-controlled OpenAI sheet: layout, prompt, cache, budget. No network."""
import base64
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_sheet  # noqa: E402
import design_spec  # noqa: E402

SIZES = [('Front', (5100, 6300)), ('Back', (5100, 6300)), ('Left', (1500, 2100))]


def fake_openai(colours=None):
    """Return a request function that draws each sheet cell in its own colour and counts calls."""
    calls = []

    def request(settings, payload):
        calls.append(payload)
        edge = int(payload['size'].split('x')[0])
        img = Image.new('RGB', (edge, edge), 'white')
        scale = edge / ai_sheet.LAYOUT_EDGE
        for i, (_, box) in enumerate(ai_sheet.layout(SIZES)):
            img.paste((40 * i + 50, 0, 0), tuple(round(v * scale) for v in box))
        stream = io.BytesIO()
        img.save(stream, 'PNG')
        return {'data': [{'b64_json': base64.b64encode(stream.getvalue()).decode()}],
                'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}}
    return request, calls


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = SimpleNamespace(model='gpt-image-2.5-flare', image_size='1K', quality='medium', api_key='sk-test')
        self.spec = design_spec.build_design_spec({'panels': [{'name': n, 'size': list(s)} for n, s in SIZES]})
        self.mockup = self.root / 'mockup.png'
        Image.new('RGB', (600, 600), 'gray').save(self.mockup)
        self.cache = ai_sheet.Cache(self.root / 'cache')
        self.budget = ai_sheet.Budget(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def run_sheet(self, request, **kw):
        return ai_sheet.run_sheet(self.mockup, self.root / 'out', self.settings, request, panel_sizes=SIZES,
                                  spec=self.spec, source_sha='a' * 64, cache=self.cache, budget=self.budget,
                                  log=lambda m: None, **kw)

    def test_layout_keeps_ratios_inside_the_sheet_without_overlap(self):
        cells = ai_sheet.layout(SIZES)
        for (name, (w, h)), (cname, (x0, y0, x1, y1)) in zip(SIZES, cells):
            self.assertEqual(name, cname)
            self.assertTrue(0 <= x0 < x1 <= 1024 and 0 <= y0 < y1 <= 1024)
            self.assertAlmostEqual((x1 - x0) / (y1 - y0), w / h, delta=0.03)
        for i, (_, a) in enumerate(cells):
            for _, b in cells[i + 1:]:
                self.assertTrue(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
        with self.assertRaises(ValueError):
            ai_sheet.layout([])

    def test_prompt_is_deterministic_and_versioned(self):
        cells = ai_sheet.layout(SIZES)
        a = ai_sheet.sheet_prompt(self.spec, cells, 'Hoodie', 'navy')
        self.assertEqual(a, ai_sheet.sheet_prompt(self.spec, cells, 'Hoodie', 'navy'))
        self.assertIn(ai_sheet.PROMPT_VERSION, a)
        self.assertIn('= Front', a)
        self.assertIn("wearer's left sleeve", a)
        self.assertNotEqual(ai_sheet.cache_key('a', a, 'm', '1K', 'medium', cells),
                            ai_sheet.cache_key('a', a, 'm', '1K', 'high', cells))

    def test_one_request_for_all_panels_then_cache(self):
        request, calls = fake_openai()
        made, meta = self.run_sheet(request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(meta['paid_calls'], 1)
        self.assertEqual(len(calls[0]['images']), 2)
        self.assertEqual(set(made), {'Front', 'Back', 'Left'})
        with Image.open(made['Left']) as left:
            self.assertEqual(left.getpixel((left.width // 2, left.height // 2)), (130, 0, 0))
            self.assertAlmostEqual(left.width / left.height, 1500 / 2100, delta=0.03)
        made2, meta2 = self.run_sheet(request)
        self.assertEqual(len(calls), 1)            # second run: no new paid request
        self.assertEqual((meta2['paid_calls'], meta2['cache_hit']), (0, True))
        rows = self.budget.entries()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['usd'], ai_sheet.DEFAULT_PRICES['1K']['medium'])

    def test_budget_blocks_before_any_request(self):
        self.budget.update({'daily_usd': 0.05})
        request, calls = fake_openai()
        with self.assertRaises(ValueError):
            self.run_sheet(request)
        self.assertEqual(calls, [])
        with self.assertRaises(ValueError):
            self.budget.update({'daily_usd': -1})

    def test_spend_is_counted_per_day_and_month(self):
        self.budget.record({'usd': 0.5})
        today, month = self.budget.spent()
        self.assertEqual((today, month), (0.5, 0.5))
        self.assertEqual(self.budget.spent(datetime(2001, 1, 1)), (0, 0))

    def test_bad_reply_is_logged_and_not_retried(self):
        calls = []

        def broken(settings, payload):
            calls.append(1)
            return {'data': []}
        with self.assertRaises(Exception):
            self.run_sheet(broken)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.budget.entries()[0]['ok'])

    def test_weak_panels_are_picked_from_the_local_report(self):
        report = {'panels': {'Front': {'observed_fraction': 0.9}, 'Back': {'observed_fraction': 0.4},
                             'Left': {'observed_fraction': 0.1}, 'Hood': {'observed_fraction': 0.65}}}
        self.assertEqual(ai_sheet.pick_weak_panels(report, ['Front', 'Back', 'Left', 'Hood']), ['Left', 'Back', 'Hood'])
        self.assertEqual(ai_sheet.pick_weak_panels(None, ['Front']), [])


if __name__ == '__main__':
    unittest.main()
