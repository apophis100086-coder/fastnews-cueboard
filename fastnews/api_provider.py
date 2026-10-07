"""Bounded OpenAI-compatible transport. No implicit retries or host fallback."""
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .host_provider import HostResult, HostUnavailable
from .strict_json import loads


def build_request(settings, prompt, schema, web_search=False):
    live_web = bool(web_search and settings.get('web_search'))
    if web_search and not live_web:
        raise HostUnavailable('此 API 未配置联网搜索；请关闭方案中的模型补搜，只整理已取得的资料，或配置 Responses 联网服务')
    instruction = '只返回符合以下 JSON Schema 的 JSON 对象，不要 Markdown 代码围栏。\n'+json.dumps(schema,ensure_ascii=False)
    if not live_web:
        instruction += '\n本次没有联网工具。只使用输入中的资料，缺少的信息标为未知，不凭记忆补造来源。'
    request=dict(model=settings['model'],stream=False)
    if settings['protocol']=='chat_completions':
        request.update(messages=[dict(role='system',content=instruction),dict(role='user',content=prompt)],
                       max_tokens=settings['max_output_tokens'])
        request['response_format']=({'type':'json_schema','json_schema':{'name':'fastnews_result','schema':schema,'strict':True}}
            if settings['response_format']=='json_schema' else {'type':'json_object'})
        return '/chat/completions',request
    request.update(input=prompt,instructions=instruction,store=False,max_output_tokens=settings['max_output_tokens'])
    request['text']={'format':({'type':'json_schema','name':'fastnews_result','schema':schema,'strict':True}
        if settings['response_format']=='json_schema' else {'type':'json_object'})}
    if live_web:
        request.update(tools=[{'type':'web_search'}],max_tool_calls=8,include=['web_search_call.action.sources'])
    return '/responses',request


def post_json(url, key, body, timeout):
    """Pin validated public DNS, verify TLS; never redirect Authorization."""
    from .network import resolve_public, _PinnedHTTP
    normalized, addresses=resolve_public(url)
    p=urlsplit(normalized)
    conn=_PinnedHTTP(p.hostname,443,addresses[0],timeout,True)
    try:
        conn.request('POST',p.path or '/',body=json.dumps(body,ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+key,'Accept':'application/json'})
        response=conn.getresponse()
        if not 200 <= response.status < 300:
            messages={401:'密钥无效或已过期',403:'该服务拒绝访问此模型',404:'接口地址或模型不存在',
                      429:'服务商限流或余额不足',400:'接口不支持本次参数，请核对协议、模型和 JSON 格式'}
            raise HostUnavailable('API 请求失败：'+messages.get(response.status,'服务暂不可用')+f'（HTTP {response.status}）')
        raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise HostUnavailable('API 返回内容超过 2 MB 上限')
        try:return loads(raw.decode('utf-8'))
        except (ValueError,UnicodeError):raise HostUnavailable('API 未返回有效 JSON') from None
    finally:
        conn.close()


def scrub(value, key):
    if isinstance(value,str):return value.replace(key,'[已隐藏密钥]') if key else value
    if isinstance(value,list):return [scrub(v,key) for v in value]
    if isinstance(value,dict):return {scrub(k,key):scrub(v,key) for k,v in value.items()}
    return value


