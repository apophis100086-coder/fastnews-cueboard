"""Official local CLI adapters. No extracted login tokens or model API keys."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .metadata import normalize_url
from .strict_json import loads
from .reader_limits import reader_limits


class ProcessGroup:
    """Contain a CLI's child processes so timeouts cannot leave workers running."""
    def __init__(self, process):
        self.handle = None
        if os.name!='nt':
            return
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),
                ('flags',wintypes.DWORD),('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),
                ('active_limit',wintypes.DWORD),('affinity',ctypes.c_size_t),
                ('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Limits(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),
                ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        api.CreateJobObjectW.restype=wintypes.HANDLE
        api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=api.CreateJobObjectW(None,None)
        limits=Limits()
        limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not handle or not api.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(handle,int(process._handle)):
            if handle:
                api.CloseHandle(handle)
            process.kill()
            process.wait()
            raise HostUnavailable('无法建立宿主任务的进程隔离，已停止启动')
        self.handle,self.api=handle,api

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle=None


class HostUnavailable(ValueError):
    pass


def clean_environment():
    allowed = {'PATH','PATHEXT','SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP','LOCALAPPDATA',
               'APPDATA','USERPROFILE','HOMEDRIVE','HOMEPATH','HOME','CODEX_HOME',
               'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','LANG','TERM'}
    return {k:v for k,v in os.environ.items() if k.upper() in allowed}


def host_command(host):
    local = Path(os.environ.get('LOCALAPPDATA',''))
    if host=='codex':
        found = shutil.which('codex')
        candidates = list((local/'OpenAI'/'Codex'/'bin').glob('*/codex.exe'))
        if found:
            return [found]
        if candidates:
            return [str(max(candidates,key=lambda p:p.stat().st_mtime))]
    elif host=='workbuddy':
        cli = local/'Programs'/'WorkBuddy'/'resources'/'app.asar.unpacked'/'cli'/'dist'/'codebuddy.js'
        node = shutil.which('node') or (str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'nodejs'/'node.exe'))
        if cli.is_file() and Path(node).is_file():
            return [node,str(cli)]
    return None


def host_status():
    return {'codex':{'installed':bool(host_command('codex')),'label':'Codex / ChatGPT 订阅',
                    'note':'运行前核对 ChatGPT 登录；与其他 Codex 任务共享额度'},
            'workbuddy':{'installed':bool(host_command('workbuddy')),'label':'WorkBuddy',
                        'note':'使用官方命令行登录状态；首次运行需验证当前账号授权'},
            'limits':dict(reader_limits(),max_parallel_tasks=1),
            'billing_note':'使用所选工具的现有额度，不配置模型 API Key；实际额度由对应工具管理。'}


@dataclass
class HostResult:
    payload: dict
    usage: dict
    trace: dict


def _urls(value):
    result = set()
    if isinstance(value,dict):
        for key,item in value.items():
            if key in {'url','urls','ref_id'} and isinstance(item,str) and item.startswith(('http://','https://')):
                try:
                    result.add(normalize_url(item))
                except ValueError:
                    pass
            result |= _urls(item)
    elif isinstance(value,list):
        for item in value:
            if isinstance(item,str) and item.startswith(('http://','https://')):
                try:
                    result.add(normalize_url(item))
                except ValueError:
                    pass
            result |= _urls(item)
    return result


def parse_codex_output(output, final):
    usage, searches, urls, actions = {}, [], set(), []
    completed, pending_error, recovered_errors = False, False, 0
    for line in output.splitlines():
        try:
            # CLI telemetry can contain repeated flattened fields in some
            # releases. The model's final report still uses strict JSON below.
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event,dict):
            continue
        if event.get('type')=='turn.completed':
            usage = event.get('usage',{})
            completed, pending_error = True, False
        item = event.get('item',{})
        if event.get('type')=='item.completed' and item.get('type')=='web_search':
            action = item.get('action') or {}
            searches.append(action.get('type','search'))
            action_urls = sorted(_urls(action))
            if action.get('type') in {'open_page', 'open', 'find_in_page', 'find'}:
                urls.update(action_urls)
            actions.append({'action_id': item.get('id') or 'web_action_'+str(len(actions)+1),
                'type': action.get('type','search'), 'status':item.get('status','completed'),
                'urls':action_urls, 'query':action.get('query') or item.get('query'),
                'queries':action.get('queries') if isinstance(action.get('queries'),list) else [],
                'ref_id':action.get('ref_id'),
                'result_content_available':False})
        if event.get('type')=='error':
            # The CLI can report reconnects and still complete this same turn.
            # Only a later explicit completion can clear an intermediate error.
            pending_error=True
            recovered_errors+=1
        if event.get('type')=='turn.failed':
            raise HostUnavailable('Codex 任务未完成；请在 Codex 中检查登录、网络或剩余额度')
    if pending_error:
        raise HostUnavailable('Codex 任务未完成；请在 Codex 中检查登录、网络或剩余额度')
    return HostResult(loads(final),usage,{'web_actions':len(searches),'visited_urls':sorted(urls),
        'actions':actions, 'tool_excerpts':[], 'turn_completed':completed,
        'recovered_error_events':recovered_errors,
        'content_observability':'CLI action records do not establish returned page text or successful retrieval'})


