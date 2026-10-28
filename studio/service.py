"""Run POD Studio as macOS background services (launchd LaunchAgents).

- com.podstudio.server: the local app (studio.py) on 127.0.0.1:8765.
- com.podstudio.online: the HTTPS tunnel + login gateway (online.py), optional.
Both start when you log in and launchd starts them again within ~30 s if they stop or crash.
A restarted tunnel gets a new link, which mailer.py emails when mail is set up.

    .venv/bin/python studio/service.py install [--no-online]
    .venv/bin/python studio/service.py restart      # after `git pull`
    .venv/bin/python studio/service.py status
    .venv/bin/python studio/service.py uninstall
"""
import os
from pathlib import Path
import plistlib
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PYTHON = REPO / '.venv' / 'bin' / 'python'
AGENTS = Path.home() / 'Library' / 'LaunchAgents'
LOGS = HERE / 'runtime' / 'logs'
SERVICES = {'server': ('com.podstudio.server', 'studio.py'), 'online': ('com.podstudio.online', 'online.py')}
PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'


def plist(name):
    label, script = SERVICES[name]
    return {
        'Label': label,
        'ProgramArguments': [str(PYTHON), str(HERE / script)],
        'WorkingDirectory': str(HERE),
        'RunAtLoad': True,
        'KeepAlive': True,             # start again whenever it exits
        'ThrottleInterval': 30,        # but not more often than every 30 s
        'ProcessType': 'Interactive',  # do not throttle the image work in the background
        'EnvironmentVariables': {'PATH': PATH, 'PYTHONUNBUFFERED': '1'},
        'StandardOutPath': str(LOGS / f'{name}.log'),
        'StandardErrorPath': str(LOGS / f'{name}.log'),
    }


def domain():
    return f'gui/{os.getuid()}'


def launchctl(*args, check=False):
    return subprocess.run(['launchctl', *args], capture_output=True, text=True, check=check)


def loaded(label):
    return launchctl('print', f'{domain()}/{label}').returncode == 0


def install(names):
    if not PYTHON.is_file():
        raise SystemExit(f'Không thấy {PYTHON}. Hãy cài môi trường .venv của repo trước.')
    AGENTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    for name in names:
        label = SERVICES[name][0]
        path = AGENTS / f'{label}.plist'
        if loaded(label):
            launchctl('bootout', f'{domain()}/{label}')
        with path.open('wb') as stream:
            plistlib.dump(plist(name), stream)
        result = launchctl('bootstrap', domain(), str(path))
        if result.returncode != 0:
            raise SystemExit(f'Không bật được {label}: {result.stderr.strip()}')
        print(f'Đã bật {label} (log: {LOGS / (name + ".log")})')


def uninstall():
    for name, (label, _) in SERVICES.items():
        if loaded(label):
            launchctl('bootout', f'{domain()}/{label}')
        (AGENTS / f'{label}.plist').unlink(missing_ok=True)
        print(f'Đã tắt {label}')


def restart():
    for name, (label, _) in SERVICES.items():
        if loaded(label):
            launchctl('kickstart', '-k', f'{domain()}/{label}')
            print(f'Đã khởi động lại {label}')


def status():
    for name, (label, _) in SERVICES.items():
        if not loaded(label):
            print(f'{label}: chưa bật')
            continue
        info = launchctl('print', f'{domain()}/{label}').stdout
        state = next((l.split('=', 1)[1].strip() for l in info.splitlines() if l.strip().startswith('state =')), '?')
        print(f'{label}: {state}')
    online = HERE / 'runtime' / 'online' / 'status.json'
    if online.is_file():
        print('Online:', online.read_text().strip())


def main(argv):
    command = argv[1] if len(argv) > 1 else ''
    if command == 'install':
        install(['server'] if '--no-online' in argv else ['server', 'online'])
    elif command == 'uninstall':
        uninstall()
    elif command == 'restart':
        restart()
    elif command == 'status':
        status()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
