"""Official WorkBuddy bundled CLI discovery and observable event parsing."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'data/workbuddy/connection.json'


def installation_roots():
    local=Path(os.environ.get('LOCALAPPDATA',''))
    roots=[local/'Programs/WorkBuddy',Path(os.environ.get('ProgramFiles','C:/Program Files'))/'WorkBuddy']
    setting=ROOT/'config/workbuddy.local.json'
    if setting.exists():
        value=json.loads(setting.read_text(encoding='utf-8')).get('installation_directory')
        if value:roots.insert(0,Path(value))
    return roots


def discover_command():
    node=ROOT/'runtime/node/node.exe'
    if not node.is_file():
        found=shutil.which('node');node=Path(found) if found else None
    if not node or not node.is_file():return None
    for root in installation_roots():
        for relative in ('resources/app.asar.unpacked/cli/bin/codebuddy','resources/app.asar.unpacked/cli/dist/codebuddy.js'):
            cli=root/relative
            if cli.is_file():return [str(node.resolve()),str(cli.resolve())]
    return None


def login_entry():
    """Bundled desktop builds can omit the interactive CLI entirely."""
    command=discover_command()
    if command:
        cli=Path(command[-1])
        interactive=cli.parent.parent/'dist/codebuddy.js' if cli.parent.name=='bin' else cli
        if interactive.is_file():return {'kind':'cli','command':command}
    for root in installation_roots():
        desktop=root/'WorkBuddy.exe'
        if desktop.is_file():return {'kind':'desktop','command':[str(desktop.resolve())]}
    return {'kind':'unavailable','command':None}


def installation_identity():
    command=discover_command()
    if not command:return None
    return hashlib.sha256(json.dumps([(p,Path(p).stat().st_size,Path(p).stat().st_mtime_ns) for p in command]).encode()).hexdigest()


def load_connection():
    try:record=json.loads(CACHE.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}
    return record if record.get('installation_identity')==installation_identity() else {}


def save_connection(record):
    CACHE.parent.mkdir(parents=True,exist_ok=True)
    target=CACHE.with_suffix('.tmp');target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');target.replace(CACHE)


def connection_status():
    saved=load_connection();installed=bool(discover_command())
    return dict(installed=installed,label='WorkBuddy',login_entry=login_entry()['kind'],login=saved.get('login','unchecked'),
        model=saved.get('model','unchecked'),web=saved.get('web','unchecked'),
        checked_at=saved.get('checked_at'),model_actual=saved.get('model_actual'),
        verified_models=saved.get('verified_models',[]),
        note=saved.get('note') or ('已找到官方入口；点击检查连接验证登录、模型和联网。' if installed else '请安装 WorkBuddy 客户端。'),
        last_error=saved.get('last_error'))


def diagnose_failure(code,output,diagnostic):
    # News articles can themselves mention quota, authentication or balances.
    # Inspect error envelopes, not successful article payloads.
    fragments=[str(diagnostic)]
    for line in str(output).splitlines():
        try:event=json.loads(line)
        except ValueError:fragments.append(line);continue
        rows=event if isinstance(event,list) else [event]
        for row in rows:
            if isinstance(row,dict) and (row.get('is_error') or row.get('type')=='error' or str(row.get('subtype','')).startswith('error')):
                fragments.append(json.dumps(row,ensure_ascii=False))
    text=' '.join(fragments).lower()
    if any(x in text for x in ('authentication required','please use /login','not logged in','unauthorized','请先登录')):
        return 'WorkBuddy 后台未取得有效登录状态。请在 WorkBuddy 客户端确认登录，再点击“检查连接”；客户端已登录仍出现此提示，表示后台账户连接尚未接通。'
    if any(x in text for x in ('insufficient quota','quota exceeded','insufficient credits','余额不足','额度不足')):
        return 'WorkBuddy 账户额度不足；请在 WorkBuddy 查看账户状态。未切换到其他入口。'
    if any(x in text for x in ('model not found','model unavailable','unsupported model','invalid model')):
        return '当前 WorkBuddy 账户无法使用所选模型；请重新检查默认模型。'
    if code:return 'WorkBuddy 未完成任务，请检查账户、网络或本机连接记录。'
    return None


def _public_url(value):
    from .metadata import normalize_url
    if not isinstance(value,str) or not value.startswith(('http://','https://')):return None
    try:return normalize_url(value)
    except ValueError:return None


def parse_output(output,*,incomplete=False):
    """Keep tool observations, never assistant reasoning or authentication data."""
    from .host_provider import HostResult,HostUnavailable
    from .strict_json import loads
    try:
        single=json.loads(output);events=single if isinstance(single,list) else [single]
    except ValueError:
        events=[]
        for line in output.splitlines():
            try:events.append(json.loads(line))
            except ValueError:continue
    actions=[];by_id={};visited=set();excerpts=[];final=None;actual=None;effort=None
    for event in events:
        if not isinstance(event,dict):continue
        message=event.get('message') or {}
        if not isinstance(message,dict):message={}
        if event.get('type')=='system' and event.get('subtype')=='init':
            actual=event.get('model') or actual;effort=event.get('effort') or effort
        if event.get('type')=='assistant':actual=message.get('model') or actual
        content=message.get('content',[])
        if not isinstance(content,list):content=[]
        for block in content:
            if not isinstance(block,dict):continue
            if block.get('type')=='tool_use' and block.get('name') in {'WebSearch','WebFetch'}:
                args=block.get('input') or {};sid=block.get('id') or 'tool_'+str(len(actions)+1)
                url=_public_url(args.get('url'));query=args.get('query') if isinstance(args.get('query'),str) else None
                action=dict(action_id=sid,type='search' if block['name']=='WebSearch' else 'open_page',
                    status='started',urls=[url] if url else [],query=query,queries=[],ref_id=url,result_content_available=False)
                actions.append(action);by_id[sid]=action
            elif block.get('type')=='tool_result' and block.get('tool_use_id') in by_id:
                action=by_id[block['tool_use_id']];raw=block.get('content','')
                text=raw if isinstance(raw,str) else '\n'.join(p.get('text','') for p in raw if isinstance(p,dict)) if isinstance(raw,list) else ''
                success=not block.get('is_error',False) and bool(text.strip())
                action.update(status='completed' if success else 'failed',result_content_available=success)
                if success and action['type']=='open_page':
                    visited.update(action['urls'])
                    for url in action['urls']:excerpts.append(dict(url=url,excerpt=text[:18000],text=text[:18000],kind='tool_output',tool_ref=action['action_id'],truncated=len(text)>18000))
        if event.get('type')=='result' or 'structured_output' in event or ('result' in event and 'usage' in event):final=event
    failed=bool(final and (final.get('is_error') or str(final.get('subtype','')).startswith('error')))
    trace=dict(web_actions=len(actions),actions=actions,visited_urls=sorted(visited),tool_excerpts=excerpts,
        turn_completed=bool(final and not failed),incomplete=incomplete,
        model_actual=actual,reasoning_effort_actual=effort,
        content_observability='Observed WebFetch tool output; original page facts require independent retrieval, search summaries are not full text')
    if incomplete:return HostResult({}, {},trace)
    if not final or failed:
        raise HostUnavailable(diagnose_failure(0,output,'') or 'WorkBuddy 未返回完整结果；已保留可观察的运行记录。')
    result=final.get('structured_output')
    if result is None:result=final.get('result')
    if isinstance(result,str):
        try:result=loads(result)
        except ValueError:raise HostUnavailable('WorkBuddy 返回了文本，但结构化结果未通过解析；请查看本轮记录。') from None
    if not isinstance(result,dict):raise HostUnavailable('WorkBuddy 未返回有效的结构化结果。')
    usage=final.get('usage') or {}
    models=final.get('modelUsage') or final.get('model_usage')
    if isinstance(models,dict) and len(models)==1:trace['model_actual']=next(iter(models))
    return HostResult(result,usage,trace)
