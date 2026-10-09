"""One explicit runtime choice; no silent cross-provider fallback."""
from pathlib import Path
from .host_provider import HostUnavailable

LABELS={'workbuddy':'WorkBuddy','codex':'Codex','api':'自接 API','none':'暂不使用模型'}


def is_agent(settings):
    return settings.get('host')=='codex' or (settings.get('host')=='workbuddy' and bool(settings.get('agent_direct')))


def preflight(settings, connection_root):
    host=settings.get('host','none')
    if host=='none':raise HostUnavailable('请先选择 WorkBuddy、Codex 或自接 API 入口')
    if host=='api':
        from .api_connection import public_status
        status=public_status(connection_root)
        if not status['configured'] or not status.get('authorized'):
            raise HostUnavailable('请在“使用入口”保存 API 地址、模型、密钥及材料发送授权')
        if settings.get('web_search') and not status.get('web_search'):
            raise HostUnavailable('此 API 只整理已有材料。请在使用入口关闭模型补搜，或配置支持联网的 Responses 服务')


def runner_options(settings, run_id, database, kind='writing'):
    if is_agent(settings):
        from .agent_runner import file_runner
        from .workspace import identity
        return {'runner':file_runner(run_id,kind=kind,root=Path(database).parent/'agent-exchange',
            workspace_id=identity(database),expected_host=settings['host'])}
    if settings.get('host')=='api':
        from .api_provider import make_runner
        return {'runner':make_runner(Path(database).parent/'connections')}
    return {}


def status(database):
    import json
    from .api_connection import public_status
    from .agent_runner import pending_requests
    root=Path(database).parent
    checks={}
    for host in ('codex','workbuddy'):
        path=root/'connections'/('probe-'+host+'.json')
        if path.exists():
            try:checks[host]=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
    return dict(api=public_status(root/'connections'),pending=pending_requests(root/'agent-exchange'),checks=checks)
