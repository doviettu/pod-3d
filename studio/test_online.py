import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import online


class OnlineTests(unittest.TestCase):
    def test_config_requires_login_and_checks_origin_before_forwarding(self):
        text = online.caddy_config('example.trycloudflare.com')
        self.assertIn('bind 127.0.0.1', text)
        self.assertIn('admin off', text)
        self.assertLess(text.index('respond @foreign'), text.index('reverse_proxy'))
        self.assertNotIn('basic_auth', text)
        self.assertIn('header_up X-POD-Online 1', text)
        self.assertIn('not header Origin https://example.trycloudflare.com', text)
        self.assertNotIn('header_up -Cookie', text)
        for host in ('evil.invalid', 'example.trycloudflare.com\nrespond 200', ''):
            with self.assertRaises(ValueError):
                online.caddy_config(host)

    def test_private_credential_is_reused_without_rotation(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(online, 'ROOT', Path(folder)):
            first = online.credential()
            self.assertGreaterEqual(len(first), 24)
            self.assertEqual(online.credential(), first)
            path = Path(folder) / 'access.json'
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())['username'], 'studio')

    def test_public_dns_requires_published_address(self):
        for result, expected in (({'Status': 3}, False),
                                 ({'Status': 0, 'Answer': [{'type': 5}]}, False),
                                 ({'Status': 0, 'Answer': [{'type': 1}]}, True)):
            response = MagicMock()
            response.__enter__.return_value = io.BytesIO(json.dumps(result).encode())
            with self.subTest(result=result), patch('online.urllib.request.urlopen', return_value=response):
                self.assertEqual(online.public_dns_ready('example.trycloudflare.com'), expected)


if __name__ == '__main__':
    unittest.main()
