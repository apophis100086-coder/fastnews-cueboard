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
            {'type':'assistant','message':{'content':[{'type':'thinking','thinking':'must not persist'}]}},
            {'type':'assistant','message':{'content':[{'id':'s1','type':'tool_use','name':'WebSearch',
                'input':{'query':'public news'}}]}},
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
                host_provider.run_host('workbuddy','public fixture',{},control=control,timeout=120)
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

    def test_explicit_model_uses_same_subscription_command_and_records_request(self):
        process=Mock(returncode=0)
        process.communicate.return_value=(json.dumps({'type':'result','structured_output':{'items':[]},'usage':{}}),'')
        process.poll.return_value=0
        commands=[]
        def start(args,**kwargs):
            commands.append(args)
            return process
        with patch.object(host_provider,'host_command',return_value=['fixture-host']), \
             patch.object(host_provider.subprocess,'run',return_value=Mock(returncode=0,stdout='ChatGPT',stderr='')), \
             patch.object(host_provider.subprocess,'Popen',side_effect=start), \
             patch.object(host_provider,'ProcessGroup'):
            result=host_provider.run_host('workbuddy','public fixture',{},model='verified-fixture-model',reasoning_effort='low')
        self.assertEqual(commands[0][commands[0].index('--model')+1],'verified-fixture-model')
        self.assertEqual(commands[0][commands[0].index('--effort')+1],'low')
        self.assertEqual(result.usage['model_requested'],'verified-fixture-model')
        self.assertFalse(result.usage['api_key_used'])
        self.assertEqual(result.usage['host_tasks'],1)

    def test_invalid_override_does_not_launch_another_host(self):
        with patch.object(host_provider.subprocess,'Popen') as launch:
            with self.assertRaises(ValueError):host_provider.run_host('workbuddy','public fixture',{},model='gpt-6-sol')
            with self.assertRaises(ValueError):host_provider.run_host('codex','public fixture',{},model='bad\nmodel')
        launch.assert_not_called()

    def test_writing_capacity_can_exceed_reader_default_but_includes_runtime_prefix(self):
        process=Mock(returncode=0)
        process.communicate.return_value=(json.dumps({'structured_output':{'items':[]},'usage':{}}),'')
        process.poll.return_value=0
        with patch.object(host_provider,'host_command',return_value=['fixture-host']), \
             patch.object(host_provider.subprocess,'Popen',return_value=process) as launch, \
             patch.object(host_provider,'ProcessGroup'):
            with self.assertRaisesRegex(ValueError,'输入上限'):
                host_provider.run_host('workbuddy','x'*37000,{})
            launch.assert_not_called()
            result=host_provider.run_host('workbuddy','x'*37000,{},input_character_limit=50000)
            self.assertEqual(result.payload,{'items':[]})
            with self.assertRaisesRegex(ValueError,'运行指令'):
                host_provider.run_host('workbuddy','x'*37000,{},input_character_limit=37000)
            self.assertEqual(launch.call_count,1)
            with self.assertRaises(ValueError):
                host_provider.run_host('workbuddy','x',{},input_character_limit=True)


if __name__ == '__main__':
    unittest.main()
