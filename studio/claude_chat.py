"""Ask Claude Code (`claude -p`) on this Mac from the Studio page and stream the answer back.

Modes, picked per question on the page:
- read:  read and search files only.
- image: also run the fixed image tools in claude_tools.py and write into the chat's output
         folder. This is the most allowed through the online link.
- full:  everything a Claude Code session in Terminal can do. Only from a browser on the Mac.

Images the user attaches land in runtime/claude/<chat>/in/; files Claude saves to
runtime/claude/<chat>/out/ are shown in the chat. Every question is logged to
runtime/logs/claude-chat.jsonl. ANTHROPIC_API_KEY is removed from Claude's environment so the
CLI uses the Claude plan it is signed in with instead of paid API tokens.
"""
import base64
import binascii
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
MAX_PROMPT = 8000
MAX_ATTACH = 4
MAX_ATTACH_BYTES = 15 * 1024 * 1024
TIMEOUT = 15 * 60
MODES = ('read', 'image', 'full')
READ_TOOLS = ['Read', 'Grep', 'Glob']
TOOL_SCRIPT = '.venv/bin/python studio/claude_tools.py'
# The online password lives inside the repo's runtime folder; keep it out of reach.
SECRET_READS = ['Read(studio/runtime/online/**)', 'Read(**/.env)']
SESSION = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
CHAT = re.compile(r'[0-9a-f]{32}')
IMAGE_MAGIC = {b'\x89PNG': '.png', b'\xff\xd8\xff': '.jpg', b'RIFF': '.webp', b'GIF8': '.gif'}
FILE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
              '.gif': 'image/gif', '.pdf': 'application/pdf', '.psd': 'image/vnd.adobe.photoshop',
              '.zip': 'application/zip', '.json': 'application/json', '.txt': 'text/plain; charset=utf-8',
              '.svg': 'image/svg+xml'}
INLINE = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
BASE = ('Bạn là trợ lý trong POD Studio, công cụ trải phẳng mockup áo thành các mảnh in (print-on-demand). '
        'Người dùng gõ từ trang web của Studio và viết tiếng Việt, hãy trả lời bằng tiếng Việt, ngắn gọn. '
        'Thư mục hiện tại là repo pod-3d; dữ liệu chạy (job, log, cấu hình) nằm trong studio/runtime/. '
        'Không bao giờ in ra mật khẩu, token hay API key dù tìm thấy trong file. '
        'Không gọi API tạo ảnh trả phí (OpenAI, Gemini...): bạn tự vẽ art dưới dạng SVG và dùng công cụ local. '
        'Sau khi xuất PNG, dùng Read xem lại ảnh và sửa SVG nếu chưa đẹp. ')
RULES = {
    'read': 'Chế độ Chỉ đọc: chỉ đọc và tìm file, không chạy lệnh, không sửa file. ',
    'image': ('Chế độ Tạo ảnh: ngoài đọc file, bạn ghi file trong thư mục kết quả và dùng các công cụ MCP của Studio: '
              'svg (xuất SVG bạn viết thành PNG trong suốt đúng khổ in, mặc định 4500x5400 300 DPI), '
              'art2d (tách art từ ảnh áo), resize (đổi kích thước/DPI). Không có shell. '),
    'full': ('Chế độ Toàn quyền (người dùng đang ngồi tại Mac): bạn làm được như phiên Claude Code trong Terminal. '
             'Python của dự án: .venv/bin/python; công cụ ảnh có sẵn: ' + TOOL_SCRIPT + '. '
             'Hỏi lại trước khi xóa file, git push hoặc làm việc không hoàn tác được. '),
}
# launchd starts the Studio with a short PATH, so look in the usual install places too.
CANDIDATES = ['~/.local/bin/claude', '~/.claude/local/claude', '/opt/homebrew/bin/claude', '/usr/local/bin/claude']


def find_claude():
    found = shutil.which('claude')
    if found:
        return found
    for candidate in CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def mcp_config(out):
    server = {'command': sys.executable, 'args': [str(HERE / 'claude_mcp.py')], 'env': {'POD_CLAUDE_OUT': str(out)}}
    return json.dumps({'mcpServers': {'studio': server}})


