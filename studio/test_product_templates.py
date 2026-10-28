import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from product_templates import ProductTemplates


class ProductTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = SimpleNamespace(name='sweater')
        self.base = SimpleNamespace(available=lambda:['sweater'], load=lambda name:self.old,
                                    Panel=lambda **values:SimpleNamespace(**values))
        self.catalog = ProductTemplates(self.base,self.root)
        self.data = {'name':'tshirt_back','label':'Áo thun · Lưng','dpi':72,
            'metadata':{'production_ready':False,'background':'model'},
            'panels':[{'name':'Back','size':[384,568],'source':'unwrap','method':'quad',
                       'quad':[[.2,.2],[.8,.2],[.8,.9],[.2,.9]]}]}
        self.directory = self.root / 'tshirt_back'
        self.directory.mkdir()
        self.write()

    def write(self):
        (self.directory / 'template.json').write_text(json.dumps(self.data))

    def test_back_only_profile_preserves_legacy_and_working_dimensions(self):
        self.assertIs(self.catalog.load('sweater'),self.old)
        tpl = self.catalog.load('tshirt_back')
        self.assertEqual([p.name for p in tpl.panels],['Back'])
        self.assertEqual(tpl.panel('Back').size,(384,568))
        self.assertEqual(tpl.dpi,72)
        self.assertFalse(self.catalog.metadata('tshirt_back')['production_ready'])
        self.assertEqual(self.catalog.metadata('sweater')['production_ready'],True)
        self.assertEqual(self.catalog.available(),['sweater','tshirt_back'])
        self.assertEqual(tpl.map_path('Back'),self.directory.resolve() / 'map_Back.npz')
        with self.assertRaises(ValueError):
            tpl.map_path('../private')

    def test_bad_names_and_invalid_geometry_fail_closed(self):
        for name in ('../sweater','../../access.json','', 'A/B'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                self.catalog.load(name)
        self.data['panels'][0]['quad'][0][0] = 1.2
        self.write()
        with self.assertRaises(ValueError):
            self.catalog.load('tshirt_back')
        self.data['panels'][0]['quad'][0][0] = .2
        self.data['panels'][0]['size'] = [100000,100000]
        self.write()
        with self.assertRaises(ValueError):
            self.catalog.load('tshirt_back')

    def test_reference_is_catalog_local_and_not_symlink(self):
        target = self.root / 'outside.jpg'
        target.write_bytes(b'photo')
        (self.directory / 'reference.jpg').symlink_to(target)
        self.assertIsNone(self.catalog.reference_path('tshirt_back'))

    def test_new_profile_cannot_shadow_template_used_by_historical_jobs(self):
        directory = self.root / 'sweater'
        directory.mkdir()
        data = {**self.data,'name':'sweater'}
        (directory / 'template.json').write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'ghi đè'):
            self.catalog.load('sweater')

    def test_only_complete_profiles_are_candidates_for_automatic_selection(self):
        (self.directory / 'reference.jpg').write_bytes(b'photo')
        refs = self.catalog.detection_references()
        self.assertEqual(refs[0]['id'],'tshirt_back')
        self.assertFalse(refs[0]['ready'])
        (self.directory / 'map_Back.npz').touch()
        (self.directory / 'silhouette.png').touch()
        self.assertTrue(self.catalog.detection_references()[0]['ready'])


if __name__ == '__main__':
    unittest.main()
