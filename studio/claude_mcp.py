"""Minimal MCP server (stdio, JSON-RPC per line) that exposes claude_tools.py to "Hỏi Claude".

In the read and image modes Claude gets no shell at all; the image tools arrive as MCP tools
instead, so the only things it can run are the commands defined in claude_tools.py.
"""
import contextlib
import io
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_tools  # noqa: E402

PROTOCOL = '2025-06-18'
TOOLS = [
    {'name': 'budget', 'description': 'Xem trần ngân sách ảnh AI và số tiền đã dùng hôm nay/tháng này.',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'ai_image', 'description': ('Tạo hoặc sửa ảnh bằng OpenAI (TỐN PHÍ, có trần ngân sách). Không có image thì tạo '
                                         'từ prompt; có image (đường dẫn trong studio/runtime/) thì sửa/ghép từ ảnh đó. '
                                         'dry_run=true chỉ ước tính chi phí.'),
     'inputSchema': {'type': 'object', 'required': ['prompt', 'out'], 'properties': {
         'prompt': {'type': 'string'}, 'out': {'type': 'string', 'description': 'tên file kết quả'},
         'image': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 4},
         'size': {'type': 'string', 'enum': ['1K', '2K']},
         'quality': {'type': 'string', 'enum': ['low', 'medium', 'high']},
         'dry_run': {'type': 'boolean'}}}},
    {'name': 'art2d', 'description': ('Miễn phí: tách art in từ 1-2 ảnh áo (trước/sau) thành PNG trong suốt 4500x5400, '
                                      '300 DPI.'),
     'inputSchema': {'type': 'object', 'required': ['image'], 'properties': {
         'image': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 2},
         'name': {'type': 'string'}, 'sides': {'type': 'string', 'enum': ['auto', 'front', 'back', 'both']}}}},
    {'name': 'resize', 'description': 'Miễn phí: đổi kích thước (giữ tỉ lệ, nền trong suốt) và đặt DPI.',
     'inputSchema': {'type': 'object', 'required': ['image', 'size', 'out'], 'properties': {
         'image': {'type': 'string'}, 'size': {'type': 'string', 'description': 'ví dụ 4500x5400'},
         'dpi': {'type': 'integer'}, 'out': {'type': 'string'}}}},
]


def argv_for(name, args):
    """Turn MCP arguments into the claude_tools command line."""
    if name == 'budget':
        return ['budget']
    if name == 'ai_image':
        argv = ['ai-image', '--prompt', str(args.get('prompt', '')), '--out', str(args.get('out', ''))]
        for path in args.get('image') or []:
            argv += ['--image', str(path)]
        for key in ('size', 'quality'):
            if args.get(key):
                argv += [f'--{key}', str(args[key])]
        return argv + (['--dry-run'] if args.get('dry_run') else [])
    if name == 'art2d':
        argv = ['art2d']
        for path in args.get('image') or []:
            argv += ['--image', str(path)]
        for key in ('name', 'sides'):
            if args.get(key):
                argv += [f'--{key}', str(args[key])]
        return argv
    if name == 'resize':
        argv = ['resize', '--image', str(args.get('image', '')), '--size', str(args.get('size', '')),
                '--out', str(args.get('out', ''))]
        return argv + (['--dpi', str(int(args['dpi']))] if args.get('dpi') else [])
    raise KeyError(name)


def call(name, args):
    output = io.StringIO()
    try:
        argv = argv_for(name, args if isinstance(args, dict) else {})
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            claude_tools.main(argv)
        return {'content': [{'type': 'text', 'text': output.getvalue()}]}
    except KeyError:
        return {'content': [{'type': 'text', 'text': f'Không có công cụ {name}.'}], 'isError': True}
    except SystemExit as exc:
        text = exc.code if isinstance(exc.code, str) else output.getvalue() or 'Tham số không hợp lệ.'
        return {'content': [{'type': 'text', 'text': text}], 'isError': True}
    except Exception as exc:  # report any tool failure to Claude instead of killing the server
        return {'content': [{'type': 'text', 'text': f'Lỗi: {exc}'[:1000]}], 'isError': True}


def handle(message):
    method, params = message.get('method'), message.get('params') or {}
    if method == 'initialize':
        return {'protocolVersion': params.get('protocolVersion', PROTOCOL), 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'pod-studio', 'version': '1'}}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'tools/call':
        return call(params.get('name'), params.get('arguments'))
    if method == 'ping':
        return {}
    raise LookupError(method)


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if 'id' not in message:
            continue  # notifications need no answer
        try:
            reply = {'jsonrpc': '2.0', 'id': message['id'], 'result': handle(message)}
        except LookupError:
            reply = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Method not found'}}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
