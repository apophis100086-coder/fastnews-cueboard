"""Local API settings; Windows protects credentials for the current user."""
import base64
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from .workspace import ROOT


def location(root=None):
    return Path(root or ROOT / 'data/connections') / 'api.json'


def protect(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('此发行版的密钥存储需要 Windows 用户保护功能')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(value, validate=True) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('无法读取本机密钥，请在当前 Windows 账户重新填写')
    try:
        output = ctypes.string_at(result.data, result.size)
        return output.decode('utf-8') if decrypt else base64.b64encode(output).decode('ascii')
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def load(root=None, secret=False):
    path = location(root)
    try:
        value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(value,dict):raise ValueError()
    except (OSError,ValueError):
        raise ValueError('本机 API 配置无法读取，请在使用入口重新填写并保存连接') from None
    if secret and value.get('protected_key'):
        value['api_key'] = protect(value['protected_key'], decrypt=True)
    return value


def validate_base(value):
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError('请填写 HTTPS API 基础地址，例如 https://api.openai.com/v1')
    p = urlsplit(value.strip())
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.port not in (None, 443):
        raise ValueError('API 地址必须为不带密钥和查询参数的公开 HTTPS 地址')
    if p.path.rstrip('/').endswith(('/chat/completions', '/responses')):
        raise ValueError('请填写基础地址（例如以 /v1 结尾），不要附加 /chat/completions 或 /responses')
    import ipaddress
    if p.hostname.lower() == 'localhost' or p.hostname.lower().endswith(('.localhost', '.local')):
        raise ValueError('API 地址不能使用本机或内网地址')
    try:
        addr = ipaddress.ip_address(p.hostname)
    except ValueError:
        addr = None
    if addr is not None and (not addr.is_global or addr.is_reserved or addr.is_multicast):
        raise ValueError('API 地址不能使用本机或内网地址')
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip('/'), '', ''))


def public_status(root=None):
    try:v = load(root)
    except ValueError as exc:v={'last_error':str(exc),'model_status':'failed'}
    fields = ('base_url','model','protocol','web_search','authorized','max_requests','max_output_tokens','response_format','checked_at','model_status','web_status','last_error')
    result = {k: v.get(k) for k in fields}
    result.update(configured=bool(v.get('base_url') and v.get('model') and v.get('protected_key')),
                  key_saved=bool(v.get('protected_key')), installed=True)
    result['note'] = '密钥仅由当前 Windows 账户解密；普通兼容接口只整理已有材料。费用由你配置的服务商收取。'
    return result


def save(data, root=None):
    try:old = load(root)
    except ValueError:old = {}
    value = dict(base_url=validate_base(data.get('base_url', old.get('base_url', ''))),
                 model=data.get('model', old.get('model', '')).strip(),
                 protocol=data.get('protocol', old.get('protocol', 'chat_completions')),
                 response_format=data.get('response_format', old.get('response_format', 'json_object')),
                 web_search=data.get('web_search', False), authorized=data.get('authorized', False))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}', value['model']):
        raise ValueError('请填写服务商给出的有效模型 ID')
    if value['protocol'] not in {'chat_completions','responses'} or value['response_format'] not in {'json_object','json_schema'}:
        raise ValueError('接口协议或 JSON 返回格式不受支持')
    if any(type(value[k]) is not bool for k in ('web_search','authorized')):
        raise ValueError('联网及授权设置必须为布尔值')
    if value['web_search'] and value['protocol'] != 'responses':
        raise ValueError('普通 Chat Completions 不提供统一联网接口；请关闭 API 联网，或选择支持 web_search 的 Responses 服务')
    for key, default, minimum, maximum in [('max_requests',4,1,4),('max_output_tokens',6000,1000,12000)]:
        number=data.get(key, old.get(key, default))
        if type(number) is not int or not minimum <= number <= maximum:
            raise ValueError('单任务最多 1—4 次请求，每次最多 1000—12000 输出 token')
        value[key]=number
    key=data.get('api_key','')
    if not isinstance(key,str) or len(key)>2048 or any(c.isspace() for c in key):
        raise ValueError('密钥格式无效，请检查空格或换行')
    if key:
        value['protected_key']=protect(key)
    elif old.get('protected_key'):
        if value['base_url'] != old.get('base_url'):
            raise ValueError('更换 API 地址时请重新填写对应密钥，避免把旧密钥发送给另一服务')
        value['protected_key']=old['protected_key']
    else:
        raise ValueError('首次配置需要填写 API 密钥')
    value.update(model_status='not_verified',web_status='not_verified' if value['web_search'] else 'disabled')
    from .agent_runner import atomic_json
    path=location(root);path.parent.mkdir(parents=True,exist_ok=True);atomic_json(path,value)
    return public_status(root)


def record_probe(result=None, error=None, root=None):
    from .agent_runner import atomic_json
    value=load(root)
    value.update(checked_at=datetime.now(timezone.utc).isoformat(),model_status='available' if result else 'failed',
                 last_error=error,web_status='not_verified' if value.get('web_search') else 'disabled')
    atomic_json(location(root),value)
    return public_status(root)
