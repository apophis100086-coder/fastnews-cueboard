"""Start the local dashboard independently of a terminal, or reuse it."""
from contextlib import closing
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8767/'
LOGGER = logging.getLogger('fastnews.launcher')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_service():
    # A local check must never follow a redirect or use a configured proxy.
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(URL + 'api/state', timeout=3) as response:
            state = json.loads(response.read(2_000_000).decode('utf-8'))
        workspace = state.get('workspace_id') if isinstance(state, dict) else None
        if not isinstance(workspace, str) or not workspace:
            return 'occupied_by_another_service'
        actual = os.path.normcase(os.path.abspath(workspace))
        expected = os.path.normcase(str(ROOT / 'data' / 'fastnews.db'))
        if actual == expected and state.get('is_fixture') is False:
            return 'ready'
        return 'occupied_by_another_service'
    except (OSError, ValueError):
        try:
            with socket.create_connection(('127.0.0.1', 8767), timeout=1):
                return 'occupied_by_another_service'
        except OSError:
            return 'stopped'


class InstanceLock:
    """OS-owned lock: released on exit/crash; a stale file cannot block restart."""
    def __init__(self, path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        if self.file.seek(0, 2) == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            return False
        return True

    def __exit__(self, *args):
        if self.file is not None:
            self.file.close()


def configure_logging(serving=False):
    directory = ROOT / 'data' / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('dashboard.log' if serving else 'launcher.log')
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    return path


def spawn_server():
    executable = Path(sys.executable)
    options = {}
    if os.name == 'nt':
        windowless = executable.with_name('pythonw.exe')
        if windowless.is_file():
            executable = windowless
        options['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    return subprocess.Popen(
        [str(executable), str(ROOT / 'tools' / 'open_dashboard.py'), '--serve'],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, close_fds=True, **options)


def wait_until_ready(timeout=25, child=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_service() == 'ready':
            return True
        # Exit 0 can mean a concurrent launcher already owns the instance lock.
        if child is not None and child.poll() not in (None, 0):
            return False
        time.sleep(0.25)
    return False


def ensure_running():
    status = check_service()
    if status == 'ready':
        return
    if status != 'stopped':
        if wait_until_ready(timeout=4):
            return
        raise RuntimeError('8767 端口已被其他程序占用，或看板未能响应。请查看启动日志。')
    child = spawn_server()
    if not wait_until_ready(child=child):
        raise RuntimeError('看板未能在 25 秒内启动。请查看 data/logs/dashboard.log。')


def serve():
    with InstanceLock(ROOT / 'data' / 'dashboard.lock') as acquired:
        if not acquired:
            LOGGER.info('Another dashboard instance is running or starting.')
            return 0
        status = check_service()
        if status == 'ready':
            LOGGER.info('Dashboard already ready; reused existing server.')
            return 0
        if status != 'stopped':
            raise RuntimeError('8767 端口已被其他程序占用，未启动或停止任何程序。')
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        from fastnews.config import load_config
        from fastnews.db import sync_registry
        from fastnews.source_catalog import configured_view
        from fastnews.server import LocalApplication, make_server
        app = LocalApplication(ROOT / 'data' / 'fastnews.db', load_config(),
                               output_dir=ROOT / 'exports')
        with closing(app.connection()) as conn:
            sync_registry(conn, configured_view(conn, app.config))
        server = make_server(app, port=8767)
        runtime_path = ROOT / 'data' / 'dashboard-runtime.json'
        try:
            runtime_path.write_text(json.dumps({
                'pid': os.getpid(), 'url': URL, 'workspace_id': app.database,
                'python': sys.executable, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            }, ensure_ascii=False, indent=2), encoding='utf-8')
            LOGGER.info('Dashboard ready: pid=%s url=%s', os.getpid(), URL)
            # Startup only serves saved data. No collection, LLM, or browser action.
            server.serve_forever()
        except KeyboardInterrupt:
            LOGGER.info('Dashboard stopped by user.')
        finally:
            server.server_close()
            runtime_path.unlink(missing_ok=True)
    return 0


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='check only; no start or browser')
    group.add_argument('--serve', action='store_true', help='run the server; no browser')
    parser.add_argument('--no-browser', action='store_true', help='ensure running without opening a browser')
    args = parser.parse_args(argv)
    if args.check:
        status = check_service()
        print(json.dumps({'status': status, 'url': URL, 'proxy_used': False}))
        return 0 if status == 'ready' else 1
    log_path = ROOT / 'data' / 'logs'
    try:
        log_path = configure_logging(serving=args.serve)
        if args.serve:
            return serve()
        ensure_running()
        LOGGER.info('Dashboard available: %s', URL)
        if not args.no_browser:
            webbrowser.open(URL)
        if sys.stdout is not None:
            print('看板已在后台运行：' + URL)
            print('可以关闭此窗口；下次双击“一键启动”即可重新打开。')
        return 0
    except Exception as exc:
        # Do not log configuration contents, source material, or secrets.
        LOGGER.error('Dashboard launch failed: %s', type(exc).__name__)
        detail = str(exc) if isinstance(exc, RuntimeError) else f'启动失败（{type(exc).__name__}）。'
        message = f'{detail}\n启动日志：{log_path}'
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        elif os.name == 'nt' and not args.serve and not args.no_browser:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, '华泰柏瑞快讯看板启动失败', 0x10)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
