"""Agent-direct model runner: exchange request/response files with the agent.

WorkBuddy 5.6.2 的模型登录态绑定在桌面端沙箱，看板自带 CLI 拿不到（401 /
account list is empty）。因此写作流水线的"模型调用"这一步外置给 WorkBuddy
智能体：本模块把每个步骤的 prompt+schema 落成
data/agent-exchange/<run_id>/step-NN.request.json，阻塞等待同名的
step-NN.response.json 出现后按 run_host 的约定返回 HostResult。

看板的校验、版本保存、失败保留逻辑完全不变——智能体只是"充当模型"，
不绕过任何一道程序检查。响应文件同样由智能体原子写入，不存在半成品读取。
"""
from datetime import datetime, timezone
import json
import re
import time
from pathlib import Path

from .host_provider import HostResult, HostUnavailable
from .task_control import TaskStopped

ROOT = Path(__file__).resolve().parents[1]
EXCHANGE_ROOT = ROOT / 'data' / 'agent-exchange'

AGENT_HINT = '智能体直连：本步骤已交给 WorkBuddy 智能体，请在对话中说“处理看板任务”。'


def _atomic_write(path, text):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def exchange_dir(run_id):
    # 写作 run 以 writing_ 开头；晨报/产品检索的 job_id 是纯 uuid hex。
    # 只要目录名是安全标识即可，任务类型由 request 文件里的 kind 字段区分。
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,64}', run_id):
        raise ValueError('Invalid run id')
    folder = EXCHANGE_ROOT / run_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def pending_requests(root=None):
    """List exchange requests that have no response yet (oldest first)."""
    base = Path(root) if root else EXCHANGE_ROOT
    items = []
    if not base.is_dir():
        return items
    for request in sorted(base.glob('*/step-*.request.json')):
        response = request.with_name(request.name.replace('.request.json', '.response.json'))
        if response.exists():
            continue
        try:
            meta = json.loads(request.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            meta = {}
        items.append({'run_id': request.parent.name, 'step': meta.get('step'),
                      'kind': meta.get('kind'), 'web_search': meta.get('web_search'),
                      'created_at': meta.get('created_at'), 'respond_by': meta.get('respond_by'),
                      'path': str(request)})
    return items


def file_runner(run_id, *, kind='writing'):
    """Return a run_host-compatible runner bound to one task run.

    kind 用于标记 exchange 任务类型：writing / morning / product / research。
    调用方（server.py）不传 task_kind 时，runner 用这里的 kind 落 request 文件。
    """
    folder = exchange_dir(run_id)
    state = {'step': 0}

    def runner(host, prompt, schema, *, web_search=True, timeout=None, progress=None, control=None,
               model=None, reasoning_effort=None, input_character_limit=36000, task_kind=None):
        task_kind = task_kind or kind
        if not isinstance(prompt, str) or len(prompt) > input_character_limit:
            raise ValueError('本次任务输入超过容量上限，请精简选材')
        if control:
            control.check()
            timeout = control.host_timeout(timeout if isinstance(timeout, (int, float)) else 10 ** 9, 5)
        elif not isinstance(timeout, (int, float)):
            timeout = 600
        state['step'] += 1
        step = state['step']
        now = datetime.now(timezone.utc)
        request = folder / f'step-{step:02d}.request.json'
        response = folder / f'step-{step:02d}.response.json'
        # 智能体直连不设同步模型时限：智能体在对话里异步回写，快慢取决于对话节奏。
        # 看板持续等待 response 文件出现，只有用户点"停止"（control.cancelled）才中断。
        # 用 control.unbounded 标志判断（make_unbounded 会把 deadline 推到极远，
        # 因此不能再靠 deadline 是否为 None 来识别）。
        unbounded = control is not None and getattr(control, 'unbounded', False)
        _atomic_write(request, json.dumps({
            'run_id': run_id, 'step': step, 'kind': task_kind, 'web_search': bool(web_search),
            'created_at': now.isoformat(), 'respond_by': None,
            'agent_instructions': (
                '你是本步骤的模型。完整阅读 prompt（含 INPUT_JSON），需要联网时使用检索工具核实公开来源，'
                '然后只输出一个严格符合 schema 的 JSON 对象，写入本目录下同名 .response.json 文件，格式为 '
                '{"payload": <符合schema的对象>, "trace": {"note": "可选，检索与判断说明"}}。'
                'payload 之外不要输出解释文字；无法完成时让 payload.status="needs_materials" 并在 issues 列明缺口。'),
            'prompt': prompt, 'schema': schema,
        }, ensure_ascii=False, indent=1))
        if progress:
            limit = '不限时' if unbounded else f'限时约 {round(timeout)} 秒'
            progress({'stage': 'agent_direct',
                      'message': f'{AGENT_HINT}（第 {step} 步，{limit}）'})
        end = None if unbounded else time.monotonic() + timeout
        while True:
            if control:
                control.check()
            if response.exists():
                break
            if end is not None and time.monotonic() >= end:
                raise TaskStopped('timed_out')
            time.sleep(0.4)
        try:
            raw = json.loads(response.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise HostUnavailable('智能体响应文件不是有效 JSON，请查看 ' + response.name) from None
        payload = raw.get('payload') if isinstance(raw, dict) else None
        if not isinstance(payload, dict):
            raise HostUnavailable('智能体响应缺少 payload 对象，请按 request 文件中的格式重写')
        usage = {'host': 'workbuddy_agent', 'billing': 'workbuddy_account', 'api_key_used': False,
                 'model_actual': raw.get('model') or 'workbuddy-agent',
                 'model_requested': model, 'reasoning_effort_requested': reasoning_effort,
                 'input_tokens': None, 'output_tokens': None, 'cost': None,
                 'cost_status': 'managed_by_host', 'exchange_file': request.name}
        trace = raw.get('trace') if isinstance(raw.get('trace'), dict) else {}
        trace.setdefault('agent_direct', True)
        trace.setdefault('exchange_dir', str(folder))
        return HostResult(payload, usage, trace)

    return runner
