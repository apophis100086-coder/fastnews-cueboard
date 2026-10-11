"""Offline launcher boundary tests; no subprocesses or external HTTP requests."""
import io
import errno
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import open_dashboard as launcher
from fastnews.workspace import dashboard_address


class LauncherTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows virtual environment redirector')
    def test_process_identity_does_not_trust_virtualenv_launcher_path(self):
        actual = launcher.executable_image()
        self.assertTrue(Path(actual).is_file())
        with patch.object(launcher.sys, 'executable', 'C:/synthetic-missing/python.exe'):
            self.assertEqual(launcher.executable_image(), actual)

    def setUp(self):
        logging_patch = patch.object(launcher, 'configure_logging', return_value=Path('launcher.log'))
        logging_patch.start()
        self.addCleanup(logging_patch.stop)
        logger_patch = patch.object(launcher, 'LOGGER')
        logger_patch.start()
        self.addCleanup(logger_patch.stop)

    def health(self, value):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(value).encode())
        with patch.object(launcher, 'build_opener', return_value=opener) as build:
            status = launcher.check_service()
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], launcher.NoRedirect)
        return status

    def test_health_requires_original_live_database(self):
        self.assertEqual(self.health({'workspace_id': str(launcher.ROOT / 'data' / 'fastnews.db'),
                                      'is_fixture': False}), 'ready')
        for value in ({'workspace_id': str(launcher.ROOT / 'data' / 'fastnews.db'), 'is_fixture': True},
                      {'workspace_id': str(launcher.ROOT / 'other.db'), 'is_fixture': False},
                      {'workspace_id': None}, {'workspace_id': 5}, [], {}):
            with self.subTest(value=value):
                self.assertEqual(self.health(value), 'occupied_by_another_service')

    def test_health_failure_distinguishes_closed_and_occupied_port(self):
        with patch.object(launcher, 'build_opener', side_effect=OSError), \
                patch.object(launcher.socket, 'create_connection') as connect:
            connect.side_effect = OSError
            self.assertEqual(launcher.check_service(), 'stopped')
            connect.side_effect = None
            connect.return_value = io.BytesIO()
            self.assertEqual(launcher.check_service(), 'occupied_by_another_service')

    def test_health_rejects_redirects(self):
        self.assertIsNone(launcher.NoRedirect().redirect_request(
            None, None, 302, 'Found', {}, 'https://example.com/'))

    def test_ready_instance_is_reused(self):
        with patch.object(launcher, 'check_service', return_value='ready'), \
                patch.object(launcher, 'spawn_server') as spawn:
            launcher.ensure_running()
        spawn.assert_not_called()

    def test_stopped_instance_starts_and_waits(self):
        with patch.object(launcher, 'check_service', return_value='stopped'), \
                patch.object(launcher, 'spawn_server') as spawn, \
                patch.object(launcher, 'wait_until_ready', return_value=True) as wait:
            launcher.ensure_running()
        spawn.assert_called_once_with()
        wait.assert_called_once_with(child=spawn.return_value)

    def test_unrelated_port_owner_starts_separate_workspace_service(self):
        with patch.object(launcher, 'check_service', return_value='occupied_by_another_service'), \
                patch.object(launcher, 'spawn_server') as spawn, \
                patch.object(launcher, 'wait_until_ready', return_value=True) as wait:
            launcher.ensure_running()
        spawn.assert_called_once_with()
        wait.assert_called_once_with(child=spawn.return_value)

    def test_concurrent_start_can_become_ready(self):
        with patch.object(launcher, 'check_service', return_value='occupied_by_another_service'), \
                patch.object(launcher, 'spawn_server') as spawn, \
                patch.object(launcher, 'wait_until_ready', return_value=True):
            launcher.ensure_running()
        spawn.assert_called_once_with()

    def test_failed_start_is_reported(self):
        with patch.object(launcher, 'check_service', return_value='stopped'), \
                patch.object(launcher, 'spawn_server'), \
                patch.object(launcher, 'wait_until_ready', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '25'):
                launcher.ensure_running()

    def test_child_exits_zero_while_another_launch_finishes(self):
        child = Mock()
        child.poll.return_value = 0
        with patch.object(launcher, 'check_service', side_effect=['stopped', 'ready']), \
                patch.object(launcher.time, 'sleep'):
            self.assertTrue(launcher.wait_until_ready(child=child))

    def test_failed_child_ends_wait(self):
        child = Mock()
        child.poll.return_value = 2
        with patch.object(launcher, 'check_service', return_value='stopped'):
            self.assertFalse(launcher.wait_until_ready(child=child))

    def test_wait_is_bounded(self):
        with patch.object(launcher, 'check_service', return_value='stopped'), \
                patch.object(launcher.time, 'monotonic', side_effect=[0, 1, 26]), \
                patch.object(launcher.time, 'sleep'):
            self.assertFalse(launcher.wait_until_ready())

    def test_address_follows_only_own_valid_loopback_runtime_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/workspace.yaml').write_text('port: 8797\n', encoding='utf-8')
            (root / 'data').mkdir()
            record = root / 'data/dashboard-runtime.json'
            valid = dict(workspace_id=str(root / 'data/fastnews.db'), url='http://127.0.0.1:51000/')
            record.write_text(json.dumps(valid), encoding='utf-8')
            self.assertEqual(dashboard_address(root), (51000, valid['url']))
            invalid = [dict(valid, workspace_id=str(root / 'other.db')),
                       dict(valid, url='http://example.com:51000/'),
                       dict(valid, url='http://127.0.0.1:51000/?other=1'),
                       dict(valid, url='http://user@127.0.0.1:51000/'),
                       dict(valid, url='http://127.0.0.1:80/'), None, [], {}]
            for value in invalid:
                record.write_text(json.dumps(value), encoding='utf-8')
                self.assertEqual(dashboard_address(root), (8797, 'http://127.0.0.1:8797/'))
            record.write_text('{partial', encoding='utf-8')
            self.assertEqual(dashboard_address(root)[0], 8797)

    def test_wait_discovers_address_published_by_child(self):
        with patch.object(launcher, 'dashboard_address', return_value=(51001, 'http://127.0.0.1:51001/')), \
                patch.object(launcher, 'PORT', 8797), patch.object(launcher, 'URL', 'http://127.0.0.1:8797/'), \
                patch.object(launcher, 'check_service', return_value='ready'):
            self.assertTrue(launcher.wait_until_ready())
            self.assertEqual(launcher.URL, 'http://127.0.0.1:51001/')

    def test_busy_port_binding_uses_another_loopback_listener(self):
        with socket.socket() as old:
            old.bind(('127.0.0.1', 0))
            old.listen()
            original = old.getsockname()
            server = launcher.bind_workspace_server(Mock(), original[1])
            try:
                self.assertEqual(server.server_address[0], '127.0.0.1')
                self.assertNotEqual(server.server_port, original[1])
                self.assertEqual(old.getsockname(), original)
            finally:
                server.server_close()

    def test_non_collision_bind_error_is_not_hidden(self):
        with patch('fastnews.server.make_server', side_effect=OSError(errno.EINVAL, 'synthetic')) as bind:
            with self.assertRaises(OSError):
                launcher.bind_workspace_server(Mock(), 8797)
        bind.assert_called_once()

    def test_background_child_has_absolute_paths_and_no_parent_console(self):
        with patch.object(launcher.subprocess, 'Popen') as popen:
            launcher.spawn_server()
        args, options = popen.call_args
        self.assertEqual(args[0][1:], [str(launcher.ROOT / 'tools' / 'open_dashboard.py'), '--serve'])
        self.assertEqual(options['cwd'], str(launcher.ROOT))
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        self.assertEqual(options['stdout'], subprocess.DEVNULL)
        self.assertEqual(options['stderr'], subprocess.DEVNULL)
        if os.name == 'nt':
            self.assertTrue(options['creationflags'] & subprocess.DETACHED_PROCESS)
            self.assertEqual(Path(args[0][0]).name, 'pythonw.exe')
        else:
            self.assertTrue(options['start_new_session'])

    def test_lock_is_exclusive_and_released_even_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dashboard.lock'
            with self.assertRaises(RuntimeError):
                with launcher.InstanceLock(path) as first:
                    self.assertTrue(first)
                    with launcher.InstanceLock(path) as second:
                        self.assertFalse(second)
                    raise RuntimeError('synthetic failure')
            self.assertTrue(path.exists())  # A leftover file does not mean locked.
            with launcher.InstanceLock(path) as recovered:
                self.assertTrue(recovered)

    def test_second_server_does_not_initialize_database(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(launcher, 'ROOT', Path(directory)), \
                patch('fastnews.db.sync_registry') as sync, \
                patch.object(launcher, 'check_service') as check:
            with launcher.InstanceLock(Path(directory) / 'data' / 'dashboard.lock'):
                self.assertEqual(launcher.serve(), 0)
            sync.assert_not_called()
            check.assert_not_called()

    def test_check_only_never_starts_or_opens_browser(self):
        with patch.object(launcher, 'check_service', return_value='stopped'), \
                patch.object(launcher, 'ensure_running') as ensure, \
                patch.object(launcher.webbrowser, 'open') as browser, \
                patch.object(sys, 'stdout', io.StringIO()) as output:
            self.assertEqual(launcher.main(['--check']), 1)
            self.assertEqual(json.loads(output.getvalue())['status'], 'stopped')
        ensure.assert_not_called()
        browser.assert_not_called()
        launcher.configure_logging.assert_not_called()

    def test_startup_does_not_open_browser(self):
        with patch.object(launcher, 'serve', return_value=0) as serve, \
                patch.object(launcher.webbrowser, 'open') as browser:
            self.assertEqual(launcher.main(['--serve']), 0)
        serve.assert_called_once_with()
        browser.assert_not_called()

    def test_manual_open_and_no_browser_recovery(self):
        with patch.object(launcher, 'ensure_running') as ensure, \
                patch.object(launcher.webbrowser, 'open') as browser, \
                patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(launcher.main([]), 0)
            ensure.assert_called_once_with()
            browser.assert_called_once_with(launcher.URL)
            browser.reset_mock()
            self.assertEqual(launcher.main(['--no-browser']), 0)
            browser.assert_not_called()

    def test_startup_error_is_quiet_and_secrets_are_not_logged(self):
        with patch.object(launcher, 'serve', side_effect=ValueError('synthetic-secret')), \
                patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None), \
                patch.object(launcher.webbrowser, 'open') as browser:
            self.assertEqual(launcher.main(['--serve']), 2)
        browser.assert_not_called()
        self.assertNotIn('synthetic-secret', str(launcher.LOGGER.mock_calls))


if __name__ == '__main__':
    unittest.main()
