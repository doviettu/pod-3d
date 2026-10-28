import copy
import json
from pathlib import Path
import tempfile
import unittest
from market_library import load_market_library, DEFAULT


class MarketLibraryTests(unittest.TestCase):
    def setUp(self):
        self.library = json.loads(DEFAULT.read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'library.json'

    def test_catalog_is_sourced_and_never_claims_manufacturing_geometry(self):
        data = load_market_library()
        self.assertGreaterEqual(len(data['items']),8)
        self.assertGreaterEqual(len({row['brand'] for row in data['items']}),4)
        self.assertTrue(all(row['manufacturing_ready'] is False for row in data['items']))
        self.assertTrue(all(row['product_url'].startswith('https://') for row in data['items']))

    def test_external_links_and_production_claims_fail_closed(self):
        for field,value in [('product_url','javascript:alert(1)'),
                            ('image_url','https://user:secret@example.org/photo.jpg'),
                            ('manufacturing_ready',True)]:
            data = copy.deepcopy(self.library)
            data['items'][0][field] = value
            self.path.write_text(json.dumps(data))
            with self.subTest(field=field),self.assertRaises(ValueError):
                load_market_library(self.path)

    def test_duplicate_ids_and_malformed_tags_are_rejected(self):
        for change in ('duplicate','tags'):
            data = copy.deepcopy(self.library)
            if change == 'duplicate':
                data['items'].append(copy.deepcopy(data['items'][0]))
            else:
                data['items'][0]['style_tags'] = ['<script>']
            self.path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_market_library(self.path)


if __name__ == '__main__':
    unittest.main()
