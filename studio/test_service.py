"""Tests for the launchd service files and the online-link mail (no network, no launchctl)."""
import json
import os
from pathlib import Path
import plistlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mailer  # noqa: E402
import service  # noqa: E402

CFG = {'smtp_host': 'smtp.gmail.com', 'port': 465, 'user': 'me@gmail.com', 'app_password': 'abcd efgh ijkl mnop',
       'to': ['me@gmail.com'], 'include_password': True}


class FakeSMTP:
    sent = []

    def __init__(self, host, port):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        assert password == 'abcdefghijklmnop'   # spaces Google shows are removed

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


class MailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / 'mail.json'
        FakeSMTP.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_file_is_private_and_checked(self):
        mailer.save(CFG, self.config)
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o600)
        self.assertEqual(mailer.load(self.config)['to'], ['me@gmail.com'])
        self.assertIsNone(mailer.load(self.root / 'missing.json'))
        mailer.save({**CFG, 'to': 'not-an-email'}, self.config)
        with self.assertRaises(mailer.MailError):
            mailer.load(self.config)

    def test_new_link_is_mailed_once(self):
        mailer.save(CFG, self.config)
        state = self.root / 'mail-state.json'
        args = dict(config=self.config, smtp=FakeSMTP, log=lambda m: None)
        self.assertTrue(mailer.notify_online('https://a-b.trycloudflare.com', 'pw-123', state, **args))
        self.assertFalse(mailer.notify_online('https://a-b.trycloudflare.com', 'pw-123', state, **args))
        self.assertTrue(mailer.notify_online('https://c-d.trycloudflare.com', 'pw-123', state, **args))
        self.assertEqual(len(FakeSMTP.sent), 2)
        body = FakeSMTP.sent[0].get_content()
        self.assertIn('https://a-b.trycloudflare.com', body)
        self.assertIn('Tài khoản: studio', body)
        self.assertIn('Mật khẩu: pw-123', body)

    def test_password_can_be_left_out_and_no_config_sends_nothing(self):
        mailer.save({**CFG, 'include_password': False}, self.config)
        mailer.notify_online('https://x.trycloudflare.com', 'secret-pw', self.root / 's.json',
                             config=self.config, smtp=FakeSMTP, log=lambda m: None)
        self.assertNotIn('secret-pw', FakeSMTP.sent[0].get_content())
        self.assertFalse(mailer.notify_online('https://y.trycloudflare.com', 'pw', self.root / 's2.json',
                                              config=self.root / 'none.json', smtp=FakeSMTP, log=lambda m: None))

    def test_mail_failure_is_logged_not_raised(self):
        class Broken(FakeSMTP):
            def login(self, user, password):
                raise OSError('network down')
        mailer.save(CFG, self.config)
        logs = []
        state = self.root / 'mail-state.json'
        self.assertFalse(mailer.notify_online('https://z.trycloudflare.com', 'pw', state, config=self.config,
                                              smtp=Broken, log=logs.append))
        self.assertIn('network down', logs[0])
        self.assertFalse(state.exists())   # the next start tries again


class ServiceTests(unittest.TestCase):
    def test_launch_agents_restart_and_log(self):
        for name in ('server', 'online'):
            data = plistlib.loads(plistlib.dumps(service.plist(name)))
            self.assertTrue(data['RunAtLoad'] and data['KeepAlive'])
            self.assertEqual(data['ProgramArguments'][0], str(service.PYTHON))
            self.assertTrue(data['ProgramArguments'][1].endswith(service.SERVICES[name][1]))
            self.assertIn('/opt/homebrew/bin', data['EnvironmentVariables']['PATH'])
            self.assertTrue(data['StandardOutPath'].endswith(f'{name}.log'))
        self.assertNotEqual(service.plist('server')['Label'], service.plist('online')['Label'])

    def test_launcher_app_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = service.make_app(Path(tmp) / 'POD Studio.app')
            info = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
            script = app / 'Contents' / 'MacOS' / info['CFBundleExecutable']
            self.assertTrue(os.access(script, os.X_OK))
            text = script.read_text()
            self.assertIn('kickstart gui/$UID/$label', text)
            self.assertIn("/usr/bin/open http://127.0.0.1:8765", text)
            self.assertTrue((app / 'Contents' / 'Resources' / 'AppIcon.icns').stat().st_size > 10000)
            service.make_app(app)   # rebuilding replaces the old bundle
            self.assertTrue(script.is_file())


if __name__ == '__main__':
    unittest.main()
