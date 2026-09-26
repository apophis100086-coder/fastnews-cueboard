"""Host launch isolation, including Windows packaged-app directory redirection."""
from contextlib import nullcontext
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastnews import host_provider
from fastnews.task_control import TaskControl, TaskStopped


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

    def test_cancel_retains_public_actions_and_sends_earlier_model_deadline(self):
        control=TaskControl(480)
        process=Mock(returncode=0)
        telemetry='\n'.join(json.dumps(e) for e in [
            {'type':'item.completed','item':{'type':'reasoning','text':'must not persist'}},
            {'type':'item.completed','item':{'id':'s1','type':'web_search',
                'action':{'type':'search','query':'public news'}}},
            {'type':'error','message':'private diagnostic must not persist'}])
        calls=[]
        def interrupted(*args,**kwargs):
            calls.append(1)
            if len(calls)==1:
                control.cancelled.set()
                # Windows returns no partial output until reader threads drain.
                raise subprocess.TimeoutExpired('fixture',.5)
            return telemetry,''
        process.communicate.side_effect=interrupted
        process.poll.return_value=None
        group=Mock()
        with patch.object(host_provider,'host_command',return_value=['fixture-host']), \
             patch.object(host_provider.subprocess,'run',return_value=Mock(returncode=0,stdout='ChatGPT',stderr='')), \
             patch.object(host_provider.subprocess,'Popen',return_value=process), \
             patch.object(host_provider,'ProcessGroup',return_value=group):
            with self.assertRaises(TaskStopped) as caught:
                host_provider.run_host('codex','public fixture',{},control=control,timeout=120)
        sent=process.communicate.call_args_list[0].args[0]
        self.assertIn('120 秒',sent)
        import re
        deadline=datetime.fromisoformat(re.search(r'必须在 (\S+) 前',sent).group(1))
        self.assertLess(deadline,datetime.fromisoformat(control.deadline_at))
        self.assertLess((deadline-datetime.now(timezone.utc)).total_seconds(),121)
        trace=caught.exception.host_trace
        self.assertEqual(trace['actions'][0]['query'],'public news')
        self.assertTrue(trace['incomplete'])
        self.assertFalse(trace['turn_completed'])
        self.assertNotIn('must not persist',json.dumps(trace))
        group.close.assert_called_once()
        process.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
