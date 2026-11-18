"""Tests for the Oracle -> Mac AI queue (no network, no models)."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from pod3d import offload  # noqa: E402
import panel_detect  # noqa: E402
import worker  # noqa: E402
import worker_api  # noqa: E402


class BrokerTests(unittest.TestCase):
    def test_no_worker_runs_locally(self):
        self.assertIsNone(offload.Broker().run('upscale', {'img': np.zeros((2, 2, 3), np.uint8)}))

    def test_round_trip(self):
        broker = offload.Broker()
        broker.next(wait=0)  # worker polled: online
        out = {}
        thread = threading.Thread(target=lambda: out.update(r=broker.run('upscale', {'img': np.ones((2, 2, 3), np.uint8)}, {'factor': 2})))
        thread.start()
        task = broker.next(wait=2)
        self.assertEqual((task.kind, task.params), ('upscale', {'factor': 2}))
        img = offload.unpack(task.payload)['img']
        self.assertTrue(broker.finish(task.id, offload.pack({'out': img * 7})))
        thread.join(2)
        self.assertEqual(int(out['r']['out'][0, 0, 0]), 7)
        self.assertEqual(broker.stats['offloaded'], 1)

    def test_unclaimed_task_falls_back(self):
        broker = offload.Broker()
        broker.next(wait=0)
        self.assertIsNone(broker.run('inpaint', {'img': np.zeros(1)}, claim_timeout=0.05))
        self.assertEqual(broker.status()['pending'], 0)

    def test_worker_error_falls_back(self):
        broker = offload.Broker()
        broker.next(wait=0)
        out = {}
        thread = threading.Thread(target=lambda: out.update(r=broker.run('sam3', {'rgb': np.zeros(1)})))
        thread.start()
        task = broker.next(wait=2)
        broker.finish(task.id, error='boom')
        thread.join(2)
        self.assertIsNone(out['r'])

    def test_disabled_without_env(self):
        os.environ.pop('POD_OFFLOAD', None)
        self.assertIsNone(offload.run('upscale', {'img': np.zeros(1)}))

    def test_segments_pack_round_trip(self):
        found = {'hoodie': [(np.eye(3, dtype=bool), 0.9)], 'sleeve': []}
        back = panel_detect.unpack_segments(['hoodie', 'sleeve'], panel_detect.pack_segments(['hoodie', 'sleeve'], found))
        self.assertEqual(back['sleeve'], [])
        self.assertTrue((back['hoodie'][0][0] == np.eye(3, dtype=bool)).all())
        self.assertAlmostEqual(back['hoodie'][0][1], 0.9, places=5)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.token_path = Path(self.dir.name) / 'token'
        self.token = worker_api.create_token(self.token_path)
        self.broker = offload.Broker()
        broker, token_path = self.broker, self.token_path

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                worker_api.handle(self, broker, token_path)

            do_POST = do_GET

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def test_token_required(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        conn.request('GET', '/api/worker/status', headers={'Authorization': 'Bearer wrong'})
        self.assertEqual(conn.getresponse().status, 401)
        self.assertEqual(oct(self.token_path.stat().st_mode & 0o777), '0o600')

    def test_worker_client_runs_task(self):
        self.broker.next(wait=0)
        out = {}
        thread = threading.Thread(target=lambda: out.update(r=self.broker.run('double', {'x': np.arange(3)})))
        thread.start()
        original = worker.run_task
        try:
            worker.run_task = lambda kind, params, arrays: {'out': arrays['x'] * 2}
            self.assertTrue(worker.once(self.base, self.token, log=lambda m: None))
        finally:
            worker.run_task = original
        thread.join(3)
        self.assertEqual(out['r']['out'].tolist(), [0, 2, 4])

    def test_idle_poll_is_204(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=40)
        import time
        start = time.monotonic()
        self.broker_next = self.broker.next
        self.broker.next = lambda wait=25.0: offload.Broker.next(self.broker, wait=0.1)
        conn.request('GET', '/api/worker/next', headers={'Authorization': 'Bearer ' + self.token})
        self.assertEqual(conn.getresponse().status, 204)
        self.assertLess(time.monotonic() - start, 5)
        status = json.loads(_get(self.base + '/api/worker/status', self.token))
        self.assertTrue(status['worker_online'])


def _get(url, token):
    import urllib.request
    return urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})).read()


if __name__ == '__main__':
    unittest.main()