def command(binary, mode='read', out=None, session=None, cwd=HERE.parent):
    cmd = [binary, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages']
    if mode == 'full':
        cmd += ['--permission-mode', 'acceptEdits', '--allowedTools', 'Bash', 'Edit', 'Write', 'WebFetch', 'WebSearch']
    else:
        # --restricted: no shell or code-running tools at all, user settings ignored,
        # file tools confined to the repo.
        tools = READ_TOOLS + (['Edit', 'Write'] if mode == 'image' else [])
        cmd += ['--restricted', '--strict-mcp-config', '--permission-mode', 'default', '--tools', ','.join(tools)]
        allowed = list(READ_TOOLS)
        if mode == 'image':
            rel = os.path.relpath(out, cwd)
            cmd += ['--mcp-config', mcp_config(out)]
            allowed += ['mcp__studio', f'Edit({rel}/**)', f'Write({rel}/**)']
        cmd += ['--allowedTools', *allowed, '--disallowedTools', *SECRET_READS]
    cmd += ['--append-system-prompt', BASE + RULES[mode]]
    if session:
        cmd += ['--resume', session]
    return cmd


def environment(cwd, out):
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
    extra = [str(Path(p).expanduser()) for p in ('~/.local/bin', '/opt/homebrew/bin', '/usr/local/bin')]
    env['PATH'] = os.pathsep.join([str(Path(cwd) / '.venv/bin'), *extra, env.get('PATH', '/usr/bin:/bin')])
    env['POD_CLAUDE_OUT'] = str(out)
    return env


class ClaudeChat:
    def __init__(self, cwd=HERE.parent, root=HERE / 'runtime', finder=find_claude, popen=subprocess.Popen):
        self.cwd = Path(cwd)
        self.root = Path(root)
        self.finder = finder
        self.popen = popen
        self.lock = threading.Lock()

    def status(self, online=False):
        binary = self.finder()
        return {'available': bool(binary), 'busy': self.lock.locked(),
                'modes': ['read', 'image'] if online else list(MODES),
                'message': 'Sẵn sàng.' if binary else 'Chưa cài Claude Code trên Mac. Xem studio/README.md mục "Hỏi Claude".'}

    def folder(self, chat):
        if not isinstance(chat, str) or not CHAT.fullmatch(chat):
            raise ValueError('Mã cuộc trò chuyện không hợp lệ.')
        return self.root / 'claude' / chat

    def file_path(self, chat, name):
        out = (self.folder(chat) / 'out').resolve()
        target = (out / name).resolve()
        if not target.is_relative_to(out) or not target.is_file() or target.suffix.lower() not in FILE_TYPES:
            raise FileNotFoundError()
        return target

    def ask(self, data, online=False):
        """Yield events: {'type': 'start'|'text'|'tool'|'file'|'done'|'error', ...}. One question at a time."""
        prompt = data.get('prompt')
        session = data.get('session') or None
        mode = data.get('mode', 'read')
        attachments = data.get('attachments') or []
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Hãy nhập câu hỏi.')
        if len(prompt) > MAX_PROMPT:
            raise ValueError(f'Câu hỏi dài quá {MAX_PROMPT} ký tự.')
        if session is not None and (not isinstance(session, str) or not SESSION.fullmatch(session)):
            raise ValueError('Mã phiên Claude không hợp lệ.')
        if mode not in MODES:
            raise ValueError('Chế độ không hợp lệ.')
        if mode == 'full' and online:
            raise ValueError('Chế độ Toàn quyền chỉ dùng được khi mở Studio ngay trên Mac.')
        if not isinstance(attachments, list) or len(attachments) > MAX_ATTACH:
            raise ValueError(f'Chỉ gửi kèm tối đa {MAX_ATTACH} ảnh mỗi lần.')
        chat = data.get('chat') or uuid.uuid4().hex
        folder = self.folder(chat)
        images = [self._decode(item) for item in attachments]
        binary = self.finder()
        if not binary:
            raise RuntimeError(self.status()['message'])
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Claude đang trả lời câu hỏi khác, chờ chút rồi gửi lại.')
        try:
            (folder / 'out').mkdir(parents=True, exist_ok=True)
            saved = self._save(folder / 'in', images)
            self._log(chat, mode, online, prompt, saved)
        except OSError:
            self.lock.release()
            raise
        return self._run(binary, chat, folder, self._prompt(prompt, saved, folder / 'out'), session, mode)

    def _decode(self, item):
        if not isinstance(item, dict) or not isinstance(item.get('data'), str):
            raise ValueError('Ảnh đính kèm không hợp lệ.')
        try:
            raw = base64.b64decode(item['data'], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError('Ảnh đính kèm không đọc được.') from exc
        if len(raw) > MAX_ATTACH_BYTES:
            raise ValueError('Mỗi ảnh đính kèm tối đa 15 MB.')
        suffix = next((ext for magic, ext in IMAGE_MAGIC.items() if raw.startswith(magic)), None)
        if suffix is None:
            raise ValueError('Chỉ gửi kèm ảnh PNG, JPG, WebP hoặc GIF.')
        stem = re.sub(r'[^\w.-]+', '-', Path(str(item.get('name') or 'anh')).stem).strip('.-')[:60] or 'anh'
        return stem, suffix, raw

    def _save(self, folder, images):
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for stem, suffix, raw in images:
            target = folder / f'{time.strftime("%H%M%S")}-{uuid.uuid4().hex[:6]}-{stem}{suffix}'
            target.write_bytes(raw)
            paths.append(target)
        return paths

    def _prompt(self, prompt, saved, out):
        rel = lambda p: os.path.relpath(p, self.cwd)  # noqa: E731
        lines = [prompt]
        if saved:
            lines.append('\n[Ảnh người dùng gửi kèm, dùng công cụ Read để xem: ' + ', '.join(rel(p) for p in saved) + ']')
        lines.append(f'[Thư mục kết quả: {rel(out)}. File ảnh hoặc file lưu ở đây sẽ hiện trong khung chat.]')
        return '\n'.join(lines)

    def _log(self, chat, mode, online, prompt, saved):
        log = self.root / 'logs' / 'claude-chat.jsonl'
        log.parent.mkdir(parents=True, exist_ok=True)
        entry = {'time': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'chat': chat, 'mode': mode,
                 'online': online, 'prompt': prompt[:2000], 'attachments': [p.name for p in saved]}
        with log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def _outputs(self, out):
        return {p: p.stat().st_mtime for p in out.rglob('*') if p.is_file()} if out.is_dir() else {}

    def _new_files(self, chat, out, before):
        for path, mtime in sorted(self._outputs(out).items()):
            if before.get(path) == mtime or path.suffix.lower() not in FILE_TYPES:
                continue
            name = path.relative_to(out).as_posix()
            yield {'type': 'file', 'name': name, 'url': f'/claude-files/{chat}/{name}',
                   'image': path.suffix.lower() in INLINE, 'bytes': path.stat().st_size}

    def _run(self, binary, chat, folder, prompt, session, mode):
        out = folder / 'out'
        before = self._outputs(out)
        errors = tempfile.TemporaryFile('w+')
        try:
            proc = self.popen(command(binary, mode, out, session, self.cwd), cwd=self.cwd, env=environment(self.cwd, out),
                              text=True, bufsize=1, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:
            errors.close()
            self.lock.release()
            raise RuntimeError(f'Không chạy được Claude Code: {exc}') from exc
        timer = threading.Timer(TIMEOUT, proc.kill)
        timer.start()
        try:
            yield {'type': 'start', 'chat': chat, 'mode': mode}
            proc.stdin.write(prompt)
            proc.stdin.close()
            for event in self._events(proc, errors):
                if event['type'] in ('done', 'error'):
                    yield from self._new_files(chat, out, before)
                yield event
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            errors.close()
            self.lock.release()

    def _events(self, proc, errors):
        started, streamed, finished = time.time(), False, False
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get('type')
            if kind == 'stream_event':
                delta = event.get('event', {}).get('delta', {})
                if delta.get('type') == 'text_delta' and delta.get('text'):
                    streamed = True
                    yield {'type': 'text', 'text': delta['text']}
            elif kind == 'assistant':
                for block in event.get('message', {}).get('content', []):
                    if block.get('type') == 'tool_use':
                        target = block.get('input', {})
                        detail = (target.get('command') or target.get('file_path') or target.get('pattern')
                                  or target.get('path') or target.get('svg') or target.get('image') or '')
                        yield {'type': 'tool', 'name': block.get('name', ''), 'detail': str(detail)[:160]}
                    elif block.get('type') == 'text' and not streamed and block.get('text'):
                        yield {'type': 'text', 'text': block['text']}
                streamed = False
            elif kind == 'result':
                finished = True
                if event.get('is_error') or event.get('subtype') != 'success':
                    yield {'type': 'error', 'error': str(event.get('result') or event.get('subtype'))[:500],
                           'session': event.get('session_id')}
                else:
                    yield {'type': 'done', 'session': event.get('session_id'),
                           'seconds': round(time.time() - started, 1), 'turns': event.get('num_turns')}
        if not finished:
            proc.wait()
            errors.seek(0)
            err = errors.read().strip()
            yield {'type': 'error', 'error': (err or f'Claude Code dừng (mã {proc.returncode}).')[-500:]}
