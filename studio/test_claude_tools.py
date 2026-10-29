"""Tests for the fixed image tools that "Hỏi Claude" may run through the online link."""
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

    def test_svg_renders_print_size_transparent_png(self):
        svg = self.out / 'meo.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 120">'
                       '<circle cx="50" cy="60" r="40" fill="#e4572e"/></svg>')
        result = self.run_tool('svg', '--svg', str(svg), '--size', '450x540', '--out', 'meo')
        with Image.open(result['file']) as img:
            self.assertEqual(img.size, (450, 540))
            self.assertEqual(img.mode, 'RGBA')
            self.assertEqual(img.getpixel((2, 2))[3], 0)
            self.assertEqual(img.getpixel((225, 270))[:3], (0xe4, 0x57, 0x2e))
            self.assertEqual(round(img.info['dpi'][0]), 300)
        self.assertTrue(Path(self.run_tool('svg', '--svg', 'meo.svg', '--size', '45x54', '--out', 'nho')['file']).is_file())
        with self.assertRaises(SystemExit):
            self.run_tool('svg', '--svg', str(self.source), '--out', 'x')

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
        self.assertEqual({t['name'] for t in replies[1]['result']['tools']}, {'svg', 'art2d', 'resize'})
        self.assertFalse(replies[2]['result'].get('isError'))
        self.assertTrue((self.out / 'nho.png').is_file())
        self.assertTrue(replies[3]['result']['isError'])


if __name__ == '__main__':
    unittest.main()
