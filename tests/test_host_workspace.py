"""Host launch isolation, including Windows packaged-app directory redirection."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastnews import host_provider


class HostWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.os_temp = self.root / 'os-temp'
        self.os_temp.mkdir()

    def test_launch_uses_os_temp_even_when_local_app_data_is_redirected(self):
        launched = []
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'structured_output': {'items': []}, 'usage': {}}), '')
        process.poll.return_value = 0
        def start(args, **kwargs):
            work = Path(kwargs['cwd']).resolve()
            launched.append(work)
            self.assertTrue(work.is_relative_to(self.os_temp))
            self.assertTrue((work / 'schema.json').is_file())
            self.assertIn('--strict-mcp-config', args)
            return process
        with patch.dict('os.environ', {'LOCALAPPDATA': str(self.root / 'virtual-local-appdata')}), \
             patch.object(host_provider.tempfile, 'gettempdir', return_value=str(self.os_temp)), \
             patch.object(host_provider, 'host_command', return_value=['fixture-host']), \
             patch.object(host_provider.subprocess, 'Popen', side_effect=start), \
             patch.object(host_provider, 'ProcessGroup'):
            result = host_provider.run_host('workbuddy', 'synthetic public input', {'type': 'object'}, web_search=False)
        self.assertEqual(result.payload, {'items': []})
        self.assertEqual(len(launched), 1)
        self.assertFalse(launched[0].exists())

    def test_resolved_workspace_outside_expected_root_is_still_rejected(self):
        with patch.object(host_provider.tempfile, 'gettempdir', return_value=str(self.os_temp)), \
             patch.object(host_provider.tempfile, 'TemporaryDirectory', return_value=nullcontext(str(self.root / 'outside'))), \
             patch.object(host_provider, 'host_command', return_value=['fixture-host']), \
             patch.object(host_provider.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'Unsafe host work directory'):
                host_provider.run_host('workbuddy', 'synthetic input', {'type': 'object'}, web_search=False)
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
