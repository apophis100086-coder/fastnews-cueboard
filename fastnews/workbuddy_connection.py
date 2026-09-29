"""Explicit connection probes; page loads never make model calls."""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import json
import os
import subprocess
from . import workbuddy_runtime as runtime


def bootstrap(database):
    from .db import connect
    if Path(database).resolve()!=runtime.ROOT/'data/fastnews.db':return
    marker=runtime.ROOT/'data/setup/applied.json'
    if marker.exists():return
    seed=runtime.ROOT/'data/setup/source-settings.json'
    settings=json.loads(seed.read_text(encoding='utf-8')) if seed.exists() else {}
    with closing(connect(database,mode='live')) as conn,conn:
        for row in settings.get('profiles',[]):
            value=json.loads(row['settings_json']);value['host']='none' if value.get('host')=='none' else 'workbuddy'
            conn.execute('INSERT OR REPLACE INTO fn_reader_profiles VALUES(?,?,?,?)',(row['profile_id'],row['name'],json.dumps(value,ensure_ascii=False),row['created_at']))
        if not settings.get('profiles'):
            conn.execute("UPDATE fn_reader_profiles SET settings_json=? WHERE profile_id='default'",(json.dumps(dict(host='workbuddy',web_search=True)),))
        for row in settings.get('preferences',[]):
            conn.execute('INSERT OR REPLACE INTO fn_source_preferences VALUES(?,?,?,?)',(row['profile_id'],row['source_id'],row['settings_json'],row['updated_at']))
    marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('{"applied":true}',encoding='utf-8')


def model_options(settings):
    if settings.get('host')!='workbuddy':return {}
    model=settings.get('workbuddy_model');effort=settings.get('workbuddy_effort')
    return {k:v for k,v in dict(model=model,reasoning_effort=effort).items() if v}


def require_verified_model(model,effort):
    if not model and not effort:return
    records=runtime.load_connection().get('verified_models',[])
    if not any(x.get('model')==(model or None) and x.get('effort')==(effort or None) for x in records):
        raise ValueError('请先通过连接检查验证所选模型与档位，再保存设置')


def login():
    command=runtime.discover_command()
    if not command:raise ValueError('尚未找到 WorkBuddy，请先安装官方 Windows 客户端')
    if os.name!='nt':raise ValueError('此试用包的首次连接引导仅支持 Windows')
    # User clicked the login button: this is an intentionally visible official UI.
    folder=runtime.ROOT/'data/workbuddy/login';folder.mkdir(parents=True,exist_ok=True)
    from .host_provider import clean_environment
    subprocess.Popen(command+['--setting-sources','','--settings','{"disableAllHooks":true}','/login'],cwd=folder,env=clean_environment(),
        creationflags=subprocess.CREATE_NEW_CONSOLE)
    return {'message':'已打开官方登录窗口。完成登录后回到看板点击“检查连接”。'}


def probe(data,control,progress=None,runner=None):
    from .host_provider import run_host
    runner=runner or run_host
    model=data.get('model') or None;effort=data.get('effort') or None
    record=runtime.load_connection()
    record.update(installation_identity=runtime.installation_identity(),checked_at=datetime.now(timezone.utc).isoformat(),
        login='unchecked',model='unchecked',web='unchecked',last_error=None)
    if not record['installation_identity']:
        record.update(note='未找到 WorkBuddy 官方入口；请先安装客户端。');runtime.save_connection(record);return record
    stages=[]
    try:
        if progress:progress(dict(stage='connection_model',message='检查官方登录与默认模型'))
        result=runner('workbuddy','仅回复 JSON {"ok":true}。这是本机连接测试。',
            {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False},
            web_search=False,timeout=50,control=control,model=model,reasoning_effort=effort)
        if result.payload.get('ok') is not True:raise ValueError('模型连接测试没有得到预期回答')
        record.update(login='authenticated',model='available',model_actual=result.usage.get('model_actual'),note='模型已返回有效结果，正在检查联网。')
        verified=dict(model=model,effort=effort,model_actual=result.usage.get('model_actual'),checked_at=record['checked_at'])
        records=record.setdefault('verified_models',[])
        if not any(v.get('model')==model and v.get('effort')==effort for v in records):records.append(verified)
        stages.append(dict(stage='model',usage=result.usage,trace=result.trace));runtime.save_connection(record)
        if progress:progress(dict(stage='connection_web',message='检查公开网页检索和读取'))
        result=runner('workbuddy','先联网搜索 Python 官方 Windows 下载页面，再用 WebFetch 打开 https://www.python.org/downloads/windows/ 。输出网页标题和原文中一个不超过30字的短句；无法访问则明确返回空串，不用记忆代替联网。',
            {'type':'object','properties':{'title':{'type':'string'},'quote':{'type':'string'}},'required':['title','quote'],'additionalProperties':False},
            web_search=True,timeout=80,control=control,model=model,reasoning_effort=effort)
        trace=result.trace;quote=result.payload.get('quote','')
        observed=bool(quote and any(quote in x.get('excerpt','') for x in trace.get('tool_excerpts',[]))
            and any(a['type']=='search' and a['status']=='completed' for a in trace.get('actions',[])))
        record.update(web='available' if observed else 'unverified',note='登录、模型与联网均已观察到成功结果。' if observed else '模型可用；未观察到完整的公开网页检索与读取证据，联网仍待验证。')
        stages.append(dict(stage='web',usage=result.usage,trace=trace,network_observed=observed))
    except Exception as exc:
        message=str(exc)[:500]
        record['last_error']=message
        if '登录' in message:record.update(login='required',model='unchecked',web='unchecked')
        elif record['model']=='available':record['web']='failed'
        else:record['model']='unavailable'
        record['note']=message
        if getattr(exc,'code',None) in {'cancelled','timed_out'}:
            record.update(stages=stages);runtime.save_connection(record);raise
    record['stages']=stages;runtime.save_connection(record);return record
