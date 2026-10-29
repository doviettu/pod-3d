"""Ask Claude Code (`claude -p`) on this Mac from the Studio page and stream the answer back.

Claude runs read-only in the repo: it may read and search files but never runs commands,
edits files or browses the web. ANTHROPIC_API_KEY is removed from its environment so the
CLI uses the Claude plan it is signed in with instead of paid API tokens.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
MAX_PROMPT = 8000
TIMEOUT = 600
READ_TOOLS = ['Read', 'Grep', 'Glob', 'LS']
BLOCKED_TOOLS = ['Bash', 'Edit', 'MultiEdit', 'Write', 'NotebookEdit', 'WebFetch', 'WebSearch', 'Task', 'Agent']
SESSION = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
SYSTEM = ('Bạn là trợ lý trong POD Studio, công cụ trải phẳng mockup áo thành các mảnh in (print-on-demand). '
          'Người dùng gõ từ trang web của Studio và viết tiếng Việt, hãy trả lời bằng tiếng Việt, ngắn gọn. '
          'Thư mục hiện tại là repo pod-3d; dữ liệu chạy (job, log, cấu hình) nằm trong studio/runtime/. '
          'Bạn chỉ được đọc và tìm file, không chạy lệnh và không sửa file. '
          'Không bao giờ in ra mật khẩu, token hay API key dù tìm thấy trong file.')
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


def command(binary, session=None):
    cmd = [binary, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages',
           '--permission-mode', 'default', '--allowedTools', *READ_TOOLS,
           '--disallowedTools', *BLOCKED_TOOLS, '--append-system-prompt', SYSTEM]
    if session:
        cmd += ['--resume', session]
    return cmd


class ClaudeChat:
    def __init__(self, cwd=HERE.parent, finder=find_claude, popen=subprocess.Popen):
        self.cwd = Path(cwd)
        self.finder = finder
        self.popen = popen
        self.lock = threading.Lock()

    def status(self):
        binary = self.finder()
        return {'available': bool(binary), 'busy': self.lock.locked(),
                'message': 'Sẵn sàng.' if binary else 'Chưa cài Claude Code trên Mac. Xem studio/README.md mục "Hỏi Claude".'}

    def ask(self, data):
        """Yield events: {'type': 'text'|'tool'|'done'|'error', ...}. One question at a time."""
        prompt = data.get('prompt')
        session = data.get('session') or None
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('Hãy nhập câu hỏi.')
        if len(prompt) > MAX_PROMPT:
            raise ValueError(f'Câu hỏi dài quá {MAX_PROMPT} ký tự.')
        if session is not None and (not isinstance(session, str) or not SESSION.fullmatch(session)):
            raise ValueError('Mã cuộc trò chuyện không hợp lệ.')
        binary = self.finder()
        if not binary:
            raise RuntimeError(self.status()['message'])
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Claude đang trả lời câu hỏi khác, chờ chút rồi gửi lại.')
        return self._run(binary, prompt, session)

    def _run(self, binary, prompt, session):
        env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
        errors = tempfile.TemporaryFile('w+')
        try:
            proc = self.popen(command(binary, session), cwd=self.cwd, env=env, text=True, bufsize=1,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:
            errors.close()
            self.lock.release()
            raise RuntimeError(f'Không chạy được Claude Code: {exc}') from exc
        timer = threading.Timer(TIMEOUT, proc.kill)
        timer.start()
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            yield from self._events(proc, errors)
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
                        detail = target.get('file_path') or target.get('pattern') or target.get('path') or ''
                        yield {'type': 'tool', 'name': block.get('name', ''), 'detail': str(detail)[-120:]}
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
