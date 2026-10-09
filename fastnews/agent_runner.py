"""Bound local exchange with WorkBuddy; responses remain untrusted model output."""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from .host_provider import HostResult, HostUnavailable
from .task_control import TaskStopped
from .workspace import ROOT, identity

EXCHANGE_ROOT = ROOT / 'data/agent-exchange'
BINDING = ('workspace_id', 'run_id', 'step', 'request_hash')
ACTIVE = {'waiting', 'processing'}


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def exchange_dir(run_id, root=None):
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,64}', run_id):
        raise ValueError('Invalid run id')
    base = Path(root or EXCHANGE_ROOT).resolve()
    folder = base / run_id
    if folder.is_symlink() or folder.resolve().parent != base:
        raise ValueError('任务目录不属于当前工作区')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def sidecar(request, suffix):
    return request.with_name(request.name.replace('.request.json', suffix))


def request_path(path, root=None):
    base = Path(root or EXCHANGE_ROOT).resolve()
    path = Path(path)
    if path.is_symlink() or path.resolve().parent.parent != base or not re.fullmatch(r'step-\d+\.request\.json', path.name):
        raise ValueError('请求文件不属于当前工作区')
    return path.resolve()


def state_of(request):
    closed = sidecar(request, '.closed.json')
    if closed.exists():
        return json.loads(closed.read_text(encoding='utf-8'))
    path = sidecar(request, '.state.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def set_state(request, status, **extra):
    current = state_of(request)
    current.update(status=status, updated_at=datetime.now(timezone.utc).isoformat(), **extra)
    if status not in ACTIVE:
        atomic_json(sidecar(request, '.closed.json'), current)
    atomic_json(sidecar(request, '.state.json'), current)


def pending_requests(root=None):
    items = []
    base = Path(root or EXCHANGE_ROOT)
    for path in base.glob('*/step-*.request.json'):
        try:
            request = request_path(path, base)
            meta = json.loads(request.read_text(encoding='utf-8'))
            status = state_of(request).get('status')
            if meta.get('protocol_version') != 2 or status not in ACTIVE:
                continue
            items.append({k: meta.get(k) for k in (*BINDING, 'host', 'kind', 'web_search', 'created_at', 'respond_by')}
                         | dict(status=status, path=str(request)))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(items, key=lambda i: (i['created_at'], i['run_id'], i['step']))


def claim_request(path, *, root=None, workspace_id=None):
    path = request_path(path, root)
    meta = json.loads(path.read_text(encoding='utf-8'))
    if meta.get('workspace_id') != (workspace_id or identity()):
        raise ValueError('请求工作区不匹配')
    if state_of(path).get('status') != 'waiting':
        raise ValueError('此步骤已接手或已结束，请检查待办')
    claim = sidecar(path, '.claim.json')
    with claim.open('x', encoding='utf-8') as out:
        json.dump({k: meta[k] for k in BINDING}, out)
    set_state(path, 'processing')
    return meta


def validate_envelope(raw, meta):
    if not isinstance(raw, dict) or any(raw.get(k) != meta[k] for k in BINDING):
        raise HostUnavailable('响应的工作区、任务、步骤或输入版本不匹配；已保留材料，请重新发起任务')
    if not isinstance(raw.get('payload'), dict):
        raise HostUnavailable('智能体响应缺少有效的 payload 对象；已保留材料')


def submit_response(path, raw, *, root=None, workspace_id=None):
    path = request_path(path, root)
    meta = json.loads(path.read_text(encoding='utf-8'))
    if meta.get('workspace_id') != (workspace_id or identity()) or state_of(path).get('status') not in ACTIVE:
        raise ValueError('任务已结束或不属于当前工作区，拒绝回写')
    validate_envelope(raw, meta)
    response = sidecar(path, '.response.json')
    lock = sidecar(path, '.response.lock')
    with lock.open('x'):
        if response.exists():
            raise ValueError('此步骤已收到响应，拒绝重复回写')
        atomic_json(response, raw)
    return {'run_id': meta['run_id'], 'step': meta['step'], 'status': 'submitted'}


def recover_requests(root=None):
    """Only after owning the server lock; no automatic rerun on restart."""
    for item in pending_requests(root):
        path = Path(item['path'])
        if state_of(path).get('owner_pid') != os.getpid():
            set_state(path, 'interrupted', reason='server_restarted')


def file_runner(run_id, *, kind='writing', root=None, workspace_id=None, expected_host=None):
    folder = exchange_dir(run_id, root)
    workspace_id = workspace_id or identity()
    state = {'step': max((int(p.name.split('-')[1].split('.')[0]) for p in folder.glob('step-*.request.json')), default=0)}

    def runner(host, prompt, schema, *, web_search=True, timeout=None, progress=None, control=None,
               model=None, reasoning_effort=None, input_character_limit=36000, task_kind=None):
        if host not in {'workbuddy','codex'} or (expected_host and host!=expected_host):
            raise HostUnavailable('任务与所选智能体入口不匹配')
        label='Codex' if host=='codex' else 'WorkBuddy'
        if not isinstance(prompt, str) or len(prompt) > input_character_limit:
            raise ValueError('本次任务输入超过容量上限，请精简选材')
        if control:
            control.check()
        unbounded = bool(control and control.unbounded)
        timeout = control.host_timeout(timeout or 600, 5) if control else timeout or 600
        state['step'] += 1
        step = state['step']
        path = folder / f'step-{step:02d}.request.json'
        meta = dict(protocol_version=2, workspace_id=workspace_id, run_id=run_id, step=step, host=host,
                    kind=task_kind or kind, web_search=bool(web_search),
                    created_at=datetime.now(timezone.utc).isoformat(), respond_by=None,
                    nonce=uuid.uuid4().hex, prompt=prompt, schema=schema)
        meta['request_hash'] = hashlib.sha256(json.dumps(meta, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        meta['agent_instructions'] = (
            '完整阅读 prompt 与 schema；只返回符合 schema 的 payload。使用项目内 agent_bridge.py claim 接手，'
            '使用 respond 回写；响应包含 workspace_id、run_id、step、request_hash、payload。'
            '联网资料保留真实工具记录与来源，搜索摘要不标为已读原文；不要猜实际模型。'
            '对本任务每个步骤顺序处理，完成后再查看 pending-writes，直到任务结束。')
        atomic_json(path, meta)
        set_state(path, 'waiting', owner_pid=os.getpid())
        def report(status):
            if progress:
                progress(dict(stage='agent_direct', agent_status=status, step=step,
                    message={'waiting':f'等待 {label} 接手：请在该项目对话中说“处理看板任务”',
                             'processing':f'{label} 已接手，正在处理本步骤',
                             'completed':'本步骤已收到响应，继续业务校验'}[status]))
        report('waiting')
        end = None if unbounded else time.monotonic() + timeout
        processing = False
        try:
            while True:
                if control:
                    control.check()
                if state_of(path).get('status') == 'processing' and not processing:
                    processing = True; report('processing')
                response = sidecar(path, '.response.json')
                if response.exists():
                    if response.is_symlink() or response.stat().st_size > 2_000_000:
                        raise HostUnavailable('智能体响应文件无效或超过容量')
                    try:
                        raw = json.loads(response.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        raise HostUnavailable('智能体响应不是有效 JSON；材料与中间稿已保留') from None
                    validate_envelope(raw, meta)
                    if control:
                        control.check()
                    break
                if end is not None and time.monotonic() >= end:
                    raise TaskStopped('timed_out')
                time.sleep(0.1)
            usage = dict(host=host+'_agent', billing=host+'_account', api_key_used=False,
                model_actual=None, model_reported=raw.get('model'), model_requested=model,
                reasoning_effort_requested=reasoning_effort, input_tokens=None, output_tokens=None,
                cost=None, cost_status='managed_by_host', exchange_file=path.name)
            trace = raw.get('trace') if isinstance(raw.get('trace'), dict) else {}
            trace = dict(trace, agent_direct=True, evidence_status='agent_reported', exchange_dir=str(folder))
            set_state(path, 'completed')
            report('completed')
            return HostResult(raw['payload'], usage, trace)
        except Exception as exc:
            set_state(path, getattr(exc, 'code', 'failed'), reason=type(exc).__name__)
            raise

    return runner
