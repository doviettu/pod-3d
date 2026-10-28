import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from online_auth import COOKIE, TTL, OnlineAuth
import studio


class OnlineAuthTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.password = 'test-online-password-with-enough-length'
        self.file = Path(folder.name) / 'access.json'
        self.file.write_text(json.dumps({'username': 'studio', 'password': self.password}))
        self.auth = OnlineAuth(self.file)
        server = studio.ThreadingHTTPServer(('127.0.0.1', 0), studio.Handler)
        server.online_auth = self.auth
        server.engine = SimpleNamespace(status=lambda: {'app': 'pod-studio'})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.server = server
        def close():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(close)

    def request(self, method='GET', path='/api/status', body=None, extra=None, public=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'X-POD-Online': '1'} if public else {}
        headers.update(extra or {})
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def login(self, password=None):
        return self.request('POST', '/login',
            urlencode({'username': 'studio', 'password': self.password if password is None else password}),
            {'Content-Type': 'application/x-www-form-urlencoded'})

    def test_anonymous_online_pages_show_form_but_api_and_files_are_private(self):
        status, headers, _ = self.request(path='/')
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/login')
        self.assertNotIn('WWW-Authenticate', headers)
        status, headers, body = self.request(path='/login')
        self.assertEqual(status, 200)
        self.assertIn(b'type="password"', body)
        self.assertNotIn(self.password.encode(), body)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        for path in ('/api/status', '/api/jobs', '/files/' + 'a' * 32 + '/local.zip', '/review.js'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path)[0], 401)
        self.assertEqual(self.request('POST', '/api/jobs', '{}', {'Content-Type': 'application/json'})[0], 401)

    def test_form_session_opens_app_and_logout_clears_cookie(self):
        status, headers, _ = self.login()
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        cookie = headers['Set-Cookie']
        for flag in ('Secure', 'HttpOnly', 'SameSite=Strict', 'Path=/', 'Max-Age=' + str(TTL)):
            self.assertIn(flag, cookie)
        self.assertNotIn(self.password, cookie)
        session = cookie.split(';', 1)[0]
        status, response_headers, body = self.request(extra={'Cookie': session})
        self.assertEqual(response_headers['Referrer-Policy'], 'same-origin')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['online_access'])
        status, headers, _ = self.request('POST', '/logout', '', {'Cookie': session})
        self.assertEqual(status, 303)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])

    def test_wrong_and_repeated_logins_never_grant_session(self):
        for _ in range(10):
            status, headers, _ = self.login('wrong')
            self.assertEqual(status, 401)
            self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(self.login()[0], 429)

    def test_malformed_expired_or_tampered_sessions_are_rejected(self):
        with patch('online_auth.time.time', return_value=1000):
            token = self.auth.issue(self.password)
        with patch('online_auth.time.time', return_value=1000 + TTL + 1):
            self.assertFalse(self.auth.valid_session(self.password, COOKIE + '=' + token))
        for value in ('bad', token[:-1] + 'z', token + 'é', '9999999999999.a.b'):
            with self.subTest(value=value):
                self.assertEqual(self.request(extra={'Cookie': COOKIE + '=' + value})[0], 401)

    def test_localhost_mode_and_programmatic_basic_access_still_work(self):
        status, _, body = self.request(public=False)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['online_access'])
        basic = 'Basic ' + base64.b64encode(('studio:' + self.password).encode()).decode()
        self.assertEqual(self.request(extra={'Authorization': basic})[0], 401)
        status, headers, body = self.request(extra={'Authorization': basic, 'X-POD-API-Client': '1'})
        self.assertEqual(status, 200)
        self.assertNotIn('WWW-Authenticate', headers)
        self.assertNotIn(self.password.encode(), body)
        self.assertEqual(self.request(extra={'Authorization': 'Basic not-base64'})[0], 401)

    def test_bad_form_and_missing_configuration_fail_closed(self):
        self.assertEqual(self.request('POST', '/login', '{}', {'Content-Type': 'application/json'})[0], 415)
        self.assertEqual(self.request('POST', '/login', 'x' * 5000, {'Content-Type': 'application/x-www-form-urlencoded'})[0], 413)
        self.file.unlink()
        self.assertEqual(self.request()[0], 503)


if __name__ == '__main__':
    unittest.main()
