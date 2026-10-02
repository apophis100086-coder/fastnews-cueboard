"""Official local CLI adapters. No extracted login tokens or model API keys."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import re
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
               'APPDATA','USERPROFILE','HOMEDRIVE','HOMEPATH','HOME',
               'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','LANG','TERM'}
    return {k:v for k,v in os.environ.items() if k.upper() in allowed}


def host_command(host):
    if host!='workbuddy':return None
    from .workbuddy_runtime import discover_command
    return discover_command()


def host_status():
    from .workbuddy_runtime import connection_status
    return {'codex':{'installed':False,'disabled':True,'label':'Codex（此版本停用）'},
        'workbuddy':connection_status(), 'limits':dict(reader_limits(),max_parallel_tasks=1),
        'billing_note':'只使用 WorkBuddy 官方入口；账户权益由 WorkBuddy 管理，不配置付费 API。',
        'agent_direct':{'mode':'agent_direct','available':True,
            'note':'本机 CLI 无法直连模型；已启用「智能体直连」——检索与成稿由 WorkBuddy 智能体完成，结果回写看板（见 docs/AGENT_BRIDGE.md）。'}}


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


def parse_codex_output(output, final, *, incomplete=False):
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
        if event.get('type')=='turn.failed' and not incomplete:
            raise HostUnavailable('Codex 任务未完成；请在 Codex 中检查登录、网络或剩余额度')
    if pending_error and not incomplete:
        raise HostUnavailable('Codex 任务未完成；请在 Codex 中检查登录、网络或剩余额度')
    return HostResult(loads(final),usage,{'web_actions':len(searches),'visited_urls':sorted(urls),
        'actions':actions, 'tool_excerpts':[], 'turn_completed':completed,
        'incomplete':incomplete,
        'recovered_error_events':recovered_errors,
        'content_observability':'CLI action records do not establish returned page text or successful retrieval'})


def run_host(host, prompt, schema, *, web_search=True, timeout=None, progress=None, control=None,
             model=None, reasoning_effort=None, input_character_limit=36000, task_kind='research'):
    if type(input_character_limit) is not int or not 1000<=input_character_limit<=128000:
        raise ValueError('宿主任务输入容量无效')
    if host!='workbuddy':raise HostUnavailable('此项目只连接 WorkBuddy，Codex 运行入口已停用')
    if model is not None and (not isinstance(model,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',model) or model.startswith(('gpt-','codex'))):
        raise ValueError('请选择 WorkBuddy 已验证的模型')
    if reasoning_effort is not None and reasoning_effort not in {'minimal','low','medium','high','xhigh','max'}:
        raise ValueError('WorkBuddy 推理档位无效')
    limits=reader_limits()
    timeout=limits['timeout_seconds'] if timeout is None else min(timeout,limits['timeout_seconds'])
    if control:
        control.check()
    command = host_command(host)
    if command is None:
        raise HostUnavailable('未找到所选宿主的命令行入口，请先安装并登录对应软件')
    if not isinstance(prompt,str) or len(prompt)>input_character_limit:
        raise ValueError('本次资料超过宿主任务输入上限，请减少来源或缩短时间范围')
    env = clean_environment()
    flags = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    if control:
        timeout=min(timeout,control.host_timeout())
    # The outer task also reserves verification and saving time. Tell the model
    # its earlier, actual deadline rather than only the outer 480-second limit.
    model_deadline=(datetime.now(timezone.utc)+timedelta(seconds=timeout)).isoformat()
    timing=(f'运行时限：本次模型最多使用 {timeout:.0f} 秒，必须在 {model_deadline} 前返回完整JSON。'
            '这早于资料中可能出现的整项任务截止时间，以本条为准。')
    if task_kind=='writing':
        timing+='直接使用已提供的材料完成当前写作步骤。'
    else:
        timing+=('预留最后至少90秒组织和输出结果；时间不足立即结束检索，用已取得材料交付并列明未检查缺口。'
                 '查询和容量均为上限，不要求用满。')
    prompt=timing+'\n'+prompt
    if len(prompt)>input_character_limit:
        raise ValueError('本次资料及运行指令超过宿主任务输入上限')
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
        args = command+['--print','--output-format','stream-json','--verbose','--permission-mode','dontAsk',
            '--tools','WebSearch,WebFetch' if web_search else '',
            '--allowedTools','WebSearch,WebFetch' if web_search else '',
            '--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--setting-sources','',
            '--settings','{"disableAllHooks":true}','--no-session-persistence',
            '--json-schema',json.dumps(schema,ensure_ascii=False)]
        if model:args+=['--model',model]
        if reasoning_effort:args+=['--effort',reasoning_effort]
        env['CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS']='1'
        started = time.monotonic()
        if progress:
            progress({'stage':'host','message':f'正在使用 {host} 整理资料'+('并补充联网检索' if web_search else '')})
        try:
            # stdin carries public material; no user text is interpreted by a shell.
            process = subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                text=True,encoding='utf-8',errors='replace',cwd=root,env=env,**flags)
            group = ProcessGroup(process)
            partial_output=''
            failure=None
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
                        except subprocess.TimeoutExpired as pending:
                            partial_output=pending.output or partial_output
                            first=False
                else:
                    output, diagnostic = process.communicate(prompt,timeout=timeout)
                result = subprocess.CompletedProcess(args,process.returncode,output,diagnostic)
            except BaseException as exc:
                failure=exc
                raise
            finally:
                group.close()
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                # Preserve observable public search actions on timeout/cancel.
                # Never retain raw diagnostics, reasoning or login output.
                if failure is not None:
                    # Windows communicate keeps data in reader threads and may
                    # omit TimeoutExpired.output. Drain after killing the group.
                    try:
                        drained,diagnostic=process.communicate(timeout=1)
                        partial_output=drained or partial_output
                    except (OSError,subprocess.TimeoutExpired):
                        pass
                    if isinstance(partial_output,bytes):
                        partial_output=partial_output.decode('utf-8',errors='replace')
                    from .workbuddy_runtime import parse_output
                    partial=parse_output(partial_output[:4_000_000],incomplete=True)
                    partial.trace['trace_truncated']=len(partial_output)>4_000_000
                    failure.host_trace=partial.trace
                    # Some official CLI versions print an authentication error
                    # then keep a background handle alive. Report that cause,
                    # even when the process needed to be stopped at the deadline.
                    if getattr(failure,'code',None)!='cancelled':
                        from .workbuddy_runtime import diagnose_failure
                        known=diagnose_failure(0,partial_output,locals().get('diagnostic',''))
                        if known:
                            error=HostUnavailable(known);error.host_trace=partial.trace
                            raise error from None
        except subprocess.TimeoutExpired:
            raise HostUnavailable(f'宿主任务达到 {timeout} 秒上限，已停止；请缩小来源范围后重试') from None
        except OSError:
            raise HostUnavailable('无法启动模型任务，请检查宿主软件是否仍在安装位置') from None
        from .workbuddy_runtime import parse_output,diagnose_failure
        failure=diagnose_failure(result.returncode,result.stdout,result.stderr)
        if failure:raise HostUnavailable(failure)
        if len(result.stdout)>4_000_000:raise HostUnavailable('WorkBuddy 返回内容超过本次容量')
        parsed=parse_output(result.stdout)
        parsed.usage = {'host':host,'billing':'workbuddy_account','api_key_used':False,
                        'model_actual':parsed.trace.get('model_actual'),
                        'reasoning_effort_actual':parsed.trace.get('reasoning_effort_actual'),
                        'model_requested':model,'reasoning_effort_requested':reasoning_effort,
                        'input_tokens':parsed.usage.get('input_tokens'),
                        'output_tokens':parsed.usage.get('output_tokens'),
                        'cached_input_tokens':parsed.usage.get('cached_input_tokens'),
                        'elapsed_seconds':round(time.monotonic()-started,2),
                        'host_tasks':1,'cost':None,'cost_status':'managed_by_host',
                        'internal_requests':'controlled_by_host_not_measured',
                        'token_limit_note':'宿主内部 token 由宿主管理；本地限制资料长度、并发和运行时间'}
        return parsed