def parse_response(raw, settings):
    if not isinstance(raw,dict):raise HostUnavailable('API 返回结构不正确')
    usage=raw.get('usage') or {}; actions=[];visited=[]
    try:
        if settings['protocol']=='chat_completions':
            choice=raw['choices'][0]
            if choice.get('finish_reason')!='stop':raise HostUnavailable('API 输出未完整结束；请检查输出上限或模型拒绝原因')
            text=choice['message']['content']
        else:
            if raw.get('status')!='completed':raise HostUnavailable('Responses 任务未完整完成，已有资料保留')
            text=''.join(c.get('text','') for o in raw.get('output',[]) if o.get('type')=='message'
                         for c in o.get('content',[]) if c.get('type')=='output_text')
            for o in raw.get('output',[]):
                if o.get('type')!='web_search_call':continue
                a=o.get('action') or {};urls=[x['url'] for x in a.get('sources',[]) if isinstance(x,dict) and isinstance(x.get('url'),str)]
                if isinstance(a.get('url'),str):urls.append(a['url'])
                typ={'open_page':'open','find_in_page':'find'}.get(a.get('type'),a.get('type','search'))
                actions.append(dict(action_id=o.get('id'),type=typ,status=o.get('status'),urls=urls,
                    query=a.get('query'),queries=a.get('queries',[]),result_content_available=False))
                if typ in {'open','find'} and o.get('status')=='completed':visited+=urls
        payload=loads(text)
        if not isinstance(payload,dict):raise ValueError()
    except (KeyError,IndexError,TypeError,ValueError) as exc:
        if isinstance(exc,HostUnavailable):raise
        raise HostUnavailable('模型没有返回要求的 JSON 对象；请检查该模型是否支持结构化输出') from None
    return HostResult(payload,dict(host='api',billing='user_api',api_key_used=True,model_requested=settings['model'],
        model_actual=raw.get('model'),input_tokens=usage.get('input_tokens',usage.get('prompt_tokens')),
        output_tokens=usage.get('output_tokens',usage.get('completion_tokens')),cost=None,cost_status='provider_billed_unknown'),
        dict(actions=actions,web_actions=len(actions),visited_urls=sorted(set(visited)),tool_excerpts=[],
             evidence_status='api_tool_records',content_observability='Tool actions and citations do not establish independently read original text'))


def transport(settings, path, body, timeout, control=None):
    """Worker receives secrets on stdin only; stopping closes the worker tree."""
    from .host_provider import ProcessGroup, clean_environment
    from .task_control import TaskStopped
    worker=Path(__file__).resolve().parents[1]/'tools/api_worker.py'
    proc=subprocess.Popen([sys.executable,'-B','-X','utf8',str(worker)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,env=clean_environment(),creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    group=ProcessGroup(proc);end=time.monotonic()+timeout
    packet=json.dumps(dict(url=settings['base_url']+path,key=settings['api_key'],body=body,timeout=timeout),ensure_ascii=False).encode()
    first=True
    try:
        while True:
            if control:control.check()
            if time.monotonic()>=end:raise TaskStopped('timed_out')
            try:
                stdout,_=proc.communicate(input=packet if first else None,timeout=.15)
                break
            except subprocess.TimeoutExpired:first=False
        if len(stdout)>2_100_000:raise HostUnavailable('API 响应超过容量上限')
        try:out=loads(stdout.decode('utf-8'))
        except (ValueError,UnicodeError):raise HostUnavailable('API 连接进程未正常返回') from None
        if out.get('error'):raise HostUnavailable(out['error'])
        return out['result']
    finally:
        if proc.poll() is None:proc.kill()
        proc.wait();group.close()


def make_runner(root=None, sender=None):
    from .api_connection import load
    settings=load(root,secret=True)
    if not settings.get('api_key') or not settings.get('authorized'):
        raise HostUnavailable('请先保存 API 配置，并确认允许向此服务发送任务材料及承担调用费用')
    send=sender or transport
    count=0;characters=0
    def runner(host,prompt,schema,*,web_search=False,timeout=120,control=None,progress=None,input_character_limit=96000,**kwargs):
        nonlocal count,characters
        if host!='api':raise HostUnavailable('API 任务与所选入口不匹配')
        if control:control.check()
        if not isinstance(prompt,str) or len(prompt)>input_character_limit:raise ValueError('本次 API 输入超过容量上限')
        if count>=settings['max_requests'] or characters+len(prompt)>384000:
            raise HostUnavailable('本次 API 任务达到请求或输入容量上限，已有稿件保留')
        path,body=build_request(settings,prompt,schema,web_search)
        count+=1;characters+=len(prompt)
        if progress:progress(dict(stage='api_request',message=f'正在调用已配置 API（本任务第 {count} 次）'))
        raw=send(settings,path,body,min(timeout or 120,450),control)
        if control:control.check()
        result=parse_response(scrub(raw,settings['api_key']),settings)
        result.usage.update(request_number=count,input_characters=len(prompt))
        return result
    return runner
