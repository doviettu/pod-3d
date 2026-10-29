"""Tests for the "Hỏi Claude" tab: the claude -p wrapper and its streaming HTTP endpoint."""
import base64
import http.client
import os
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

from PIL import Image
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_chat  # noqa: E402
from claude_chat import ClaudeChat  # noqa: E402
import studio  # noqa: E402

SESSION = '0f6c1a52-8a5e-4d4e-9f0a-3c2b1d0e9f8a'
STREAM = [
    {'type': 'system', 'subtype': 'init', 'session_id': SESSION},
    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Read',
                                                   'input': {'file_path': '/x/studio/runtime/logs/server.log'}}]}},
    {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Xin '}}},
    {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'chào'}}},
    {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Xin chào'}]}},
    {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'Xin chào', 'session_id': SESSION, 'num_turns': 2},
]


class FakeProc:
    def __init__(self, cmd, lines, **kwargs):
        self.cmd, self.kwargs, self.returncode = cmd, kwargs, None
        self.stdin = io.StringIO()
        self.stdin.close = lambda: setattr(self, 'prompt', self.stdin.getvalue())
        self.stdout = io.StringIO(''.join(json.dumps(line) + '\n' for line in lines))

    def poll(self):
        return self.returncode

    def wait(self):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


class Recorder:
    def __init__(self, lines=STREAM):
        self.lines, self.procs = lines, []

    def __call__(self, cmd, **kwargs):
        proc = FakeProc(cmd, self.lines, **kwargs)
        self.procs.append(proc)
        return proc


