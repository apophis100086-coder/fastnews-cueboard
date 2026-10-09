"""Explicitly authorized portable DeepSeek preset, separate from tracked source."""
import json
from pathlib import Path

DEFAULTS=dict(base_url='https://api.deepseek.com',model='deepseek-flash',protocol='chat_completions',
              response_format='json_object',web_search=False,authorized=False,max_requests=4,max_output_tokens=6000)
SEED='data/setup/shared-api.json'
NOTICE=('本发行包按项目所有者授权内置共享 DeepSeek 密钥。持有压缩包的人可以读取并使用该密钥，'
        '相关费用计入提供者账户。首次启动会另存为当前 Windows 账户加密的本机配置；'
        '发行包内的共享配置仍然可读取。仅向预定带教分享本包。')


def read_seed(path):
    try:
        v=json.loads(Path(path).read_text(encoding='utf-8'))
        key=v.get('api_key')
        if not isinstance(key,str) or not 10<=len(key)<=2048 or any(c.isspace() for c in key):raise ValueError()
        if v.get('base_url',DEFAULTS['base_url'])!=DEFAULTS['base_url']:raise ValueError()
    except (OSError,ValueError,AttributeError):
        raise ValueError('共享 DeepSeek 配置无效，请使用完整发行包') from None
    return dict(DEFAULTS,api_key=key)


def bootstrap(project):
    from .api_connection import location,save,load
    from .agent_runner import atomic_json
    project=Path(project);seed=project/SEED;connections=project/'data/connections'
    if location(connections).exists() or not seed.is_file():return False
    save(read_seed(seed),connections)
    value=load(connections);value['credential_origin']='shared_package'
    atomic_json(location(connections),value)
    return True