def run_host(host, prompt, schema, *, web_search=True, timeout=None, progress=None, control=None):
    limits=reader_limits()
    timeout=limits['timeout_seconds'] if timeout is None else min(timeout,limits['timeout_seconds'])
    if control:
        control.check()
    command = host_command(host)
    if command is None:
        raise HostUnavailable('未找到所选宿主的命令行入口，请先安装并登录对应软件')
    if not isinstance(prompt,str) or len(prompt)>36000:
        raise ValueError('本次资料超过宿主任务输入上限，请减少来源或缩短时间范围')
    env = clean_environment()
    flags = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    if host=='codex':
        try:
            auth = subprocess.run(command+['login','status'],capture_output=True,text=True,encoding='utf-8',
                                  errors='replace',timeout=12,env=env,**flags)
        except (OSError,subprocess.TimeoutExpired):
            raise HostUnavailable('无法检查 Codex 登录状态，请打开 Codex 后重试') from None
        if auth.returncode or 'ChatGPT' not in auth.stdout+auth.stderr:
            raise HostUnavailable('请先以 ChatGPT 账号登录 Codex；本功能不使用 API Key 计费模式')
    if control:
        timeout=min(timeout,control.host_timeout())
    # Use the OS temporary directory for ephemeral runs. Packaged Windows apps
    # can redirect new LOCALAPPDATA children into a package's LocalCache while
    # resolving an existing parent to its unredirected path.
    cache_root = Path(tempfile.gettempdir()).resolve()/'FastnewsCueboard'/'host-runs'
    cache_root.mkdir(parents=True,exist_ok=True)
    # An isolated empty work directory avoids inheriting project-editing tasks.
    with tempfile.TemporaryDirectory(prefix='reading-',dir=cache_root,ignore_cleanup_errors=True) as folder:
        root = Path(folder)
        if not root.resolve().is_relative_to(cache_root.resolve()):
            raise ValueError('Unsafe host work directory')
        schema_path, output_path = root/'schema.json', root/'result.json'
        schema_path.write_text(json.dumps(schema,ensure_ascii=False),encoding='utf-8')
        if host=='codex':
            args = command+['exec','--ignore-user-config','--ephemeral','--skip-git-repo-check',
                '--sandbox','read-only','--disable','shell_tool','--disable','multi_agent',
                '--disable','plugins','-c','web_search="live"' if web_search else 'web_search="disabled"',
                '--json','-C',str(root),'--output-schema',str(schema_path),'-o',str(output_path),'-']
        else:
            args = command+['--print','--output-format','json','--permission-mode','dontAsk',
                '--tools','WebSearch,WebFetch' if web_search else '',
                '--allowedTools','WebSearch,WebFetch' if web_search else '',
                '--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--setting-sources','',
                '--settings','{"disableAllHooks":true}','--no-session-persistence','--max-turns','8',
                '--json-schema',json.dumps(schema,ensure_ascii=False)]
        started = time.monotonic()
        if progress:
            progress({'stage':'host','message':f'正在使用 {host} 整理资料'+('并补充联网检索' if web_search else '')})
        try:
            # stdin carries public material; no user text is interpreted by a shell.
            process = subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                text=True,encoding='utf-8',errors='replace',cwd=root,env=env,**flags)
            group = ProcessGroup(process)
            try:
                if control:
                    from .task_control import TaskStopped
                    end=min(time.monotonic()+timeout,control.deadline-30)
                    first=True
                    while True:
                        control.check()
                        remaining=end-time.monotonic()
                        if remaining<=0:
                            raise TaskStopped('timed_out')
                        try:
                            output,diagnostic=process.communicate(prompt if first else None,timeout=min(0.5,remaining))
                            break
                        except subprocess.TimeoutExpired:
                            first=False
                else:
                    output, diagnostic = process.communicate(prompt,timeout=timeout)
                result = subprocess.CompletedProcess(args,process.returncode,output,diagnostic)
            finally:
                group.close()
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            raise HostUnavailable(f'宿主任务达到 {timeout} 秒上限，已停止；请缩小来源范围后重试') from None
        except OSError:
            raise HostUnavailable('无法启动模型任务，请检查宿主软件是否仍在安装位置') from None
        if result.returncode:
            raise HostUnavailable('宿主未成功完成任务，请在对应软件检查登录、权限、网络和额度；未切换为 API 扣费')
        if len(result.stdout)>4_000_000:
            raise HostUnavailable('宿主返回内容超过限制，请缩小任务范围')
        if host=='codex':
            if not output_path.is_file() or output_path.stat().st_size>200_000:
                raise HostUnavailable('Codex 没有返回有效的结构化报告')
            parsed = parse_codex_output(result.stdout,output_path.read_text(encoding='utf-8'))
            if not parsed.trace.get('turn_completed'):
                raise HostUnavailable('Codex 未确认本轮完成，已有结果已保留')
        else:
            try:
                outer = loads(result.stdout)
                if outer.get('is_error'):
                    raise ValueError()
                payload = outer.get('structured_output') or outer.get('result')
                if isinstance(payload,str):
                    payload = loads(payload)
                if not isinstance(payload,dict):
                    raise ValueError()
                parsed = HostResult(payload,outer.get('usage',{}),{'web_actions':None,'visited_urls':[]})
            except (ValueError,AttributeError):
                raise HostUnavailable('WorkBuddy 尚未返回可用报告，请先确认命令行登录与订阅授权') from None
        parsed.usage = {'host':host,'billing':'host_subscription','api_key_used':False,
                        'input_tokens':parsed.usage.get('input_tokens'),
                        'output_tokens':parsed.usage.get('output_tokens'),
                        'cached_input_tokens':parsed.usage.get('cached_input_tokens'),
                        'elapsed_seconds':round(time.monotonic()-started,2),
                        'host_tasks':1,'cost':None,'cost_status':'managed_by_host',
                        'internal_requests':'controlled_by_host_not_measured',
                        'token_limit_note':'宿主内部 token 由宿主管理；本地限制资料长度、并发和运行时间'}
        return parsed