class ClaudeChatTest(unittest.TestCase):
    def chat(self, recorder):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return ClaudeChat(cwd=self.tmp.name, root=Path(self.tmp.name) / 'runtime',
                          finder=lambda: '/usr/bin/claude', popen=recorder)

    def test_streams_text_tools_and_session(self):
        recorder = Recorder()
        events = list(self.chat(recorder).ask({'prompt': 'chào'}))
        self.assertEqual([e['type'] for e in events], ['start', 'tool', 'text', 'text', 'done'])
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'text'), 'Xin chào')
        self.assertTrue(events[1]['detail'].endswith('server.log'))
        self.assertEqual(events[-1]['session'], SESSION)
        self.assertTrue(recorder.procs[0].prompt.startswith('chào'))

    def test_read_only_and_no_api_key(self):
        recorder = Recorder()
        with unittest.mock.patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'sk-ant-secret'}):
            list(self.chat(recorder).ask({'prompt': 'hi', 'session': SESSION, 'mode': 'read'}))
        proc = recorder.procs[0]
        allowed = proc.cmd[proc.cmd.index('--allowedTools') + 1:proc.cmd.index('--disallowedTools')]
        self.assertEqual(set(allowed), set(claude_chat.READ_TOOLS))
        self.assertIn('--restricted', proc.cmd)
        self.assertEqual(proc.cmd[proc.cmd.index('--tools') + 1], 'Read,Grep,Glob')
        self.assertNotIn('--dangerously-skip-permissions', proc.cmd)
        self.assertEqual(proc.cmd[proc.cmd.index('--resume') + 1], SESSION)
        self.assertNotIn('ANTHROPIC_API_KEY', proc.kwargs['env'])
        self.assertIn('Read(studio/runtime/online/**)', proc.cmd)

    def test_image_mode_only_allows_studio_tools_and_output_folder(self):
        recorder = Recorder()
        chat = self.chat(recorder)
        list(chat.ask({'prompt': 'tạo ảnh', 'mode': 'image'}, online=True))
        cmd = recorder.procs[0].cmd
        allowed = cmd[cmd.index('--allowedTools') + 1:cmd.index('--disallowedTools')]
        out = recorder.procs[0].kwargs['env']['POD_CLAUDE_OUT']
        rel = os.path.relpath(out, self.tmp.name)
        self.assertIn('mcp__studio', allowed)
        self.assertIn(f'Edit({rel}/**)', allowed)
        self.assertIn('--restricted', cmd)
        self.assertNotIn('Bash', ' '.join(cmd))
        self.assertEqual(cmd[cmd.index('--tools') + 1], 'Read,Grep,Glob,Edit,Write')
        config = json.loads(cmd[cmd.index('--mcp-config') + 1])
        self.assertEqual(config['mcpServers']['studio']['env']['POD_CLAUDE_OUT'], out)

    def test_full_mode_only_from_the_mac(self):
        chat = self.chat(Recorder())
        with self.assertRaises(ValueError):
            chat.ask({'prompt': 'rm', 'mode': 'full'}, online=True)
        self.assertEqual(chat.status(online=True)['modes'], ['read', 'image'])
        list(chat.ask({'prompt': 'ok', 'mode': 'full'}))
        with self.assertRaises(ValueError):
            chat.ask({'prompt': 'x', 'mode': 'root'})

    def test_attachments_saved_outputs_listed_and_log_written(self):
        png = io.BytesIO()
        Image.new('RGB', (4, 4), 'red').save(png, format='PNG')
        recorder = Recorder()
        chat = self.chat(recorder)
        real = recorder.__call__

        def popen(cmd, **kwargs):
            out = Path(kwargs['env']['POD_CLAUDE_OUT'])
            (out / 'ket-qua.png').write_bytes(png.getvalue())
            return real(cmd, **kwargs)
        chat.popen = popen
        events = list(chat.ask({'prompt': 'xem ảnh', 'mode': 'image',
                                'attachments': [{'name': '../../evil.png', 'data': base64.b64encode(png.getvalue()).decode()}]}))
        chat_id = events[0]['chat']
        saved = list((chat.folder(chat_id) / 'in').iterdir())
        self.assertEqual(len(saved), 1)
        self.assertNotIn('..', saved[0].name)
        self.assertIn(os.path.relpath(saved[0], self.tmp.name), recorder.procs[0].prompt)
        files = [e for e in events if e['type'] == 'file']
        self.assertEqual(files, [{'type': 'file', 'name': 'ket-qua.png', 'url': f'/claude-files/{chat_id}/ket-qua.png',
                                  'image': True, 'bytes': len(png.getvalue())}])
        self.assertEqual(chat.file_path(chat_id, 'ket-qua.png').name, 'ket-qua.png')
        for bad in ('../in/' + saved[0].name, '../../logs/claude-chat.jsonl'):
            with self.assertRaises(FileNotFoundError):
                chat.file_path(chat_id, bad)
        with self.assertRaises(ValueError):
            chat.file_path('../x', 'a.png')
        log = (Path(self.tmp.name) / 'runtime/logs/claude-chat.jsonl').read_text().splitlines()
        self.assertEqual(json.loads(log[0])['mode'], 'image')
        for bad in ([{'data': 'not base64!'}], [{'data': base64.b64encode(b'MZ exe').decode()}], [{}] * 5):
            with self.assertRaises(ValueError):
                chat.ask({'prompt': 'a', 'attachments': bad})

    def test_rejects_bad_input_and_parallel_questions(self):
        chat = self.chat(Recorder())
        for bad in ({'prompt': ''}, {'prompt': 'x' * 9000}, {'prompt': 'a', 'session': '--dangerously-skip-permissions'}):
            with self.assertRaises(ValueError):
                chat.ask(bad)
        first = chat.ask({'prompt': 'a'})
        next(first)
        with self.assertRaises(RuntimeError):
            chat.ask({'prompt': 'b'})
        first.close()
        self.assertFalse(chat.status()['busy'])
        list(chat.ask({'prompt': 'c'}))

    def test_reports_crash_and_missing_cli(self):
        events = list(self.chat(Recorder(lines=[])).ask({'prompt': 'a'}))
        self.assertEqual(events[-1]['type'], 'error')
        missing = ClaudeChat(finder=lambda: None)
        self.assertFalse(missing.status()['available'])
        with self.assertRaises(RuntimeError):
            missing.ask({'prompt': 'a'})


class HttpTest(unittest.TestCase):
    def test_chat_endpoint_streams_events(self):
        server = studio.ThreadingHTTPServer(('127.0.0.1', 0), studio.Handler)
        server.engine = SimpleNamespace(settings=SimpleNamespace(api_key=None))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server.claude = ClaudeChat(cwd=tmp.name, root=Path(tmp.name) / 'runtime', finder=lambda: '/usr/bin/claude', popen=Recorder())
        server.online_auth = studio.OnlineAuth(Path('/nonexistent/access.json'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))

        def post(body):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            connection.request('POST', '/api/claude/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = response.status, response.getheader('Content-Type'), response.read().decode()
            connection.close()
            return result

        status, kind, body = post({'prompt': 'chào'})
        self.assertEqual(status, 200)
        self.assertTrue(kind.startswith('text/event-stream'))
        events = [json.loads(line[6:]) for line in body.split('\n\n') if line.startswith('data: ')]
        self.assertEqual(events[-1], {'type': 'done', 'session': SESSION, 'seconds': events[-1]['seconds'], 'turns': 2})
        status, _, body = post({'prompt': ''})
        self.assertEqual(status, 400)


if __name__ == '__main__':
    unittest.main()
