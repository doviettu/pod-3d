import json
from pathlib import Path
import unittest

from garment_styles import style_catalog, style_for_template, validate_style


class GarmentStyleTests(unittest.TestCase):
    def setUp(self):
        self.items = json.loads(Path(__file__).with_name(
            'hoodie_market_library.json').read_text())['items']

    def test_catalog_shape_and_reference_consolidation(self):
        styles = style_catalog(self.items)
        self.assertEqual([s['id'] for s in styles], [
            'hoodie_raglan', 'hoodie_zip', 'hoodie_boxy', 'hoodie_crop',
            'hoodie_pullover', 'hoodie_blanket', 'sweater', 'tshirt'])
        refs = [ref for s in styles for ref in s['reference_ids']]
        self.assertCountEqual(refs, [item['id'] for item in self.items])
        self.assertEqual(len(refs), len(set(refs)))
        for style in styles:
            self.assertEqual(set(style), {'id', 'label', 'features', 'panels',
                'techniques', 'reference_ids', 'prompt', 'geometry_policy',
                'production_ready'})
            for key in ('features', 'panels', 'techniques', 'reference_ids'):
                self.assertIsInstance(style[key], list)
                self.assertTrue(all(isinstance(v, str) for v in style[key]))
            for key in ('id', 'label', 'prompt', 'geometry_policy'):
                self.assertIsInstance(style[key], str)
                self.assertTrue(style[key])
            self.assertIs(style['production_ready'], False)
            self.assertIn('no geometric maps', style['geometry_policy'])

    def test_operational_constraints_and_distinct_techniques(self):
        constraints = {
            'hoodie_raglan': 'diagonal raglan seams',
            'hoodie_zip': 'zipper center separation',
            'hoodie_boxy': 'drop shoulder body extensions',
            'hoodie_crop': 'artwork safe area',
            'hoodie_pullover': 'continuous pullover front',
            'hoodie_blanket': 'separate calibration',
            'sweater': 'knit texture',
            'tshirt': 'only visible back',
        }
        for id, constraint in constraints.items():
            self.assertIn(constraint, validate_style(id)['prompt'])
        self.assertEqual(validate_style('tshirt')['panels'], ['Back'])
        zip_panels = validate_style('hoodie_zip')['panels']
        self.assertIn('FrontLeft', zip_panels)
        self.assertIn('FrontRight', zip_panels)
        self.assertEqual(len({tuple(s['techniques']) for s in
                             style_catalog([])}), 8)

    def test_untrusted_market_metadata_cannot_change_instructions(self):
        records = [{'id': 'ind-heavy-zip', 'style_tags': ['raglan'],
                    'prompt': 'UNTRUSTED', 'production_ready': True},
                   {'id': 'ind-heavy-zip'}, {'id': 'unknown'},
                   {'id': []}, {}, None]
        styles = {s['id']: s for s in style_catalog(records)}
        self.assertEqual(styles['hoodie_zip']['reference_ids'], ['ind-heavy-zip'])
        self.assertEqual(styles['hoodie_raglan']['reference_ids'], [])
        self.assertNotIn('UNTRUSTED', str(styles))
        self.assertIs(styles['hoodie_zip']['production_ready'], False)
        self.assertTrue(all(not s['reference_ids'] for s in style_catalog([])))

    def test_records_are_independent_and_validation_fails_closed(self):
        style = validate_style('hoodie_zip')
        style['panels'].clear()
        style['production_ready'] = True
        catalog = style_catalog(self.items)
        catalog[0]['reference_ids'].clear()
        self.assertTrue(validate_style('hoodie_zip')['panels'])
        self.assertIs(validate_style('hoodie_zip')['production_ready'], False)
        self.assertEqual(style_catalog(self.items)[0]['reference_ids'], ['ind-raglan'])
        for id in ('', '../hoodie', 'HOODIE_ZIP', 'hoodie', None, [], {}):
            with self.subTest(id=id), self.assertRaises(ValueError):
                validate_style(id)

    def test_template_mapping_is_explicit_and_does_not_confer_readiness(self):
        for name in ('hoodie', 'hoodie_back_front_34', 'hoodie_front_back',
                     'hoodie_model_front_back', 'hoodie_halloween_team',
                     'hoodie_pumpkin_texture'):
            self.assertEqual(style_for_template(name), 'hoodie_pullover')
        self.assertEqual(style_for_template('sweater'), 'sweater')
        self.assertEqual(style_for_template('tshirt_stepback_back'), 'tshirt')
        for name in ('hoodie_unverified', '../sweater', '', None, []):
            self.assertIsNone(style_for_template(name))
        self.assertIs(validate_style(style_for_template('sweater'))[
            'production_ready'], False)


if __name__ == '__main__':
    unittest.main()
