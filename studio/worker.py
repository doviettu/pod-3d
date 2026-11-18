"""Mac side of the AI queue: take heavy steps from the Oracle server and run them on the Mac GPU.

The Mac only makes outgoing HTTPS requests (long-poll), so nothing is opened on the Mac.
Settings in ~/.config/pod-studio/worker.json (mode 600):
    {"url": "https://pod.vitumonitor.duckdns.org", "token": "<copy of runtime/worker/token on the server>"}

    .venv/bin/python studio/worker.py          # run (service.py installs it as com.podstudio.worker)
    .venv/bin/python studio/worker.py --once   # take at most one task, for testing
"""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
CONFIG = Path.home() / '.config/pod-studio/worker.json'


def load_config(path=CONFIG):
    data = json.loads(Path(path).read_text())
    if not str(data.get('url', '')).startswith('https://') or len(data.get('token', '')) < 32:
        raise SystemExit(f'{path}: cần "url" (https://...) và "token".')
    return data['url'].rstrip('/'), data['token']


def run_task(kind, params, arrays):
    """Run one step locally (offload is off in this process, so these never re-queue)."""
    from pod3d import quality
    with quality.hq_scope(params.get('hq')):
        return _run_task(kind, params, arrays)


def _run_task(kind, params, arrays):
    if kind == 'upscale':
        from pod3d import upscale
        return {'out': upscale.upscale(arrays['img'], params['factor'], params.get('model') or upscale.DEFAULT_MODEL,
                                       log=lambda *a: None)}
    if kind == 'inpaint':
        from pod3d import inpaint
        if not inpaint.available():
            raise RuntimeError('LaMa chưa có trên Mac')
        return {'out': inpaint.inpaint(arrays['img'], arrays['mask'])}
    if kind == 'sam3':
        import panel_detect
        prompts = params['prompts']
        found = panel_detect.sam3_segmenter()(arrays['rgb'], prompts)
        return panel_detect.pack_segments(prompts, found)
    raise ValueError(f'không biết việc {kind}')


def request(url, token, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, method='POST' if data is not None else 'GET',
                                 headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'pod-studio-worker',
                                          **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def once(base, token, log=print):
    """Poll once. True when a task was handled."""
    with request(base + '/api/worker/next', token, timeout=40) as response:
        if response.status == 204:
            return False
        task_id = response.headers['X-Task-Id']
        kind = response.headers['X-Task-Kind']
        params = json.loads(response.headers.get('X-Task-Params') or '{}')
        payload = response.read()
    from pod3d import offload
    started = time.monotonic()
    try:
        body, headers = offload.pack(run_task(kind, params, offload.unpack(payload))), {}
        log(f'{kind}: xong trong {time.monotonic() - started:.1f} s')
    except Exception as exc:  # noqa: BLE001 - report to the server, which then runs it itself
        body, headers = b'', {'X-Task-Error': f'{type(exc).__name__}: {exc}'[:300]}
        log(f'{kind}: lỗi {exc}')
    request(f'{base}/api/worker/result/{task_id}', token, body,
            {'Content-Type': 'application/octet-stream', **headers}, timeout=300).close()
    return True


def main():
    parser = argparse.ArgumentParser(description='POD Studio: Mac nhận việc AI nặng từ máy chủ')
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    base, token = load_config(args.config)
    print(f'Worker: nhận việc từ {base}', flush=True)
    delay = 5
    while True:
        try:
            handled = once(base, token, log=lambda m: print(m, flush=True))
            delay = 5
            if args.once:
                return
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise SystemExit('Token worker sai: chép lại runtime/worker/token từ máy chủ.')
            print(f'Máy chủ trả {exc.code}, thử lại sau {delay} s', flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 300)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f'Không kết nối được ({exc}), thử lại sau {delay} s', flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 300)


if __name__ == '__main__':
    main()
