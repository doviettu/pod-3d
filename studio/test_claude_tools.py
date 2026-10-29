"""Tests for the fixed image tools that "Hỏi Claude" may run through the online link."""
import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_tools  # noqa: E402
import claude_mcp  # noqa: E402


def png(size=(8, 8), color='red'):
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


class ClaudeToolsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runtime = Path(tmp.name) / 'runtime'
        self.out = self.runtime / 'claude' / ('a' * 32) / 'out'
        self.out.mkdir(parents=True)
        self.source = self.runtime / 'claude' / ('a' * 32) / 'in' / 'ao.png'
        self.source.parent.mkdir()
        self.source.write_bytes(png((40, 20)))
        self.outside = Path(tmp.name) / 'secret.png'
        self.outside.write_bytes(png())
        patches = [patch.object(claude_tools, 'RUNTIME', self.runtime),
                   patch.dict(os.environ, {'POD_CLAUDE_OUT': str(self.out)})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_tool(self, *argv):
        with patch('sys.stdout', new=io.StringIO()) as stdout:
            claude_tools.main(list(argv))
        return json.loads(stdout.getvalue())

    def test_resize_writes_into_chat_output_only(self):
        result = self.run_tool('resize', '--image', str(self.source), '--size', '100x120', '--out', '../../evil')
        target = Path(result['file'])
        self.assertEqual(target.parent, self.out)
        with Image.open(target) as img:
            self.assertEqual(img.size, (100, 120))
            self.assertEqual(round(img.info['dpi'][0]), 300)

    def test_inputs_must_live_in_runtime_and_out_must_be_set(self):
        with self.assertRaises(SystemExit):
            self.run_tool('resize', '--image', str(self.outside), '--size', '10x10', '--out', 'x')
        with patch.dict(os.environ, {'POD_CLAUDE_OUT': str(self.outside.parent)}):
            with self.assertRaises(SystemExit):
                self.run_tool('resize', '--image', str(self.source), '--size', '10x10', '--out', 'x')

    def test_ai_image_respects_budget_and_records_spend(self):
        settings = type('S', (), {'model': 'gpt-image-2.5-flare', 'api_key': 'k', 'image_size': '1K', 'quality': 'medium'})()
        calls = []

        def fake_request(_settings, payload, endpoint):
            calls.append((endpoint, payload))
            return {'data': [{'b64_json': base64.b64encode(png((16, 16), 'blue')).decode()}]}
        with patch.object(claude_tools.openai_api, 'Settings', return_value=settings), \
                patch.object(claude_tools.openai_api, '_request', side_effect=fake_request):
            result = self.run_tool('ai-image', '--prompt', 'áo hoodie mèo', '--out', 'meo')
            self.assertEqual(calls[0][0], 'images/generations')
            self.assertTrue(Path(result['file']).is_file())
            self.run_tool('ai-image', '--prompt', 'sửa màu', '--out', 'sua', '--image', str(self.source))
            self.assertEqual(calls[1][0], 'images/edits')
            ledger = (self.runtime / 'ai-ledger.jsonl').read_text().splitlines()
            self.assertEqual([json.loads(r)['kind'] for r in ledger], ['claude-chat', 'claude-chat'])
            (self.runtime / 'ai-budget.json').write_text(json.dumps({'daily_usd': 0}))
            with self.assertRaises(SystemExit):
                self.run_tool('ai-image', '--prompt', 'nữa', '--out', 'nua')
            self.assertEqual(len(calls), 2)

    def test_mcp_server_lists_and_calls_tools(self):
        lines = [json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}),
                 json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
                 json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}),
                 json.dumps({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'resize', 'arguments': {
                     'image': str(self.source), 'size': '20x20', 'out': 'nho'}}}),
                 json.dumps({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'resize', 'arguments': {
                     'image': str(self.outside), 'size': '20x20', 'out': 'nho'}}})]
        with patch('sys.stdin', new=io.StringIO('\n'.join(lines) + '\n')), patch('sys.stdout', new=io.StringIO()) as out:
            claude_mcp.main()
        replies = [json.loads(r) for r in out.getvalue().splitlines()]
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4])
        self.assertEqual({t['name'] for t in replies[1]['result']['tools']}, {'budget', 'ai_image', 'art2d', 'resize'})
        self.assertFalse(replies[2]['result'].get('isError'))
        self.assertTrue((self.out / 'nho.png').is_file())
        self.assertTrue(replies[3]['result']['isError'])


if __name__ == '__main__':
    unittest.main()
