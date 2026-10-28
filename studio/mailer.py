"""Email the online link and login when the HTTPS tunnel gets a new URL (stdlib SMTP only).

Settings (never in the repo): ~/.config/pod-studio/mail.json, mode 600:
    {"smtp_host": "smtp.gmail.com", "port": 465, "user": "you@gmail.com",
     "app_password": "<16-letter Google app password>", "to": ["you@gmail.com"], "include_password": true}
A Gmail app password needs 2-Step Verification: https://myaccount.google.com/apppasswords

Set up or test from Terminal:
    .venv/bin/python studio/mailer.py setup
    .venv/bin/python studio/mailer.py test
"""
from datetime import datetime
from email.message import EmailMessage
import getpass
import json
import os
from pathlib import Path
import re
import smtplib
import socket
import ssl
import sys

DEFAULT_CONFIG = Path.home() / '.config/pod-studio/mail.json'
EMAIL = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')


class MailError(RuntimeError):
    pass


def config_path():
    return Path(os.environ.get('POD_MAIL_CONFIG') or DEFAULT_CONFIG)


def load(path=None):
    path = Path(path or config_path())
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise MailError(f'Không đọc được {path}.') from None
    for key in ('smtp_host', 'user', 'app_password', 'to'):
        if not data.get(key):
            raise MailError(f'{path} thiếu "{key}".')
    if isinstance(data['to'], str):
        data['to'] = [data['to']]
    if not all(EMAIL.fullmatch(a) for a in data['to'] + [data['user']]):
        raise MailError('Địa chỉ email không hợp lệ.')
    data.setdefault('port', 465)
    data.setdefault('include_password', True)
    return data


def save(data, path=None):
    path = Path(path or config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    temp.replace(path)


def online_message(cfg, url, password, reason=''):
    msg = EmailMessage()
    msg['Subject'] = 'POD Studio online: ' + url.removeprefix('https://')
    msg['From'] = cfg['user']
    msg['To'] = ', '.join(cfg['to'])
    lines = [f'POD Studio đã mở đường truy cập online lúc {datetime.now():%H:%M %d/%m/%Y}.', '',
             f'Link: {url}', 'Tài khoản: studio']
    lines.append(f'Mật khẩu: {password}' if cfg.get('include_password', True)
                 else 'Mật khẩu: xem runtime/online/access.json trên Mac.')
    if reason:
        lines += ['', reason]
    lines += ['', 'Link đổi mỗi lần đường truyền khởi động lại; khi đó sẽ có mail mới.',
              'Ai có mật khẩu này đều dùng được toàn bộ tool, kể cả lệnh AI có phí: đừng chuyển tiếp mail này.']
    msg.set_content('\n'.join(lines))
    return msg


def send(cfg, msg, smtp=None, timeout=30):
    """smtp: injectable factory for tests; default is SMTP over SSL (465) or STARTTLS (587)."""
    context = ssl.create_default_context()
    try:
        if smtp:
            server = smtp(cfg['smtp_host'], cfg['port'])
        elif int(cfg['port']) == 465:
            server = smtplib.SMTP_SSL(cfg['smtp_host'], 465, timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(cfg['smtp_host'], int(cfg['port']), timeout=timeout)
            server.starttls(context=context)
        with server:
            server.login(cfg['user'], cfg['app_password'].replace(' ', ''))
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise MailError('Gmail từ chối đăng nhập: kiểm tra email và mật khẩu ứng dụng (không phải mật khẩu Gmail).') from None
    except (smtplib.SMTPException, OSError, socket.timeout) as exc:
        raise MailError(f'Không gửi được mail: {exc}') from None


def notify_online(url, password, state_path, *, config=None, smtp=None, log=print):
    """Send the link once per new URL. Returns True when a mail went out."""
    try:
        cfg = load(config)
    except MailError as exc:
        log(str(exc))
        return False
    if not cfg:
        return False
    state_path = Path(state_path)
    try:
        last = json.loads(state_path.read_text()).get('url')
    except (OSError, ValueError):
        last = None
    if last == url:
        return False
    try:
        send(cfg, online_message(cfg, url, password), smtp)
    except MailError as exc:
        log(str(exc))
        return False
    state_path.write_text(json.dumps({'url': url, 'sent_at': datetime.now().isoformat(timespec='seconds')}))
    log('Đã gửi link online tới ' + ', '.join(cfg['to']))
    return True


def setup():
    print('Cài gửi mail link POD Studio online (Gmail).')
    print('Cần mật khẩu ứng dụng Google: https://myaccount.google.com/apppasswords (phải bật xác minh 2 bước).')
    user = input('Gmail gửi đi: ').strip()
    app_password = getpass.getpass('Mật khẩu ứng dụng (16 chữ, không hiện khi gõ): ').strip()
    to = input(f'Gửi tới (cách nhau bởi dấu phẩy, Enter = {user}): ').strip() or user
    include = input('Ghi mật khẩu đăng nhập POD Studio trong mail? [Y/n]: ').strip().lower() not in ('n', 'no', 'k')
    data = {'smtp_host': 'smtp.gmail.com', 'port': 465, 'user': user, 'app_password': app_password,
            'to': [a.strip() for a in to.split(',') if a.strip()], 'include_password': include}
    save(data)
    cfg = load()
    send(cfg, online_message(cfg, 'https://vi-du.trycloudflare.com', '(mật khẩu thật sẽ có ở mail sau)',
                             'Đây là mail thử: cài đặt gửi mail đã chạy.'))
    print(f'Đã lưu {config_path()} và gửi mail thử tới {", ".join(cfg["to"])}.')


def main(argv):
    command = argv[1] if len(argv) > 1 else ''
    try:
        if command == 'setup':
            setup()
        elif command == 'test':
            cfg = load()
            if not cfg:
                raise MailError('Chưa cài: chạy "mailer.py setup" trước.')
            send(cfg, online_message(cfg, 'https://vi-du.trycloudflare.com', '(ẩn)', 'Đây là mail thử.'))
            print('Đã gửi mail thử.')
        else:
            print(__doc__)
            return 2
    except MailError as exc:
        print(exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
